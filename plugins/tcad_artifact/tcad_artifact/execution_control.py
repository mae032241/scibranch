"""Execution-only TCAD dispatcher and MCP surface."""

from __future__ import annotations

from scidiscovery.plugin_runtime.submission import SubmissionNotCreated

from scidiscovery.operation_contract import declared_violation

import hashlib
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .execution_policy import AgentExecutionPolicy, RunnerPolicy, DebugPolicy, ExecutionPolicySnapshot
from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission


def _validate_relative_path(value: str) -> str:
    if value.startswith("/") or "\\" in value or any(
        part in {"", ".", ".."} for part in value.split("/")
    ):
        raise ValueError("unsafe relative path")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


SolverKind = Literal["sprocess", "sdevice", "shell_runner", "deterministic_tool"]
ExecutionPurpose = Literal["production", "development_debug"]
_PUBLIC_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_PUBLIC_RELEASE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,255}$")
_CREDENTIAL_LABEL_RE = re.compile(
    r"password|passwd|secret|token|credential|license[ _-]?file|snpslmd",
    re.IGNORECASE,
)


def _safe_public_release_label(value: str) -> str:
    if (
        not _PUBLIC_RELEASE_LABEL_RE.fullmatch(value)
        or _CREDENTIAL_LABEL_RE.search(value)
    ):
        raise ValueError(
            "public release label must be a plain non-credential label"
        )
    return value


def _safe_public_profile_id(value: str) -> str:
    if not _PUBLIC_PROFILE_RE.fullmatch(value) or _CREDENTIAL_LABEL_RE.search(value):
        raise ValueError("tool profile id must be a plain non-credential identifier")
    return value


class FileDescriptor(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    local_path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=2**50)
    media_type: str = Field(min_length=3, max_length=255)


class ResourceLimits(StrictModel):
    wall_time_seconds: int = Field(ge=1, le=604800)
    cpu_time_seconds: int = Field(ge=1, le=604800)
    max_memory_bytes: int = Field(ge=1, le=2**50)
    max_output_bytes: int = Field(ge=1, le=2**50, description="Total collection budget in bytes for ALL generated outputs, including PLX/TDR and captured logs; also the per-file runtime size limit. Choose for the complete output inventory, not just the process log. Exceeding the limit fails collection; no files are silently omitted.")
    max_storage_bytes: int = Field(ge=1, le=2**50)


class ArchiveEntry(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=2**50)

    _safe_path = field_validator("relative_path")(_validate_relative_path)


PROCESS_LOG_PATH = ".scid-capture/solver_stdout.log"


class ExpectedOutput(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    relative_path: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=3, max_length=255)
    required: bool = True
    max_bytes: int = Field(ge=1, le=2**50)
    capture: Literal["workspace_file", "process_log"] = "workspace_file"
    experiment_key: str | None = Field(default=None, min_length=1, max_length=256)
    case_key: str | None = Field(default=None, min_length=1, max_length=256)

    _safe_path = field_validator("relative_path")(_validate_relative_path)

    @model_validator(mode="after")
    def _case_identity_is_paired(self) -> ExpectedOutput:
        if (self.experiment_key is None) != (self.case_key is None):
            raise declared_violation("output experiment_key and case_key must be declared together",
                path="$.experiment_key" if self.experiment_key is None else "$.case_key")
        return self

    def execution_output(self) -> ExpectedOutput:
        """Map a frozen logical log alias to the control-owned transport path."""
        return self.model_copy(update={"relative_path": PROCESS_LOG_PATH}) if self.capture == "process_log" else self


class TCADJobSpec(StrictModel):
    schema_version: Literal[4] = 4
    execution_purpose: ExecutionPurpose
    tool_profile: str = Field(min_length=1, max_length=256)
    solver_kind: SolverKind
    capability_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    input_archive: FileDescriptor
    archive_entries: tuple[ArchiveEntry, ...] = Field(min_length=1, max_length=4096)
    arguments: tuple[str, ...] = Field(default=(), max_length=256)
    expected_outputs: tuple[ExpectedOutput, ...] = Field(max_length=4096)
    collect_generated_outputs: bool = False
    limits: ResourceLimits


    @model_validator(mode="after")
    def _unique_paths(self) -> TCADJobSpec:
        inputs = tuple(item.relative_path for item in self.archive_entries)
        outputs = tuple(item.relative_path for item in self.expected_outputs)
        names = tuple(item.name for item in self.expected_outputs)
        if len(inputs) != len(set(inputs)):
            raise ValueError("archive entry paths must be unique")
        if len(outputs) != len(set(outputs)) or len(names) != len(set(names)):
            raise ValueError("expected output paths and names must be unique")
        if any("\x00" in value or len(value) > 4096 for value in self.arguments):
            raise ValueError("job argument is invalid")
        if sum(item.capture == "process_log" for item in self.expected_outputs) > 1:
            raise ValueError("job can declare at most one process-log output")
        if any(path.startswith(".scid-capture/") for path in inputs):
            raise ValueError("input archive uses the reserved control capture directory")
        for item in self.expected_outputs:
            if item.capture == "process_log" and item.relative_path != PROCESS_LOG_PATH:
                raise ValueError("process log must use the reserved stdout mirror path")
            if item.capture != "process_log" and item.relative_path.startswith(".scid-capture/"):
                raise ValueError("solver output uses the reserved control capture directory")
        return self


