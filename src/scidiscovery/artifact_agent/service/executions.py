"""Control-owned generic execution lifecycle."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import os
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from ..schema.approval import (
    ApprovalRequest,
    CompiledApprovalIdentity,
    HumanDecision,
    execution_decision_authorizes,
)
from ..schema.artifact import ArtifactRegistration
from ..schema.execution import (
    ExecutionAdmission,
    ExecutionRequest,
    ExecutionResultManifest,
    LocalFileDescriptor,
)
from ..schema.refs import ActorRef, ArtifactRef
from ..schema.common import canonical_json, canonical_sha256
from ..storage import (
    ArtifactIdentityConflictError,
    ArtifactNotFoundError,
    IdempotencyConflictError,
)
from .approvals import ApprovalService
from .artifacts import ArtifactService


# Shared with identity-preserving archive schema projection.
EXECUTION_POLICY_SCHEMA = """
                CREATE TABLE IF NOT EXISTS execution_policy_authorizations (
                    execution_id TEXT NOT NULL, digest TEXT NOT NULL,
                    record_json BLOB NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (execution_id, digest)
                );
                CREATE TABLE IF NOT EXISTS execution_current_policy (
                    execution_id TEXT PRIMARY KEY, digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_budget_pools (
                    executor TEXT NOT NULL, budget_key TEXT NOT NULL,
                    allowance_json BLOB NOT NULL,
                    PRIMARY KEY (executor, budget_key)
                );
                CREATE TABLE IF NOT EXISTS execution_budget_reservations (
                    execution_id TEXT PRIMARY KEY, executor TEXT NOT NULL,
                    budget_key TEXT NOT NULL, reserved_json BLOB NOT NULL,
                    consumed_json BLOB
                );
                CREATE TABLE IF NOT EXISTS execution_budget_extensions (
                    decision_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL,
                    executor TEXT NOT NULL, budget_key TEXT NOT NULL,
                    additional_seconds INTEGER NOT NULL CHECK(additional_seconds > 0),
                    decision_ref_json BLOB NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS execution_budget_extensions_owner
                    ON execution_budget_extensions(executor, budget_key);
                CREATE TABLE IF NOT EXISTS execution_prepared_submissions (
                    execution_id TEXT PRIMARY KEY, descriptor_json BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_budget_estimates (
                    execution_id TEXT PRIMARY KEY, estimated_json BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_observation_retries (
                    execution_id TEXT PRIMARY KEY, failures INTEGER NOT NULL CHECK(failures >= 0)
                );
"""


class ExecutionServiceError(RuntimeError):
    pass


class ExecutionStateConflict(ExecutionServiceError):
    pass


class ExecutionApprovalError(ExecutionServiceError):
    pass


class ExecutionBudgetNotApplicable(ExecutionApprovalError):
    """This execution contract has no cumulative wall-time allowance."""


@dataclass(frozen=True)
class ExecutionStatusView:
    execution_id: str
    executor: str
    state: str
    external_run_id: str | None
    result_ref: ArtifactRef | None
    created_at: str
    authorization: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutputView:
    logical_name: str
    artifact_id: str
    media_type: str
    size_bytes: int


class ExecutionNotFound(ExecutionServiceError):
    """The exact execution has never been created."""


class ExecutionService:
    """Own execution identity without interpreting domain payloads."""

    def __init__(
        self,
        *,
        artifacts: ArtifactService,
        approvals: ApprovalService,
        database_path: Path | str,
        exchange_root: Path | str,
        service_actor: ActorRef,
        deadline_monotonic: float | None = None,
        budget_extension_limits=None,
    ) -> None:
        from ...agent_execution_settings import BudgetExtensionSettings
        self.budget_extension_limits = budget_extension_limits or BudgetExtensionSettings()
        self.deadline_monotonic = deadline_monotonic
        self.artifacts = artifacts
        self.approvals = approvals
        self.database_path = Path(database_path).expanduser().absolute()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.exchange_root = Path(exchange_root).expanduser().absolute()
        self.exchange_root.mkdir(parents=True, exist_ok=True, mode=0o770)
        self.service_actor = service_actor
        self._initialize()

    @staticmethod
    def resolve_result_scope(*, artifacts, database_path, result_ref):
        """Trusted read-only lookup from an already bound immutable result."""
        result = ExecutionResultManifest.model_validate_json(artifacts.read(result_ref), strict=True)
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT * FROM executions WHERE execution_id=?', (result.execution_id,)).fetchone()
        if row is None or row['state'] != 'collected' or row['external_run_id'] != result.external_run_id or _parse_ref(row['result_ref_json']) != result_ref:
            raise ExecutionStateConflict('bound result does not identify a collected execution')
        return {'executor':row['executor'], 'external_run_id':result.external_run_id,
                'payload_ref':_parse_ref(row['payload_ref_json']), 'result_ref':result_ref}

    def create(
        self,
        *,
        executor: str,
        preparation_profile: str,
        payload_ref: ArtifactRef,
        compiled_identity: CompiledApprovalIdentity | None = None,
        labels: dict[str, str] | None = None,
        execution_id: str | None = None,
    ) -> str:
        self.artifacts.verify(payload_ref)
        execution_id = execution_id or f"exe_{uuid.uuid4().hex}"
        expected_labels = labels or {}
        request_artifact_id = "execution_request_" + hashlib.sha256(
            execution_id.encode("utf-8")
        ).hexdigest()
        registration = ArtifactRegistration(
            artifact_id=request_artifact_id,
            kind="execution_request",
            schema_id="scidiscovery.execution-request",
            payload_schema_version=1,
            media_type="application/json",
            creator=self.service_actor,
            parent_refs=(payload_ref,),
            labels=expected_labels,
            confidentiality="approval_only",
        )
        idempotency_key = f"execution:{execution_id}:request"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
        if existing is not None:
            request_ref = _parse_ref(existing["request_ref_json"])
            if (
                existing["executor"] != executor
                or _parse_ref(existing["payload_ref_json"]) != payload_ref
                or request_ref.artifact_id != request_artifact_id
            ):
                raise ExecutionServiceError(
                    "execution identity is already bound to a different request"
                )
            request = self._validate_request_artifact(
                request_ref,
                execution_id=execution_id,
                executor=executor,
                preparation_profile=preparation_profile,
                payload_ref=payload_ref,
                compiled_identity=compiled_identity,
                labels=expected_labels,
            )
            replay = self.artifacts.register(
                request.canonical_json(),
                registration,
                idempotency_key=idempotency_key,
            )
            if replay.ref != request_ref:
                raise ExecutionServiceError(
                    "execution request replay returned a different Artifact"
                )
            return execution_id
        existing_request = self._request_artifact_by_id(
            request_artifact_id,
            execution_id=execution_id,
            executor=executor,
            preparation_profile=preparation_profile,
            payload_ref=payload_ref,
            compiled_identity=compiled_identity,
            labels=expected_labels,
        )
        request = existing_request or ExecutionRequest(
            execution_id=execution_id,
            executor=executor,
            preparation_profile=preparation_profile,
            payload_ref=payload_ref,
            created_at=_timestamp(),
            compiled_identity=compiled_identity,
        )
        try:
            request_envelope = self.artifacts.register(
                request.canonical_json(),
                registration,
                idempotency_key=idempotency_key,
            )
        except (ArtifactIdentityConflictError, IdempotencyConflictError):
            request = self._request_artifact_by_id(
                request_artifact_id,
                execution_id=execution_id,
                executor=executor,
                preparation_profile=preparation_profile,
                payload_ref=payload_ref,
                compiled_identity=compiled_identity,
                labels=expected_labels,
            )
            if request is None:
                raise
            request_envelope = self.artifacts.register(
                request.canonical_json(),
                registration,
                idempotency_key=idempotency_key,
            )
        request_ref = request_envelope.ref
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO executions (
                    execution_id, executor, request_ref_json, payload_ref_json,
                    state, created_at
                ) VALUES (?, ?, ?, ?, 'created', ?)
                """,
                (
                    execution_id,
                    executor,
                    request_ref.canonical_json(),
                    payload_ref.canonical_json(),
                    request.created_at,
                ),
            )
        with self._connect() as connection:
            created = self._row(connection, execution_id)
        if (
            created["executor"] != executor
            or _parse_ref(created["request_ref_json"]) != request_ref
            or _parse_ref(created["payload_ref_json"]) != payload_ref
        ):
            raise ExecutionServiceError(
                "execution identity is already bound to a different request"
            )
        return execution_id

    def _request_artifact_by_id(
        self,
        artifact_id: str,
        *,
        execution_id: str,
        executor: str,
        preparation_profile: str,
        payload_ref: ArtifactRef,
        compiled_identity: CompiledApprovalIdentity | None,
        labels: dict[str, str],
    ) -> ExecutionRequest | None:
        try:
            envelope = self.artifacts.get_by_id(artifact_id)
        except ArtifactNotFoundError:
            return None
        return self._validate_request_artifact(
            envelope.ref,
            execution_id=execution_id,
            executor=executor,
            preparation_profile=preparation_profile,
            payload_ref=payload_ref,
            compiled_identity=compiled_identity,
            labels=labels,
        )

    def _validate_request_artifact(
        self,
        request_ref: ArtifactRef,
        *,
        execution_id: str,
        executor: str,
        preparation_profile: str,
        payload_ref: ArtifactRef,
        compiled_identity: CompiledApprovalIdentity | None,
        labels: dict[str, str],
    ) -> ExecutionRequest:
        envelope = self.artifacts.catalog(request_ref)
        try:
            request = ExecutionRequest.model_validate_json(
                self.artifacts.read(request_ref), strict=True
            )
        except ValidationError as error:
            raise ExecutionServiceError(
                "stored execution request is invalid"
            ) from error
        if (
            request.execution_id != execution_id
            or request.executor != executor
            or request.preparation_profile != preparation_profile
            or request.payload_ref != payload_ref
            or request.compiled_identity != compiled_identity
            or envelope.kind != "execution_request"
            or envelope.schema_id != "scidiscovery.execution-request"
            or envelope.payload_schema_version != 1
            or envelope.media_type != "application/json"
            or envelope.creator != self.service_actor
            or envelope.parent_refs != (payload_ref,)
            or envelope.labels != labels
            or envelope.confidentiality != "approval_only"
        ):
            raise ExecutionServiceError(
                "execution identity is already bound to a different request"
            )
        return request

    def request_artifact_id(self, execution_id: str) -> str:
        with self._connect() as connection:
            row = self._row(connection, execution_id)
        return _parse_ref(row["request_ref_json"]).artifact_id

    def record_references(self, execution_id: str) -> dict:
        """Read persisted control references without revalidating old payloads."""

        with self._connect() as connection:
            row = self._row(connection, execution_id)
        return {
            "request_ref": _parse_ref(row["request_ref_json"]),
            "payload_ref": _parse_ref(row["payload_ref_json"]),
            "result_ref": _parse_optional_ref(row["result_ref_json"]),
        }

    def approval_subject_refs(self, execution_id: str) -> tuple[ArtifactRef, ArtifactRef]:
        """Return the exact request and executable payload a human must review."""

        with self._connect() as connection:
            row = self._row(connection, execution_id)
        request_ref = _parse_ref(row["request_ref_json"])
        payload_ref = _parse_ref(row["payload_ref_json"])
        request = self.request(execution_id)
        if request.payload_ref != payload_ref:
            raise ExecutionServiceError(
                "execution request payload binding differs from stored execution state"
            )
        self.artifacts.verify(request_ref)
        self.artifacts.verify(payload_ref)
        return request_ref, payload_ref

    def request(self, execution_id: str) -> ExecutionRequest:
        with self._connect() as connection:
            row = self._row(connection, execution_id)
        request_ref = _parse_ref(row["request_ref_json"])
        try:
            return ExecutionRequest.model_validate_json(
                self.artifacts.read(request_ref), strict=True
            )
        except ValidationError as error:
            raise ExecutionServiceError("stored execution request is invalid") from error

    def authorize(
        self,
        *,
        execution_id: str,
        approval_id: str,
        compiled_identity: CompiledApprovalIdentity,
    ) -> LocalFileDescriptor:
        execution_request = self.request(execution_id)
        if execution_request.compiled_identity != compiled_identity:
            raise ExecutionApprovalError(
                "execution request compiled identity is missing or changed"
            )
        approval = self.approvals.status(approval_id)
        if approval.status != "decided" or approval.decision_ref is None:
            raise ExecutionApprovalError("execution approval is not decided")
        raw_decision = self.artifacts.read(approval.decision_ref)
        decision = HumanDecision.model_validate_json(raw_decision, strict=True)
        try:
            request_approval = ApprovalRequest.model_validate_json(
                self.artifacts.read(approval.approval_request_ref), strict=True
            )
        except ValidationError as error:
            raise ExecutionApprovalError("execution approval request is invalid") from error
        if decision.approval_request_ref != approval.approval_request_ref:
            raise ExecutionApprovalError(
                "execution decision does not bind the stored approval request"
            )
        if request_approval.kind != "execution_authorization":
            raise ExecutionApprovalError("approval is not an execution authorization")
        if not execution_decision_authorizes(request_approval, decision):
            raise ExecutionApprovalError("human decision did not authorize execution")
        if request_approval.compiled_identity != compiled_identity:
            raise ExecutionApprovalError(
                "execution approval compiled identity is missing or changed"
            )
        if decision.additional_wall_time_seconds:
            if self.execution_for_approval(approval_id) != execution_id:
                raise ExecutionApprovalError('Budget extension belongs to another execution')
            self.apply_budget_extension(approval_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            request_ref = _parse_ref(row["request_ref_json"])
            payload_ref = _parse_ref(row["payload_ref_json"])
            exact_subjects = (request_ref, payload_ref)
            if (
                request_approval.subject_refs != exact_subjects
                or decision.subject_refs != exact_subjects
            ):
                raise ExecutionApprovalError(
                    "human decision does not bind the exact request and payload"
                )
            payload = self.artifacts.read(payload_ref)
            directory = self.exchange_root / execution_id
            descriptor = _payload_descriptor(
                directory / "payload.bin",
                payload,
                media_type=self.artifacts.catalog(payload_ref).media_type,
            )
            if row["state"] == "authorized" and row["decision_ref_json"] is not None:
                existing_decision = _parse_optional_ref(row["decision_ref_json"])
                if existing_decision != approval.decision_ref:
                    raise ExecutionStateConflict(
                        "execution was authorized by a different decision"
                    )
                _read_descriptor(descriptor)
                connection.execute("ROLLBACK")
                return descriptor
            if row["state"] not in {"created", "authorized"}:
                raise ExecutionStateConflict(
                    "execution is not authorizable from current state"
                )
            directory.mkdir(mode=0o770, exist_ok=True)
            _write_readonly(Path(descriptor.local_path), payload)
            connection.execute(
                """
                UPDATE executions
                SET state = 'authorized', decision_ref_json = ?
                WHERE execution_id = ?
                """,
                (approval.decision_ref.canonical_json(), execution_id),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        return descriptor

    def authorize_policy(self, *, execution_id: str, admission: ExecutionAdmission,
                         compiled_identity: CompiledApprovalIdentity) -> LocalFileDescriptor:
        request = self.request(execution_id)
        if request.compiled_identity != compiled_identity or admission.outcome != "policy":
            raise ExecutionApprovalError("policy authorization has an invalid request binding")
        if canonical_sha256(admission.policy) != admission.policy_digest:
            raise ExecutionApprovalError("policy authorization digest differs from its projection")
        request_ref, payload_ref = self.approval_subject_refs(execution_id)
        record = {"source": "policy", "request_ref": request_ref.model_dump(mode="json"),
            "payload_ref": payload_ref.model_dump(mode="json"),
            "compiled_identity": compiled_identity.model_dump(mode="json"),
            "admission": admission.model_dump(mode="json")}
        payload = self.artifacts.read(payload_ref)
        directory = self.exchange_root / execution_id
        descriptor = _payload_descriptor(directory / "payload.bin", payload,
            media_type=self.artifacts.catalog(payload_ref).media_type)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if row["state"] not in {"created", "authorized"}:
                raise ExecutionStateConflict("execution is not policy-authorizable from current state")
            if row["external_run_id"] is not None:
                raise ExecutionStateConflict("started execution cannot be reauthorized")
            prior = self._current_policy_record(connection, execution_id)
            prepared = connection.execute("SELECT 1 FROM execution_prepared_submissions WHERE execution_id=?", (execution_id,)).fetchone()
            if prior is not None and prior != record and prepared is not None:
                raise ExecutionApprovalError("prepared submission remains uncertain; recover its exact submission before changing policy authorization")
            self._reserve_budget(connection, execution_id, request.executor, admission)
            directory.mkdir(mode=0o770, exist_ok=True)
            if Path(descriptor.local_path).exists():
                _read_descriptor(descriptor)
            else:
                _write_readonly(Path(descriptor.local_path), payload)
            self._record_policy_decision(connection, execution_id, record)
            connection.execute("UPDATE executions SET state='authorized', decision_ref_json=NULL WHERE execution_id=?", (execution_id,))
            connection.execute("COMMIT")
        return descriptor

    @staticmethod
    def _current_policy_record(connection, execution_id):
        row = connection.execute("SELECT history.record_json FROM execution_current_policy active JOIN execution_policy_authorizations history ON history.execution_id=active.execution_id AND history.digest=active.digest WHERE active.execution_id=?", (execution_id,)).fetchone()
        return None if row is None else json.loads(row["record_json"])

    def _record_policy_decision(self, connection, execution_id, record):
        # Pointer loss cannot erase the frozen owner and request of past policy.
        for stored in connection.execute('SELECT record_json FROM execution_policy_authorizations WHERE execution_id=?', (execution_id,)):
            prior = json.loads(stored['record_json'])
            if any(prior[key] != record[key] for key in ("request_ref", "payload_ref", "compiled_identity")):
                raise ExecutionApprovalError("policy reauthorization changed the exact execution identity")
            if any(prior["admission"][key] != record["admission"][key] for key in ("budget_key", "budget")):
                raise ExecutionApprovalError("policy reauthorization changed the frozen budget owner or request")
        digest = canonical_sha256(record)
        connection.execute("INSERT OR IGNORE INTO execution_policy_authorizations VALUES (?, ?, ?, ?)",
            (execution_id, digest, canonical_json(record), _timestamp()))
        connection.execute("INSERT INTO execution_current_policy VALUES (?, ?) ON CONFLICT(execution_id) DO UPDATE SET digest=excluded.digest", (execution_id, digest))

    def defer_policy(self, *, execution_id, admission):
        """Record human/deny reassessment; release only a never-prepared reservation."""
        if admission.outcome not in {"require_human_approval", "deny"}:
            raise ExecutionApprovalError("policy deferral requires a non-autonomous outcome")
        if canonical_sha256(admission.policy) != admission.policy_digest:
            raise ExecutionApprovalError("policy deferral digest differs from its projection")
        request = self.request(execution_id)
        request_ref, payload_ref = self.approval_subject_refs(execution_id)
        record = {"source": "policy", "request_ref": request_ref.model_dump(mode="json"),
            "payload_ref": payload_ref.model_dump(mode="json"),
            "compiled_identity": request.compiled_identity.model_dump(mode="json"),
            "admission": admission.model_dump(mode="json")}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if row["state"] not in {"created", "authorized"} or row["external_run_id"] is not None:
                raise ExecutionStateConflict("submitted execution cannot reopen its budget")
            self._record_policy_decision(connection, execution_id, record)
            prepared = connection.execute("SELECT 1 FROM execution_prepared_submissions WHERE execution_id=?", (execution_id,)).fetchone()
            if prepared is None:
                connection.execute("DELETE FROM execution_budget_reservations WHERE execution_id=? AND consumed_json IS NULL", (execution_id,))
                connection.execute("UPDATE executions SET state='created', decision_ref_json=NULL WHERE execution_id=?", (execution_id,))
            else:
                connection.execute("UPDATE executions SET decision_ref_json=NULL WHERE execution_id=?", (execution_id,))
            # A prepared/unknown submission keeps both identity and reservation.
            # It remains recordable if a subsequent exact lookup finds acceptance.
            connection.execute("COMMIT")

    def scientific_budget_owner(self, payload_ref: ArtifactRef, schemas: tuple[str, ...]) -> str:
        return self.scientific_budget_owner_from_refs((payload_ref,), schemas)

    def scientific_budget_owner_from_refs(self, refs: tuple[ArtifactRef, ...], schemas: tuple[str, ...]) -> str:
        """Use the same lineage traversal before and after payload sealing."""
        if not schemas:
            raise ExecutionApprovalError("policy authorization requires a declared scientific budget subject")
        # Prefer the explicit skeleton; otherwise use the nearest exact detailed
        # plan. Revision/project/review hashes do not create an allowance pool.
        for schema in schemas:
            frontier, seen = refs, set()
            while frontier:
                matches, following = set(), []
                for ref in frontier:
                    if ref in seen:
                        continue
                    seen.add(ref)
                    envelope = self.artifacts.catalog(ref)
                    if envelope.schema_id == schema:
                        matches.add(ref)
                    else:
                        following.extend(envelope.parent_refs)
                if matches:
                    if len(matches) != 1:
                        raise ExecutionApprovalError("scientific execution budget subject is ambiguous")
                    return canonical_sha256(next(iter(matches)))
                frontier = tuple(following)
        raise ExecutionApprovalError("scientific execution budget subject is missing")

    @staticmethod
    def _budget_allowance(connection, executor, admission):
        allowance = dict(admission.allowance)
        extra = connection.execute("SELECT COALESCE(SUM(additional_seconds), 0) FROM execution_budget_extensions WHERE executor=? AND budget_key=?",
            (executor, admission.budget_key)).fetchone()[0]
        if 'wall_time_seconds' in allowance:
            allowance['wall_time_seconds'] += extra
        return allowance

    @staticmethod
    def _budget_remaining(connection, executor, admission, execution_id=None):
        # Current administrator policy sets the ceiling, including explicit increases.
        # Historical reservations and actual consumption remain unchanged.
        remaining = ExecutionService._budget_allowance(connection, executor, admission)
        if (set(remaining) != set(admission.budget) or set(admission.allowance) != set(remaining)
                or any(type(v) is not int or v < 1 for v in (*admission.budget.values(), *admission.allowance.values()))):
            raise ExecutionApprovalError("execution budget dimensions are invalid")
        rows = connection.execute("SELECT * FROM execution_budget_reservations WHERE executor=? AND budget_key=?",
            (executor, admission.budget_key)).fetchall()
        for row in rows:
            if row["execution_id"] == execution_id:
                continue
            used = {**json.loads(row["reserved_json"]), **json.loads(row["consumed_json"] or "{}")}
            for key in remaining:
                remaining[key] -= used[key]
        return remaining

    def execution_for_approval(self, approval_id):
        """Resolve the exact request frozen into this approval, never a UI supplied execution ID."""
        if self.approvals is None:
            raise ExecutionApprovalError('Approval service is unavailable')
        status = self.approvals.status(approval_id)
        approval = ApprovalRequest.model_validate_json(self.artifacts.read(status.approval_request_ref), strict=True)
        refs = [r for r in approval.subject_refs if r.schema_id == 'scidiscovery.execution-request']
        if approval.kind != 'execution_authorization' or len(refs) != 1:
            raise ExecutionApprovalError('Approval does not identify one execution request')
        request = ExecutionRequest.model_validate_json(self.artifacts.read(refs[0]), strict=True)
        registered = self.artifacts.get_by_id(self.request_artifact_id(request.execution_id))
        if (registered.ref != refs[0] or approval.subject_refs != (refs[0], request.payload_ref)
                or approval.compiled_identity != request.compiled_identity):
            raise ExecutionApprovalError('Approval does not bind the exact execution')
        return request.execution_id

    def _extension_scope(self, execution_id, *, connection=None):
        """Recover immutable grant ownership independently of the policy pointer."""
        with (self._connect() if connection is None else nullcontext(connection)) as db:
            row = self._row(db, execution_id)
            request_ref = _parse_ref(row['request_ref_json'])
            request = ExecutionRequest.model_validate_json(self.artifacts.read(request_ref), strict=True)
            scopes = set()
            budgets = set()
            for history in db.execute('SELECT digest, record_json FROM execution_policy_authorizations WHERE execution_id=?', (execution_id,)):
                record = json.loads(history['record_json'])
                if (canonical_sha256(record) != history['digest']
                        or _parse_ref(canonical_json(record['request_ref'])) != request_ref
                        or _parse_ref(canonical_json(record['payload_ref'])) != request.payload_ref
                        or record['compiled_identity'] != request.compiled_identity.model_dump(mode='json')):
                    raise ExecutionApprovalError('Budget policy history does not bind the exact execution')
                admission = ExecutionAdmission.model_validate(record['admission'])
                if 'wall_time_seconds' not in admission.allowance:
                    raise ExecutionBudgetNotApplicable('Execution has no wall time budget')
                scopes.add((row['executor'], admission.budget_key))
                budgets.add(canonical_json(admission.budget))
            if len(scopes) != 1 or len(budgets) != 1:
                raise ExecutionApprovalError('Execution has no unique immutable budget policy history')
            scope = next(iter(scopes))
            reservation = db.execute('SELECT executor, budget_key, reserved_json FROM execution_budget_reservations WHERE execution_id=?', (execution_id,)).fetchone()
            if reservation is not None and ((reservation['executor'], reservation['budget_key']) != scope
                    or canonical_json(json.loads(reservation['reserved_json'])) not in budgets):
                raise ExecutionApprovalError('Budget reservation differs from its immutable policy history')
            return scope

    def validate_budget_extension(self, approval_id, seconds):
        from .approvals import ApprovalError, ApprovalAccessDenied, BudgetExtensionAccountingError
        from ..storage import CASIntegrityError, RegistryIntegrityError, ArtifactReferenceError
        status = self.approvals.status(approval_id)
        # Exact replay is checked by the approval service. New administrator
        # limits must not invalidate a decision already sealed before restart.
        if status.status != 'pending':
            return
        limits = self.budget_extension_limits
        if type(seconds) is not int or seconds < 0 or seconds > limits.max_seconds_per_decision:
            raise ApprovalAccessDenied('Additional time exceeds the configured per-decision limit')
        scope = self._extension_scope(self.execution_for_approval(approval_id))
        with self._connect() as db:
            rows = db.execute('SELECT decision_id, additional_seconds FROM execution_budget_extensions WHERE executor=? AND budget_key=?', scope).fetchall()
            # Immutable policy history retains the pool even if its current
            # pointer is missing. Reservations also survive instance archiving.
            requests = db.execute('''SELECT e.execution_id, e.request_ref_json FROM executions e
                WHERE e.executor=? AND e.execution_id IN (
                    SELECT execution_id FROM execution_policy_authorizations
                    WHERE json_extract(record_json, '$.admission.budget_key')=?
                    UNION SELECT execution_id FROM execution_budget_reservations
                    WHERE executor=? AND budget_key=?)''', (*scope, *scope)).fetchall()
        projected = {row['decision_id'] for row in rows}
        used = sum(row['additional_seconds'] for row in rows)
        execution_ids = {row['execution_id'] for row in requests}
        # A crash after sealing a decision cannot make its grant disappear from
        # admission accounting before the idempotent projection is repaired.
        try:
            for aid, decision in self.approvals.budget_extension_decisions(
                    execution_refs=tuple(_parse_ref(row['request_ref_json']) for row in requests),
                    projected_decision_ids=projected):
                if self.execution_for_approval(aid) not in execution_ids:
                    raise ExecutionApprovalError('Decision has no exact request in this budget pool')
                used += decision.additional_wall_time_seconds
        except (ApprovalError, ExecutionApprovalError, ArtifactNotFoundError,
                CASIntegrityError, RegistryIntegrityError, ArtifactReferenceError,
                ValidationError, json.JSONDecodeError) as error:
            raise BudgetExtensionAccountingError(
                '无法核对当前预算池的历史授权，请修复相关审批记录后重试；本次未封存决定。') from error
        if used + seconds > limits.max_total_seconds_per_pool:
            raise ApprovalAccessDenied('Additional time exceeds the configured cumulative grant limit')

    def record_budget_extension_decision(self, **arguments):
        """Serialize the short human grant transaction, never solver submission IO."""
        import fcntl
        approval_id = arguments['approval_id']
        seconds = arguments['additional_wall_time_seconds']
        # All grant writers use this control-private lock. It covers validation,
        # sealing and projection across processes; ordinary approvals do not wait.
        with (self.exchange_root / '.budget-approval.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ref = self.approvals.record_ui_decision(**arguments,
                budget_extension_validator=lambda: self.validate_budget_extension(approval_id, seconds))
            self.apply_budget_extension(approval_id)
            return ref

    def apply_budget_extension(self, approval_id):
        """Idempotent projection of a sealed human decision; also repairs an interrupted UI write."""
        status = self.approvals.status(approval_id)
        if status.status != 'decided' or status.decision_ref is None:
            return
        decision = HumanDecision.model_validate_json(self.artifacts.read(status.decision_ref), strict=True)
        if not decision.additional_wall_time_seconds:
            return
        execution_id = self.execution_for_approval(approval_id)
        approval = ApprovalRequest.model_validate_json(self.artifacts.read(status.approval_request_ref), strict=True)
        if (decision.approval_request_ref != status.approval_request_ref or decision.subject_refs != approval.subject_refs
                or not execution_decision_authorizes(approval, decision)):
            raise ExecutionApprovalError('Budget extension requires the exact sealed execution authorization')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT decision_ref_json FROM execution_budget_extensions WHERE decision_id=?',
                (decision.decision_id,)).fetchone()
            if existing:
                if _parse_ref(existing[0]) != status.decision_ref:
                    raise ExecutionApprovalError('Budget extension decision identity changed')
                return
            executor, budget_key = self._extension_scope(execution_id, connection=db)
            db.execute('INSERT INTO execution_budget_extensions VALUES (?, ?, ?, ?, ?, ?, ?)',
                (decision.decision_id, execution_id, executor, budget_key,
                 decision.additional_wall_time_seconds, status.decision_ref.canonical_json(), decision.decided_at))

    def budget_admission(self, *, executor, admission, execution_id=None, allow_denial=False):
        with self._connect() as connection:
            remaining = self._budget_remaining(connection, executor, admission, execution_id)
        if admission.outcome == "policy" and any(admission.budget[key] > remaining[key] for key in remaining):
            admission = admission.model_copy(update={"outcome": admission.outside_allowance,
                "reason": "cumulative_budget_exceeded"})
        if admission.outcome == "deny" and not allow_denial:
            raise ExecutionApprovalError("cumulative task budget exhausted: " + json.dumps(remaining, sort_keys=True))
        return admission

    def _reserve_budget(self, connection, execution_id, executor, admission):
        existing = connection.execute("SELECT * FROM execution_budget_reservations WHERE execution_id=?", (execution_id,)).fetchone()
        if existing is not None:
            if existing["budget_key"] != admission.budget_key or json.loads(existing["reserved_json"]) != dict(admission.budget):
                raise ExecutionApprovalError("execution reservation differs from its frozen budget")
            remaining = self._budget_remaining(connection, executor, admission, execution_id)
            if admission.outcome == "policy" and any(admission.budget[key] > remaining[key] for key in remaining):
                raise ExecutionApprovalError("cumulative task budget changed before reauthorization")
            return
        remaining = self._budget_remaining(connection, executor, admission)
        if admission.outcome == "policy" and any(admission.budget[key] > remaining[key] for key in remaining):
            raise ExecutionApprovalError("cumulative task budget changed before reservation")
        connection.execute("INSERT INTO execution_budget_pools VALUES (?, ?, ?) "
            "ON CONFLICT(executor,budget_key) DO UPDATE SET allowance_json=excluded.allowance_json",
            (executor, admission.budget_key, canonical_json(admission.allowance)))
        connection.execute("INSERT INTO execution_budget_reservations VALUES (?, ?, ?, ?, NULL)",
            (execution_id, executor, admission.budget_key, canonical_json(admission.budget)))

    def reserve_human_budget(self, execution_id, admission):
        request = self.request(execution_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if row["decision_ref_json"] is None:
                raise ExecutionApprovalError("human budget override requires the exact sealed decision")
            self._reserve_budget(connection, execution_id, request.executor, admission)
            connection.execute("COMMIT")

    def budget_owner(self, execution_id):
        with self._connect() as connection:
            row = connection.execute("SELECT budget_key FROM execution_budget_reservations WHERE execution_id=?", (execution_id,)).fetchone()
        return None if row is None else row["budget_key"]

    def settle_budget(self, execution_id, consumed, *, finalize_missing=False):
        """Only terminal adapter observations settle a reservation; missing data never refunds."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            execution = self._row(connection, execution_id)
            row = connection.execute("SELECT * FROM execution_budget_reservations WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                return
            reserved = json.loads(row["reserved_json"])
            settled = json.loads(row["consumed_json"] or "{}")
            if execution["state"] not in {"succeeded", "failed", "cancelled", "collected"}:
                raise ExecutionStateConflict("only terminal execution can settle its budget")
            if (not consumed and not finalize_missing) or not set(consumed).issubset(reserved) or any(type(v) is not int or v < 0 for v in consumed.values()):
                raise ExecutionApprovalError("terminal resource observation is invalid")
            actual = {**consumed, **settled}
            connection.execute("UPDATE execution_budget_reservations SET consumed_json=? WHERE execution_id=?",
                (canonical_json(actual), execution_id))
            previous = connection.execute("SELECT estimated_json FROM execution_budget_estimates WHERE execution_id=?", (execution_id,)).fetchone()
            estimates = ({key: value for key, value in reserved.items() if key not in actual} if finalize_missing else
                {key: value for key, value in json.loads(previous[0] if previous else "{}").items() if key not in actual})
            connection.execute("INSERT OR REPLACE INTO execution_budget_estimates VALUES (?, ?)",
                (execution_id, canonical_json(estimates)))
            connection.execute("COMMIT")

    def budget_accounting(self, execution_id):
        with self._connect() as connection:
            row = connection.execute("SELECT reserved_json,consumed_json FROM execution_budget_reservations WHERE execution_id=?", (execution_id,)).fetchone()
            estimate_row = connection.execute("SELECT estimated_json FROM execution_budget_estimates WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            return {}
        reserved, actual = json.loads(row["reserved_json"]), json.loads(row["consumed_json"] or "{}")
        estimated = {key: value for key, value in json.loads(estimate_row[0] if estimate_row else "{}").items() if key not in actual}
        return {"state": "settled" if set(actual) == set(reserved) else "settled_estimated" if set(actual) | set(estimated) == set(reserved) else "partial" if actual else "reserved",
            "requested": reserved, "actual": actual,
            **({"estimated": estimated, "estimate_basis": "reserved_limit_without_terminal_observation"} if estimated else {}),
            "unsettled_reservation": {key: amount for key, amount in reserved.items() if key not in actual and key not in estimated}}

    def budget_needs_settlement(self, execution_id):
        value = self.budget_accounting(execution_id)
        return bool(value.get("unsettled_reservation"))

    def release_unsubmitted_budget(self, execution_id):
        """Preparation failure cannot consume a reservation when no submission exists."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            prepared = connection.execute("SELECT 1 FROM execution_prepared_submissions WHERE execution_id=?", (execution_id,)).fetchone()
            if row["external_run_id"] is None and prepared is None:
                connection.execute("DELETE FROM execution_budget_reservations WHERE execution_id=? AND consumed_json IS NULL", (execution_id,))

    def release_rejected_submission(self, execution_id, *, submission_sha256):
        """A typed exact pre-creation rejection clears preparation and reservation."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            prepared = connection.execute("SELECT descriptor_json FROM execution_prepared_submissions WHERE execution_id=?",
                (execution_id,)).fetchone()
            if row["external_run_id"] is not None or prepared is None:
                raise ExecutionStateConflict("Rejected submission identity is no longer unsubmitted")
            descriptor = LocalFileDescriptor.model_validate_json(prepared["descriptor_json"], strict=True)
            if descriptor.sha256 != submission_sha256:
                raise ExecutionStateConflict("Rejected submission identity differs")
            connection.execute("DELETE FROM execution_prepared_submissions WHERE execution_id=?", (execution_id,))
            connection.execute("DELETE FROM execution_budget_reservations WHERE execution_id=? AND consumed_json IS NULL", (execution_id,))

    def authorization(self, execution_id: str) -> dict:
        with self._connect() as connection:
            row = self._row(connection, execution_id)
            decision = json.loads(row["decision_ref_json"]) if row["decision_ref_json"] else None
            value = self._current_policy_record(connection, execution_id)
            result = {"source": "human", "decision_ref": decision} if decision else {"source": "none"}
            if value is not None:
                admission = ExecutionAdmission.model_validate(value["admission"])
                remaining = self._budget_remaining(connection, row["executor"], admission)
                result.update(policy_digest=admission.policy_digest, reason=admission.reason,
                    budget=admission.budget, allowance=self._budget_allowance(connection, row['executor'], admission),
                    configured_allowance=admission.allowance,
                    per_execution_allowance=admission.per_execution_allowance,
                    budget_usage={"scope": "shared_scientific_subject",
                        "remaining": {key: max(0, amount) for key, amount in remaining.items()},
                        "overage": {key: max(0, -amount) for key, amount in remaining.items()}})
                if decision is None:
                    if admission.outcome == "policy":
                        result.update(source="policy", budget_key=admission.budget_key)
                    else:
                        result["outcome"] = admission.outcome
        accounting = self.budget_accounting(execution_id) if value is not None else {}
        if accounting:
            result["accounting"] = accounting
        return result

    def prepared_submission(self, execution_id: str) -> LocalFileDescriptor | None:
        with self._connect() as connection:
            row = connection.execute("SELECT descriptor_json FROM execution_prepared_submissions WHERE execution_id=?", (execution_id,)).fetchone()
        return None if row is None else LocalFileDescriptor.model_validate_json(row["descriptor_json"], strict=True)

    def record_prepared_submission(self, execution_id: str, descriptor: LocalFileDescriptor, *, policy_digest: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if row["state"] != "authorized" or row["external_run_id"] is not None:
                raise ExecutionStateConflict("execution authorization changed before preparation completed")
            active = self._current_policy_record(connection, execution_id)
            if policy_digest is not None and (active is None or active["admission"]["policy_digest"] != policy_digest):
                raise ExecutionApprovalError("policy changed during preparation; reauthorize before submitting")
            connection.execute("INSERT OR IGNORE INTO execution_prepared_submissions VALUES (?, ?)", (execution_id, descriptor.canonical_json()))
        if self.prepared_submission(execution_id) != descriptor:
            raise ExecutionStateConflict("prepared submission differs from frozen execution")

    def record_submission(self, *, execution_id: str, external_run_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if (
                row["state"] in {
                    "submitted",
                    "running",
                    "cancelling",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "collected",
                }
                and row["external_run_id"] == external_run_id
            ):
                connection.execute("ROLLBACK")
                return
            if row["state"] != "authorized":
                raise ExecutionStateConflict("execution is not submittable from current state")
            connection.execute(
                "UPDATE executions SET state = 'submitted', external_run_id = ? WHERE execution_id = ?",
                (external_run_id, execution_id),
            )
            connection.execute("COMMIT")

    def abandon(self, execution_id: str, *, absence_confirmed=False) -> None:
        """Permanently prevent an unsubmitted execution from being authorized."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if row["state"] not in {"created", "authorized", "abandoned"} or row["external_run_id"] is not None:
                raise ExecutionStateConflict(
                    "only an unsubmitted execution can be abandoned"
                )
            prepared = connection.execute("SELECT 1 FROM execution_prepared_submissions WHERE execution_id=?", (execution_id,)).fetchone()
            if prepared is not None and row["state"] != "abandoned" and not absence_confirmed:
                raise ExecutionStateConflict("prepared execution has no permanent absence confirmation; recover its existing submission before cancellation")
            reservation = connection.execute("SELECT reserved_json,consumed_json FROM execution_budget_reservations WHERE execution_id=?", (execution_id,)).fetchone()
            if reservation is not None and reservation["consumed_json"] is None:
                connection.execute("UPDATE execution_budget_reservations SET consumed_json=? WHERE execution_id=?",
                    (canonical_json({key: 0 for key in json.loads(reservation["reserved_json"])}), execution_id))
            connection.execute(
                "UPDATE executions SET state = 'abandoned' WHERE execution_id = ?",
                (execution_id,),
            )
            connection.execute("COMMIT")
        # Idempotent after the execution commit; a retry completes interrupted cleanup.
        if self.approvals is not None:
            self.approvals.expire_pending("apr_" + execution_id)

    def record_status(
        self, *, execution_id: str, external_run_id: str, state: str
    ) -> None:
        normalized = {
            "accepted": "submitted",
            "running": "running",
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "cancelling": "cancelling",
        }.get(state)
        if normalized is None:
            raise ValueError("unknown executor state")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, execution_id)
            if row["external_run_id"] != external_run_id:
                raise ExecutionStateConflict("external run identity differs from submission")
            allowed = {
                "submitted": {"submitted", "running", "cancelling", "succeeded", "failed", "cancelled"},
                "running": {"running", "cancelling", "succeeded", "failed", "cancelled"},
                "cancelling": {"cancelling", "succeeded", "failed", "cancelled"},
            }
            if normalized not in allowed.get(row["state"], set()):
                raise ExecutionStateConflict("executor status is not a valid transition")
            connection.execute(
                "UPDATE executions SET state = ? WHERE execution_id = ?",
                (normalized, execution_id),
            )
            connection.execute("COMMIT")

    def ingest_result(
        self,
        *,
        execution_id: str,
        external_run_id: str,
        outputs: tuple[LocalFileDescriptor, ...],
        collected_at: str | None = None,
        context=None,
    ) -> ArtifactRef:
        if context is not None:
            context.remaining_seconds()
        with self._connect() as connection:
            row = self._row(connection, execution_id)
        if row["external_run_id"] != external_run_id:
            raise ExecutionStateConflict("external run identity differs from submission")
        if row["state"] not in {"succeeded", "failed", "cancelled"}:
            raise ExecutionStateConflict("execution is not terminal")
        if len({item.name for item in outputs}) != len(outputs):
            raise ExecutionServiceError("result names must be unique")
        request_ref = _parse_ref(row["request_ref_json"])
        payload_ref = _parse_ref(row["payload_ref_json"])
        output_refs = []
        for descriptor in outputs:
            if context is not None:
                context.remaining_seconds()
            output_refs.append(
                self.artifacts.register_file(
                    descriptor.local_path,
                    ArtifactRegistration(
                        kind="execution_output",
                        schema_id="opaque",
                        payload_schema_version=1,
                        media_type=descriptor.media_type,
                        creator=self.service_actor,
                        parent_refs=(request_ref, payload_ref),
                        labels={"execution_id": execution_id, "logical_name": descriptor.name},
                    ),
                    expected_sha256=descriptor.sha256,
                    expected_size=descriptor.size_bytes,
                    check_budget=None if context is None else context.remaining_seconds,
                    idempotency_key=f"execution:{execution_id}:output:{descriptor.name}",
                ).ref
            )
        manifest = ExecutionResultManifest(
            execution_id=execution_id,
            external_run_id=external_run_id,
            terminal_state=row["state"],
            output_refs=tuple(output_refs),
            collected_at=collected_at or _timestamp(),
        )
        result_ref = self.artifacts.register(
            manifest.canonical_json(),
            ArtifactRegistration(
                kind="execution_result",
                schema_id="scidiscovery.execution-result",
                payload_schema_version=1,
                media_type="application/json",
                creator=self.service_actor,
                parent_refs=(request_ref, payload_ref, *output_refs),
            ),
            idempotency_key=f"execution:{execution_id}:result",
        ).ref
        if context is not None:
            context.remaining_seconds()
        with self._connect() as connection:
            if context is not None:
                connection.execute(f"PRAGMA busy_timeout = {max(1, int(context.remaining_seconds() * 1000))}")
            connection.execute("BEGIN IMMEDIATE")
            current = self._row(connection, execution_id)
            if current["state"] != row["state"]:
                raise ExecutionStateConflict("execution changed during result ingestion")
            connection.execute(
                "UPDATE executions SET state = 'collected', result_ref_json = ? WHERE execution_id = ?",
                (result_ref.canonical_json(), execution_id),
            )
            connection.execute("COMMIT")
        # The manifest and all declared outputs are durable before cache cleanup.
        try:
            saved = self.observation(execution_id)
            if saved:
                self.save_observation(execution_id, saved)
        except (OSError, ValueError, sqlite3.Error):
            # Cache cleanup must not turn committed collection into a false failure.
            import logging
            logging.getLogger(__name__).warning("Collected execution log cache cleanup deferred")
        return result_ref

    def record_observation_result(self, execution_id: str, *, succeeded: bool, previous_failures=0) -> int:
        """Persist retry accounting independently of replaceable observation caches."""
        if type(previous_failures) is not int or previous_failures < 0:
            previous_failures = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._row(connection, execution_id)
            row = connection.execute("SELECT failures FROM execution_observation_retries WHERE execution_id=?",
                (execution_id,)).fetchone()
            # Seed pre-upgrade counts once if their cache is still readable.
            failures = 0 if succeeded else (row[0] if row is not None else previous_failures) + 1
            connection.execute("INSERT INTO execution_observation_retries VALUES (?,?) "
                "ON CONFLICT(execution_id) DO UPDATE SET failures=excluded.failures", (execution_id, failures))
            return failures

    def observation(self, execution_id: str) -> dict:
        from .engineering_diagnostics import read_json
        self.status(execution_id)
        try:
            return read_json(self.exchange_root / execution_id / "observation.json")
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            from .engineering_diagnostics import exception_facts
            return {"observation_error": exception_facts(error, layer="execution_observation", action="read")}

    def save_observation(self, execution_id: str, value: dict) -> None:
        from .engineering_diagnostics import atomic_json
        # Serialize against collection commit: a concurrent late refresh cannot
        # restore discarded log bodies after this execution is collected.
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._row(connection, execution_id)["state"] == "collected":
                progress = value.get("progress")
                if isinstance(progress, dict):
                    logs = progress.get("log_tails")
                    progress = {**progress, "log_cache_state": "released"}
                    if isinstance(logs, list):
                        progress["log_tails"] = [{k: item[k] for k in
                            ("source", "size_bytes", "updated_at", "truncated") if k in item}
                            for item in logs if isinstance(item, dict)]
                    value = {**value, "progress": progress}
            atomic_json(self.exchange_root / execution_id / "observation.json", value)

    def cached_log(self, execution_id: str, *, log_index: int, direction: str = "tail",
                   cursor: str | None = None, max_bytes: int) -> dict:
        """Read this execution's latest, earlier or incremental diagnostic cache page."""
        from .execution_log_reader import invalid, log_page
        if type(log_index) is not int or log_index < 0:
            raise invalid("log_index must be a nonnegative integer")
        if type(max_bytes) is not int or max_bytes < 4:
            raise invalid("log page budget must fit a UTF-8 character")
        if self.status(execution_id).state == "collected":
            return {"cache_state":"released", "source":"collected_outputs", "before":None, "after":None}
        observation = self.observation(execution_id)
        logs = (observation.get("progress") or {}).get("log_tails") or []
        if not logs:
            return {"cache_state":"unavailable", "before":None, "after":None,
                "observed_at":observation.get("progress_observed_at"),
                **({"observation_error":observation["observation_error"]} if observation.get("observation_error") else {})}
        if log_index >= len(logs):
            raise invalid("Select 0 <= log_index < log_count from this execution's status")
        return {"cache_state":"available", "log_index":log_index,
            "observed_at":observation.get("progress_observed_at"), "updated_at":logs[log_index].get("updated_at"),
            **log_page(execution_id, log_index, logs[log_index], direction=direction, cursor=cursor, max_bytes=max_bytes)}

    def status(self, execution_id: str) -> ExecutionStatusView:
        with self._connect() as connection:
            row = self._row(connection, execution_id)
        return ExecutionStatusView(
            execution_id=execution_id,
            executor=row["executor"],
            state=row["state"],
            external_run_id=row["external_run_id"],
            result_ref=_parse_optional_ref(row["result_ref_json"]),
            created_at=row["created_at"],
            authorization=self.authorization(execution_id),
        )

    def observation_candidates(self, *, after: str = "", limit: int = 32) -> tuple[str, ...]:
        """Keyset page of already submitted jobs; never resolves uncertain submissions."""
        with self._connect() as connection:
            return tuple(row[0] for row in connection.execute(
                "SELECT e.execution_id FROM executions e LEFT JOIN execution_budget_reservations b USING(execution_id) "
                "LEFT JOIN execution_budget_estimates a USING(execution_id) "
                "WHERE e.execution_id > ? AND e.external_run_id IS NOT NULL AND "
                "(e.state IN ('submitted','running','cancelling') OR "
                "(e.state IN ('succeeded','failed','cancelled','collected') AND EXISTS "
                "(SELECT 1 FROM json_each(CAST(b.reserved_json AS TEXT)) r "
                "WHERE NOT EXISTS (SELECT 1 FROM json_each(CAST(b.consumed_json AS TEXT)) c WHERE c.key=r.key) "
                "AND NOT EXISTS (SELECT 1 FROM json_each(CAST(a.estimated_json AS TEXT)) c WHERE c.key=r.key)))) "
                "ORDER BY e.execution_id LIMIT ?", (after, limit)))

    def list_statuses(
        self, *, state: str | None = None, limit: int = 50
    ) -> tuple[ExecutionStatusView, ...]:
        """Return a bounded newest-first recovery view."""

        allowed = {
            "created",
            "authorized",
            "submitted",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "cancelling",
            "collected",
            "abandoned",
        }
        if state is not None and state not in allowed:
            raise ValueError("unknown execution state")
        if not 1 <= limit <= 100:
            raise ValueError("execution list limit must be between 1 and 100")
        query = "SELECT execution_id FROM executions"
        parameters: tuple[object, ...]
        if state is None:
            query += " ORDER BY created_at DESC LIMIT ?"
            parameters = (limit,)
        else:
            query += " WHERE state = ? ORDER BY created_at DESC LIMIT ?"
            parameters = (state, limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self.status(row["execution_id"]) for row in rows)

    def active_ids(self, *, instance_id: str, scheduler_database_path: Path | str,
                   limit: int = 31) -> tuple[str, ...]:
        """Read exact instance-owned unfinished or uncollected execution IDs."""
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("active execution limit must be between 1 and 101")
        with self._connect() as connection:
            connection.execute("ATTACH DATABASE ? AS workbench_scope", (str(scheduler_database_path),))
            rows = connection.execute(
                "SELECT execution_id FROM executions e WHERE state NOT IN ('collected', 'abandoned') "
                "AND EXISTS (SELECT 1 FROM workbench_scope.scheduler_bindings b "
                "WHERE b.instance = ? AND b.namespace = 'execution' AND b.object_id = e.execution_id) "
                "ORDER BY created_at DESC, execution_id LIMIT ?", (instance_id, limit),
            ).fetchall()
        return tuple(str(row["execution_id"]) for row in rows)

    def outputs(self, execution_id: str) -> tuple[ExecutionOutputView, ...]:
        """Resolve collected outputs without exposing their payload bytes."""

        status = self.status(execution_id)
        if status.state != "collected" or status.result_ref is None:
            raise ExecutionStateConflict("execution outputs require collected state")
        try:
            manifest = ExecutionResultManifest.model_validate_json(
                self.artifacts.read(status.result_ref), strict=True
            )
        except ValidationError as error:
            raise ExecutionServiceError("stored execution result is invalid") from error
        if manifest.execution_id != execution_id:
            raise ExecutionServiceError("execution result identity differs")
        result = []
        names = set()
        for reference in manifest.output_refs:
            envelope = self.artifacts.catalog(reference)
            logical_name = envelope.labels.get("logical_name")
            if not logical_name or logical_name in names:
                raise ExecutionServiceError("execution output logical names are invalid")
            names.add(logical_name)
            result.append(
                ExecutionOutputView(
                    logical_name=logical_name,
                    artifact_id=envelope.artifact_id,
                    media_type=envelope.media_type,
                    size_bytes=envelope.size_bytes,
                )
            )
        return tuple(result)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    executor TEXT NOT NULL,
                    request_ref_json BLOB NOT NULL,
                    payload_ref_json BLOB NOT NULL,
                    state TEXT NOT NULL,
                    decision_ref_json BLOB,
                    external_run_id TEXT UNIQUE,
                    result_ref_json BLOB,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.executescript(EXECUTION_POLICY_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        import time
        timeout = 30.0 if self.deadline_monotonic is None else min(30.0, self.deadline_monotonic - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("execution registration connection budget exhausted")
        connection = sqlite3.connect(self.database_path, timeout=timeout)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row(connection: sqlite3.Connection, execution_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            raise ExecutionNotFound("execution does not exist")
        return row


def _write_readonly(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o440)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _payload_descriptor(
    path: Path,
    payload: bytes,
    *,
    media_type: str,
) -> LocalFileDescriptor:
    return LocalFileDescriptor(
        name="execution_payload",
        local_path=str(path),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
    )


def _read_descriptor(descriptor: LocalFileDescriptor) -> bytes:
    path = Path(descriptor.local_path).absolute()
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ExecutionServiceError("result path is not a regular non-symlink file")
    content = path.read_bytes()
    if len(content) != descriptor.size_bytes or hashlib.sha256(content).hexdigest() != descriptor.sha256:
        raise ExecutionServiceError("result bytes differ from descriptor")
    return content


def _parse_ref(raw: bytes | str) -> ArtifactRef:
    try:
        return ArtifactRef.model_validate_json(raw, strict=True)
    except ValidationError as error:
        raise ExecutionServiceError("stored artifact reference is invalid") from error


def _parse_optional_ref(raw: bytes | str | None) -> ArtifactRef | None:
    return None if raw is None else _parse_ref(raw)


def _timestamp(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "ExecutionApprovalError",
    "ExecutionService",
    "ExecutionServiceError",
    "ExecutionOutputView",
    "ExecutionStateConflict",
    "ExecutionStatusView",
]
