"""Small immutable records and serialization helpers for minimal Runs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from ..schema.common import canonical_json
from ..schema.refs import ArtifactRef
from ..schema.run_signal import SchedulerSignal
from ...operation_contract import DiagnosticError, contract_diagnostic


# Additive read metadata only; NULL preserves records created before this field.
RUN_READ_COLUMNS = {"decision_fields_json": ("BLOB", None)}


class RunError(RuntimeError):
    pass


class RunNotFound(RunError):
    pass


class RunStateConflict(RunError):
    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        # Internal ownership for failures after an assignment has been selected.
        # The Worker wire diagnostic never exposes this control identifier.
        self.run_id = run_id


class RunPreparationBusy(RunStateConflict, DiagnosticError):
    """An exact queued assignment exists but cannot yet be opened."""
    def __init__(self, run_id):
        message = "Workspace preparation is in progress. Retry opening this same assignment after preparation completes; do not create another task."
        RunStateConflict.__init__(self, message, run_id=run_id)
        self.details = (contract_diagnostic('run_preparation_pending', phase='protocol',
            affected_action='tool_call', repairable=True, message=message),)


class RunAttemptLimit(RunStateConflict):
    def __init__(self, used: int, limit: int) -> None:
        self.used = used
        self.limit = limit
        super().__init__(f"recovery attempt limit reached: {used} Runs used, limit {limit}; scheduler may set max_attempts for a new Run")


class RunContractUnavailable(RunStateConflict):
    """The frozen operation cannot be resolved in the installed catalog."""


class RunSlotBusy(RunError):
    pass


class RunCapacityBusy(RunSlotBusy):
    """Configured task capacity has no available slot."""


@dataclass(frozen=True, slots=True)
class CurrentAnchor:
    kind: str
    logical_name: str
    artifact_ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class RunInputBinding:
    port_name: str
    source_name: str
    artifact_name: str
    artifact_ref: ArtifactRef
    media_type: str
    exposure: str
    usage: str
    require_current: bool
    producer_run_id: str | None = None
    current_anchors: tuple[CurrentAnchor, ...] = ()


@dataclass(frozen=True, slots=True)
class RunCompletionReceipt:
    candidate_digest: str
    output_ref: ArtifactRef
    head_advance: str
    current_anchors: tuple[CurrentAnchor, ...]
    completed_at: str


@dataclass(frozen=True, slots=True)
class RunStatus:
    run_id: str
    instance_id: str
    operation_id: str
    operation_version: str
    operation_digest: str
    agent_type: str
    backend_id: str
    backend_version: str
    backend_capabilities: tuple[str, ...]
    output_binding_name: str
    output_logical_name: str
    output_revision: int
    output_binding_fingerprint: str
    state: str
    inputs: tuple[RunInputBinding, ...]
    output_ref: ArtifactRef | None
    signal: SchedulerSignal | None
    reason: str | None
    created_at: str
    started_at: str | None
    deadline_at: str
    completed_at: str | None
    last_activity_at: str | None
    accepted_candidate_digest: str | None
    completion_receipt: RunCompletionReceipt | None
    recovery_candidate_digest: str | None
    recovery_draft: dict[str, Any] | None
    request_digest: str
    workspace_ready: bool = True
    draft_from_run_id: str | None = None
    recovery_policy: dict[str, Any] | None = None
    execution_profile: dict[str, Any] | None = None
    decision_fields: tuple[str, ...] | None = None


def status_from_row(row: sqlite3.Row) -> RunStatus:
    signal = parse_stored_signal(row["signal_json"])
    return RunStatus(
        run_id=str(row["run_id"]),
        instance_id=str(row["instance_id"]),
        operation_id=str(row["operation_id"]),
        operation_version=str(row["operation_version"]),
        operation_digest=str(row["operation_digest"]),
        agent_type=str(row["agent_type"]),
        backend_id=str(row["backend_id"]),
        backend_version=str(row["backend_version"]),
        backend_capabilities=tuple(
            str(item)
            for item in json.loads(row["backend_capabilities_json"])
        ),
        output_binding_name=str(row["output_binding_name"]),
        output_logical_name=str(row["output_logical_name"]),
        output_revision=int(row["output_revision"]),
        output_binding_fingerprint=str(row["output_binding_fingerprint"]),
        state=str(row["state"]),
        inputs=parse_inputs(row["inputs_json"]),
        output_ref=optional_ref(row["output_ref_json"]),
        signal=signal,
        reason=None if row["reason"] is None else str(row["reason"]),
        created_at=str(row["created_at"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
        deadline_at=str(row["deadline_at"]),
        completed_at=None if row["completed_at"] is None else str(row["completed_at"]),
        last_activity_at=(
            None if row["last_activity_at"] is None else str(row["last_activity_at"])
        ),
        accepted_candidate_digest=(
            None
            if row["accepted_candidate_digest"] is None
            else str(row["accepted_candidate_digest"])
        ),
        completion_receipt=_completion_receipt(row["completion_receipt_json"]),
        recovery_candidate_digest=(
            None
            if row["recovery_candidate_digest"] is None
            else str(row["recovery_candidate_digest"])
        ),
        recovery_draft=(
            None
            if row["recovery_draft_json"] is None
            else _json_object(row["recovery_draft_json"], "stored recovery draft")
        ),
        execution_profile=(None if "execution_profile_json" not in row.keys() or row["execution_profile_json"] is None
                           else _json_object(row["execution_profile_json"], "stored execution profile")),
        decision_fields=(None if "decision_fields_json" not in row.keys() or row["decision_fields_json"] is None
                         else tuple(json.loads(row["decision_fields_json"]))),
        workspace_ready=bool(row["workspace_ready"]) if "workspace_ready" in row.keys() else True,
        request_digest=str(row["request_digest"]),
        draft_from_run_id=row["draft_from_run_id"],
        recovery_policy=(None if row["recovery_policy_json"] is None else
                         _json_object(row["recovery_policy_json"], "stored recovery policy")),
    )


def parse_stored_signal(raw: bytes | str | None) -> SchedulerSignal | None:
    """Fail closed when a stored signal no longer matches the current wire schema."""

    if raw is None:
        return None
    try:
        return SchedulerSignal.model_validate_json(raw, strict=True)
    except ValidationError:
        return None


def inputs_json(inputs: tuple[RunInputBinding, ...]) -> bytes:
    return canonical_json(
        [
            {
                **{
                    key: value
                    for key, value in asdict(item).items()
                    if key not in {"artifact_ref", "current_anchors"}
                },
                "artifact_ref": item.artifact_ref.model_dump(mode="json"),
                "current_anchors": [
                    {
                        "kind": anchor.kind,
                        "logical_name": anchor.logical_name,
                        "artifact_ref": anchor.artifact_ref.model_dump(mode="json"),
                    }
                    for anchor in item.current_anchors
                ],
            }
            for item in inputs
        ]
    )


def parse_inputs(raw: bytes) -> tuple[RunInputBinding, ...]:
    try:
        values = json.loads(raw)
        return tuple(
            RunInputBinding(
                port_name=str(item["port_name"]),
                source_name=str(item["source_name"]),
                artifact_name=str(item["artifact_name"]),
                artifact_ref=ArtifactRef.model_validate(item["artifact_ref"], strict=True),
                media_type=str(item["media_type"]),
                exposure=str(item["exposure"]),
                usage=str(item["usage"]),
                require_current=bool(item["require_current"]),
                producer_run_id=(
                    None
                    if item.get("producer_run_id") is None
                    else str(item["producer_run_id"])
                ),
                current_anchors=tuple(
                    CurrentAnchor(
                        kind=str(value["kind"]),
                        logical_name=str(value["logical_name"]),
                        artifact_ref=ArtifactRef.model_validate(
                            value["artifact_ref"], strict=True
                        ),
                    )
                    for value in item.get("current_anchors", ())
                ),
            )
            for item in values
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise RunError("stored Run inputs are invalid") from error


def optional_ref(raw: bytes | None) -> ArtifactRef | None:
    return None if raw is None else ArtifactRef.model_validate_json(raw, strict=True)


def completion_receipt_json(value: RunCompletionReceipt) -> bytes:
    return canonical_json(
        {
            "candidate_digest": value.candidate_digest,
            "output_ref": value.output_ref.model_dump(mode="json"),
            "head_advance": value.head_advance,
            "current_anchors": [
                {
                    "kind": item.kind,
                    "logical_name": item.logical_name,
                    "artifact_ref": item.artifact_ref.model_dump(mode="json"),
                }
                for item in value.current_anchors
            ],
            "completed_at": value.completed_at,
        }
    )


def _completion_receipt(raw: bytes | None) -> RunCompletionReceipt | None:
    if raw is None:
        return None
    value = _json_object(raw, "stored completion receipt")
    try:
        return RunCompletionReceipt(
            candidate_digest=str(value["candidate_digest"]),
            output_ref=ArtifactRef.model_validate(value["output_ref"], strict=True),
            head_advance=str(value["head_advance"]),
            current_anchors=tuple(
                CurrentAnchor(
                    kind=str(item["kind"]),
                    logical_name=str(item["logical_name"]),
                    artifact_ref=ArtifactRef.model_validate(
                        item["artifact_ref"], strict=True
                    ),
                )
                for item in value.get("current_anchors", ())
            ),
            completed_at=str(value["completed_at"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise RunError("stored completion receipt is invalid") from error


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise RunError(f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise RunError(f"{label} is invalid")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def future(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    return (parsed + timedelta(seconds=seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def expired(value: str) -> bool:
    return datetime.now(timezone.utc) >= datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


__all__ = [
    "RunError",
    "CurrentAnchor",
    "RunCompletionReceipt",
    "RunInputBinding",
    "RunNotFound",
    "RunSlotBusy",
    "RunStateConflict",
    "RunStatus",
    "expired",
    "completion_receipt_json",
    "future",
    "inputs_json",
    "optional_ref",
    "status_from_row",
    "timestamp",
]