class SolverCapability(StrictModel):
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    profile_id: str = Field(min_length=1, max_length=256)
    solver_kind: SolverKind
    executable: str = Field(min_length=1, max_length=4096)
    arguments: tuple[str, ...] = Field(default=(), max_length=256)
    environment: dict[str, str] = Field(default_factory=dict)
    release_evidence: str = Field(min_length=1, max_length=4096)
    public_arguments: tuple[str, ...] = Field(default=(), max_length=256)
    public_release_label: str | None = Field(default=None, min_length=1, max_length=256)

    _public_profile_id = field_validator("profile_id")(_safe_public_profile_id)

    @field_validator("executable")
    @classmethod
    def _absolute_executable(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("tool executable must be absolute")
        return value

    @field_validator("public_release_label")
    @classmethod
    def _public_label(cls, value: str | None) -> str | None:
        return None if value is None else _safe_public_release_label(value)

    @model_validator(mode="after")
    def _public_arguments_are_exact_private_arguments(self) -> SolverCapability:
        remaining = iter(self.arguments)
        for public in self.public_arguments:
            if not any(private == public for private in remaining):
                raise ValueError(
                    "public arguments must be an ordered exact subset of fixed arguments"
                )
        return self

    def canonical_sha256(self) -> str:
        private_profile = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "solver_kind": self.solver_kind,
            "executable": self.executable,
            "arguments": self.arguments,
            "environment": self.environment,
            "release_evidence": self.release_evidence,
        }
        return hashlib.sha256(_canonical(private_profile)).hexdigest()

    def public_snapshot(self) -> SolverCapabilitySnapshot:
        if self.public_release_label is None:
            public_release_label = _safe_public_release_label(self.profile_id)
        else:
            public_release_label = self.public_release_label
        release_raw = self.release_evidence.encode("utf-8")
        return SolverCapabilitySnapshot(
            profile_id=self.profile_id,
            solver_kind=self.solver_kind,
            launch_name=Path(self.executable).name,
            public_arguments=self.public_arguments,
            public_release_label=public_release_label,
            private_fixed_argument_count=len(self.arguments),
            private_fixed_arguments_sha256=hashlib.sha256(
                _canonical(self.arguments)
            ).hexdigest(),
            private_release_evidence_bytes=len(release_raw),
            private_release_evidence_sha256=hashlib.sha256(release_raw).hexdigest(),
            capability_sha256=self.canonical_sha256(),
        )


class SolverCapabilitySnapshot(StrictModel):
    """Worker-safe projection bound to one complete private tool profile."""

    schema_version: Annotated[int, Field(ge=2, le=2)] = 2
    profile_id: str = Field(min_length=1, max_length=256)
    solver_kind: SolverKind
    launch_name: str = Field(min_length=1, max_length=256)
    public_arguments: tuple[str, ...] = Field(default=(), max_length=256)
    public_release_label: str = Field(min_length=1, max_length=256)
    private_fixed_argument_count: int = Field(ge=0, le=256)
    private_fixed_arguments_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    private_release_evidence_bytes: int = Field(ge=1, le=16384)
    private_release_evidence_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    capability_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    _public_profile_id = field_validator("profile_id")(_safe_public_profile_id)

    @field_validator("launch_name")
    @classmethod
    def _public_launch_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("public launch name must not contain a path")
        return value

    _safe_release_label = field_validator("public_release_label")(
        _safe_public_release_label
    )


class ToolProfile(SolverCapability):
    pass


