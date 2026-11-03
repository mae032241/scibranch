"""One minimal Run authority for validation, completion and recovery."""

from __future__ import annotations

from dataclasses import replace

from ...agent_execution_settings import (EXECUTION_SETTINGS_COLUMNS, AgentSettings, ExecutionIOSettings, ExecutionProfile, MaterialInputSettings,
                                          parse_settings, resolve_settings)

import hashlib
import json
import sqlite3
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...operation_contract import contract_diagnostic, sanitize_diagnostic_details
from ...operations.tooling import operation_worker_tools
from ...operations.catalog import CompiledCatalog
from ...operations.invoke import (
    BoundOperationCall,
    BoundInput,
    InvocationArtifact,
    preflight_operation,
    operation_output_validation_contract,
    operation_primary_output,
    active_direct_revision_ports,
)
from ...operations.tooling import operation_agent_type, tool_evidence_ports
from ...operations.workspace import (
    WorkspaceFinalizationRequest,
    WorkspaceMaterializationRequest,
    WorkspaceProtocolError,
    operation_workspace_hooks,
)
from ..schema.artifact import ArtifactRegistration
from ..schema.common import canonical_json, canonical_sha256
from ..schema.refs import ActorRef, ArtifactRef
from ..schema.run_signal import SchedulerSignal
from .artifacts import ArtifactService
from .worker_connections import WORKER_CONNECTION_SCHEMA, WORKER_PARTICIPANT_SCHEMA
from .local_workspace import (
    OpenWorkspace,
    WorkspaceBackend,
    WorkspaceError,
    WorkspaceOutputError,
    WorkspaceInput,
)
from ..storage import CASIntegrityError
from .reference_proofs import ReferenceProofError
from .run_assignment import assignment_json, result_schema_json, revision_draft_json
from .run_current import RunCurrentGuard
from .run_outputs import (
    InputBindingDescriptor,
    RunCheckerError,
    RunOutputError,
    ValidatedRunOutput,
    validate_run_output,
)
from .run_records import (
    RUN_READ_COLUMNS,
    RunCompletionReceipt,
    RunContractUnavailable,
    RunError,
    RunInputBinding,
    RunNotFound,
    RunAttemptLimit,
    RunSlotBusy,
    RunCapacityBusy,
    RunStateConflict,
    RunPreparationBusy,
    RunStatus,
    completion_receipt_json,
    expired,
    future,
    inputs_json,
    parse_stored_signal,
    status_from_row,
    timestamp,
)
from .scheduler_bindings import SchedulerBindingService, SchedulerInstanceClosed
from .instance_maintenance import InstanceMaintenanceUnavailable


from .tool_evidence import ToolEvidenceMixin


_COMPACT_RECOVERY_REASON_CODES = frozenset(
    {
        "backend_unavailable",
        "contract_unavailable",
        "isolation_incomplete",
        "snapshot_unavailable",
        "writers_unconfirmed",
    }
)


