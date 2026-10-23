"""Bounded command adapter for administrator-provided TCAD transports."""

from __future__ import annotations

import json
import os
import stat
import time
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scidiscovery.operations.execution import AdapterCapability
from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor

from .transport_logs import preserve_log
from scidiscovery.plugin_runtime.collection import CollectionContext, QUERY_SECONDS, run_bounded
from scidiscovery.operation_contract import DiagnosticError, contract_diagnostic

from .execution_control import FileDescriptor, SolverCapabilitySnapshot, TCADJobSpec
from .execution_policy import ExecutionPolicySnapshot, execution_admission, collection_context
from .project_packager import (
    PackagerError,
    package_execution_package_json,
    materialize_execution_inputs,
    validate_execution_package_json,
)


class CommandAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    executable: str = Field(min_length=1, max_length=4096)
    arguments: tuple[str, ...] = Field(default=(), max_length=64)
    environment: dict[str, str] = Field(default_factory=dict)
    operation_timeout_seconds: int = Field(default=30, ge=1, le=300)
    query_timeout_seconds: float = Field(default=QUERY_SECONDS, gt=0)

    @field_validator("executable")
    @classmethod
    def _absolute_executable(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("transport executable must be absolute")
        return value


def bundled_ssh_configuration(config: CommandAdapterConfig) -> Path | None:
    """Resolve only the transport owned by this plugin, never an arbitrary executor."""
    arguments = config.arguments
    if arguments[:2] == ("-m", "tcad_artifact.ssh_transport"):
        arguments = arguments[2:]
    elif Path(config.executable).name != "scidiscovery-tcad-transport":
        return None
    if len(arguments) == 2 and arguments[0] == "--config":
        path = arguments[1]
    elif len(arguments) == 1 and arguments[0].startswith("--config="):
        path = arguments[0].split("=", 1)[1]
    else:
        raise ValueError("bundled TCAD transport requires one --config argument")
    if not path or not Path(path).is_absolute():
        raise ValueError("bundled TCAD transport configuration path must be absolute")
    return Path(path)


def validate_command_configuration(config: CommandAdapterConfig) -> None:
    """Check our bundled transport locally; arbitrary external executors own their config."""
    path = bundled_ssh_configuration(config)
    if path is None:
        return
    from .ssh_transport import read_transport_config
    read_transport_config(path)


def read_command_configuration(path: Path | str) -> CommandAdapterConfig:
    """Parse local settings and resolve this plugin's declared file references."""
    from scidiscovery.plugin_configuration import read_configuration
    source = Path(path).expanduser().absolute()
    metadata = os.lstat(source)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError('transport configuration must be a regular file')
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError('transport configuration must not be group/world writable')
    body = read_configuration(source)
    if isinstance(body.get('executable'), str):
        body['executable'] = str((source.parent / Path(body['executable']).expanduser()).resolve())
    arguments = body.get('arguments', [])
    if isinstance(arguments, list) and all(isinstance(arg, str) for arg in arguments):
        offset = 2 if arguments[:2] == ['-m', 'tcad_artifact.ssh_transport'] else 0
        bundled = offset == 2 or Path(body.get('executable', '')).name == 'scidiscovery-tcad-transport'
        if bundled:
            args = arguments[offset:]
            if len(args) == 2 and args[0] == '--config':
                arguments[offset+1] = os.path.abspath(source.parent / Path(args[1]).expanduser())
            elif len(args) == 1 and args[0].startswith('--config='):
                arguments[offset] = '--config=' + os.path.abspath(source.parent / Path(args[0].split('=',1)[1]).expanduser())
    return CommandAdapterConfig.model_validate_json(json.dumps(body), strict=True)


class CommandTCADExecutorAdapter:
    """Invoke one bounded external operation for each execution transition."""

    def __init__(
        self,
        config: CommandAdapterConfig,
        *,
        local_result_root: Path | str,
    ) -> None:
        executable = Path(config.executable).resolve(strict=True)
        metadata = os.stat(executable)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o111 == 0:
            raise ValueError("transport executable must be an executable regular file")
        self.config = config.model_copy(update={"executable": str(executable)})
        self.local_result_root = Path(local_result_root).expanduser().absolute()
        self.local_result_root.mkdir(parents=True, exist_ok=True, mode=0o770)

    @staticmethod
    def supports_preparation_profile(value: str) -> bool:
        return value in {"tcad.execution-package.v2", "tcad.execution-package.v3"}

    def capabilities(self) -> tuple[AdapterCapability, ...]:
        value = self._call("capabilities", {})
        snapshots = tuple(
            SolverCapabilitySnapshot.model_validate_json(
                _canonical(item), strict=True
            )
            for item in value.get("capabilities", ())
        )
        return tuple(_adapter_capability(item) for item in snapshots)

    def execution_policy(self) -> ExecutionPolicySnapshot:
        return ExecutionPolicySnapshot.model_validate_json(json.dumps(self._call("execution_policy", {})), strict=True)

    def validate_submission_protocol(self):
        value = self._call("submission_protocol", {})
        if value != {"version": 2, "submit": "tcad_submit_retirable", "retire": "tcad_retire_submission"}:
            raise RuntimeError("TCAD submission protocol is incompatible; upgrade the runner before submitting.")
        return value

    def execution_admission(self, raw: bytes, *, preparation_profile: str):
        self.validate_preparation_payload(raw, preparation_profile=preparation_profile)
        return execution_admission(self.execution_policy(), validate_execution_package_json(raw, preparation_profile=preparation_profile))

    def validate_preparation_payload(
        self, raw: bytes, *, preparation_profile: str
    ) -> None:
        if not self.supports_preparation_profile(preparation_profile):
            raise ValueError("command adapter requires tcad.execution-package.v2")
        try:
            reviewed = validate_execution_package_json(raw, preparation_profile=preparation_profile)
        except PackagerError as error:
            raise ValueError("TCAD execution package is invalid") from error
        self._require_active_capability(reviewed.capability)

    @classmethod
    def from_file(
        cls,
        path: Path | str,
        *,
        local_result_root: Path | str,
    ) -> CommandTCADExecutorAdapter:
        config = read_command_configuration(path)
        return cls(config, local_result_root=local_result_root)

    def prepare_with_artifacts(self, payload, *, artifacts, **options):
        return self.prepare(payload, artifacts=artifacts, **options)

    def prepare(
        self,
        payload: LocalFileDescriptor,
        *,
        preparation_profile: str,
        exchange_directory: Path,
        artifacts=None,
        input_metadata=None,
        open_input=None,
    ) -> LocalFileDescriptor:
        if not self.supports_preparation_profile(preparation_profile):
            raise ValueError("command adapter requires tcad.execution-package.v2")
        raw = Path(payload.local_path).read_bytes()
        self.validate_preparation_payload(raw, preparation_profile=preparation_profile)
        reviewed = validate_execution_package_json(raw, preparation_profile=preparation_profile)
        inputs = (materialize_execution_inputs(reviewed, artifacts, exchange_directory,
            self.execution_policy().runner.transfer_chunk_bytes,
            input_metadata=input_metadata, open_input=open_input,context=getattr(self,'operation_context',None))
            if reviewed.resolved_inputs or artifacts is not None
            or input_metadata is not None or open_input is not None else {})
        packaged = package_execution_package_json(
            raw,
            output_root=exchange_directory / "prepared",
            input_payloads=inputs,
            preparation_profile=preparation_profile,
            context=getattr(self,'operation_context',None),
        )
        value = self._call(
            "prepare",
            {
                "job_spec": _without_schema(packaged.job_spec_file),
                "archive": _without_schema(packaged.archive),
                "execution_exchange": str(exchange_directory),
                "local_result_root": str(self.local_result_root),
            },
        )
        return LocalFileDescriptor.model_validate(value["submission"], strict=True)

    def prepare_development_debug(
        self,
        *,
        job_spec_file: LocalFileDescriptor,
        archive: LocalFileDescriptor,
        exchange_directory: Path,
    ) -> LocalFileDescriptor:
        self.validate_submission_protocol()
        job_raw = _read_bound_descriptor(
            job_spec_file, exchange_directory=exchange_directory
        )
        _read_bound_descriptor(archive, exchange_directory=exchange_directory, read_content=False)
        job = TCADJobSpec.model_validate_json(job_raw, strict=True)
        expected_archive = FileDescriptor.model_validate(
            archive.model_dump(mode="python", exclude={"schema_version"}),
            strict=True,
        )
        if (
            job.execution_purpose != "development_debug"
            or job.input_archive != expected_archive
            or job.solver_kind not in {"sprocess", "sdevice"}
        ):
            raise ValueError("development debug job binding is invalid")
        snapshots = tuple(
            SolverCapabilitySnapshot.model_validate_json(item.content, strict=True)
            for item in self.capabilities()
        )
        if not any(
            item.profile_id == job.tool_profile
            and item.solver_kind == job.solver_kind
            and item.capability_sha256 == job.capability_sha256
            for item in snapshots
        ):
            raise ValueError("development debug capability is not active")
        value = self._call(
            "prepare",
            {
                "job_spec": _without_schema(job_spec_file),
                "archive": _without_schema(archive),
                "execution_exchange": str(exchange_directory),
                "local_result_root": str(self.local_result_root),
            },
        )
        return LocalFileDescriptor.model_validate(value["submission"], strict=True)

    def submit(self, submission: LocalFileDescriptor, *, authorization=None) -> tuple[str, str]:
        value = self._call("submit_retirable", {"submission": _without_schema(submission),
            "authorization": None if authorization is None else authorization.model_dump(mode="json")})
        return str(value["run_id"]), str(value["state"])

    def lookup_submission(
        self, submission: LocalFileDescriptor
    ) -> tuple[str, str] | None:
        value = self._call(
            "lookup_submission", {"submission": _without_schema(submission)}
        )
        if value.get("found") is False:
            return None
        if value.get("found") is not True:
            raise RuntimeError("TCAD transport lookup returned no authority")
        return str(value["run_id"]), str(value["state"])

    def status(self, external_run_id: str) -> str:
        return str(self.status_details(external_run_id)["state"])

    def retire_submission(self, submission: LocalFileDescriptor) -> tuple[str, str] | None:
        value = self.retire_submission_details(submission)
        return (str(value["run_id"]), str(value["state"])) if value["found"] else None

    def retire_submission_details(self, submission: LocalFileDescriptor) -> dict:
        value = self._call("retire_submission", {"submission": _without_schema(submission)})
        if value.get("found") is True:
            return value
        if value.get("found") is False and value.get("retired") is True and value.get("submission_id") == _submission_id(submission):
            return value
        raise RuntimeError("TCAD transport did not confirm exact submission retirement")

    def status_details(self, external_run_id: str) -> dict[str, Any]:
        return self._call("status", {"run_id": external_run_id})

    def cancel(self, external_run_id: str) -> str:
        value = self._call("cancel", {"run_id": external_run_id})
        return str(value["state"])

    def inspect_outputs(self, external_run_id: str, relative_path: str | None = None, max_bytes: int = 32*1024*1024, *, deadline_monotonic=None) -> dict[str, Any]:
        payload = {"run_id": external_run_id, "relative_path": relative_path, "max_bytes": max_bytes}
        if deadline_monotonic is not None:
            payload["deadline_monotonic"] = deadline_monotonic
        value = self._call("inspect_outputs", payload)
        if value.get("status") == "available" and "file" in value:
            descriptor = LocalFileDescriptor.model_validate(value["file"], strict=True)
            path = Path(descriptor.local_path)
            if not _within(path.resolve(), self.local_result_root.resolve()) or path.is_symlink() or descriptor.size_bytes > max_bytes:
                raise RuntimeError("inspection output is outside its bounds")
        return value

    def collect(self, external_run_id: str) -> tuple[LocalFileDescriptor, ...]:
        value = self._call(
            "collect",
            {
                "run_id": external_run_id,
                "local_result_root": str(self.local_result_root),
            },
        )
        return self._collected_outputs(value)

    def collect_with_budget(self, external_run_id: str, *, context: CollectionContext) -> tuple[LocalFileDescriptor, ...]:
        context = collection_context(context, self.execution_policy().runner)
        context.remaining_seconds()
        value = self._call("collect", {"run_id": external_run_id,
            "local_result_root": str(self.local_result_root), "collection": context.wire(),
            "progress_path": context.progress_path}, context=context)
        return self._collected_outputs(value, context=context)

    def _collected_outputs(self, value, *, context=None):
        outputs = tuple(
            LocalFileDescriptor.model_validate(item, strict=True)
            for item in value["outputs"]
        )
        for item in outputs:
            if context is not None:
                context.remaining_seconds()
            path = Path(item.local_path).expanduser().absolute()
            if not _within(path, self.local_result_root):
                raise RuntimeError("transport output is outside its result root")
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("transport output is not a regular file")
            if metadata.st_size != item.size_bytes:
                raise RuntimeError("transport output differs from its descriptor")
        return outputs

    def _call(self, operation: str, payload: dict[str, Any], *, context=None) -> dict[str, Any]:
        deadline = payload.get("deadline_monotonic")
        operation_context=getattr(self,'operation_context',None)
        if operation_context is not None:
            deadline=min(deadline if deadline is not None else float('inf'),
                         time.monotonic()+operation_context.remaining_seconds())
        if operation in {"status", "capabilities", "execution_policy"}:
            # Bound the whole command locally without changing its business
            # payload: discovery transports require an empty object.
            query_deadline = time.monotonic() + self.config.query_timeout_seconds
            deadline = min(deadline, query_deadline) if deadline is not None else query_deadline
        timeout = context.remaining_seconds() if context is not None else max(.001, deadline - time.monotonic()) if deadline is not None else self.config.operation_timeout_seconds
        if operation_context is not None:
            timeout=min(timeout,operation_context.remaining_seconds(),self.config.operation_timeout_seconds)
        if deadline is not None and deadline <= time.monotonic():
            raise TimeoutError("TCAD transport request deadline exhausted before start")
        request = _canonical(
            {
                "schema_version": 2,
                "operation": operation,
                "payload": payload,
            }
        )
        environment = {
            "HOME": str(self.local_result_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            **self.config.environment,
        }
        try:
            active_context=context
            if operation_context is not None:
                timeout=min(timeout,operation_context.remaining_seconds(),deadline-time.monotonic())
                if active_context is None or operation_context.deadline_monotonic<active_context.deadline_monotonic:
                    active_context=operation_context
            completed = run_bounded([self.config.executable, *self.config.arguments],
                input=request, env=environment, timeout=timeout, context=active_context)
        except subprocess.TimeoutExpired as error:
            log = preserve_log(self.local_result_root, "stderr", error.stderr or b"")
            preserve_log(self.local_result_root, "stdout", error.stdout or b"")
            raise RuntimeError(f"TCAD transport operation exceeded its short bound; stderr log: {log}") from error
        stderr_log = preserve_log(self.local_result_root, "stderr", completed.stderr) if completed.stderr else None
        if completed.returncode != 0:
            preserve_log(self.local_result_root, "stdout", completed.stdout)
            error = subprocess.CalledProcessError(completed.returncode, "TCAD transport",
                output=completed.stdout, stderr=completed.stderr)
            raise RuntimeError(f"TCAD transport operation failed; stderr log: {stderr_log}") from error
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            log = preserve_log(self.local_result_root, "stdout", completed.stdout)
            raise RuntimeError(f"TCAD transport returned invalid JSON; stdout log: {log}") from error
        if (not isinstance(response, dict) or response.get("schema_version") != 2
                or response.get("operation") != operation or response.get("ok") is not True
                or not isinstance(response.get("payload"), dict)):
            preserve_log(self.local_result_root, "stdout", completed.stdout)
        if not isinstance(response, dict) or response.get("schema_version") != 2:
            raise RuntimeError("TCAD transport response has an invalid envelope")
        if response.get("operation") != operation:
            raise RuntimeError("TCAD transport response operation differs")
        if response.get("ok") is not True or not isinstance(response.get("payload"), dict):
            safe_messages = {
                "tcad_transport_configuration_invalid": (
                    "TCAD execution is blocked by invalid local transport configuration before remote contact. "
                    "Ask the administrator to repair the service configuration, then retry the same action. "
                    "Do not change scientific inputs or repeat this request before repair."),
                "tcad_runner_upgrade_required": (
                    "The remote TCAD runner cannot confirm required policy/submission protocol support. "
                    "Ask the administrator to upgrade the remote runner and validate its configuration. "
                    "This read-only discovery did not submit a job. Do not retry execution before repair."),
            }
            code = response.get("error_code")
            if isinstance(code, str) and code in safe_messages:
                # Reconstruct a declared safe diagnostic; never trust wire messages,
                # configuration values, paths or arbitrary wire diagnostic fields.
                message = safe_messages[code]
                error = DiagnosticError(message, details=(contract_diagnostic(
                    code, phase="tool_execution",
                    affected_action="tool_call", message=message, repairable=False),))
                error.failure_stage = "configuration"
            else:
                error = RuntimeError(str(response.get("error", "TCAD transport rejected operation")))
            if isinstance(response.get("engineering"), dict):
                error.engineering = response["engineering"]
            raise error
        return response["payload"]

    def _require_active_capability(
        self, expected: SolverCapabilitySnapshot
    ) -> None:
        snapshots = tuple(
            SolverCapabilitySnapshot.model_validate_json(item.content, strict=True)
            for item in self.capabilities()
        )
        if expected not in snapshots:
            raise ValueError("execution package capability is not active on this adapter")


def _without_schema(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude={"schema_version"})
    raise TypeError("descriptor is not a schema model")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_bound_descriptor(
    descriptor: LocalFileDescriptor, *, exchange_directory: Path, read_content: bool = True
) -> bytes:
    path = Path(descriptor.local_path).expanduser().absolute()
    if not _within(path, exchange_directory.expanduser().absolute()):
        raise ValueError("development debug file is outside its exchange")
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("development debug file is not regular")
    if metadata.st_size != descriptor.size_bytes:
        raise ValueError("development file differs from its declared size")
    if not read_content:
        return b""

    raw = path.read_bytes()
    if (
        len(raw) != descriptor.size_bytes
    ):
        raise ValueError("development debug file differs from its descriptor")
    return raw


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _adapter_capability(value: SolverCapabilitySnapshot) -> AdapterCapability:
    return AdapterCapability(
        key=value.profile_id,
        kind="solver_capability",
        schema_id="tcad.solver-capability.v2",
        payload_schema_version=2,
        media_type="application/json",
        content=_canonical(value.model_dump(mode="json")),
        public_summary={
            "profile": value.profile_id,
            "solver_kind": value.solver_kind,
            "launch_name": value.launch_name,
            "public_arguments": list(value.public_arguments),
            "public_release_label": value.public_release_label,
            "private_fixed_argument_count": value.private_fixed_argument_count,
        },
    )


__all__ = ["CommandAdapterConfig", "CommandTCADExecutorAdapter", "validate_command_configuration", "bundled_ssh_configuration"]


def _submission_id(submission):
    value=json.loads(Path(submission.local_path).read_bytes())
    # Command transports return a marker retaining the exact remote request.
    if 'request_id' in value:return value['request_id']
    if 'submission_id' in value:return value['submission_id']
    raise ValueError('Prepared submission has no exact request identity')