class TCADExecutionPolicy(StrictModel):
    agent_execution_policy: AgentExecutionPolicy
    runner: RunnerPolicy
    debug: DebugPolicy
    allowed_input_roots: tuple[str, ...] = Field(min_length=1, max_length=32)
    tools: tuple[ToolProfile, ...] = Field(min_length=1, max_length=128)
    max_concurrent_runs: int = Field(default=1, ge=1, le=128)
    preparation_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def _valid_policy(self) -> TCADExecutionPolicy:
        roots = tuple(str(Path(value).expanduser().absolute()) for value in self.allowed_input_roots)
        profiles = tuple(tool.profile_id for tool in self.tools)
        if len(roots) != len(set(roots)) or len(profiles) != len(set(profiles)):
            raise ValueError("policy roots and tool profiles must be unique")
        object.__setattr__(self, "allowed_input_roots", roots)
        return self

    def tool(self, profile_id: str) -> ToolProfile:
        for profile in self.tools:
            if profile.profile_id == profile_id:
                return profile
        raise ExecutionToolError("TCAD tool profile is not allowed")

    def snapshot(self) -> ExecutionPolicySnapshot:
        return ExecutionPolicySnapshot(agent_execution_policy=self.agent_execution_policy,
            runner=self.runner, debug=self.debug)


class ExecutionToolError(RuntimeError):
    pass


class ExecutionToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ExecutionToolInput):
    pass


class SubmitInput(ExecutionToolInput):
    submission: FileDescriptor
    authorization: ExecutionAdmission | None = None


class RunIdInput(ExecutionToolInput):
    run_id: str = Field(min_length=1, max_length=256)


class InspectOutputsInput(RunIdInput):
    max_bytes: int = Field(default=32*1024*1024, ge=0)
    relative_path: str | None = Field(default=None, max_length=1024)
    deadline_monotonic: float | None = None


class CollectionBudgetInput(ExecutionToolInput):
    deadline_monotonic: float
    stop_deadline_monotonic: float
    file_timeout_seconds: float
    idle_timeout_seconds: float


class CollectInput(RunIdInput):
    collection: CollectionBudgetInput | None = None


class SubmissionDigestInput(ExecutionToolInput):
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ExecutionTool:
    name: str
    description: str
    input_model: type[ExecutionToolInput]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_model.model_json_schema(),
        }


EXECUTION_TOOLS = (
    ExecutionTool("tcad_execution_policy", "Return active administrator execution policy.", EmptyInput),
    ExecutionTool(
        "tcad_capabilities",
        "Return bounded public snapshots of configured TCAD capabilities.",
        EmptyInput,
    ),
    ExecutionTool(
        "tcad_lookup_submission",
        "Authoritatively look up one exact prepared TCAD submission.",
        SubmissionDigestInput,
    ),
    ExecutionTool("tcad_submit", "Submit one prepared TCAD job and return immediately.", SubmitInput),
    ExecutionTool("tcad_submission_protocol", "Discover submission and retirement protocol support.", EmptyInput),
    ExecutionTool("tcad_submit_retirable", "Submit with permanent exact-submission retirement support.", SubmitInput),
    ExecutionTool("tcad_retire_submission", "Permanently fence an absent exact submission or return its existing job.", SubmissionDigestInput),
    ExecutionTool("tcad_status", "Read one durable TCAD job status and optional bounded, redacted log tails with observed job timing.", RunIdInput),
    ExecutionTool("tcad_cancel", "Request cancellation of one TCAD job.", RunIdInput),
    ExecutionTool("tcad_inspect_outputs", "Read bounded terminal files without execution or mutation.", InspectOutputsInput),
    ExecutionTool("tcad_collect", "Return local descriptors for terminal TCAD outputs within the supplied collection budget.", CollectInput),
)


