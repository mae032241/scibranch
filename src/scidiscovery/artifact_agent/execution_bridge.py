"""Deterministic bridge between generic execution state and local adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .schema.approval import CompiledApprovalIdentity
from .schema.execution import ExecutionAdmission, LocalFileDescriptor
from .service.executions import ExecutionService, ExecutionServiceError


@dataclass(frozen=True)
class AdapterCapability:
    """One public immutable capability document supplied by an adapter."""

    key: str
    kind: str
    schema_id: str
    payload_schema_version: int
    media_type: str
    content: bytes
    public_summary: Mapping[str, object]


class ExecutionAdapter(Protocol):
    def capabilities(self) -> tuple[AdapterCapability, ...]: ...

    def prepare(
        self,
        payload: LocalFileDescriptor,
        *,
        preparation_profile: str,
        exchange_directory: Path,
    ) -> LocalFileDescriptor: ...

    def submit(self, submission: LocalFileDescriptor) -> tuple[str, str]:
        """Idempotently submit this exact frozen descriptor."""
        ...

    def lookup_submission(
        self, submission: LocalFileDescriptor
    ) -> tuple[str, str] | None:
        """Return an existing exact submission; None does not retire an in-flight submit."""
        ...

    def status(self, external_run_id: str) -> str: ...

    def cancel(self, external_run_id: str) -> str: ...

    def collect(self, external_run_id: str) -> tuple[LocalFileDescriptor, ...]: ...


class ExecutionBridge:
    """Advance execution state only from one configured adapter's responses."""

    def __init__(
        self,
        executions: ExecutionService,
        *,
        adapters: Mapping[str, ExecutionAdapter],
        lifecycle_settings=None,
    ) -> None:
        self.executions = executions
        self.adapters = dict(adapters)
        from ..agent_execution_settings import LifecycleSettings
        self.lifecycle_settings = lifecycle_settings or LifecycleSettings()
        from .service.execution_submission import ExecutionSubmission
        self.submissions = ExecutionSubmission(self)
        missing_lookup = next(
            (
                name
                for name, adapter in self.adapters.items()
                if not callable(getattr(adapter, "lookup_submission", None))
            ),
            None,
        )
        if missing_lookup is not None:
            raise ExecutionServiceError(
                f"execution adapter {missing_lookup} has no authoritative "
                "submission lookup"
            )

    def has_adapter(self, executor: str) -> bool:
        return executor in self.adapters

    def start(
        self,
        *,
        execution_id: str,
        approval_id: str | None,
        compiled_identity: CompiledApprovalIdentity,
        allow_policy_authorization: bool = False,
        budget_subject_schemas: tuple[str, ...] = (),
        before_submit=None,
    ) -> None:
        request = self.executions.request(execution_id)
        adapter = self._adapter(request.executor)
        current = self.executions.status(execution_id)
        if current.external_run_id is not None:
            return
        # Recover a possibly accepted submission before applying a changed
        # policy. Never submit a second solver to resolve an unknown response.
        submission = self.executions.prepared_submission(execution_id)
        if submission is not None:
            recovered = adapter.lookup_submission(submission)
            if recovered is not None:
                if before_submit is not None:
                    before_submit()
                self._record_started(execution_id, *recovered)
                return
        protocol_check = getattr(adapter, "validate_submission_protocol", None)
        if callable(protocol_check):
            protocol_check()  # Before authorization, budget reservation or prepare.
        admission = self.validate_request(
            executor=request.executor,
            preparation_profile=request.preparation_profile,
            payload=self.executions.artifacts.read(request.payload_ref), allow_denial=True,
        )
        if admission is not None:
            owner = (self.executions.budget_owner(execution_id) or
                self.executions.scientific_budget_owner(request.payload_ref, budget_subject_schemas))
            admission = self.executions.budget_admission(executor=request.executor,
                admission=admission.model_copy(update={"budget_key": owner}), execution_id=execution_id, allow_denial=True)
        if admission is not None and admission.outcome != "policy":
            self.executions.defer_policy(execution_id=execution_id, admission=admission)
            if admission.outcome == "deny":
                raise ExecutionServiceError("current execution policy denied request: " + admission.reason)
        if admission is not None and admission.outcome == "policy":
            if not allow_policy_authorization:
                raise ExecutionServiceError("compiled execution contract does not permit policy authorization")
            payload = self.executions.authorize_policy(execution_id=execution_id,
                admission=admission, compiled_identity=compiled_identity)
        else:
            if approval_id is None:
                raise ExecutionAuthorizationRequired("current policy requires human execution approval")
            payload = self.executions.authorize(execution_id=execution_id,
                approval_id=approval_id, compiled_identity=compiled_identity)
            if admission is not None:
                self.executions.reserve_human_budget(execution_id, admission)
        if submission is None:
            prepare = getattr(adapter, "prepare_with_artifacts", None)
            options = {"preparation_profile": request.preparation_profile,
                       "exchange_directory": Path(payload.local_path).parent}
            try:
                submission = (prepare(payload, artifacts=self.executions.artifacts, **options)
                    if prepare is not None else adapter.prepare(payload, **options))
                self.executions.record_prepared_submission(execution_id, submission,
                    policy_digest=admission.policy_digest if admission is not None else None)
            except Exception:
                self.executions.release_unsubmitted_budget(execution_id)
                raise
        recovered = adapter.lookup_submission(submission)
        if recovered is None:
            if before_submit is not None:
                before_submit()
            from ..plugin_runtime.submission import SubmissionNotCreated
            try:
                if admission is None:
                    external_run_id, external_state = adapter.submit(submission)
                else:
                    external_run_id, external_state = adapter.submit(submission, authorization=admission)
            except SubmissionNotCreated as error:
                if error.submission_sha256 != submission.sha256:
                    raise ExecutionServiceError("Submission rejection identity differs; preserve its reservation") from error
                self.executions.release_rejected_submission(execution_id, submission_sha256=submission.sha256)
                raise
        else:
            if before_submit is not None:
                before_submit()
            external_run_id, external_state = recovered
        self._record_started(execution_id, external_run_id, external_state)

    def abandon(self, execution_id: str) -> None:
        with self.submissions.idle(execution_id, allow_unknown=True):
            self._abandon(execution_id)

    def _abandon(self, execution_id: str) -> None:
        if not self._retire_unsubmitted(execution_id):
            raise ExecutionServiceError("This execution was submitted; use status or cancel instead of abandon.")

    def _retire_unsubmitted(self, execution_id: str) -> bool:
        current = self.executions.status(execution_id)
        if current.state == "abandoned":
            return True
        if current.external_run_id is not None:
            return False
        descriptor = self.executions.prepared_submission(execution_id)
        if descriptor is not None:
            retire = getattr(self._adapter(current.executor), "retire_submission", None)
            if callable(retire) and self.submissions.can_retire(execution_id):
                found = retire(descriptor)
                if found is None:
                    self.executions.abandon(execution_id, absence_confirmed=True)
                    return True
                self._record_started(execution_id, *found)
                return False
            found = self._adapter(current.executor).lookup_submission(descriptor)
            if found is not None:
                self._record_started(execution_id, *found)
                return False
            raise ExecutionServiceError("This exact prepared submission is not yet visible; an earlier submit may still be processing. Abandonment was not confirmed and its budget remains reserved. Recover the same execution after service recovery, then cancel the confirmed job; do not create a replacement.")
        self.executions.abandon(execution_id)
        return True

    def _record_started(self, execution_id: str, external_run_id: str, external_state: str) -> None:
        self.executions.record_submission(
            execution_id=execution_id,
            external_run_id=external_run_id,
        )
        if external_state != "accepted":
            self.executions.record_status(
                execution_id=execution_id,
                external_run_id=external_run_id,
                state=external_state,
            )
        if external_state in {"succeeded", "failed", "cancelled"} and self.executions.budget_needs_settlement(execution_id):
            self.sync(execution_id=execution_id)

    def validate_request(
        self,
        *,
        executor: str,
        preparation_profile: str,
        payload: bytes | None = None,
        allow_denial: bool = False,
    ) -> ExecutionAdmission | None:
        adapter = self._adapter(executor)
        checker = getattr(adapter, "supports_preparation_profile", None)
        if checker is not None and checker(preparation_profile) is not True:
            raise ExecutionServiceError(
                f"execution adapter {executor} does not support preparation profile "
                f"{preparation_profile}"
            )
        payload_checker = getattr(adapter, "validate_preparation_payload", None)
        if payload is not None and payload_checker is not None:
            from ..operation_contract import DiagnosticError
            try:
                payload_checker(payload, preparation_profile=preparation_profile)
            except DiagnosticError:
                # A service configuration failure is not a scientific payload defect.
                raise
            except (TypeError, ValueError, RuntimeError) as error:
                raise ExecutionServiceError(
                    f"execution payload is invalid for preparation profile "
                    f"{preparation_profile}: {error}"
                ) from error
        judge = getattr(adapter, "execution_admission", None)
        if payload is not None and judge is not None:
            admission = judge(payload, preparation_profile=preparation_profile)
            if not isinstance(admission, ExecutionAdmission):
                raise ExecutionServiceError("execution adapter returned an invalid policy judgment")
            if admission.outcome == "deny" and not allow_denial:
                raise ExecutionServiceError("execution policy denied request: " + admission.reason)
            return admission
        return None

    def capabilities(self, *, executor: str) -> tuple[AdapterCapability, ...]:
        adapter = self._adapter(executor)
        reader = getattr(adapter, "capabilities", None)
        if reader is None:
            raise ExecutionServiceError(
                f"execution adapter {executor} does not expose capabilities"
            )
        values = tuple(reader())
        if not values:
            raise ExecutionServiceError(
                f"execution adapter {executor} returned no capabilities"
            )
        keys = tuple(item.key for item in values)
        if len(keys) != len(set(keys)):
            raise ExecutionServiceError(
                f"execution adapter {executor} returned duplicate capabilities"
            )
        if any(
            not item.key
            or not item.schema_id
            or type(item.content) is not bytes
            or len(item.content) > 1024 * 1024
            for item in values
        ):
            raise ExecutionServiceError(
                f"execution adapter {executor} returned an invalid capability"
            )
        return values

    def sync(self, *, execution_id: str, diagnostic_scope: str | None = None) -> dict | None:
        current = self.executions.status(execution_id)
        if current.external_run_id is None:
            raise ExecutionServiceError("execution has no submitted external run")
        observation = self.executions.observation(execution_id)
        from datetime import datetime, timezone
        try:
            adapter = self._adapter(current.executor)
            reader = getattr(adapter, "status_details", None)
            details = (reader(current.external_run_id) if callable(reader)
                       else {"state": adapter.status(current.external_run_id)})
            external_state = details["state"]
            if isinstance(details.get("progress"), dict):
                observation["progress"] = details["progress"]
                observation["progress_observed_at"] = datetime.now(timezone.utc).isoformat()
            if current.state not in {"succeeded", "failed", "cancelled", "collected"}:
                self.executions.record_status(execution_id=execution_id,
                    external_run_id=current.external_run_id, state=external_state)
            if external_state in {"succeeded", "failed", "cancelled"}:
                self.executions.settle_budget(execution_id, details.get("consumed_budget") or {}, finalize_missing=True)
            observation["solver_state"] = external_state
            observation["status_observed_at"] = datetime.now(timezone.utc).isoformat()
            observation.pop("observation_error", None)
            self.executions.record_observation_result(execution_id, succeeded=True)
            observation.pop("terminal_query_failures", None)
        except Exception as error:
            if current.state in {"succeeded", "failed", "cancelled", "collected"}:
                # A known terminal job must not poll forever for measurements
                # that may have disappeared with its workspace. Never refund
                # missing dimensions or mislabel estimates as observations.
                failures = self.executions.record_observation_result(execution_id, succeeded=False,
                    previous_failures=observation.get("terminal_query_failures", 0))
                observation["terminal_query_failures"] = failures
                if failures >= self.lifecycle_settings.terminal_observation_failure_limit:
                    self.executions.settle_budget(execution_id, {}, finalize_missing=True)
            from .service.engineering_diagnostics import EngineeringDiagnostics, exception_facts
            observation["observation_error"] = (EngineeringDiagnostics(
                self.executions.database_path.parent.parent / "engineering-diagnostics").capture(
                    error, scope=diagnostic_scope, layer="execution_observation", action="sync") if diagnostic_scope
                else exception_facts(error, layer="execution_observation", action="sync"))
        self.executions.save_observation(execution_id, observation)
        return observation.get("progress")

    def cancel(self, *, execution_id: str) -> None:
        with self.submissions.idle(execution_id, allow_unknown=True):
            self._cancel(execution_id=execution_id)

    def _cancel(self, *, execution_id: str) -> None:
        current = self.executions.status(execution_id)
        if current.state in {"created", "authorized", "abandoned"}:
            if self._retire_unsubmitted(execution_id):
                return
            current = self.executions.status(execution_id)
        if current.state in {"succeeded", "failed", "cancelled", "collected"}:
            return
        if current.state not in {"submitted", "running", "cancelling"}:
            raise ExecutionServiceError("execution is not cancellable from current state")
        if current.external_run_id is None:
            raise ExecutionServiceError("execution has no submitted external run")
        adapter = self._adapter(current.executor)
        external_state = adapter.cancel(current.external_run_id)
        self.executions.record_status(
            execution_id=execution_id,
            external_run_id=current.external_run_id,
            state=external_state,
        )
        if external_state in {"succeeded", "failed", "cancelled"} and self.executions.budget_needs_settlement(execution_id):
            self.sync(execution_id=execution_id)

    def _adapter(self, executor: str) -> ExecutionAdapter:
        try:
            return self.adapters[executor]
        except KeyError as error:
            raise ExecutionServiceError(
                f"no execution adapter is configured for {executor}"
            ) from error


class ExecutionAuthorizationRequired(ExecutionServiceError):
    pass


__all__ = ["AdapterCapability", "ExecutionAdapter", "ExecutionBridge", "ExecutionAuthorizationRequired"]
