"""SciDiscovery execution adapter for the internal TCAD control socket."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from scidiscovery.plugin_runtime.collection import CollectionContext, QUERY_SECONDS
from typing import Any

from scidiscovery.operations.execution import AdapterCapability
from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor

from .execution_control import FileDescriptor, SolverCapabilitySnapshot, TCADJobSpec
from .execution_policy import ExecutionPolicySnapshot, execution_admission, collection_context
from .project_packager import (
    PackagerError,
    package_execution_package_json,
    materialize_execution_inputs,
    validate_execution_package_json,
)


class TCADExecutorAdapter:
    def __init__(self, socket_path: Path | str, *, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("adapter timeout must be positive")
        self.socket_path = Path(socket_path).expanduser().absolute()
        self.timeout = timeout

    @staticmethod
    def supports_preparation_profile(value: str) -> bool:
        return value in {"tcad.execution-package.v2", "tcad.execution-package.v3"}

    def capabilities(self) -> tuple[AdapterCapability, ...]:
        value = self._call("tcad_capabilities", {})
        snapshots = tuple(
            SolverCapabilitySnapshot.model_validate_json(
                json.dumps(
                    item,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                strict=True,
            )
            for item in value.get("capabilities", ())
        )
        return tuple(_adapter_capability(item) for item in snapshots)

    def execution_policy(self) -> ExecutionPolicySnapshot:
        return ExecutionPolicySnapshot.model_validate_json(json.dumps(self._call("tcad_execution_policy", {})), strict=True)

    def validate_submission_protocol(self):
        value = self._call("tcad_submission_protocol", {})
        if value != {"version": 2, "submit": "tcad_submit_retirable", "retire": "tcad_retire_submission"}:
            raise RuntimeError("TCAD submission protocol is incompatible; upgrade the service before submitting.")
        return value

    def execution_admission(self, raw: bytes, *, preparation_profile: str):
        self.validate_preparation_payload(raw, preparation_profile=preparation_profile)
        return execution_admission(self.execution_policy(), validate_execution_package_json(raw, preparation_profile=preparation_profile))

    def validate_preparation_payload(
        self, raw: bytes, *, preparation_profile: str
    ) -> None:
        if not self.supports_preparation_profile(preparation_profile):
            raise ValueError("TCAD execution requires tcad.execution-package.v2")
        try:
            reviewed = validate_execution_package_json(raw, preparation_profile=preparation_profile)
        except PackagerError as error:
            raise ValueError("TCAD execution package is invalid") from error
        self._require_active_capability(reviewed.capability)

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
        payload_path = Path(payload.local_path).absolute()
        if payload_path.parent != exchange_directory.absolute():
            raise ValueError("execution payload is outside its exchange directory")
        if not self.supports_preparation_profile(preparation_profile):
            raise ValueError(
                "TCAD execution requires tcad.execution-package.v2"
            )
        raw = payload_path.read_bytes()
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
        return LocalFileDescriptor.model_validate(
            packaged.job_spec_file.model_dump(mode="python"), strict=True
        )

    def prepare_development_debug(
        self,
        *,
        job_spec_file: LocalFileDescriptor,
        archive: LocalFileDescriptor,
        exchange_directory: Path,
    ) -> LocalFileDescriptor:
        self.validate_submission_protocol()
        raw = _read_bound_descriptor(
            job_spec_file, exchange_directory=exchange_directory
        )
        _read_bound_descriptor(archive, exchange_directory=exchange_directory, read_content=False)
        job = TCADJobSpec.model_validate_json(raw, strict=True)
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
        return job_spec_file

    def submit(self, submission: LocalFileDescriptor, *, authorization=None) -> tuple[str, str]:
        value = self._call(
            "tcad_submit_retirable",
            {
                "authorization": None if authorization is None else authorization.model_dump(mode="json"),
                "submission": submission.model_dump(
                    mode="json", exclude={"schema_version"}
                )
            },
        )
        return str(value["run_id"]), str(value["state"])

    def lookup_submission(
        self, submission: LocalFileDescriptor
    ) -> tuple[str, str] | None:
        value = self._call(
            "tcad_lookup_submission",
            {"submission_id": _submission_id(submission)},
        )
        if value.get("found") is False:
            return None
        if value.get("found") is not True:
            raise RuntimeError("TCAD submission lookup returned no authority")
        return str(value["run_id"]), str(value["state"])

    def status(self, external_run_id: str) -> str:
        return str(self.status_details(external_run_id)["state"])

    def retire_submission(self, submission: LocalFileDescriptor) -> tuple[str, str] | None:
        value = self.retire_submission_details(submission)
        return (str(value["run_id"]), str(value["state"])) if value["found"] else None

    def retire_submission_details(self, submission: LocalFileDescriptor) -> dict:
        value = self._call("tcad_retire_submission", {"submission_id": _submission_id(submission)})
        if value.get("found") is True:
            return value
        if value.get("found") is False and value.get("retired") is True and value.get("submission_id") == _submission_id(submission):
            return value
        raise RuntimeError("TCAD service did not confirm exact submission retirement")

    def status_details(self, external_run_id: str) -> dict[str, Any]:
        return self._call("tcad_status", {"run_id": external_run_id})

    def cancel(self, external_run_id: str) -> str:
        return str(self._call("tcad_cancel", {"run_id": external_run_id})["state"])

    def inspect_outputs(self, external_run_id: str, relative_path: str | None = None, max_bytes: int = 32*1024*1024, *, deadline_monotonic=None) -> dict[str, Any]:
        payload = {"run_id": external_run_id, "relative_path": relative_path, "max_bytes": max_bytes}
        if deadline_monotonic is not None:
            payload["deadline_monotonic"] = deadline_monotonic
        return self._call("tcad_inspect_outputs", payload)

    def collect_with_budget(self, external_run_id: str, *, context: CollectionContext) -> tuple[LocalFileDescriptor, ...]:
        context = collection_context(context, self.execution_policy().runner)
        context.remaining_seconds()
        value = self._call("tcad_collect", {"run_id": external_run_id, "collection": context.wire()})
        context.remaining_seconds()
        outputs = tuple(LocalFileDescriptor.model_validate(item, strict=True) for item in value["outputs"])
        context.report_progress({"completed_files": len(outputs), "completed_bytes": sum(x.size_bytes for x in outputs),
            "transfer_coverage": "local_descriptors"})
        return outputs

    def collect(self, external_run_id: str) -> tuple[LocalFileDescriptor, ...]:
        value = self._call("tcad_collect", {"run_id": external_run_id})
        return tuple(
            LocalFileDescriptor.model_validate(item, strict=True)
            for item in value["outputs"]
        )

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        deadline = (arguments.get("collection") or {}).get("deadline_monotonic",
            arguments.get("deadline_monotonic", time.monotonic() + (min(QUERY_SECONDS, self.timeout)
                if tool in {"tcad_status", "tcad_capabilities", "tcad_execution_policy"} else self.timeout)))
        context=getattr(self,'operation_context',None)
        if context is not None:
            deadline=min(deadline,time.monotonic()+context.remaining_seconds())
        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("TCAD socket request budget exhausted")
            return seconds
        request_id = uuid.uuid4().hex
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        from scidiscovery.plugin_runtime.transport import forward_request
        try:
            response = forward_request(self.socket_path, request, timeout=remaining())
        except TimeoutError as error:
            error.timeout_kind = ("collection_total" if arguments.get("collection") else
                "inspection_io" if arguments.get("deadline_monotonic") is not None else "query")
            raise
        if response.get("id") != request_id:
            raise RuntimeError("TCAD control response identity differs")
        if "error" in response:
            error = RuntimeError(str(response["error"].get("message", "TCAD call failed")))
            detail = response["error"].get("data", {}).get("engineering")
            if isinstance(detail, dict):
                error.engineering = detail
            raise error
        try:
            value = response["result"]["structuredContent"]
        except (KeyError, TypeError) as error:
            raise RuntimeError("TCAD control response has no structured result") from error
        if not isinstance(value, dict):
            raise RuntimeError("TCAD control result is not an object")
        return value

    def _require_active_capability(
        self, expected: SolverCapabilitySnapshot
    ) -> None:
        snapshots = tuple(
            SolverCapabilitySnapshot.model_validate_json(item.content, strict=True)
            for item in self.capabilities()
        )
        if expected not in snapshots:
            raise ValueError("execution package capability is not active on this adapter")


def _adapter_capability(value: SolverCapabilitySnapshot) -> AdapterCapability:
    content = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return AdapterCapability(
        key=value.profile_id,
        kind="solver_capability",
        schema_id="tcad.solver-capability.v2",
        payload_schema_version=2,
        media_type="application/json",
        content=content,
        public_summary={
            "profile": value.profile_id,
            "solver_kind": value.solver_kind,
            "launch_name": value.launch_name,
            "public_arguments": list(value.public_arguments),
            "public_release_label": value.public_release_label,
            "private_fixed_argument_count": value.private_fixed_argument_count,
        },
    )


def _read_bound_descriptor(
    descriptor: LocalFileDescriptor, *, exchange_directory: Path, read_content: bool = True
) -> bytes:
    path = Path(descriptor.local_path).expanduser().absolute()
    try:
        path.relative_to(exchange_directory.expanduser().absolute())
    except ValueError as error:
        raise ValueError("development debug file is outside its exchange") from error
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("development debug file is not regular")
    if metadata.st_size != descriptor.size_bytes:
        raise ValueError("development file differs from its declared size")
    if not read_content:
        return b""

    raw = path.read_bytes()
    if (
        metadata.st_size != descriptor.size_bytes
        or len(raw) != descriptor.size_bytes
    ):
        raise ValueError("development debug file differs from its descriptor")
    return raw


__all__ = ["TCADExecutorAdapter"]


def _submission_id(submission):
    value=json.loads(Path(submission.local_path).read_bytes())
    # Command transports return a marker retaining the exact remote request.
    if 'request_id' in value:return value['request_id']
    if 'submission_id' in value:return value['submission_id']
    raise ValueError('Prepared submission has no exact request identity')