class TCADExecutionFacade:
    """Own only TCAD runtime state; scientific identity remains outside."""

    def __init__(
        self,
        *,
        policy: TCADExecutionPolicy,
        state_root: Path | str,
    ) -> None:
        self.policy = policy
        self.state_root = Path(state_root).expanduser().absolute()
        self.runs_root = self.state_root / "runs"
        self.database_path = self.state_root / "submissions.sqlite3"
        self.runs_root.mkdir(parents=True, exist_ok=True, mode=0o770)
        self._initialize()

    def tcad_capabilities(self) -> dict[str, Any]:
        return {
            "capabilities": [
                tool.public_snapshot().model_dump(mode="json")
                for tool in sorted(self.policy.tools, key=lambda item: item.profile_id)
            ]
        }

    def tcad_submission_protocol(self) -> dict[str, Any]:
        return {"version": 1, "submit": "tcad_submit_retirable", "retire": "tcad_retire_submission"}

    def tcad_execution_policy(self) -> dict[str, Any]:
        return self.policy.snapshot().model_dump(mode="json")

    def tcad_submit_retirable(self, *, submission: FileDescriptor, authorization: ExecutionAdmission | None = None) -> dict[str, Any]:
        return self.tcad_submit(submission=submission, authorization=authorization)

    def tcad_submit(self, *, submission: FileDescriptor, authorization: ExecutionAdmission | None = None) -> dict[str, Any]:
        raw = self._read_input(submission)
        try:
            job = TCADJobSpec.model_validate_json(raw, strict=True)
        except ValidationError as error:
            raise ExecutionToolError("TCAD job specification is invalid") from error
        job_raw = _canonical(job.model_dump(mode="python"))
        if raw not in {job_raw, job_raw + b"\n"}:
            raise ExecutionToolError("TCAD job specification must use canonical JSON")
        tool = self.policy.tool(job.tool_profile)
        if tool.solver_kind != job.solver_kind:
            raise ExecutionToolError(
                "TCAD job solver kind differs from the configured tool capability"
            )
        if tool.canonical_sha256() != job.capability_sha256:
            raise ExecutionToolError(
                "TCAD job capability differs from the configured tool capability"
            )
        snapshot = self.policy.snapshot()
        budget = {"max_storage_bytes": job.limits.max_storage_bytes,
                  "wall_time_seconds": job.limits.wall_time_seconds}
        if sum(item.size_bytes for item in job.archive_entries) > job.limits.max_storage_bytes:
            raise ExecutionToolError("declared inputs exceed task storage limit")
        if job.execution_purpose == "production":
            if authorization is None:
                raise ExecutionToolError("production submission requires control authorization")
            current = snapshot.admission(budget=budget, budget_key=authorization.budget_key)
            if (authorization.reason == "cumulative_budget_exceeded"
                    and authorization.outcome == "require_human_approval"
                    and current.outside_allowance == "require_human_approval"):
                current = current.model_copy(update={"outcome": authorization.outcome,
                    "reason": authorization.reason})
            if current != authorization or current.outcome == "deny":
                raise ExecutionToolError("execution policy changed before submission; reauthorize exact request")
        elif (job.limits.wall_time_seconds > snapshot.debug.total_wall_seconds
              or job.limits.max_storage_bytes > snapshot.debug.max_storage_bytes):
            raise ExecutionToolError("development job exceeds configured debug allowance")
        archive = Path(job.input_archive.local_path).absolute()
        if not any(_within(archive, Path(root)) for root in self.policy.allowed_input_roots):
            raise ExecutionToolError("TCAD archive is outside allowed roots")
        if _file_identity(archive, self.policy.runner.transfer_chunk_bytes) != (job.input_archive.sha256, job.input_archive.size_bytes):
            raise ExecutionToolError("archive differs from its descriptor")
        digest = hashlib.sha256(job_raw).hexdigest()
        self._reconcile_active()
        submitted_at = _timestamp()
        run_id = f"run_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM retired_submissions WHERE job_sha256=?", (digest,)).fetchone():
                raise SubmissionNotCreated(submission.sha256, "submission_retired")
            previous = connection.execute(
                "SELECT run_id FROM submissions WHERE job_sha256 = ?", (digest,)
            ).fetchone()
            if previous is not None:
                connection.execute("ROLLBACK")
                return self.tcad_status(run_id=previous["run_id"])
            active = connection.execute(
                """
                SELECT COUNT(*) AS count FROM submissions
                WHERE lifecycle_state IN ('preparing', 'running', 'cancel_requested')
                """
            ).fetchone()["count"]
            if active >= self.policy.max_concurrent_runs:
                connection.execute("ROLLBACK")
                raise SubmissionNotCreated(submission.sha256, "capacity_exhausted")
            connection.execute(
                """
                INSERT INTO submissions (
                    job_sha256, run_id, submitted_at, lifecycle_state
                ) VALUES (?, ?, ?, 'preparing')
                """,
                (digest, run_id, submitted_at),
            )
            connection.execute("COMMIT")

        executable = _verified_executable(tool.executable)
        run_dir = self.runs_root / run_id
        process: subprocess.Popen[bytes] | None = None
        try:
            run_dir.mkdir(mode=0o770)
            work_dir = run_dir / "work"
            _extract_archive(archive, job.archive_entries, work_dir, self.policy.runner.transfer_chunk_bytes)
            runtime = {
                "arguments": [*tool.arguments, *job.arguments],
                "environment": dict(tool.environment),
                "executable": str(executable),
                "execution_purpose": job.execution_purpose,
                "expected_outputs": [
                    item.model_dump(mode="python") for item in job.expected_outputs
                ],
                "collect_generated_outputs": job.collect_generated_outputs,
                "archive_entries": [item.model_dump(mode="python") for item in job.archive_entries],
                "limits": job.limits.model_dump(mode="python"),
                "runner_policy": self.policy.runner.model_dump(mode="json"),
                "authorization": None if authorization is None else authorization.model_dump(mode="json"),
            }
            _write_new(run_dir / "job.json", _canonical(runtime), mode=0o440)
            _write_new(run_dir / "submitted_at", (submitted_at + "\n").encode("ascii"), mode=0o440)
            with self._connect() as connection:
                lifecycle_state = connection.execute(
                    "SELECT lifecycle_state FROM submissions WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["lifecycle_state"]
            if lifecycle_state == "cancel_requested":
                _write_abandoned_terminal(
                    run_dir,
                    terminal_state="cancelled",
                    exit_code=130,
                    error="cancelled_before_launch",
                )
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE submissions SET lifecycle_state = 'terminal' WHERE run_id = ?",
                        (run_id,),
                    )
                return self.tcad_status(run_id=run_id)
            with (run_dir / "launcher.log").open("xb") as launcher_log:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("worker.py")), str(run_dir)],
                    cwd=run_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=launcher_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            _write_new(run_dir / "launcher_pid", f"{process.pid}\n".encode("ascii"), mode=0o440)
            identity = _process_identity(process.pid)
            if identity is None:
                raise ExecutionToolError("TCAD worker identity is unavailable after launch")
            _write_new(
                run_dir / "launcher_identity",
                (identity + "\n").encode("ascii"),
                mode=0o440,
            )
            with self._connect() as connection:
                updated = connection.execute(
                    """
                    UPDATE submissions SET lifecycle_state = 'running'
                    WHERE run_id = ? AND lifecycle_state = 'preparing'
                    """,
                    (run_id,),
                )
                lifecycle_state = connection.execute(
                    "SELECT lifecycle_state FROM submissions WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["lifecycle_state"]
            if updated.rowcount == 0 and lifecycle_state == "cancel_requested":
                (run_dir / "cancel_requested").touch(exist_ok=True)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        except Exception:
            if process is not None:
                _terminate_process_group(process.pid)
            with self._connect() as connection:
                connection.execute("DELETE FROM submissions WHERE run_id = ?", (run_id,))
            shutil.rmtree(run_dir, ignore_errors=True)
            raise
        return {"run_id": run_id, "state": "accepted", "accepted_at": submitted_at}

    def tcad_lookup_submission(
        self, *, submission_sha256: str
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM submissions WHERE job_sha256 = ?",
                (submission_sha256,),
            ).fetchone()
        if row is None:
            return {"found": False}
        return {"found": True, **self.tcad_status(run_id=row["run_id"])}

    def tcad_status(self, *, run_id: str) -> dict[str, Any]:
        from .remote_runner_py36 import _job_progress

        value = self._tcad_status_value(run_id=run_id)
        value["progress"] = _job_progress(str(self.runs_root / run_id))
        return value

    def tcad_retire_submission(self, *, submission_sha256: str) -> dict[str, Any]:
        SubmissionDigestInput(submission_sha256=submission_sha256)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT run_id FROM submissions WHERE job_sha256=?", (submission_sha256,)).fetchone()
            if row is None:
                connection.execute("INSERT OR IGNORE INTO retired_submissions VALUES (?)", (submission_sha256,))
            connection.commit()
        if row is not None:
            return {"found": True, **self.tcad_status(run_id=row["run_id"])}
        return {"found": False, "retired": True, "submission_sha256": submission_sha256}

    def _tcad_status_value(self, *, run_id: str) -> dict[str, Any]:
        row = self._submission_row(run_id)
        run_dir = self.runs_root / run_id
        submitted = row["submitted_at"]
        if (run_dir / "done").is_file():
            manifest = _read_json(run_dir / "output_manifest.json")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE submissions SET lifecycle_state = 'terminal' WHERE run_id = ?",
                    (run_id,),
                )
            return {
                "run_id": run_id,
                "state": manifest["terminal_state"],
                "consumed_budget": _consumed_budget(manifest),
                "accepted_at": submitted,
                "exit_code": manifest["exit_code"],
                "done": True,
            }
        if row["lifecycle_state"] == "cancel_requested":
            identity_exists = (run_dir / "launcher_identity").is_file()
            if identity_exists and _recorded_process_is_alive(run_dir):
                (run_dir / "cancel_requested").touch(exist_ok=True)
                try:
                    launcher_pid = int(
                        (run_dir / "launcher_pid").read_text(encoding="ascii").strip()
                    )
                    os.killpg(launcher_pid, signal.SIGTERM)
                except (FileNotFoundError, ProcessLookupError, ValueError):
                    pass
                return {
                    "run_id": run_id,
                    "state": "cancelling",
                    "accepted_at": submitted,
                    "exit_code": None,
                    "done": False,
                }
            if identity_exists or (
                _elapsed_seconds(submitted) >= self.policy.preparation_timeout_seconds
            ):
                run_dir.mkdir(mode=0o770, exist_ok=True)
                _write_abandoned_terminal(
                    run_dir,
                    terminal_state="cancelled",
                    exit_code=130,
                    error="cancelled",
                )
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE submissions SET lifecycle_state = 'terminal' WHERE run_id = ?",
                        (run_id,),
                    )
                return {
                    "run_id": run_id,
                    "state": "cancelled",
                    "accepted_at": submitted,
                    "exit_code": 130,
                    "done": True,
                }
            return {
                "run_id": run_id,
                "state": "cancelling",
                "accepted_at": submitted,
                "exit_code": None,
                "done": False,
            }
        if row["lifecycle_state"] == "preparing":
            if (run_dir / "launcher_identity").is_file() and _recorded_process_is_alive(
                run_dir
            ):
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE submissions SET lifecycle_state = 'running' WHERE run_id = ?",
                        (run_id,),
                    )
            elif _elapsed_seconds(submitted) >= self.policy.preparation_timeout_seconds:
                run_dir.mkdir(mode=0o770, exist_ok=True)
                _write_abandoned_terminal(
                    run_dir,
                    terminal_state="failed",
                    exit_code=96,
                    error="preparation_lost",
                )
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE submissions SET lifecycle_state = 'terminal' WHERE run_id = ?",
                        (run_id,),
                    )
                return {
                    "run_id": run_id,
                    "state": "failed",
                    "accepted_at": submitted,
                    "exit_code": 96,
                    "done": True,
                }
            else:
                return {
                    "run_id": run_id,
                    "state": "accepted",
                    "accepted_at": submitted,
                    "exit_code": None,
                    "done": False,
                }
        if not _recorded_process_is_alive(run_dir):
            _write_worker_lost_terminal(run_dir)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE submissions SET lifecycle_state = 'terminal' WHERE run_id = ?",
                    (run_id,),
                )
            manifest = _read_json(run_dir / "output_manifest.json")
            return {
                "run_id": run_id,
                "state": manifest["terminal_state"],
                "consumed_budget": _consumed_budget(manifest),
                "accepted_at": submitted,
                "exit_code": manifest["exit_code"],
                "done": True,
            }
        return {
            "run_id": run_id,
            "state": "running" if (run_dir / "running").is_file() else "accepted",
            "accepted_at": submitted,
            "exit_code": None,
            "done": False,
        }

    def tcad_cancel(self, *, run_id: str) -> dict[str, Any]:
        status = self.tcad_status(run_id=run_id)
        if status["done"]:
            return status
        run_dir = self._run_dir(run_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM submissions WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ExecutionToolError("TCAD run does not exist")
            if row["lifecycle_state"] in {"preparing", "running"}:
                connection.execute(
                    "UPDATE submissions SET lifecycle_state = 'cancel_requested' WHERE run_id = ?",
                    (run_id,),
                )
            connection.execute("COMMIT")
        if run_dir.is_dir():
            (run_dir / "cancel_requested").touch(exist_ok=True)
        for name in ("pid", "launcher_pid"):
            try:
                pid = int((run_dir / name).read_text(encoding="ascii").strip())
                os.killpg(pid, signal.SIGTERM)
                break
            except (FileNotFoundError, ProcessLookupError, ValueError):
                continue
        return {**status, "state": "cancelling"}

    def tcad_inspect_outputs(self, *, run_id: str, relative_path: str | None = None, max_bytes: int = 32*1024*1024, deadline_monotonic=None) -> dict[str, Any]:
        from .remote_runner_py36 import _inspect_directory
        from scidiscovery.plugin_runtime.collection import CollectionContext
        context = CollectionContext(deadline_monotonic, deadline_monotonic) if deadline_monotonic is not None else None
        if context: context.remaining_seconds()
        self.tcad_status(run_id=run_id)
        result = _inspect_directory(str(self._run_dir(run_id)), relative_path, min(max_bytes, self.policy.runner.max_preview_bytes),
            deadline_monotonic=deadline_monotonic)
        if context: context.remaining_seconds()
        return result

    def tcad_collect(self, *, run_id: str, collection=None) -> dict[str, Any]:
        from scidiscovery.plugin_runtime.collection import CollectionContext
        if isinstance(collection, CollectionBudgetInput):
            collection = collection.model_dump()
        context = CollectionContext(**collection) if collection is not None else None
        if context: context.remaining_seconds()
        status = self.tcad_status(run_id=run_id)
        if not status["done"]:
            raise ExecutionToolError("TCAD job is not terminal")
        run_dir = self._run_dir(run_id)
        manifest = _read_json(run_dir / "output_manifest.json")
        outputs = [
            _descriptor(
                name=item["name"],
                path=run_dir / "work" / item["relative_path"],
                media_type=item["media_type"],
                context=context,
            )
            for item in manifest["outputs"]
        ]
        outputs.append(
            _descriptor(
                name="tcad_log",
                path=(run_dir / "diagnostic.log") if (run_dir / "diagnostic.log").is_file() else (run_dir / "worker.log"),
                media_type="text/plain; charset=utf-8",
                context=context,
            )
        )
        outputs.append(
            _descriptor(
                name="tcad_manifest",
                path=run_dir / "output_manifest.json",
                media_type="application/json",
                context=context,
            )
        )
        return {"run_id": run_id, "outputs": outputs}

    def _read_input(self, descriptor: FileDescriptor) -> bytes:
        path = Path(descriptor.local_path).expanduser().absolute()
        if not any(_within(path, Path(root)) for root in self.policy.allowed_input_roots):
            raise ExecutionToolError("TCAD input is outside allowed roots")
        return _read_descriptor(descriptor)

    def _run_dir(self, run_id: str) -> Path:
        self._submission_row(run_id)
        return self.runs_root / run_id

    def _submission_row(self, run_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM submissions WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ExecutionToolError("TCAD run does not exist")
        return row

    def _reconcile_active(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id FROM submissions
                WHERE lifecycle_state IN ('preparing', 'running', 'cancel_requested')
                """
            ).fetchall()
        for row in rows:
            self.tcad_status(run_id=row["run_id"])

    def _initialize(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o770)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS retired_submissions (job_sha256 TEXT PRIMARY KEY)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS submissions (
                    job_sha256 TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    submitted_at TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL DEFAULT 'running'
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(submissions)").fetchall()
            }
            if "lifecycle_state" not in columns:
                connection.execute(
                    """
                    ALTER TABLE submissions
                    ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'running'
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection


class TCADExecutionRouter:
    def __init__(self, facade: TCADExecutionFacade) -> None:
        self.facade = facade
        self._tools = {tool.name: tool for tool in EXECUTION_TOOLS}

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in EXECUTION_TOOLS]

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> Any:
        try:
            tool = self._tools[name]
        except KeyError as error:
            raise ExecutionToolError(f"unknown TCAD tool: {name}") from error
        try:
            parsed = tool.input_model.model_validate(arguments or {}, strict=False)
        except ValidationError as error:
            raise ExecutionToolError(f"invalid arguments for {name}: {error}") from error
        values = {field: getattr(parsed, field) for field in type(parsed).model_fields}
        try:
            return getattr(self.facade, name)(**values)
        except SubmissionNotCreated as error:
            # This is the private TCAD control protocol. Do not attach private
            # descriptor identities to generic Root/Worker error responses.
            return {"submission_rejection": error.wire()}


def _file_identity(path, chunk_bytes):
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("scientific file is not regular")
        digest, size = hashlib.sha256(), 0
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _extract_archive(archive_path, entries, destination, chunk_bytes):
    expected = {item.relative_path: item.model_dump(mode="python") for item in entries}
    if not os.path.isdir(str(destination)):
        os.makedirs(str(destination), mode=0o750)
    seen = set()
    with tarfile.open(str(archive_path), "r:*") as archive:
        for member in archive:
            name = _validate_relative_path(member.name)
            if name in seen or name not in expected or not member.isfile():
                raise ValueError("archive contains an undeclared member")
            item = expected[name]
            if member.size != item["size_bytes"]:
                raise ValueError("archive member size differs from manifest")
            source = archive.extractfile(member)
            target = os.path.join(str(destination), *name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with source, open(target, "xb") as output:
                for chunk in iter(lambda: source.read(chunk_bytes), b""):
                    size += len(chunk)
                    if size > item["size_bytes"]:
                        raise ValueError("archive member exceeds manifest")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("archive member differs from manifest")
            os.chmod(target, 0o440)
            seen.add(name)
    if seen != set(expected):
        raise ValueError("archive is missing declared members")


def _read_descriptor(descriptor: FileDescriptor) -> bytes:
    path = Path(descriptor.local_path).absolute()
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ExecutionToolError("TCAD input must be a regular non-symlink file")
    content = path.read_bytes()
    if len(content) != descriptor.size_bytes or hashlib.sha256(content).hexdigest() != descriptor.sha256:
        raise ExecutionToolError("TCAD input differs from its descriptor")
    return content


def _descriptor(*, name: str, path: Path, media_type: str, context=None) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if context: context.remaining_seconds()
            digest.update(chunk)
            size += len(chunk)
    return FileDescriptor(
        name=name,
        local_path=str(path),
        sha256=digest.hexdigest(),
        size_bytes=size,
        media_type=media_type,
    ).model_dump(mode="json")


def _verified_executable(value: str) -> Path:
    # Verify the symlink target but retain the configured invocation path.
    # Multi-call launchers such as Synopsys GENERIC dispatch from argv[0].
    path = Path(value).expanduser().absolute()
    metadata = os.stat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ExecutionToolError("TCAD executable must resolve to a regular file")
    if metadata.st_mode & 0o111 == 0:
        raise ExecutionToolError("TCAD executable is not executable")
    return path


def _process_identity(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw.rpartition(") ")[2].split()
        if fields[0] == "Z":
            return None
        start_ticks = fields[19]
    except (FileNotFoundError, IndexError, OSError, UnicodeError):
        return None
    return f"{pid}:{start_ticks}"


def _recorded_process_is_alive(run_dir: Path) -> bool:
    try:
        pid = int((run_dir / "launcher_pid").read_text(encoding="ascii").strip())
        expected = (run_dir / "launcher_identity").read_text(encoding="ascii").strip()
    except (FileNotFoundError, ValueError, OSError):
        return False
    return _process_identity(pid) == expected


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _write_worker_lost_terminal(run_dir: Path) -> None:
    try:
        solver_pid = int((run_dir / "solver_pid").read_text(encoding="ascii").strip())
        _terminate_process_group(solver_pid)
    except (FileNotFoundError, ValueError, OSError):
        pass
    cancelled = (run_dir / "cancel_requested").is_file()
    _write_abandoned_terminal(
        run_dir,
        terminal_state="cancelled" if cancelled else "failed",
        exit_code=130 if cancelled else 98,
        error="cancelled" if cancelled else "worker_lost",
    )


def _write_abandoned_terminal(
    run_dir: Path,
    *,
    terminal_state: str,
    exit_code: int,
    error: str,
) -> None:
    log = run_dir / "worker.log"
    if not log.exists():
        _write_new(log, (error + "\n").encode("ascii"), mode=0o440)
    manifest = {
        "completed_at": _timestamp(),
        "error": error,
        "exit_code": exit_code,
        "outputs": [],
        "started_at": None,
        "terminal_state": terminal_state,
    }
    _write_new(
        run_dir / "output_manifest.json",
        _canonical(manifest),
        mode=0o440,
    )
    _write_new(run_dir / "status", f"{exit_code}\n".encode("ascii"), mode=0o440)
    (run_dir / "running").unlink(missing_ok=True)
    (run_dir / "done").touch(exist_ok=False)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.expanduser().absolute())
    except ValueError:
        return False
    return True


def _write_new(path: Path, content: bytes, *, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionToolError("TCAD runtime state is invalid")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _elapsed_seconds(timestamp: str) -> float:
    recorded = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - recorded).total_seconds()


__all__ = [
    "ArchiveEntry",
    "EXECUTION_TOOLS",
    "ExecutionToolError",
    "ExecutionPurpose",
    "ExpectedOutput",
    "FileDescriptor",
    "ResourceLimits",
    "SolverCapability",
    "SolverCapabilitySnapshot",
    "SolverKind",
    "TCADExecutionFacade",
    "TCADExecutionPolicy",
    "TCADExecutionRouter",
    "TCADJobSpec",
    "ToolProfile",
]


def _consumed_budget(manifest):
    from .remote_runner_py36 import _consumed_budget as observed_usage
    return observed_usage(manifest)