class RunService(ToolEvidenceMixin):
    """The only service allowed to publish an Agent result as scientific data."""

    def __init__(
        self,
        *,
        artifacts: ArtifactService,
        database_path: Path | str,
        service_actor: ActorRef,
        operation_catalog: CompiledCatalog,
        backend: WorkspaceBackend,
        scheduler_bindings: SchedulerBindingService,
        instance_maintenance=None,
        agent_settings: AgentSettings | None = None,
        approval_database_path: Path | None = None,
    ) -> None:
        self.approval_database_path = approval_database_path
        self.agent_settings = agent_settings or AgentSettings()
        self.artifacts = artifacts
        self.database_path = Path(database_path).expanduser().absolute()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.service_actor = service_actor
        self.operation_catalog = operation_catalog
        self.backend = backend
        self.scheduler_bindings = scheduler_bindings
        self.scheduler_database_path = scheduler_bindings.database_path
        from .instance_maintenance import InstanceMaintenance
        self.instance_maintenance = instance_maintenance or InstanceMaintenance(self.database_path.parent.parent)
        self.current = RunCurrentGuard(
            artifacts=artifacts,
            scheduler_database_path=self.scheduler_database_path,
        )
        self._initialize()
        from .worker_connections import WorkerConnections
        self.worker_connections = WorkerConnections(self)
        self.experiment_executions = None

    def material_input_settings(self, instance_id=None):
        values = self.agent_settings.input_materials.model_dump()
        if instance_id is not None:
            instance = parse_settings(self.scheduler_bindings.agent_settings(instance_id)["settings"])
            values.update(instance.input_materials.model_dump(exclude_unset=True))
        return MaterialInputSettings.model_validate(values)

    def task_lineage(self, run_id):
        """Current Run through its immutable recovery ancestors, newest first."""
        lineage, seen = [], set()
        with self._connect() as connection:
            while run_id and run_id not in seen and len(seen) < 128:
                seen.add(run_id)
                run = self.status(run_id)
                if lineage and (run.instance_id, run.operation_id) != (lineage[0].instance_id, lineage[0].operation_id):
                    raise RunStateConflict("Recovery lineage crosses the task boundary")
                lineage.append(run)
                row = self._row(connection, run_id)
                run_id = row["draft_from_run_id"] or row["resume_from_run_id"]
            if run_id:
                raise RunStateConflict("Recovery lineage is cyclic or exceeds its bound")
        return tuple(lineage)

    def compiled_operation(self, run):
        """Resolve the frozen Run contract, including its identity check."""
        return self._compiled(run)

    def task_family(self, run_id):
        """All Runs sharing the sealed recovery root, including other branches."""
        with self._connect() as connection:
            origin = self._recovery_root_id(connection, run_id)
            rows = connection.execute("""WITH RECURSIVE family(run_id) AS (
                SELECT run_id FROM runs WHERE run_id=?
                UNION SELECT child.run_id FROM runs AS child JOIN family AS parent
                    ON child.resume_from_run_id=parent.run_id OR child.draft_from_run_id=parent.run_id
            ) SELECT runs.* FROM runs JOIN family USING(run_id)
                ORDER BY runs.created_at, runs.run_id""", (origin,)).fetchall()
        family = self._statuses_from_rows(rows)
        root = next(run for run in family if run.run_id == origin)
        if any((run.instance_id, run.operation_id) != (root.instance_id, root.operation_id) for run in family):
            raise RunStateConflict("Recovery task family crosses the task boundary")
        return origin, family

    def running_task(self, run_id):
        """Require the existing task and its original deadline to remain live."""
        return self._require_running(run_id)

    def execution_settings(self, compiled, *, instance_id, profile=None, recovery_source=None, max_attempts=None):
        source = self.status(recovery_source) if recovery_source else None
        if source is not None and source.instance_id != instance_id:
            raise RunStateConflict("recovery source belongs to another instance")
        if profile is not None and (source is not None or max_attempts is not None):
            value = profile if isinstance(profile, ExecutionProfile) else ExecutionProfile.model_validate(profile)
            return {"profile": value.model_dump(), "sources": {key: "request_snapshot" for key in ExecutionProfile.model_fields},
                    "default_max_attempts": None}
        # A legacy recovery must not silently acquire the latest instance/global preferences.
        global_settings = AgentSettings() if source else self.agent_settings
        instance = AgentSettings() if source else parse_settings(self.scheduler_bindings.agent_settings(instance_id)["settings"])
        resolved = resolve_settings(global_settings, instance, operation_id=compiled.spec.operation_id,
            operation_model=compiled.spec.executor.model, operation_max_attempts=compiled.spec.limits.max_attempts)
        budget = resolved["max_attempts"] if source is None else None
        if profile is not None:
            value = profile if isinstance(profile, ExecutionProfile) else ExecutionProfile.model_validate(profile)
            return {"profile": value.model_dump(), "sources": {key: "request_snapshot" for key in ExecutionProfile.model_fields},
                    "default_max_attempts": budget}
        if source is not None and source.execution_profile is not None:
            return {**source.execution_profile, "default_max_attempts": None}
        return {"profile": resolved["profile"].model_dump(),
                "sources": {key: "legacy_recovery_default" if source else resolved["sources"][key]
                            for key in ExecutionProfile.model_fields}, "default_max_attempts": budget}

    @staticmethod
    def _input_bindings(bound):
        compiled = bound.compiled
        return tuple(
            RunInputBinding(
                port_name=item.port_name,
                source_name=item.source_name,
                artifact_name=item.artifact_name,
                artifact_ref=item.artifact.ref,
                media_type=item.artifact.media_type,
                exposure=item.exposure,
                usage=item.usage,
                require_current=next(
                    port.require_current
                    for port in compiled.spec.inputs
                    if port.name == item.port_name
                ),
            )
            for item in bound.inputs
        )

    def input_origins(self, instance_id):
        from .operation_origins import OperationOrigins
        return OperationOrigins(artifacts=self.artifacts, bindings=self.scheduler_bindings,
            runs=self, catalog=self.operation_catalog, instance_id=instance_id)

    def validate_input_bindings(self, bound, *, instance_id):
        with self._connect() as connection:
            self._attach_scheduler(connection)
            return self.current.freeze(connection, instance_id, self._input_bindings(bound),
                compiled=bound.compiled, origins=self.input_origins(instance_id))

    def schedule(
        self,
        bound: BoundOperationCall,
        *,
        instance_id: str,
        output_binding_name: str,
        output_logical_name: str,
        output_revision: int,
        output_binding_fingerprint: str,
        resume_from: str | None = None,
        draft_from: str | None = None,
        draft_digest: str | None = None,
        max_attempts: int | None = None,
    ) -> str:
        if resume_from is not None and draft_from is not None:
            raise RunStateConflict("resume_from and draft_from are mutually exclusive")
        if draft_digest is not None and draft_from is None:
            raise RunStateConflict("draft digest requires a source Run")
        self._validate_attempt_limit(max_attempts)
        compiled = bound.compiled
        execution_profile = bound.execution_profile
        required_review = bound.require_execution_review
        if compiled.spec.executor.kind != "agent":
            raise ValueError("RunService accepts Agent operations only")
        if not self.backend.supports_operation(compiled):
            raise RunError("runtime backend does not support the compiled Operation")
        if any(port.collection is not None for port in compiled.spec.outputs) and not tool_evidence_ports(compiled):
            raise RunError("minimal local Run does not yet support output collections")
        if compiled.spec.limits is None:
            raise ValueError("compiled operation has no limits")
        # Reconstruct the call from authoritative records before any Run/workspace write.
        by_port = {port.name: [] for port in compiled.spec.inputs}
        for item in bound.inputs:
            envelope = (self.artifacts.verify(item.artifact.ref) if item.exposure == "file_reference"
                        else self.artifacts.catalog(item.artifact.ref))
            producer = self.completed_for_output(envelope.ref)
            by_port.setdefault(item.port_name, []).append(InvocationArtifact(
                artifact_name=item.artifact_name, ref=envelope.ref, schema_id=envelope.schema_id,
                media_type=envelope.media_type, size_bytes=envelope.size_bytes,
                parent_refs=envelope.parent_refs, labels=tuple(envelope.labels.items()),
                producer_run_id=producer.run_id if producer else None,
                producer_inputs=(tuple((binding.port_name, binding.artifact_ref) for binding in producer.inputs)
                    if producer is not None and producer.state == "completed"
                    and producer.output_ref == envelope.ref else None),
                current=self.input_is_current(instance_id=instance_id,
                    artifact_name=item.artifact_name, artifact_ref=envelope.ref),
                handoff_verdict=(self.signal_for_output(envelope.ref, require_current=False).verdict
                    if self.signal_for_output(envelope.ref, require_current=False) else None),
                historical=item.artifact.historical,
            ))
        material_policy = (self.material_input_settings(instance_id)
            if any(p.exposure in {"material", "file_reference"} for p in compiled.spec.inputs) else None)
        bound = preflight_operation(compiled, name=bound.name,
            artifacts_by_port={name: tuple(items) for name, items in by_port.items()},
            instruction=bound.instruction, read_artifact=self.artifacts.read,
            material_settings=material_policy,
            source_name_overrides={(item.port_name, item.artifact.ref): item.source_name
                                   for item in bound.inputs})
        if execution_profile is None:
            settings = self.execution_settings(compiled, instance_id=instance_id,
                recovery_source=resume_from or draft_from)
            execution_profile = {key: settings[key] for key in ("profile", "sources")}
            if max_attempts is None:
                max_attempts = settings["default_max_attempts"]
        bound = replace(bound, execution_profile=execution_profile, require_execution_review=required_review)
        raw_inputs = self._input_bindings(bound)
        for item in raw_inputs:
            self.artifacts.verify(item.artifact_ref)
        run_id = f"run_{uuid.uuid4().hex}"
        created_at = timestamp()
        deadline_at = future(created_at, compiled.spec.limits.timeout_seconds)
        recovery_digest: str | None = None
        recovery_policy = self._recovery_policy(compiled)
        if required_review:
            recovery_policy["require_execution_review"] = True
        if material_policy is not None:
            recovery_policy["input_materials"] = material_policy.model_dump()
        if resume_from is None and draft_from is None:
            instance_settings = parse_settings(self.scheduler_bindings.agent_settings(instance_id)["settings"])
            for key in ("helpers", "execution_io", "attachments"):
                override = getattr(instance_settings, key).model_dump(exclude_unset=True)
                if key == "helpers":
                    from ...agent_execution_settings import merge_helper_settings
                    recovery_policy[key] = merge_helper_settings(recovery_policy[key], override)
                else:
                    recovery_policy[key].update(override)
        with self._connect() as connection:
            self._attach_scheduler(connection)
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT run_id, output_binding_fingerprint FROM runs WHERE instance_id=? AND output_binding_name=?",
                (instance_id, output_binding_name)).fetchone()
            if existing is not None:
                if existing["output_binding_fingerprint"] != output_binding_fingerprint:
                    raise RunStateConflict("The requested task output name already belongs to a different request")
                connection.execute("COMMIT")
                existing_id = str(existing["run_id"])
                self.prepare_queued(existing_id)
                return existing_id
            active_count = connection.execute("SELECT COUNT(*) FROM runs WHERE state IN ('queued','running')").fetchone()[0]
            if active_count >= self.agent_settings.scheduling.max_active_runs:
                raise RunCapacityBusy("Configured task capacity is full; no Run was created. Wait for existing work to finish, then retry the same request.")
            frozen_inputs = self.current.freeze(connection, instance_id, raw_inputs,
                compiled=compiled, origins=self.input_origins(instance_id))
            active_revision = active_direct_revision_ports(
                compiled, (item.port_name for item in frozen_inputs)
            )
            revision_base = (
                None
                if active_revision is None
                else next(
                    item.artifact_ref
                    for item in frozen_inputs
                    if item.port_name == active_revision[0].name
                )
            )
            if (
                revision_base is not None
                and compiled.spec.review is not None
                and compiled.spec.review.max_revisions
                and self._revision_successor(
                    connection,
                    instance_id=instance_id,
                    operation_digest=compiled.digest,
                    base_ref=revision_base,
                )
                is not None
            ):
                connection.execute("ROLLBACK")
                raise RunSlotBusy("this revision base already has a successor Run")
            if resume_from is not None:
                source = self._status_from_row(self._row(connection, resume_from))
                recovery_digest = self._validate_resume(
                    connection,
                    source,
                    compiled.digest,
                    tuple(item.artifact_ref for item in frozen_inputs),
                    compiled.spec.limits.max_attempts,
                    scheduler_max_attempts=max_attempts,
                )
            if resume_from is not None and source.instance_id != instance_id:
                raise RunStateConflict("recovery source belongs to another instance")
            if draft_from is not None:
                source = self._status_from_row(self._row(connection, draft_from))
                recovery_digest = self._validate_draft_source(
                    connection, source, compiled=compiled, instance_id=instance_id,
                    scheduler_max_attempts=max_attempts,
                )
                if draft_digest is not None and draft_digest != recovery_digest:
                    raise RunStateConflict("draft source digest changed")
            scheduler_budget = max_attempts
            if resume_from is not None or draft_from is not None:
                if (source.recovery_policy or {}).get("require_execution_review") and not required_review:
                    raise RunStateConflict("Recovery cannot remove the explicit execution review requirement")
                recovery_policy["helpers"] = (source.recovery_policy or {}).get("helpers", {"max_depth": 0})
                recovery_policy["execution_io"] = (source.recovery_policy or {}).get("execution_io", ExecutionIOSettings().model_dump())
                recovery_policy["attachments"] = (source.recovery_policy or {}).get("attachments", recovery_policy["attachments"])
            if scheduler_budget is None and (resume_from is not None or draft_from is not None):
                scheduler_budget = (source.recovery_policy or {}).get("scheduler_max_attempts")
            if scheduler_budget is not None:
                recovery_policy.update(max_attempts=scheduler_budget, scheduler_max_attempts=scheduler_budget)
            recovery_policy["preparation_recovery_digest"] = recovery_digest
            recovery_policy["preparation_input_provenance"] = {
                item.source_name: {'historical': item.artifact.historical, 'labels': dict(item.artifact.labels)}
                for item in bound.inputs}
            request_digest = canonical_sha256(
                {
                    "operation_id": compiled.spec.operation_id,
                    "operation_version": compiled.spec.version,
                    "operation_digest": compiled.digest,
                    "backend": self.backend.backend_id,
                    "backend_version": self.backend.backend_version,
                    "backend_capabilities": self.backend.capabilities,
                    "instance_id": instance_id,
                    "output_binding": {
                        "name": output_binding_name,
                        "logical_name": output_logical_name,
                        "revision": output_revision,
                        "request_fingerprint": output_binding_fingerprint,
                    },
                    "execution_profile": execution_profile["profile"],
                    "instruction": bound.instruction or "",
                    **({"require_execution_review": True} if required_review else {}),
                    "inputs": json.loads(inputs_json(frozen_inputs)),
                    "limits": compiled.spec.limits.model_dump(mode="json"),
                    "resume_from": resume_from,
                    **({"draft_from": draft_from, "draft_digest": recovery_digest}
                       if draft_from is not None else {}),
                    "recovery_digest": recovery_digest,
                    **({"scheduler_max_attempts": max_attempts} if max_attempts is not None else {}),
                }
            )
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, instance_id, operation_id, operation_version,
                        operation_digest, agent_type, instruction, inputs_json,
                        backend_id, backend_version, backend_capabilities_json,
                        output_binding_name, output_logical_name,
                        output_revision, output_binding_fingerprint,
                        request_digest, resume_from_run_id, draft_from_run_id, recovery_policy_json,
                        state, created_at, deadline_at, execution_profile_json, decision_fields_json, workspace_ready
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, 'queued', ?, ?, ?, ?, 0)
                    """,
                    (
                        run_id,
                        instance_id,
                        compiled.spec.operation_id,
                        compiled.spec.version,
                        compiled.digest,
                        operation_agent_type(compiled),
                        bound.instruction or "",
                        inputs_json(frozen_inputs),
                        self.backend.backend_id,
                        self.backend.backend_version,
                        canonical_json(self.backend.capabilities),
                        output_binding_name,
                        output_logical_name,
                        output_revision,
                        output_binding_fingerprint,
                        request_digest,
                        resume_from,
                        draft_from,
                        canonical_json(recovery_policy),
                        created_at,
                        deadline_at,
                        json.dumps(execution_profile),
                        canonical_json(compiled.spec.decision_fields),
                    ),
                )
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                raise  # An unrelated database constraint is not a capacity result.
            connection.execute("COMMIT")
        self.prepare_queued(run_id)
        return run_id

    def prepare_queued(self, run_id: str) -> bool:
        """Recover frozen preparation under a cross-process lock, before dispatch.

        Readiness is persisted after all workspace hooks finish. A crashed
        preparer releases its OS lock; replay rebuilds only the never-opened
        workspace. A concurrent replay returns without dispatching it.
        """
        import fcntl
        locks = self.database_path.parent / 'preparation-locks'
        locks.mkdir(exist_ok=True, mode=0o700)
        with (locks / (run_id + '.lock')).open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            with self._connect() as connection:
                row = self._row(connection, run_id)
                value = self._status_from_row(row)
                if value.workspace_ready or value.state != 'queued':
                    return value.workspace_ready
                if expired(value.deadline_at):
                    self.record_failure(run_id, reason='Run deadline expired during preparation',
                        expected_state='queued', expected_last_activity_at=None, timed_out=True)
                    return False
                instruction = str(row['instruction'])
                resume_from = row['resume_from_run_id']
            compiled = self._compiled(value)
            frozen_inputs = value.inputs
            recovery_policy = value.recovery_policy or {}
            deadline_at = value.deadline_at
            recovery_source = resume_from or value.draft_from_run_id
            recovery_digest = recovery_policy.get('preparation_recovery_digest',
                (self.status(recovery_source).recovery_draft or {}).get('draft_digest') if recovery_source else None)
            provenance = recovery_policy.get('preparation_input_provenance', {})
            bound = BoundOperationCall(compiled=compiled, name=value.output_binding_name,
                inputs=tuple(BoundInput(item.port_name, item.source_name, item.artifact_name,
                    InvocationArtifact(artifact_name=item.artifact_name, ref=item.artifact_ref,
                        schema_id=self.artifacts.catalog(item.artifact_ref).schema_id,
                        media_type=item.media_type, size_bytes=self.artifacts.catalog(item.artifact_ref).size_bytes,
                        labels=tuple(provenance.get(item.source_name, {}).get('labels',
                            self.artifacts.catalog(item.artifact_ref).labels).items()),
                        historical=provenance.get(item.source_name, {}).get('historical', False),
                        parent_refs=self.artifacts.catalog(item.artifact_ref).parent_refs,
                        producer_run_id=item.producer_run_id),
                    item.exposure, item.usage) for item in frozen_inputs),
                instruction=instruction, execution_profile=value.execution_profile,
                require_execution_review=bool(recovery_policy.get('require_execution_review')))
            # Only unready queued work may be reset; no Worker has opened it.
            reset = getattr(self.backend, 'discard_preparation', None)
            if callable(reset):
                reset(run_id)
            try:
                materialized_inputs = tuple(
                    WorkspaceInput(
                        name=item.source_name,
                        media_type=item.media_type,
                        content=self.artifacts.read(item.artifact_ref),
                    )
                    for item in frozen_inputs
                    if item.exposure not in {"handoff_only", "file_reference"}
                    and next(port for port in compiled.spec.inputs if port.name == item.port_name).agent_visible
                )
                input_bytes = {item.name: item.content for item in materialized_inputs}
                direct_revision = active_direct_revision_ports(
                    compiled, (item.port_name for item in frozen_inputs)
                )
                workspace_hooks = operation_workspace_hooks(compiled)
                revision_workspace_mode = _revision_workspace_mode(
                    direct_revision, workspace_hooks
                )
                initial_output = (
                    revision_draft_json(bound, input_bytes)
                    if revision_workspace_mode == "result_copy"
                    else None
                )
                if (
                    initial_output is not None
                    and len(initial_output) > compiled.spec.limits.max_output_bytes
                ):
                    raise RunError("revision draft exceeds the compiled output limit")
                workspace = self.backend.prepare(
                    run_id=run_id,
                    inputs=materialized_inputs,
                    assignment=assignment_json(
                        bound,
                        frozen_inputs,
                        tool_names=self.backend.assignment_tool_names(compiled),
                        recovery_relative_path=(
                            "recovery-draft" if recovery_digest is not None else None
                        ),
                        revision_workspace_mode=revision_workspace_mode,
                        prior_source_bindings=dict(self.prior_source_bindings(self.status(run_id))),
                        deadline_at=deadline_at,
                        attachment_settings=recovery_policy.get("attachments"),
                    ),
                    result_schema=result_schema_json(
                        compiled,
                        input_source_ports={
                            item.source_name: item.port_name for item in frozen_inputs
                        },
                    ),
                    recovery_digest=recovery_digest,
                    initial_output=initial_output,
                )
                self._materialize_workspace(compiled, run_id, workspace)

                with self._connect() as connection:
                    connection.execute("UPDATE runs SET workspace_ready=1 WHERE run_id=? AND state='queued'", (run_id,))
            except Exception as error:
                self.record_failure(run_id,
                    reason=f"workspace preparation failed: {str(error)[:512] if isinstance(error, RunCheckerError) else type(error).__name__}",
                    category=error.category if isinstance(error, RunCheckerError) else "runtime_failure",
                    expected_state="queued", expected_last_activity_at=None)
            return self.status(run_id).workspace_ready

    def revision_successor(
        self, *, instance_id: str, operation_digest: str, base_ref: ArtifactRef
    ) -> RunStatus | None:
        with self._connect() as connection:
            return self._revision_successor(
                connection,
                instance_id=instance_id,
                operation_digest=operation_digest,
                base_ref=base_ref,
            )

    def _revision_successor(
        self,
        connection: sqlite3.Connection,
        *,
        instance_id: str,
        operation_digest: str,
        base_ref: ArtifactRef,
    ) -> RunStatus | None:
        rows = connection.execute(
            """
            SELECT * FROM runs
            WHERE instance_id = ? AND operation_digest = ? AND state != 'failed'
            ORDER BY created_at
            """,
            (instance_id, operation_digest),
        )
        return next(
            (
                status
                for status in self._statuses_from_rows(rows)
                if any(
                    item.usage == "revision_base" and item.artifact_ref == base_ref
                    for item in status.inputs
                )
            ),
            None,
        )

    def input_is_current(
        self,
        *,
        instance_id: str,
        artifact_name: str,
        artifact_ref: ArtifactRef,
    ) -> bool:
        with self._connect() as connection:
            self._attach_scheduler(connection)
            return self.current.input_is_current(
                connection,
                instance_id=instance_id,
                artifact_name=artifact_name,
                artifact_ref=artifact_ref,
            )

    def open(
        self, *, operation_id: str, operation_digest: str, run_id: str | None = None,
        trusted_caller: tuple[str, str] | None = None,
    ) -> tuple[RunStatus, OpenWorkspace]:
        return self._open_exact(
            operation_id=operation_id,
            operation_digest=operation_digest,
            allow_running=False,
            run_id=run_id,
            trusted_caller=trusted_caller,
        )

    def reopen(
        self, *, operation_id: str, operation_digest: str, run_id: str | None = None,
        trusted_caller: tuple[str, str] | None = None,
    ) -> tuple[RunStatus, OpenWorkspace]:
        """Open the exact slot or reattach its running Run after transport restart."""

        return self._open_exact(
            operation_id=operation_id,
            operation_digest=operation_digest,
            allow_running=True,
            run_id=run_id,
            trusted_caller=trusted_caller,
        )

    def _open_exact(
        self,
        *,
        operation_id: str,
        operation_digest: str,
        allow_running: bool,
        run_id: str | None = None,
        trusted_caller: tuple[str, str] | None = None,
    ) -> tuple[RunStatus, OpenWorkspace]:
        # Select before taking the instance lock, and reselect the same row in
        # the write transaction. Opening a reused Agent must guard its NEW Run.
        with self.instance_maintenance.global_guard():
            with self._connect() as connection:
                selected = connection.execute(
                    "SELECT run_id, instance_id FROM runs WHERE operation_id = ? AND operation_digest = ? "
                    "AND state IN ('queued', 'running') AND (? IS NULL OR run_id=?) ORDER BY created_at LIMIT 1",
                    (operation_id, operation_digest, run_id, run_id),
                ).fetchone()
            if selected is None:
                raise RunStateConflict("no exact queued Run is available")
            with self.instance_maintenance.guard(str(selected["instance_id"])):
                from .scheduler_bindings import SchedulerInstanceClosed
                from .instance_maintenance import InstanceMaintenanceUnavailable
                try:
                    self.scheduler_bindings.require_active_instance(instance_id=str(selected["instance_id"]))
                except SchedulerInstanceClosed as error:
                    raise InstanceMaintenanceUnavailable("assignment instance is closed") from error
                return self._open_selected(operation_id=operation_id, operation_digest=operation_digest,
                    allow_running=allow_running, selected_run_id=str(selected["run_id"]), trusted_caller=trusted_caller)

    def _open_selected(self, *, operation_id, operation_digest, allow_running, selected_run_id, trusted_caller=None):
        if not self.prepare_queued(selected_run_id):
            raise RunPreparationBusy(selected_run_id)
        now = timestamp()
        expired_run: tuple[str, str, str | None] | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE operation_id = ? AND operation_digest = ?
                  AND run_id = ?
                  AND state IN ('queued', 'running')
                ORDER BY created_at LIMIT 1
                """,
                (operation_id, operation_digest, selected_run_id),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise RunStateConflict("no exact queued Run is available")
            if trusted_caller is not None:
                owner = connection.execute("SELECT platform_session,thread_id FROM worker_connections WHERE run_id=?",
                    (selected_run_id,)).fetchone()
                if owner is None or tuple(owner) != trusted_caller:
                    raise RunStateConflict("Worker binding changed before assignment open; use the currently attached native thread")
            if self.operation_catalog.operation(operation_id).spec.independent_review_ports:
                attached = connection.execute("SELECT 1 FROM worker_connections WHERE run_id=?", (selected_run_id,)).fetchone()
                if attached is None:
                    raise RunStateConflict("Independent review requires a control-verified reviewer attachment.")
            if row["state"] == "running" and not allow_running:
                connection.execute("ROLLBACK")
                raise RunStateConflict("no exact queued Run is available")
            if expired(self._status_from_row(row).deadline_at):
                expired_run = (
                    str(row["run_id"]),
                    str(row["state"]),
                    row["last_activity_at"],
                )
                connection.execute("ROLLBACK")
            elif row["state"] == "running":
                run_id = str(row["run_id"])
                connection.execute("COMMIT")
            else:
                connection.execute(
                    """
                    UPDATE runs SET state = 'running', started_at = ?, last_activity_at = ?
                    WHERE run_id = ? AND state = 'queued'
                    """,
                    (now, now, row["run_id"]),
                )
                connection.execute("COMMIT")
                run_id = str(row["run_id"])
        if expired_run is not None:
            self.record_failure(
                expired_run[0],
                reason="Run deadline expired before open",
                expected_state=expired_run[1],
                expected_last_activity_at=expired_run[2],
                timed_out=True,
            )
            raise RunStateConflict("queued Run has expired", run_id=expired_run[0])
        try:
            workspace = self.backend.open(run_id)
        except Exception as error:
            value = self.status(run_id)
            from .engineering_diagnostics import EngineeringDiagnostics
            engineering = EngineeringDiagnostics(self.database_path.parent.parent / "engineering-diagnostics").capture(
                error, scope="instance:" + value.instance_id, layer="run_open", action="open")
            self.record_failure(
                run_id,
                reason="prepared workspace is unavailable",
                expected_state="running",
                expected_last_activity_at=value.last_activity_at,
                engineering=engineering,
            )
            raise RunStateConflict("prepared workspace is unavailable", run_id=run_id) from error
        try:
            self.adopt_tool_evidence(run_id)
            if tool_evidence_ports(self._compiled(self.status(run_id))):
                self._refresh_evidence_schema(run_id)
        except (RunCheckerError, ReferenceProofError) as error:
            value = self.status(run_id)
            from .engineering_diagnostics import EngineeringDiagnostics
            engineering = EngineeringDiagnostics(self.database_path.parent.parent / "engineering-diagnostics").capture(
                error, scope="instance:" + value.instance_id, layer="run_open", action="open")
            self.record_failure(run_id, reason=f"preserved tool evidence cannot be opened: {error}",
                category=getattr(error, 'category', 'integrity_failure'), expected_state="running",
                expected_last_activity_at=value.last_activity_at, engineering=engineering)
            raise RunStateConflict("preserved tool evidence integrity failure", run_id=run_id) from error
        return self.status(run_id), workspace

    def heartbeat(self, run_id: str) -> RunStatus:
        value = self._require_running(run_id)
        self.backend.heartbeat(run_id)
        now = timestamp()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE runs SET last_activity_at = ?
                WHERE run_id = ? AND state = 'running' AND last_activity_at IS ?
                """,
                (now, run_id, value.last_activity_at),
            )
            if cursor.rowcount != 1:
                raise RunStateConflict("Run activity changed concurrently")
        return self.status(run_id)

    def submit(self, run_id: str, *, trusted_tool_records=None) -> tuple[str, tuple[dict[str, str], ...]]:
        # Ending tool access does not assert that a native thread has stopped.
        self.worker_connections.release_helpers(run_id)
        value = self.status(run_id)
        if value.state == "completed":
            if value.output_ref is not None:
                from .presentation_summaries import enqueue
                enqueue(self, value, value.output_ref)
            return "completed", ()
        value = self._require_running(run_id)
        try:
            sealed, validated = self._validated_candidate(value, trusted_tool_records=trusted_tool_records)
            self._accept_candidate(run_id, sealed.digest)
            output = self._register_candidate(value, validated, sealed.digest)
            self._complete(run_id, sealed.digest, output, validated.signal)
        except Exception as error:
            return self._submission_failure(run_id, error)
        return "completed", ()

    def _submission_failure(self, run_id, error):
        from .submission_errors import classify_submission_error
        from .engineering_diagnostics import EngineeringDiagnostics
        import logging
        # Report the original failure even if its database is now unavailable.
        # Never mutate from a cached state: completion may already have committed.
        try:
            value = self.status(run_id)
        except Exception as state_error:
            state_failure = classify_submission_error(state_error)
            logging.getLogger(__name__).error("Submission state unavailable for %s (%s); inspect control storage",
                run_id, type(state_error).__name__, exc_info=True)
            details = state_failure.details if state_failure.disposition == 'retry' else (contract_diagnostic(
                'submission_state_unavailable', phase='protocol', affected_action='submit', repairable=False,
                message='Control cannot confirm the persisted task state. Preserve the report; Root must restore control storage and query this same task before retrying. Do not start another execution.'),)
            return 'rejected', details
        if value.state in {"completed", "failed"}:
            return value.state, ()
        failure = classify_submission_error(error, deadline_expired=expired(value.deadline_at))
        # Expected form corrections need structured fields, not a traceback file.
        engineering = None
        if failure.disposition != 'reject':
            engineering = EngineeringDiagnostics(self.database_path.parent.parent / "engineering-diagnostics",
                settings=self.agent_settings.diagnostics).capture(
                error, scope="instance:" + value.instance_id, layer="run_submission", action="submit")
        try:
            if failure.disposition == "fail":
                self.record_failure(run_id, reason=f"Submission stopped: {failure.details[0]['code']}",
                    category=failure.category, candidate_digest=getattr(error, "candidate_digest", None),
                    engineering=engineering, details=failure.details, expected_state="running",
                    expected_last_activity_at=value.last_activity_at, timed_out=failure.category == 'run_timeout')
                return "failed", failure.details
            diagnostic = self.record_error_observation(run_id, "output_rejected" if failure.disposition == "reject" else "tool_failed",
                diagnostic={"category": failure.category, "details": failure.details,
                    **({"engineering": engineering} if engineering else {})})
        except Exception as recording_error:
            # A lifecycle write or its post-commit cleanup can fail as well.
            # Re-read if possible; never report a terminal transition we cannot confirm.
            try:
                current = self.status(run_id)
                if current.state in {'completed', 'failed'}:
                    return current.state, failure.details if current.state == 'failed' else ()
            except Exception:
                pass
            logging.getLogger(__name__).error("Submission diagnostic/lifecycle write unavailable for %s (%s)",
                run_id, type(recording_error).__name__, exc_info=True)
            if classify_submission_error(recording_error).disposition == 'retry':
                return "rejected", classify_submission_error(recording_error).details
            return 'rejected', (contract_diagnostic('submission_state_unavailable', phase='protocol',
                affected_action='submit', repairable=False,
                message='Control could not confirm the failure record. Root must restore control storage and query the existing task; preserve its draft and do not rerun the solver.'),)
        return "rejected", tuple(diagnostic.get("details", ()))

    def validate_candidate(self, run_id: str) -> ValidatedRunOutput:
        value = self._require_running(run_id)
        try:
            _, validated = self._validated_candidate(value, final_submission=False)
        except Exception as error:
            state, details = self._submission_failure(run_id, error)
            if state == "failed":
                if isinstance(error, (RunCheckerError, RunContractUnavailable)):
                    raise
                raise RunCheckerError("Candidate validation stopped; inspect the retained submission diagnostic.") from error
            raise RunOutputError("Candidate validation interrupted.", details=details, recorded=True) from error
        return validated

    def _recovery_policy(self, compiled: Any) -> dict[str, Any]:
        limits = compiled.spec.limits
        return {
            "operation_id": compiled.spec.operation_id,
            "operation_digest": compiled.digest,
            "backend_id": self.backend.backend_id,
            "backend_version": self.backend.backend_version,
            "max_files": limits.max_files, "max_output_bytes": limits.max_output_bytes,
            "max_attempts": limits.max_attempts,
            "helpers": self.agent_settings.helpers.model_dump(),
            "execution_io": self.agent_settings.execution_io.model_dump(),
            "input_materials": self.agent_settings.input_materials.model_dump(),
            "attachments": self.agent_settings.attachments.model_dump(),
            "snapshot_max_files": 132, "snapshot_max_bytes": 32 * 1024 * 1024,
        }

    def record_failure(
        self, run_id: str, *, reason: str, expected_state: str,
        expected_last_activity_at: str | None, timed_out: bool = False,
        category: str = "runtime_failure", candidate_digest: str | None = None,
        engineering: dict | None = None, details: tuple = (),
    ) -> RunStatus:
        value = self.status(run_id)
        if value.state == "failed":
            return self._finish_failed_workspace(value)
        if expected_state not in {"queued", "running"}:
            raise ValueError("failure precondition state is invalid")
        if value.state != expected_state or value.last_activity_at != expected_last_activity_at:
            raise RunStateConflict("Run failure compare-and-set failed")
        if timed_out and not expired(value.deadline_at):
            raise RunStateConflict("Run deadline has not expired")
        # First fence the worker; cleanup is restartable and never discards an unsealed draft.
        now = timestamp()
        diagnostic = self._safe_diagnostic("run_timeout" if timed_out else category, repairable=False)
        if engineering:
            diagnostic["engineering"] = engineering
        if details:
            diagnostic = self.sanitize_diagnostic(value, {**diagnostic, "details": details}, repairable=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if timed_out and not expired(self._status_from_row(self._row(connection, run_id)).deadline_at):
                raise RunStateConflict("Run deadline has not expired")
            cursor = connection.execute(
                """UPDATE runs SET state='failed', reason=?, completed_at=?,
                    recovery_candidate_digest=COALESCE(?, recovery_candidate_digest)
                    WHERE run_id=? AND state=? AND last_activity_at IS ?""",
                (reason[:4096], now, candidate_digest, run_id, expected_state,
                 expected_last_activity_at))
            if cursor.rowcount != 1:
                raise RunStateConflict("Run failure compare-and-set failed")
            self._append_activity(connection, run_id, "framework_failure", now, canonical_json(diagnostic))
        return self._finish_failed_workspace(self.status(run_id))

    def _finish_failed_workspace(self, value: RunStatus) -> RunStatus:
        if value.state != "failed":
            raise RunStateConflict("Run is not failed")
        self.worker_connections.release_helpers(value.run_id)
        # Worker lifetime and external execution lifetime are independent. Preserve
        # authorized jobs and their immutable bindings for resume or Root recovery.
        # Cancellation is an explicit execution command, never failure cleanup.
        if value.recovery_draft and value.recovery_draft.get("draft_digest"):
            # A delivered immutable subset stays frozen. Pending native writers
            # are not cleared merely because another failure request arrived.
            if value.recovery_draft.get("recovery_pending"):
                self._verify_draft(value, value.recovery_draft["draft_digest"])
            return value
        if (value.backend_id != self.backend.backend_id
                or value.backend_version != self.backend.backend_version
                or value.backend_capabilities != self.backend.capabilities):
            self._pending_recovery(value, "backend_unavailable",
                complete_snapshot_digest=(value.recovery_draft or {}).get("complete_snapshot_digest"))
            return self.status(value.run_id)
        try:
            compiled = self._compiled(value)
        except RunContractUnavailable:
            # output/ alone cannot prove coverage of deck/ or other domain files.
            # No new-contract hooks and no destructive isolation are permitted here.
            policy = value.recovery_policy
            digest = value.recovery_candidate_digest
            if policy is not None and digest is None:
                try:
                    digest = self.backend.seal(value.run_id,
                        max_files=policy["max_files"],
                        max_bytes=policy["max_output_bytes"]).digest
                except Exception:
                    pass
            self._pending_recovery(value, "contract_unavailable", digest=digest,
                complete_snapshot_digest=(value.recovery_draft or {}).get("complete_snapshot_digest"))
            return self.status(value.run_id)
        limits = compiled.spec.limits
        snapshotter = operation_workspace_hooks(compiled).get("workspace_snapshotter")
        max_files = 132 if snapshotter else limits.max_files
        max_bytes = 32 * 1024 * 1024 if snapshotter else limits.max_output_bytes
        try:
            coverage = (value.recovery_draft or {}).get("complete_snapshot_digest")
            workspace = self.backend.open(value.run_id) if coverage is None else None
            retain_original = (self.backend.backend_id == "local_trusted"
                and (value.recovery_policy or {}).get("retain_original_on_failure") is True)
            if retain_original and workspace:
                from ...plugin_runtime.observation import request_stop
                request_stop(workspace.root)
            proof_unavailable = None
            if coverage is None and value.accepted_candidate_digest is None and value.recovery_candidate_digest is None:
                try:
                    self._prepare_evidence_snapshot(value.run_id)
                except ReferenceProofError as error:
                    proof_unavailable = error.details[0]['code']
            if coverage is not None:
                digest = coverage
            elif value.accepted_candidate_digest is not None:
                digest = self.backend.seal(value.run_id, max_files=max_files,
                    max_bytes=max_bytes, expected_digest=value.accepted_candidate_digest).digest
            elif snapshotter is not None:
                snapshot = tuple(WorkspaceInput(name=item.relative_path,
                    media_type=item.media_type, content=item.content)
                    for item in snapshotter(self.backend.open(value.run_id).root))
                digest = self.backend.seal(value.run_id, max_files=max_files,
                    max_bytes=max_bytes, snapshot=snapshot).digest
            else:
                digest = self.backend.seal(value.run_id, max_files=max_files,
                    max_bytes=max_bytes, expected_digest=(value.accepted_candidate_digest
                        or value.recovery_candidate_digest)).digest
            # Only a matching contract's snapshot scope establishes cleanup coverage.
            self._pending_recovery(value, "writers_unconfirmed" if retain_original else "isolation_incomplete",
                digest=digest, complete_snapshot_digest=None if retain_original else digest)
            draft = self.backend.discard(value.run_id, preserve_digest=digest,
                max_files=max_files, max_bytes=max_bytes,
                **({"retain_original": True} if retain_original else {}))
            if draft is None:
                raise WorkspaceError("recovery draft unavailable")
            manifest = {
                "source_run_id": value.run_id, "source_request_digest": value.request_digest,
                "operation_digest": value.operation_digest,
                "input_refs": [item.artifact_ref.model_dump(mode="json") for item in value.inputs],
                "backend": draft.backend, "backend_version": draft.backend_version,
                "draft_digest": draft.digest,
                "files": [{"relative_path": item.relative_path, "media_type": item.media_type,
                           "size_bytes": item.size_bytes, "sha256": item.sha256} for item in draft.files],
            }
            if proof_unavailable:
                manifest['evidence_proof_unavailable'] = proof_unavailable
            if retain_original:
                manifest.update(recovery_pending=True, code="writers_unconfirmed",
                    original_retained=True)
            with self._connect() as connection:
                connection.execute("UPDATE runs SET recovery_draft_json=? WHERE run_id=? AND state='failed'",
                                   (canonical_json(manifest), value.run_id))
        except Exception as error:
            current = self.status(value.run_id)
            if not current.recovery_draft:
                self._pending_recovery(current, "snapshot_unavailable")
            elif current.recovery_draft.get("complete_snapshot_digest"):
                raise RunError("failed Run workspace isolation is incomplete") from error
        return self.status(value.run_id)

    def _pending_recovery(self, value: RunStatus, code: str, *, digest: str | None = None,
                          complete_snapshot_digest: str | None = None) -> None:
        record = {**(value.recovery_draft or {}), "recovery_pending": True, "code": code}
        if complete_snapshot_digest is not None:
            record["complete_snapshot_digest"] = complete_snapshot_digest
        with self._connect() as connection:
            connection.execute("""UPDATE runs SET recovery_draft_json=?,
                recovery_candidate_digest=COALESCE(?, recovery_candidate_digest)
                WHERE run_id=? AND state='failed'""",
                (canonical_json(record), digest, value.run_id))

    def fail(self, run_id: str, *, reason: str) -> RunStatus:
        value = self.status(run_id)
        return self.record_failure(
            run_id,
            reason=reason,
            expected_state=value.state,
            expected_last_activity_at=value.last_activity_at,
        )

    def task_recovery_available(self, origin: str, *, instance_id: str, for_start: bool = False) -> bool:
        """Closed limited owners permit result recovery, but never a new start."""
        with self._connect() as connection:
            rows = connection.execute("""
                WITH RECURSIVE family(run_id, state, output_ref_json) AS (
                    SELECT run_id, state, output_ref_json FROM runs WHERE run_id=? AND instance_id=?
                    UNION
                    SELECT r.run_id, r.state, r.output_ref_json FROM runs r JOIN family f
                      ON r.resume_from_run_id=f.run_id OR r.draft_from_run_id=f.run_id
                    WHERE r.instance_id=?
                ) SELECT state, output_ref_json FROM family
            """, (origin, instance_id, instance_id)).fetchall()
        for state, output in rows:
            if state == 'failed':
                continue
            if for_start or state != 'completed' or not output:
                return False
            ref = ArtifactRef.model_validate_json(output)
            if ref.schema_id != 'scidiscovery.experiment-report.v1':
                return False
            if json.loads(self.artifacts.read(ref)).get('outcome') not in {'blocked', 'inconclusive'}:
                return False
        return bool(rows)

    def expired_active_ids(self, *, after: str = "", limit: int = 32) -> tuple[str, ...]:
        with self._connect() as connection:
            return tuple(row[0] for row in connection.execute(
                "SELECT run_id FROM runs WHERE state IN ('queued','running') "
                "AND deadline_at <= ? AND run_id > ? ORDER BY run_id LIMIT ?",
                (timestamp(), after, limit)))

    def reconcile_expired_active(self, *, limit: int | None = None, run_ids=None,
            reason: str = "Run deadline expired during control startup reconciliation") -> int:
        """Fail expired active Runs at an explicit lifecycle coordination point."""
        if run_ids is None:
            run_ids = self.expired_active_ids(limit=limit if limit is not None else -1)
        failed = 0
        for run_id in run_ids:
            for _ in range(3):
                value = self.status(run_id)
                if value.state not in {"queued", "running"} or not expired(value.deadline_at):
                    break
                try:
                    with self.instance_maintenance.guard(value.instance_id):
                        self.scheduler_bindings.require_active_instance(instance_id=value.instance_id)
                        self.record_failure(
                            run_id, reason=reason,
                            expected_state=value.state,
                            expected_last_activity_at=value.last_activity_at,
                            timed_out=True,
                        )
                except RunStateConflict:
                    continue
                except (InstanceMaintenanceUnavailable, SchedulerInstanceClosed):
                    break  # Archives and closed instances are not live lifecycle targets.
                failed += 1
                break
            else:
                raise RunStateConflict("expired Run changed during startup reconciliation", run_id=run_id)
        return failed

    def _statuses_from_rows(self, rows):
        from .approval_waits import approval_records, effective_deadline, datetime, timezone
        values = tuple(status_from_row(row) for row in rows)
        if not values:
            return ()
        with self._connect() as connection:
            events = connection.execute("SELECT run_id,recorded_at,diagnostic_json FROM run_activity "
                "WHERE run_id IN (SELECT value FROM json_each(?)) "
                "AND activity='approval_wait_started' ORDER BY recorded_at",
                (json.dumps([value.run_id for value in values]),)).fetchall()
        records = approval_records(events, self.approval_database_path)
        grouped = {}
        for event in events:
            grouped.setdefault(event['run_id'], []).append(event)
        now = datetime.now(timezone.utc)
        return tuple(replace(value, deadline_at=effective_deadline(value,
            grouped.get(value.run_id, ()), self.approval_database_path, records=records, now=now)) for value in values)

    def _status_from_row(self, row):
        return self._statuses_from_rows((row,))[0]

    def record_approval_wait(self, run_id: str, approval_id: str):
        """Record a control-owned wait once; its end comes from the approval store."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT 1 FROM run_activity WHERE run_id=? "
                "AND activity='approval_wait_started' "
                "AND json_extract(CAST(diagnostic_json AS TEXT),'$.approval_id')=?",
                (run_id, approval_id)).fetchone()
            if existing is not None:
                return
            from .approval_waits import approval_records, effective_deadline
            value = status_from_row(self._row(connection, run_id))
            events = list(connection.execute("SELECT recorded_at,diagnostic_json FROM run_activity "
                "WHERE run_id=? AND activity='approval_wait_started'", (run_id,)).fetchall())
            event = {"recorded_at": timestamp(), "diagnostic_json": canonical_json({"approval_id": approval_id})}
            events.append(event)
            records = approval_records(events, self.approval_database_path)
            deadline = effective_deadline(value, events, self.approval_database_path, records=records)
            if value.state != "running" or expired(deadline):
                raise RunStateConflict("Approval wait cannot extend an expired or terminal Run")
            self._append_activity(connection, run_id, "approval_wait_started", records[approval_id]['created_at'],
                canonical_json({"approval_id": approval_id}))

    def status(self, run_id: str) -> RunStatus:
        """Pure read: observing an expired Run never changes control state."""

        with self._connect() as connection:
            return self._status_from_row(self._row(connection, run_id))

    def list(self, *, instance_id: str, state: str | None = None) -> tuple[RunStatus, ...]:
        query = "SELECT * FROM runs WHERE instance_id = ?"
        values: list[Any] = [instance_id]
        if state is not None:
            query += " AND state = ?"
            values.append(state)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return self._statuses_from_rows(rows)

    def active_ids(self, *, instance_id: str, limit: int = 31) -> tuple[str, ...]:
        """Bound current tasks by state before paging; never read output payloads."""
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("active Run limit must be between 1 and 101")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs WHERE instance_id = ? AND state IN ('queued', 'running') "
                "ORDER BY created_at DESC, run_id LIMIT ?", (instance_id, limit),
            ).fetchall()
        return tuple(str(row["run_id"]) for row in rows)

    def recent_ids(self, *, instance_id: str, limit: int = 1) -> tuple[str, ...]:
        """Read recent control identities without assigning scientific priority."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("recent Run limit must be between 1 and 100")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs WHERE instance_id=? ORDER BY created_at DESC, run_id LIMIT ?",
                (instance_id, limit),
            ).fetchall()
        return tuple(str(row["run_id"]) for row in rows)

    def related_runs(self, *, instance_id: str, artifact_ref: ArtifactRef,
                     relation: str = "input", limit: int = 51) -> tuple[RunStatus, ...]:
        """Read exact input/output edges without loading or validating outputs."""
        if relation not in {"input", "output"} or type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("invalid related Run query")
        query = "SELECT r.* FROM runs r WHERE r.instance_id=?"
        values: list[Any] = [instance_id]
        if relation == "output":
            query += " AND r.output_ref_json=?"
            values.append(artifact_ref.canonical_json())
        else:
            query += " AND EXISTS (SELECT 1 FROM json_each(CAST(r.inputs_json AS TEXT)) i WHERE " + " AND ".join(
                "json_extract(i.value, '$.artifact_ref." + field + "')=?" for field in ("artifact_id", "sha256", "kind", "schema_id")) + ")"
            values.extend((artifact_ref.artifact_id, artifact_ref.sha256, artifact_ref.kind, artifact_ref.schema_id))
        query += " ORDER BY r.created_at, r.run_id LIMIT ?"
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return self._statuses_from_rows(rows)

    def recovery_links(self, run_id: str) -> dict[str, str | None]:
        """Read frozen resume/draft identity omitted from the public RunStatus."""
        with self._connect() as connection:
            row = self._row(connection, run_id)
        return {key: row[key] for key in ("resume_from_run_id", "draft_from_run_id")}

    def recovery_authorized(self, run_id: str, source_run_id: str) -> bool:
        """Read only the sealed control recovery chain, never workspace claims."""
        current = self.status(run_id)
        pending, seen = [run_id], set()
        while pending:
            candidate = pending.pop()
            if candidate in seen:
                continue
            seen.add(candidate)
            if self.status(candidate).instance_id != current.instance_id:
                raise RunStateConflict("recovery chain crosses instance boundary")
            if candidate == source_run_id:
                return True
            pending.extend(value for value in self.recovery_links(candidate).values() if value)
        return False

    def diagnostic_reference_belongs(self, run_id: str, reference: str) -> bool:
        """Associate an engineering reference with this exact durable Run event."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM run_activity WHERE run_id=? AND "
                "json_extract(CAST(diagnostic_json AS TEXT), '$.engineering.reference')=? LIMIT 1",
                (run_id, reference),
            ).fetchone()
        return row is not None

    def latest_subject_review(self, instance_id: str, subject: ArtifactRef) -> RunStatus | None:
        """Latest completed formal review of this exact subject, in this instance."""
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM runs WHERE instance_id=? AND operation_id=? AND state='completed' "
                "ORDER BY completed_at DESC, run_id DESC", (instance_id, "science.object.review.v1")).fetchall()
        for row in rows:
            review = self._status_from_row(row)
            subjects = [item.artifact_ref for item in review.inputs if item.port_name == "subject"]
            if subjects == [subject]:
                return review
        return None

    def completed_for_output(self, artifact_ref: ArtifactRef) -> RunStatus | None:
        """Return the unique completed Run that registered this exact output."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE state = 'completed' AND output_ref_json = ?
                """,
                (artifact_ref.canonical_json(),),
            ).fetchall()
        if len(rows) > 1:
            raise RunStateConflict("multiple Runs claim the same exact output")
        if rows:
            return self._status_from_row(rows[0])
        producer = self.artifacts.catalog(artifact_ref).labels.get('tool_producer_run')
        if producer:
            try:
                value = self.status(producer)
            except RunNotFound:
                return None
            if any(ref == artifact_ref for _, ref in self.evidence_output_refs(value)):
                return value
            envelope = self.artifacts.catalog(artifact_ref)
            if (value.state == "completed" and value.output_ref is not None
                    and artifact_ref in self.artifacts.catalog(value.output_ref).parent_refs
                    and envelope.labels.get("operation_output_port") == "recovery_manifest_output"
                    and envelope.labels.get("operation_digest") == value.operation_digest
                    and envelope.schema_id == "scidiscovery.tool-evidence-manifest.v1"):
                return value

        return None

    def record_activity(self, run_id: str, activity: str, *,
                        diagnostic: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not activity or len(activity) > 128:
            raise ValueError("Run activity is invalid")
        value = self._require_running(run_id)
        normalized = None if diagnostic is None else self.sanitize_diagnostic(
            value, diagnostic, repairable=activity == "output_rejected")
        now = timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE runs SET last_activity_at = ?
                WHERE run_id = ? AND state = 'running' AND last_activity_at IS ?
                """,
                (now, run_id, value.last_activity_at),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise RunStateConflict("Run activity changed concurrently")
            self._append_activity(connection, run_id, activity, now,
                None if normalized is None else canonical_json(normalized))
            connection.execute("COMMIT")
        return normalized

    @staticmethod
    def _safe_diagnostic(category: str, *, repairable: bool) -> dict[str, Any]:
        allowed = {"output_rejected", "integrity_failure", "admission_defect", "checker_failure",
                   "tool_failed", "runtime_failure", "run_timeout", "tool_timeout",
                   "worker_profile_mismatch", "execution_pending"}
        category = category if category in allowed else "runtime_failure"
        return {"category": category, "code": category,
                "repairable_by_output": repairable and category != "execution_pending"}

    def record_tool_observation(self, run_id: str, activity: str, observation: dict) -> None:
        """Engineering timing only: no heartbeat, candidate or lifecycle mutation."""
        with self._connect() as connection:
            self._row(connection, run_id)
            self._append_activity(connection, run_id, activity, timestamp(), canonical_json(observation))

    @staticmethod
    def _append_activity(connection, run_id, activity, recorded_at, diagnostic_json):
        """Allocate a durable event ID in the caller's original transaction.

        Archive removal may delete the largest visible rowid; the global high
        water mark preserves its identity for a later exact restore. Reading
        MAX also accommodates legacy/imported explicit event IDs without
        changing those rows or their pagination semantics.
        """
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            "UPDATE run_activity_sequence SET high_water = "
            "MAX(high_water, COALESCE((SELECT MAX(rowid) FROM run_activity), 0)) + 1 WHERE slot = 1"
        )
        if changed.rowcount != 1:
            raise RunError("Run activity identity allocator is unavailable")
        event_id = connection.execute("SELECT high_water FROM run_activity_sequence WHERE slot = 1").fetchone()[0]
        connection.execute(
            "INSERT INTO run_activity(rowid,run_id,activity,recorded_at,diagnostic_json) VALUES (?,?,?,?,?)",
            (event_id, run_id, activity, recorded_at, diagnostic_json))
        if activity in {'tool_failed', 'output_rejected'} and diagnostic_json:
            from .run_attention import record, SUBMISSION_CODES
            for detail in json.loads(diagnostic_json).get('details', ()):
                if detail.get('code') in SUBMISSION_CODES:
                    record(RunService, connection, run_id, scope='submission:' + detail['code'], code=detail['code'], now=recorded_at)
        return event_id

    def record_error_observation(self, run_id: str, activity: str, *, diagnostic: dict[str, Any]) -> dict[str, Any]:
        """Retain failures without reopening or heartbeating an expired Run."""
        value = self.status(run_id)
        normalized = self.sanitize_diagnostic(value, diagnostic, repairable=activity == "output_rejected")
        self.record_tool_observation(run_id, activity, normalized)
        return normalized

    def tool_timing(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT activity, recorded_at, diagnostic_json FROM run_activity
                WHERE run_id=? AND activity IN ('tool_call_started','tool_call_completed',
                'tool_attempt_started','tool_attempt_completed') ORDER BY rowid DESC LIMIT 128""", (run_id,)).fetchall()
        calls = {}
        for row in reversed(rows):
            raw = json.loads(row['diagnostic_json'])
            key = raw.get('call_key') or raw.get('attempt_key')
            record = calls.setdefault(key, {"tool_name": raw.get('tool_name')})
            if row['activity'].endswith('_started'):
                record['started_at'] = raw.get('started_at', row['recorded_at'])
            else:
                record['started_at'] = raw.get('started_at', record.get('started_at'))
                record['completed_at'] = raw.get('completed_at', row['recorded_at'])
                if record.get('started_at'):
                    record['duration_seconds'] = raw.get('duration_seconds', max(0,
                        (datetime.fromisoformat(record['completed_at'].replace('Z', '+00:00')) - datetime.fromisoformat(record['started_at'].replace('Z', '+00:00'))).total_seconds()))
        return list(calls.values())[-16:]

    def rejection_diagnostic(self, value: RunStatus, error: Exception) -> dict[str, Any]:
        """Build output rejection facts for the controlled diagnostic path."""
        return {"category": getattr(error, "category", "output_rejected"), "details":getattr(error, "details", ()) or
                ({"path":"$", "type":"value_error"},)}

    def sanitize_diagnostic(self, value: RunStatus, diagnostic: dict[str, Any],
                            *, repairable: bool) -> dict[str, Any]:
        """Project diagnostic facts through this Run's frozen public contract."""
        result = self._safe_diagnostic(diagnostic.get("category"), repairable=repairable)
        try:
            compiled = self._compiled(value)
            contract = operation_output_validation_contract(compiled, operation_primary_output(compiled))
            rules = {item["rule_id"]:item.get("description") for item in contract["rules"]}
            semantic = compiled.output_contracts[operation_primary_output(compiled).name].get(
                "x-scidiscovery-semantic-constraints", {})
            rules.update({item["rule_id"]: item["description"] for item in semantic.get("rules", ())})
            rule_phases = {item["rule_id"]:"output_"+item["phase"] for item in contract["checkers"]}
            for item in contract["checkers"]:
                rules.setdefault(item["rule_id"], None)
        except RunContractUnavailable:
            rules, rule_phases = {}, {}
        schema = compiled.output_contracts[operation_primary_output(compiled).name] if rules else {}
        tool_name = diagnostic.get("tool_name")
        tool = next((tool for tool in operation_worker_tools(compiled) if tool.name == tool_name), None) if rules else None
        phase, action = "output_payload", "submit"
        if result["category"] == "tool_failed":
            phase, action = "tool_execution", "tool_call"
            from ..interfaces.mcp_worker_protocol import LIFECYCLE_WORKER_TOOLS
            tool = tool or next((tool for tool in LIFECYCLE_WORKER_TOOLS if tool.name == tool_name),None)
            schema = tool.schema()["inputSchema"] if tool else {}
            if tool:
                result["tool_name"] = tool.name
            if any(item.get("phase") == "tool_arguments" for item in diagnostic.get("details", ()) if isinstance(item,dict)):
                phase = "tool_arguments"
        details = sanitize_diagnostic_details(diagnostic.get("details", ()),
            schema=schema, rules=rules, phase=phase, action=action, rule_phases=rule_phases)
        if details:
            result["details"] = list(details)
            # Set only after sanitization; never treat an incoming flag as trust.
            result["safe_details_version"] = 1
        if isinstance(diagnostic.get("engineering"), dict):
            # Produced by the shared error recorder, never scientific payload fields.
            result["engineering"] = diagnostic["engineering"]
        return result

    @classmethod
    def _stored_diagnostic(cls, row: Any) -> dict[str, Any] | None:
        if row is None or row["diagnostic_json"] is None:
            return None
        raw = json.loads(row["diagnostic_json"])
        result = cls._safe_diagnostic(raw.get("category"),
            repairable=row["activity"] == "output_rejected")
        if raw.get("tool_name"):
            result["tool_name"] = raw["tool_name"]
        if isinstance(raw.get("engineering"), dict):
            result["engineering"] = raw["engineering"]
        if raw.get("details"):
            details = raw["details"][:16]
            if raw.get("safe_details_version") == 1:
                from ...operation_contract import DeclaredDiagnostic
                from ..schema.common import ContractDiagnostic
                details = [DeclaredDiagnostic(ContractDiagnostic.model_validate(item).model_dump(
                    mode="json", exclude_none=True)) for item in details]
            result["details"] = details
        return result

    def diagnostic_events(self, value: RunStatus, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
        """Page the existing durable error records; never read Worker drafts."""
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("invalid diagnostic page bounds")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT rowid AS event_id, activity, recorded_at, diagnostic_json
                FROM run_activity WHERE run_id=? AND rowid>? AND activity IN
                ('tool_failed','tool_not_computed','output_rejected','framework_failure','observation_unavailable')
                ORDER BY rowid LIMIT ?""", (value.run_id, after, limit + 1)).fetchall()
        events = [{"event_id": row["event_id"], "recorded_at": row["recorded_at"],
                   "activity": row["activity"], "diagnostic": self._stored_diagnostic(row)}
                  for row in rows[:limit]]
        return {"events": events, "next_after": events[-1]["event_id"] if len(rows) > limit else None}

    def record_attention(self, run_id, *, scope, code):
        from .run_attention import record
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            value = self._row(connection, run_id)
            now = timestamp()
            record(self, connection, run_id, scope=scope, code=code, now=now)
            if code is None and scope.startswith('implementation:'):
                # A successful exact lookup during recovery repairs that same
                # execution's issue in its earlier owner as well, never another job.
                rows = connection.execute("SELECT DISTINCT a.run_id FROM run_activity a JOIN runs r ON r.run_id=a.run_id "
                    "WHERE r.instance_id=? AND a.run_id!=? AND a.activity='attention_state' "
                    "AND json_extract(CAST(a.diagnostic_json AS TEXT),'$.scope')=?",
                    (value['instance_id'], run_id, scope)).fetchall()
                for row in rows:
                    record(self, connection, row['run_id'], scope=scope, code=None, now=now)

    def attention_issues(self, *, run_id=None, instance_id=None):
        from .run_attention import issues
        with self._connect() as connection:
            return issues(connection, run_id=run_id, instance_id=instance_id)

    def pending_attention_notices(self, *, instance, observer, limit=8):
        from .run_attention import pending_notices
        with self._connect() as connection:
            return pending_notices(self, connection, instance=instance, observer=observer, limit=limit)

    def submission_attention(self, value: RunStatus):
        from .run_attention import projection
        return projection([item for _, item in self.attention_issues(run_id=value.run_id)])

    def diagnostic_summary(self, value: RunStatus) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT activity, diagnostic_json FROM run_activity WHERE run_id=? ORDER BY rowid",
                (value.run_id,)).fetchall()
        rejections = [row for row in rows if row["activity"] == "output_rejected"]
        safe = self._stored_diagnostic
        failures = [row for row in rows if row["activity"] == "framework_failure"]
        tool_errors = [row for row in rows if row["activity"] in {"tool_failed", "tool_not_computed"}]
        errors = [row for row in rows if row["activity"] in {"tool_failed", "tool_not_computed", "output_rejected", "framework_failure"} and row["diagnostic_json"] is not None]
        return {"latest_tool_error": safe(tool_errors[-1] if tool_errors else None),
                "recent_errors": [safe(row) for row in errors[-8:]],
                "rejection_count": len(rejections),
                "latest_rejection": safe(rejections[-1] if rejections else None),
                "failure": safe(failures[-1] if failures else None)}

    def successful_tools(self, run_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT activity FROM run_activity
                WHERE run_id = ? AND activity LIKE 'tool_succeeded:%'
                """,
                (run_id,),
            ).fetchall()
        return tuple(sorted({str(row["activity"]).partition(":")[2] for row in rows}))

    def signal_for_output(self, artifact_ref: ArtifactRef, *, require_current: bool = True) -> SchedulerSignal | None:
        status = self.completed_for_output(artifact_ref)
        if status is None:
            return None
        if require_current and not self._completed_output_is_compatible(status):
            return None
        return status.signal

    def _completed_output_is_compatible(self, value: RunStatus) -> bool:
        """Read sealed science under its versioned type, without reopening its Run."""
        if value.state != "completed" or value.output_ref is None:
            return False
        try:
            compiled = self.operation_catalog.operation(value.operation_id)
        except KeyError:
            return False
        if compiled.spec.version != value.operation_version:
            return False
        envelope = self.artifacts.catalog(value.output_ref)
        return any(
            port.name == envelope.labels.get("operation_output_port")
            and port.schema_id == value.output_ref.schema_id
            and port.kind == value.output_ref.kind
            and envelope.media_type in port.media_types
            for port in compiled.spec.outputs
        )

    def is_exact_reviewer_output(
        self,
        artifact_ref: ArtifactRef,
        *,
        reviewer_operation: str,
        reviewer_input_port: str,
        accepted_verdicts: tuple[str, ...] | None,
        subject_ref: ArtifactRef,
        require_compatible: bool = True,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE state = 'completed' AND output_ref_json = ?
                """,
                (artifact_ref.canonical_json(),),
            ).fetchone()
        if row is None or str(row["operation_id"]) != reviewer_operation:
            return False
        status = self._status_from_row(row)
        return bool(
            status.signal is not None
            and (accepted_verdicts is None or status.signal.verdict in accepted_verdicts)
            and any(
                item.port_name == reviewer_input_port and item.artifact_ref == subject_ref
                for item in status.inputs
            )
            and (not require_compatible or self._completed_output_is_compatible(status))
        )

    def _validated_candidate(
        self, value: RunStatus, *, final_submission: bool = True, trusted_tool_records=None
    ) -> tuple[Any, ValidatedRunOutput]:
        compiled = self._compiled(value)
        assert compiled.spec.limits is not None
        incomplete_collections = ()
        if final_submission and compiled.spec.executor.capability is not None:
            coordinator = getattr(self, "experiment_executions", None)
            if coordinator is None:
                raise ConnectionError("Experiment reconciliation service unavailable")
            incomplete_collections = coordinator.require_idle(value)
        try:
            if value.accepted_candidate_digest is None:
                self._finalize_workspace(compiled, value.run_id, final_submission=final_submission, trusted_tool_records=trusted_tool_records)
                self._prepare_evidence_snapshot(value.run_id)
            sealed = self.backend.seal(
                value.run_id,
                max_files=compiled.spec.limits.max_files,
                max_bytes=compiled.spec.limits.max_output_bytes,
                expected_digest=value.accepted_candidate_digest,
            )
        except (RunOutputError, RunCheckerError):
            raise
        except WorkspaceOutputError as error:
            if value.accepted_candidate_digest is None:
                raise RunOutputError("candidate files require correction", details=(
                    contract_diagnostic("output_invalid", phase="output_payload", affected_action="submit",
                        repairable=True, message=str(error)[:512], error_type="value_error"),)) from error
            raise RunCheckerError("accepted candidate integrity failed", category="integrity_failure") from error
        except WorkspaceError as error:
            raise RunCheckerError("Run workspace integrity failed", category="integrity_failure") from error
        # Reserve half the remaining Run time for publishing after domain replay.
        remaining = (datetime.fromisoformat(value.deadline_at.replace("Z", "+00:00"))
                     - datetime.now(timezone.utc)).total_seconds()
        validation_deadline = time.monotonic() + max(0, remaining) / 2
        try:
            input_bytes, descriptors = self._validation_inputs(value)
            reference_validation_budget = {}
            validated = validate_run_output(
                compiled,
                sealed,
                input_source_ports=self.validation_source_ports(value),
                input_bytes=input_bytes,
                input_binding_descriptors=descriptors,
                validation_deadline=validation_deadline,
                tool_snapshot=self._evidence_snapshot(value.run_id) if tool_evidence_ports(compiled) else None,
                reference_calculation_resolver=lambda alias: self.reference_calculation_sources(value, alias, validation_deadline=validation_deadline, validation_budget=reference_validation_budget),
                sealed_material_resolver=lambda alias, **options: self.sealed_material_record(value, alias, **options),
            )
            if incomplete_collections:
                coordinator.validate_incomplete_delivery(validated.content, incomplete_collections)
        except WorkspaceError as error:
            failure = RunCheckerError("sealed candidate integrity failed", category="integrity_failure")
            failure.candidate_digest = sealed.digest
            raise failure from error
        except RunCheckerError as error:
            error.candidate_digest = sealed.digest
            raise
        return sealed, validated

    def _input_sources(
        self, value: RunStatus
    ) -> tuple[dict[str, bytes], dict[str, InputBindingDescriptor]]:
        contents: dict[str, bytes] = {}
        descriptors: dict[str, InputBindingDescriptor] = {}
        for item in value.inputs:
            try:
                envelope = self.artifacts.catalog(item.artifact_ref)
                reference_only = item.exposure == "file_reference"
                if reference_only:
                    self.artifacts.verify(item.artifact_ref)
                content = b"" if reference_only else self.artifacts.read(item.artifact_ref)
            except CASIntegrityError as error:
                raise RunCheckerError("exact Run input bytes failed verification", category="integrity_failure") from error
            if (
                envelope.ref != item.artifact_ref
                or envelope.media_type != item.media_type
                or envelope.sha256 != item.artifact_ref.sha256
                or (not reference_only and (envelope.size_bytes != len(content)
                    or hashlib.sha256(content).hexdigest() != item.artifact_ref.sha256))
                or item.source_name in descriptors
            ):
                raise RunCheckerError("input registration contradicts the exact Run binding", category="integrity_failure")
            contents[item.source_name] = content
            descriptors[item.source_name] = self.source_descriptor(value, item.source_name)
        acquired, acquired_descriptors = self.evidence_sources(value.run_id)
        contents.update(acquired); descriptors.update(acquired_descriptors)
        return contents, descriptors

    def _validation_inputs(
        self, value: RunStatus
    ) -> tuple[dict[str, bytes], dict[str, InputBindingDescriptor]]:
        contents, descriptors = self._input_sources(value)
        if tool_evidence_ports(self._compiled(value)):
            manifest_raw = self._evidence_snapshot(value.run_id)
            contents["tool_recovery_manifest"] = manifest_raw
            descriptors["tool_recovery_manifest"] = InputBindingDescriptor(source_name="tool_recovery_manifest", port_name="recovery_manifest_output", artifact_ref=self._evidence_manifest(value), media_type="application/json", size_bytes=len(manifest_raw), sha256=hashlib.sha256(manifest_raw).hexdigest())
        return contents, descriptors

    def _materialize_workspace(
        self, compiled: Any, run_id: str, workspace: OpenWorkspace
    ) -> None:
        if self.backend.backend_id == "local_trusted":
            from ...plugin_runtime.observation import materialize_launcher
            try:
                materialize_launcher(workspace.root)
            except (OSError, WorkspaceError, ValueError) as error:
                from .engineering_diagnostics import EngineeringDiagnostics
                facts = EngineeringDiagnostics(self.database_path.parent.parent / "engineering-diagnostics").capture(
                    error, scope="instance:" + self.status(run_id).instance_id,
                    layer="worker_workspace", action="install_observation_launcher")
                try:
                    self.record_tool_observation(run_id, "observation_unavailable", {"engineering": facts})
                except Exception:
                    pass  # Optional observation never prevents scientific work.
        materializer = operation_workspace_hooks(compiled).get(
            "workspace_materializer"
        )
        if materializer is None:
            return
        recovery = workspace.root / "recovery-draft"
        value = self.status(run_id)
        contents, descriptors = self._input_sources(value)
        try:
            result = materializer(
                WorkspaceMaterializationRequest(
                    operation_id=compiled.spec.operation_id,
                    workspace=workspace.root,
                    input_paths=workspace.input_paths,
                    provisional_roots=(recovery,) if recovery.is_dir() else (),
                    edit_protocol=getattr(self.backend, "edit_protocol", "native"),
                    binding_descriptors=descriptors,
                    input_contents=contents,
                )
            )
            if (self.backend.backend_id == "local_trusted"
                    and result.manifest.get("retain_original_on_failure") is True):
                # Freeze control-produced policy before dispatch. A Worker copy
                # of domain-workspace.json cannot authorize directory deletion.
                with self._connect() as connection:
                    policy = json.loads(self._row(connection, run_id)["recovery_policy_json"])
                    policy["retain_original_on_failure"] = True
                    connection.execute("UPDATE runs SET recovery_policy_json=? WHERE run_id=? AND state='queued'",
                        (canonical_json(policy), run_id))
            payload = canonical_json(
                {
                    "schema_version": 1,
                    "name": result.manifest_name,
                    "manifest": dict(result.manifest),
                    "paths": dict(result.paths),
                    "read_paths": list(result.read_paths),
                    "patch_contract": dict(result.patch_contract),
                }
            )
            writer = getattr(self.backend, "write_domain_workspace", None)
            if not callable(writer):
                raise WorkspaceError(
                    "workspace backend cannot publish a domain workspace"
                )
            writer(run_id, payload)
        except WorkspaceProtocolError as error:
            raise RunError(str(error)) from error

    def _finalize_workspace(
        self, compiled: Any, run_id: str, *, final_submission: bool, trusted_tool_records=None
    ) -> None:
        finalizer = operation_workspace_hooks(compiled).get("workspace_finalizer")
        if finalizer is None:
            return
        workspace = self.backend.open(run_id)
        value = self.status(run_id)
        contents, descriptors = self._input_sources(value)
        try:
            raw = finalizer(
                WorkspaceFinalizationRequest(
                    operation_id=compiled.spec.operation_id,
                    workspace=workspace.root,
                    input_paths=workspace.input_paths,
                    output_limit_bytes=compiled.spec.limits.max_output_bytes,
                    final_submission=final_submission,
                    run_id=run_id,
                    trusted_tool_records=trusted_tool_records or {},
                    output_schema_id=operation_primary_output(compiled).schema_id,
                    binding_descriptors=descriptors,
                    input_contents=contents,
                    sealed_material_resolver=lambda alias, **options: self.sealed_material_record(value, alias, **options),
                )
            )
            writer = getattr(self.backend, "write_primary_output", None)
            if not callable(writer):
                raise WorkspaceError(
                    "workspace backend cannot publish a finalized output"
                )
            writer(run_id, raw)
        except TimeoutError as error:
            raise RunCheckerError("workspace finalization timed out", category="tool_timeout") from error
        except WorkspaceProtocolError as error:
            # The finalizer deliberately reports a correctable workspace error.
            # Keep its reason even when it has no per-field diagnostic details.
            details = error.details or ({"path": "$.files", "message": str(error), "type": "value_error"},)
            raise RunOutputError(str(error), details=tuple(contract_diagnostic(
                "output_invalid", phase="output_payload", affected_action="submit", repairable=True,
                path=item.get("path", "$.files"), message=item.get("message", str(error))[:512],
                error_type=item.get("type", "value_error"), rule_id=item.get("rule_id")) for item in details)) from error

    def _accept_candidate(self, run_id: str, digest: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, run_id)
            if row["state"] != "running":
                connection.execute("ROLLBACK")
                raise RunStateConflict("Run is no longer running")
            compiled = self._compiled(self.status(run_id))
            if tool_evidence_ports(compiled):
                snapshot = self.backend.seal(run_id, max_files=compiled.spec.limits.max_files, max_bytes=compiled.spec.limits.max_output_bytes, expected_digest=digest)
                expected = self._evidence_snapshot(run_id)
                from .tool_evidence import scientific_evidence_projection
                expected = scientific_evidence_projection(expected)
                if (snapshot.root / 'tool-evidence.json').read_bytes() != expected:
                    raise RunCheckerError('tool evidence snapshot changed before acceptance', category='integrity_failure')
            accepted = row["accepted_candidate_digest"]
            if accepted is not None and str(accepted) != digest:
                connection.execute("ROLLBACK")
                raise RunStateConflict("Run already accepted a different candidate")
            connection.execute(
                """
                UPDATE runs SET accepted_candidate_digest = ?
                WHERE run_id = ? AND accepted_candidate_digest IS NULL
                """,
                (digest, run_id),
            )
            connection.execute("COMMIT")

    def _register_candidate(
        self,
        value: RunStatus,
        validated: ValidatedRunOutput,
        candidate_digest: str,
    ) -> ArtifactRef:
        compiled = self._compiled(value)
        manifest = self._evidence_manifest(value)
        extra_parents = (manifest,) if manifest is not None else ()
        return self.artifacts.register(
            validated.content,
            ArtifactRegistration(
                kind=validated.kind,
                schema_id=validated.schema_id,
                payload_schema_version=validated.payload_schema_version,
                media_type=validated.media_type,
                creator=ActorRef(actor_id=f"{value.operation_id[:96]}.{value.operation_digest[:12]}", actor_type="agent_worker"),
                parent_refs=tuple(item.artifact_ref for item in value.inputs) + extra_parents,
                labels={
                    "operation_id": value.operation_id,
                    "operation_version": value.operation_version,
                    "operation_digest": value.operation_digest,
                    "operation_output_port": validated.output_port,
                    "operation_consequence": compiled.spec.consequence,
                    **(
                        {"scientific_claim_admissible": "false"}
                        if (
                            compiled.spec.consequence == "explore"
                            or compiled.spec.catalog_scope == "internal"
                        )
                        else {}
                    ),
                },
                confidentiality="run_private",
            ),
            idempotency_key=f"run:{value.run_id}:candidate:{candidate_digest}",
        ).ref

    def _complete(
        self,
        run_id: str,
        candidate_digest: str,
        output: ArtifactRef,
        signal: SchedulerSignal,
    ) -> RunCompletionReceipt:
        completed_at = timestamp()
        with self._connect() as connection:
            self._attach_scheduler(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, run_id)
            value = self._status_from_row(row)
            if value.state == "completed":
                if value.output_ref != output or value.completion_receipt is None:
                    connection.execute("ROLLBACK")
                    raise RunStateConflict("Run completion receipt is inconsistent")
                connection.execute("ROLLBACK")
                return value.completion_receipt
            if value.state != "running" or value.accepted_candidate_digest != candidate_digest:
                connection.execute("ROLLBACK")
                raise RunStateConflict("Run is not completing its accepted candidate")
            anchors = tuple(
                sorted(
                    {anchor for item in value.inputs for anchor in item.current_anchors},
                    key=lambda item: (item.kind, item.logical_name),
                )
            )
            head_advance = (
                "stale_rejected"
                if anchors and not self.current.run_is_current(connection, value)
                else "not_requested"
            )
            receipt = RunCompletionReceipt(
                candidate_digest=candidate_digest,
                output_ref=output,
                head_advance=head_advance,
                current_anchors=anchors,
                completed_at=completed_at,
            )
            self.scheduler_bindings.bind_in_connection(
                connection,
                instance=value.instance_id,
                namespace="artifact",
                name=value.output_binding_name,
                logical_name=value.output_logical_name,
                revision=value.output_revision,
                object_id=output.artifact_id,
                request_fingerprint=value.output_binding_fingerprint,
                attached=True,
            )
            evidence = [(r['alias'], ArtifactRef.model_validate(r['artifact_ref'])) for r in self.tool_evidence(run_id)]
            evidence += [('recovery_manifest', ref) for ref in self.artifacts.catalog(output).parent_refs if ref.schema_id == 'scidiscovery.tool-evidence-manifest.v1' and self.artifacts.catalog(ref).labels.get('tool_producer_run') == run_id]
            for alias, ref in evidence:
                self._bind_evidence_output(connection, value, alias, ref)
            cursor = connection.execute(
                """
                UPDATE runs SET state = 'completed', output_ref_json = ?,
                    signal_json = ?, completion_receipt_json = ?, completed_at = ?,
                    last_activity_at = ?
                WHERE run_id = ? AND state = 'running'
                  AND accepted_candidate_digest = ?
                """,
                (
                    output.canonical_json(),
                    signal.canonical_json(),
                    completion_receipt_json(receipt),
                    completed_at,
                    completed_at,
                    run_id,
                    candidate_digest,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise RunStateConflict("Run completion compare-and-set failed")
            from .run_attention import record, issues
            for _, issue in issues(connection, run_id=run_id):
                if issue['scope'].startswith('submission:'):
                    record(self, connection, run_id, scope=issue['scope'], code=None, now=completed_at)
            from .run_attention import supersede_submission_ancestors
            supersede_submission_ancestors(self, connection, row, now=completed_at)
            connection.execute("COMMIT")
        from .presentation_summaries import enqueue
        enqueue(self, value, output)
        return receipt

    def _require_running(self, run_id: str) -> RunStatus:
        value = self.status(run_id)
        if value.state != "running":
            raise RunStateConflict(f"Run is not running: {value.state}")
        if expired(value.deadline_at):
            raise RunStateConflict("Run deadline expired; record failure explicitly")
        return value

    def _compiled(self, value: RunStatus) -> Any:
        try:
            compiled = self.operation_catalog.recorded_operation(value.operation_id, version=value.operation_version, digest=value.operation_digest)
        except KeyError as error:
            raise RunContractUnavailable("Run operation is no longer installed") from error
        return compiled

    def validate_resume(
        self,
        source_run_id: str,
        *,
        operation_digest: str,
        input_refs: tuple[ArtifactRef, ...],
        max_attempts: int,
        scheduler_max_attempts: int | None = None,
        check_attempts: bool = True,
    ) -> str:
        """Validate the exact recovery source used by preflight and schedule."""

        with self._connect() as connection:
            return self._validate_resume(
                connection,
                self._status_from_row(self._row(connection, source_run_id)),
                operation_digest,
                input_refs,
                max_attempts,
                scheduler_max_attempts=scheduler_max_attempts, check_attempts=check_attempts,
            )

    def recovery_available(self, value: RunStatus) -> bool:
        """Report whether this failed Run remains a valid recovery source."""

        if value.state != "failed" or value.recovery_draft is None:
            return False
        try:
            compiled = self._compiled(value)
            assert compiled.spec.limits is not None
            if (
                value.backend_id != self.backend.backend_id
                or value.backend_version != self.backend.backend_version
                or value.backend_capabilities != self.backend.capabilities
                or not self.backend.supports_operation(compiled)
            ):
                return False
            self.validate_resume(
                value.run_id,
                operation_digest=compiled.digest,
                input_refs=tuple(item.artifact_ref for item in value.inputs),
                max_attempts=compiled.spec.limits.max_attempts,
            )
        except (RunError, KeyError, ValueError):
            return False
        return True

    def _validate_resume(
        self,
        connection: sqlite3.Connection,
        source: RunStatus,
        operation_digest: str,
        input_refs: tuple[ArtifactRef, ...],
        max_attempts: int,
        *,
        scheduler_max_attempts: int | None = None,
        check_attempts: bool = True,
    ) -> str:
        if (
            source.backend_id != self.backend.backend_id
            or source.backend_version != self.backend.backend_version
            or source.backend_capabilities != self.backend.capabilities
        ):
            raise RunStateConflict("recovery backend identity changed")
        digest = self._recovery_digest(source, operation_digest, input_refs)
        self._verify_draft(source, digest)
        if check_attempts:
            self._check_recovery_attempts(connection, source, max_attempts, scheduler_max_attempts)
        return digest

    def validate_draft_source(self, source_run_id: str, *, compiled: Any,
                              instance_id: str, scheduler_max_attempts: int | None = None,
                              check_attempts: bool = True) -> str:
        with self._connect() as connection:
            return self._validate_draft_source(connection,
                self._status_from_row(self._row(connection, source_run_id)),
                compiled=compiled, instance_id=instance_id,
                scheduler_max_attempts=scheduler_max_attempts, check_attempts=check_attempts)

    def _validate_draft_source(self, connection: sqlite3.Connection, source: RunStatus,
                               *, compiled: Any, instance_id: str, scheduler_max_attempts: int | None = None,
                              check_attempts: bool = True) -> str:
        if source.instance_id != instance_id:
            raise RunStateConflict("draft source belongs to another instance")
        if source.operation_id != compiled.spec.operation_id:
            raise RunStateConflict("draft source Operation differs")
        if (source.backend_id != self.backend.backend_id
                or source.backend_version != self.backend.backend_version
                or source.backend_capabilities != self.backend.capabilities
                or not self.backend.supports_operation(compiled)):
            raise RunStateConflict("recovery backend identity changed or unsupported")
        if source.state != "failed" or not source.recovery_draft:
            raise RunStateConflict("source Run has no recovery draft")
        digest = source.recovery_draft.get("draft_digest")
        if not isinstance(digest, str):
            raise RunStateConflict("source Run recovery is incomplete")
        self._verify_draft(source, digest)
        if check_attempts:
            self._check_recovery_attempts(connection, source, compiled.spec.limits.max_attempts, scheduler_max_attempts)
        return digest

    def _verify_draft(self, source: RunStatus, digest: str) -> Any:
        manifest = source.recovery_draft
        if (not manifest or manifest.get("source_run_id") != source.run_id
                or manifest.get("source_request_digest") != source.request_digest
                or manifest.get("operation_digest") != source.operation_digest
                or manifest.get("backend") != source.backend_id
                or manifest.get("backend_version") != source.backend_version):
            raise RunStateConflict("recovery source binding differs")
        policy = source.recovery_policy
        if policy is None:
            policy = self._recovery_policy(self._compiled(source))
        verifier = getattr(self.backend, "verify_recovery", None)
        if not callable(verifier):
            raise RunStateConflict("backend cannot verify recovery material")
        try:
            draft = verifier(digest, max_files=max(policy["max_files"], policy["snapshot_max_files"]),
                max_bytes=max(policy["max_output_bytes"], policy["snapshot_max_bytes"]))
        except WorkspaceError as error:
            raise RunStateConflict("recovery material failed verification") from error
        actual = [(item.relative_path, item.size_bytes, item.sha256) for item in draft.files]
        declared = [(item["relative_path"], item["size_bytes"], item["sha256"])
                    for item in manifest.get("files", ())]
        if actual != declared:
            raise RunStateConflict("recovery file manifest differs")
        return draft

    @staticmethod
    def _validate_attempt_limit(value: int | None) -> None:
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError("max_attempts must be a positive integer")

    def _recovery_attempt_budget(self, connection: sqlite3.Connection,
                                 source: RunStatus, max_attempts: int,
                                 scheduler_max_attempts: int | None = None) -> dict[str, int]:
        self._validate_attempt_limit(scheduler_max_attempts)
        root_run_id = self._recovery_root_id(connection, source.run_id)
        override = scheduler_max_attempts
        if override is None:
            override = (source.recovery_policy or {}).get("scheduler_max_attempts")
        if override is not None:
            self._validate_attempt_limit(override)
            limit = override
        else:
            root = self._status_from_row(self._row(connection, root_run_id))
            policy = root.recovery_policy
            if policy is None:
                try:
                    policy = self._recovery_policy(self._compiled(root))
                except RunContractUnavailable as error:
                    raise RunStateConflict("original recovery attempt limit is unavailable") from error
            limit = min(max_attempts, policy["max_attempts"])
        attempts = connection.execute(
            """WITH RECURSIVE recovery_chain(run_id) AS (
                SELECT run_id FROM runs WHERE run_id = ?
                UNION SELECT child.run_id FROM runs AS child
                JOIN recovery_chain AS parent ON child.resume_from_run_id = parent.run_id
                    OR child.draft_from_run_id = parent.run_id
            ) SELECT COUNT(*) FROM recovery_chain""", (root_run_id,)).fetchone()
        return {"used": int(attempts[0]), "limit": limit}

    def _check_recovery_attempts(self, connection: sqlite3.Connection,
                                 source: RunStatus, max_attempts: int,
                                 scheduler_max_attempts: int | None = None) -> None:
        budget = self._recovery_attempt_budget(connection, source, max_attempts, scheduler_max_attempts)
        if budget["used"] >= budget["limit"]:
            raise RunAttemptLimit(budget["used"], budget["limit"])

    def _recovery_gate_status(
        self, value: RunStatus
    ) -> tuple[dict[str, Any], bool, Any | None]:
        preserved = False
        draft = None
        try:
            if value.recovery_draft and isinstance(value.recovery_draft.get("draft_digest"), str):
                draft = self._verify_draft(value, value.recovery_draft["draft_digest"])
                preserved = True
        except (RunError, KeyError, ValueError, TypeError):
            pass
        draft_available = False
        if preserved:
            try:
                compiled = self.operation_catalog.operation(value.operation_id)
                self.validate_draft_source(value.run_id, compiled=compiled,
                                           instance_id=value.instance_id)
                draft_available = True
            except (RunError, KeyError, ValueError):
                pass
        result = {"delivery_preserved": preserved,
                "resume_available": self.recovery_available(value),
                "draft_available": draft_available,
                "recovery_pending": value.state == "failed" and (
                    not preserved or (value.recovery_draft or {}).get("recovery_pending", False))}
        return result, preserved, draft

    def compact_recovery_status(self, value: RunStatus) -> dict[str, Any]:
        """Project only recovery gates and a controlled stored reason code."""
        result, _, _ = self._recovery_gate_status(value)
        recovery = value.recovery_draft or {}
        if "original_retained" in recovery:
            result["original_retained"] = recovery["original_retained"]
        code = recovery.get("code")
        result["reason_code"] = (
            code if code in _COMPACT_RECOVERY_REASON_CODES
            else "other" if code is not None
            else None
        )
        return result

    def recovery_status(self, value: RunStatus) -> dict[str, Any]:
        result, preserved, draft = self._recovery_gate_status(value)
        evidence = self.recovery_evidence_status(value)
        if evidence is not None:
            result["tool_evidence"] = evidence
        if value.recovery_draft and "original_retained" in value.recovery_draft:
            result["original_retained"] = value.recovery_draft["original_retained"]
        if preserved and draft is not None and value.recovery_draft.get("original_retained") is True:
            entry = next((f for f in draft.files if f.relative_path == "analysis-recovery.json"), None)
            if entry and entry.size_bytes <= 64 * 1024:
                try:
                    coverage = json.loads((draft.root / entry.relative_path).read_bytes())
                    if coverage.get("format") == "analysis-work-v1":
                        result["coverage"] = {key: coverage[key] for key in
                            ("format", "scope", "saved_count", "saved_bytes", "saved_by_scope",
                             "omitted_count", "omitted", "normalized_count", "writers_stopped",
                             "original_retained", "complete") if key in coverage}
                except (OSError, ValueError, AttributeError):
                    result["coverage"] = {"status": "unavailable"}
        try:
            compiled = self.operation_catalog.operation(value.operation_id)
            with self._connect() as connection:
                result["attempt_budget"] = self._recovery_attempt_budget(
                    connection, value, compiled.spec.limits.max_attempts)
        except (RunError, KeyError, ValueError):
            pass
        return result

    def _recovery_root_id(
        self, connection: sqlite3.Connection, source_run_id: str
    ) -> str:
        current = source_run_id
        seen: set[str] = set()
        while True:
            if current in seen:
                raise RunStateConflict("recovery chain is cyclic")
            seen.add(current)
            row = self._row(connection, current)
            if row["resume_from_run_id"] and row["draft_from_run_id"]:
                raise RunStateConflict("recovery chain has multiple parents")
            parent = row["resume_from_run_id"] or row["draft_from_run_id"]
            if parent is None:
                return current
            current = str(parent)

    def _recovery_digest(
        self,
        source: RunStatus,
        operation_digest: str,
        input_refs: tuple[ArtifactRef, ...],
    ) -> str:
        if source.state != "failed" or source.recovery_draft is None:
            raise RunStateConflict("source Run has no recovery draft")
        if not self.operation_catalog.can_recover(source.operation_id, source.operation_digest, operation_digest):
            raise RunStateConflict("recovery Operation digest differs")
        if tuple(item.artifact_ref for item in source.inputs) != input_refs:
            raise RunStateConflict("recovery inputs differ")
        digest = source.recovery_draft.get("draft_digest")
        if not isinstance(digest, str):
            raise RunStateConflict("recovery draft digest is invalid")
        return digest

    @staticmethod
    def _row(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound("Run does not exist")
        return row

    def _attach_scheduler(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "ATTACH DATABASE ? AS scheduler_control",
            (str(self.scheduler_database_path),),
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
            if mode is None or str(mode[0]).lower() != "delete":
                raise RunError(
                    "Run database cannot provide atomic attached commits"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_tool_evidence (run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, evidence_key TEXT NOT NULL, record_json BLOB NOT NULL, alias TEXT NOT NULL, PRIMARY KEY(run_id, ordinal), UNIQUE(run_id,evidence_key));
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    operation_version TEXT NOT NULL,
                    operation_digest TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    inputs_json BLOB NOT NULL,
                    backend_id TEXT NOT NULL,
                    backend_version TEXT NOT NULL,
                    backend_capabilities_json BLOB NOT NULL,
                    output_binding_name TEXT NOT NULL,
                    output_logical_name TEXT NOT NULL,
                    output_revision INTEGER NOT NULL,
                    output_binding_fingerprint TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    resume_from_run_id TEXT,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed')),
                    accepted_candidate_digest TEXT,
                    output_ref_json BLOB,
                    signal_json BLOB,
                    completion_receipt_json BLOB,
                    reason TEXT,
                    recovery_candidate_digest TEXT,
                    recovery_draft_json BLOB,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    deadline_at TEXT NOT NULL,
                    completed_at TEXT,
                    last_activity_at TEXT
                );
                DROP INDEX IF EXISTS one_active_run_per_operation;
                CREATE INDEX IF NOT EXISTS active_run_capacity
                    ON runs(state) WHERE state IN ('queued','running');
                CREATE TABLE IF NOT EXISTS run_activity (
                    run_id TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS run_activity_sequence (
                    slot INTEGER PRIMARY KEY CHECK(slot = 1),
                    high_water INTEGER NOT NULL CHECK(high_water >= 0)
                );
                """
            )
            connection.executescript(WORKER_CONNECTION_SCHEMA)
            connection.executescript(WORKER_PARTICIPANT_SCHEMA)
            from .run_attention import NOTICE_SCHEMA
            connection.executescript(NOTICE_SCHEMA)
            for table, columns in {
                "runs": {"workspace_ready": "INTEGER NOT NULL DEFAULT 1", "draft_from_run_id": "TEXT", "recovery_policy_json": "BLOB",
                         **{name: value[0] for name, value in EXECUTION_SETTINGS_COLUMNS["runs"].items()},
                         **{name: value[0] for name, value in RUN_READ_COLUMNS.items()}},
                "run_activity": {"diagnostic_json": "BLOB"},
            }.items():
                existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns.items():
                    if name not in existing:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                        if table == 'runs' and name == 'workspace_ready':
                            # Legacy queued records have no durable completion
                            # proof. Rebuild before dispatch; never reset opened work.
                            connection.execute("UPDATE runs SET workspace_ready=0 WHERE state='queued'")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS run_attention_notice
                ON run_activity(run_id) WHERE activity='attention_notice'""")
            connection.execute("""CREATE INDEX IF NOT EXISTS run_attention_open_notice
                ON run_activity(run_id) WHERE activity='attention_notice'
                    AND json_extract(CAST(diagnostic_json AS TEXT),'$.state')='attention_required'""")
            connection.execute("""CREATE INDEX IF NOT EXISTS run_attention_scope
                ON run_activity(run_id,json_extract(CAST(diagnostic_json AS TEXT),'$.scope'))
                WHERE activity='attention_state'""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS run_approval_wait
                ON run_activity(run_id,json_extract(CAST(diagnostic_json AS TEXT),'$.approval_id'))
                WHERE activity='approval_wait_started'""")
            connection.execute("""CREATE INDEX IF NOT EXISTS run_reference_budget_scope
                ON run_activity(json_extract(CAST(diagnostic_json AS TEXT),'$.budget_scope'))
                WHERE activity IN ('reference_read_reserved','reference_read_settled',
                                   'reference_material_reserved','reference_io_reserved')""")
            connection.execute(
                "INSERT INTO run_activity_sequence(slot,high_water) "
                "VALUES (1, (SELECT COALESCE(MAX(rowid), 0) FROM run_activity)) "
                "ON CONFLICT(slot) DO UPDATE SET high_water = MAX(high_water, excluded.high_water)"
            )
            from .run_attention import migrate_running, migrate_notices
            migrate_notices(connection)
            migrate_running(self, connection)
            from .run_attention import refresh_notice
            missing = connection.execute("SELECT DISTINCT a.run_id FROM run_activity a "
                "WHERE a.activity='attention_state' AND NOT EXISTS (SELECT 1 FROM run_attention_notices n "
                "WHERE n.run_id=a.run_id)").fetchall()
            for row in missing:
                refresh_notice(self, connection, row['run_id'], now=timestamp())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _revision_workspace_mode(
    direct_revision: object | None, workspace_hooks: object
) -> str | None:
    if direct_revision is None:
        return None
    return (
        "domain_workspace"
        if {
            "workspace_materializer",
            "workspace_finalizer",
        }.issubset(workspace_hooks)
        else "result_copy"
    )


__all__ = [
    "RunError",
    "RunInputBinding",
    "RunNotFound",
    "RunService",
    "RunSlotBusy",
    "RunStateConflict",
    "RunStatus",
]
