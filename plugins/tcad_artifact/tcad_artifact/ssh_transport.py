"""WSL-to-VM transport for the command TCAD execution adapter."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor

from .transport_logs import preserve_log
from .collection_download import HEADER_LIMIT, unpack_results
from scidiscovery.plugin_runtime.collection import CollectionContext, run_bounded
from scidiscovery.plugin_runtime.workspace import atomic_json
from scidiscovery.plugin_runtime.diagnostics import exception_facts

from .execution_control import FileDescriptor, SolverCapabilitySnapshot, TCADJobSpec


class SSHTCADTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ssh_executable: str = Field(min_length=1, max_length=4096)
    identity_file: str | None = Field(default=None, min_length=1, max_length=4096)
    destination: str = Field(min_length=3, max_length=512)
    destination_resolver: tuple[str, ...] = Field(default=(), max_length=32)
    port: int = Field(default=22, ge=1, le=65535)
    host_key_alias: str | None = Field(default=None, min_length=1, max_length=512)
    known_hosts_file: str | None = Field(default=None, min_length=1, max_length=4096)
    remote_helper: str = Field(min_length=1, max_length=4096)
    remote_config: str = Field(min_length=1, max_length=4096)
    remote_exchange_root: str = Field(min_length=1, max_length=4096)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    operation_timeout_seconds: int = Field(default=120, ge=1, le=600)
    max_transfer_bytes: int = Field(ge=1, le=2**40)
    # Buffer tuning is optional; execution authority and transfer quota remain explicit.
    transfer_chunk_bytes: int = Field(default=1024 * 1024, ge=1)

    @field_validator(
        "ssh_executable", "remote_helper", "remote_config", "remote_exchange_root"
    )
    @classmethod
    def _safe_absolute(cls, value: str) -> str:
        if not Path(value).is_absolute() or not re.fullmatch(r"[A-Za-z0-9_./ ()-]+", value):
            raise ValueError("transport command paths must be safe absolute paths")
        return value


class RemoteClient(Protocol):
    def put(self, relative_path: str, raw: bytes) -> None: ...
    def put_file(self, relative_path: str, descriptor: FileDescriptor) -> None: ...
    def rpc(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, local_path: str) -> bytes: ...
    def get_to(self, local_path: str, destination: Path, max_bytes: int) -> None: ...


class SSHRemoteClient:
    def __init__(self, config: SSHTCADTransportConfig, *, diagnostic_root: Path | None = None) -> None:
        executable = Path(config.ssh_executable)
        if not executable.is_file():
            raise ValueError("SSH executable does not exist")
        if config.destination_resolver and not Path(
            config.destination_resolver[0]
        ).is_file():
            raise ValueError("destination resolver executable does not exist")
        self.config = config
        self.diagnostic_root = diagnostic_root
        self.context = None
        self.download_progress = lambda _: None

    def _run(self, command, **kwargs):
        try:
            if self.context is not None:
                kwargs["timeout"] = min(kwargs.get("timeout", self.context.remaining_seconds()), self.context.remaining_seconds())
            return run_bounded(command, input=kwargs.get("input", b""), env=kwargs.get("env"),
                timeout=kwargs["timeout"], context=self.context,
                input_stream=kwargs.get("input_stream"), transfer_chunk_bytes=self.config.transfer_chunk_bytes)
        except subprocess.TimeoutExpired as error:
            if self.diagnostic_root is not None:
                preserve_log(self.diagnostic_root, "ssh-stderr", error.stderr or b"")
                preserve_log(self.diagnostic_root, "ssh-stdout", error.stdout or b"")
            raise

    def put(self, relative_path: str, raw: bytes) -> None:
        response, trailing = self._call(
            "put",
            {
                "relative_path": relative_path,
                "size_bytes": len(raw),
            },
            raw,
        )
        if trailing:
            raise RuntimeError("VM upload returned unexpected bytes")
        _require_ok(response, "put")

    def put_file(self, relative_path: str, descriptor: FileDescriptor) -> None:
        if descriptor.size_bytes > self.config.max_transfer_bytes:
            raise ValueError("upload exceeds configured transfer bound")
        response, trailing = self._call("put", {"relative_path": relative_path,
            "size_bytes": descriptor.size_bytes}, b"",
            upload_path=Path(descriptor.local_path))
        if trailing:
            raise RuntimeError("VM upload returned unexpected bytes")
        _require_ok(response, "put")

    def rpc(self, request: dict[str, Any]) -> dict[str, Any]:
        response, trailing = self._call("rpc", {"request": request}, b"")
        if trailing:
            raise RuntimeError("VM RPC returned unexpected bytes")
        payload = _require_ok(response, "rpc")
        value = payload.get("response")
        if not isinstance(value, dict):
            raise RuntimeError("VM RPC response is not an object")
        return value

    def get(self, local_path: str) -> bytes:
        response, raw = self._call("get", {"local_path": local_path}, b"")
        payload = _require_ok(response, "get")
        if (
            payload.get("size_bytes") != len(raw)
        ):
            raise RuntimeError("VM download differs from response descriptor")
        return raw

    def get_to(self, local_path: str, destination: Path, max_bytes: int) -> None:
        response, _ = self._call("get", {"local_path": local_path, "max_bytes": max_bytes}, b"",
                                 download_to=destination, download_limit=max_bytes)
        payload = _require_ok(response, "get")
        if self.context:self.context.remaining_seconds()
        if payload.get("size_bytes") != destination.stat().st_size:
            raise RuntimeError("VM download differs from response descriptor")

    def get_many_to(self, items, *, on_complete, on_progress):
        """One standard tar transfer per bounded request; no per-file SSH calls."""
        batch, records = [], []
        size = len(_canonical({"schema_version": 2, "operation": "get_archive", "payload": {"files": []}})) + 1
        base_size = size
        for descriptor, path in items:
            if descriptor.size_bytes > self.config.max_transfer_bytes:
                raise ValueError("download exceeds configured transfer bound")
            record = {"local_path": descriptor.local_path, "size_bytes": descriptor.size_bytes}
            extra = len(_canonical(record)) + bool(records)
            if size + extra > HEADER_LIMIT and batch:
                self._get_many_batch(batch, records, on_complete, on_progress)
                batch, records, size = [], [], base_size
                extra = len(_canonical(record))
            if size + extra > HEADER_LIMIT:
                raise ValueError("download descriptor exceeds request header bound")
            batch.append((descriptor, path))
            records.append(record)
            size += extra
        if batch:
            self._get_many_batch(batch, records, on_complete, on_progress)

    def _get_many_batch(self, items, records, on_complete, on_progress):
        # A standard uncompressed tar stream avoids remote archive disk usage.
        # Account for all member headers, padding, manifest and final tar record.
        archive_limit = len(_canonical(records)) + sum(x.size_bytes for x, _ in items) + 1024 * (len(items) + 1) + 10240
        spool=items[0][1].parent if self.diagnostic_root is None else Path(self.diagnostic_root)
        if spool.resolve()!=spool or not spool.is_dir():
            raise ValueError('result transfer spool requires its exact private directory')
        fd, temporary = tempfile.mkstemp(prefix=".results.", suffix=".tar.download", dir=spool)
        os.close(fd)
        temporary = Path(temporary)
        previous_progress = self.download_progress
        self.download_progress = on_progress
        try:
            response, _ = self._call("get_archive", {"files": records}, b"",
                                     download_to=temporary, download_limit=archive_limit)
            if _require_ok(response, "get_archive") != {"files": len(items)}:
                raise RuntimeError("result archive response differs from request")
            unpack_results(temporary, items, records, context=self.context, on_complete=on_complete)
        except subprocess.CalledProcessError as error:
            if b"unsupported remote runner operation" in (error.stderr or b""):
                raise RemoteRunnerUpgradeRequired("Remote runner lacks archive downloads; reinstall local and remote runtime together") from error
            raise
        finally:
            self.download_progress = previous_progress
            temporary.unlink(missing_ok=True)

    def _call(
        self,
        operation: str,
        payload: dict[str, Any],
        body: bytes,
        *, download_to: Path | None = None, download_limit: int = 0,
        upload_path: Path | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        started = time.monotonic()
        request = _canonical(
            {"schema_version": 2, "operation": operation, "payload": payload}
        ) + b"\n" + body
        command = [
            self.config.ssh_executable,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout_seconds}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-p",
            str(self.config.port),
        ]
        if self.config.identity_file is not None:
            command.extend(("-i", self.config.identity_file))
        if self.config.host_key_alias is not None:
            command.extend(("-o", f"HostKeyAlias={self.config.host_key_alias}"))
        if self.config.known_hosts_file is not None:
            command.extend(
                ("-o", f"UserKnownHostsFile={self.config.known_hosts_file}")
            )
        command.extend(
            (
                self._destination(),
                f"{self.config.remote_helper} --config {self.config.remote_config}",
            )
        )
        if download_to is not None:
            header = bytearray()
            received = 0
            parsed = None
            last_report = 0.0
            with download_to.open("wb") as stream:
                def consume(raw):
                    nonlocal received, parsed, last_report
                    if parsed is None:
                        prefix, separator, body = raw.partition(b"\n")
                        header.extend(prefix)
                        if not separator:
                            return
                        parsed = json.loads(header)
                        raw = body
                    received += len(raw)
                    if received > download_limit:
                        raise RuntimeError("VM download exceeds inspection bound")
                    stream.write(raw)
                    now = time.monotonic()
                    if now - last_report >= .2:
                        self.download_progress(received)
                        last_report = now
                timeout = self.context.remaining_seconds() if self.context else self.config.operation_timeout_seconds
                timeout_kind = "collection_total" if self.context else "operation_total"
                if self.context and operation != "get_archive":
                    file_remaining = self.context.file_timeout_seconds - (time.monotonic() - started)
                    if file_remaining <= timeout:
                        timeout, timeout_kind = max(.001, file_remaining), "file_total"
                try:
                    completed = run_bounded(command, input=request, env=_transport_environment(self.config.ssh_executable),
                        timeout=timeout, idle_seconds=self.context.idle_timeout_seconds if self.context else None,
                        sink=consume, context=self.context, process_group=False, timeout_kind=timeout_kind)
                except subprocess.TimeoutExpired as error:
                    if self.diagnostic_root is not None:
                        preserve_log(self.diagnostic_root, "ssh-stderr", error.stderr or b"")
                    raise
                stream.flush(); os.fsync(stream.fileno())
            if self.diagnostic_root is not None and completed.stderr:
                preserve_log(self.diagnostic_root, "ssh-stderr", completed.stderr)
            if completed.returncode:
                raise subprocess.CalledProcessError(completed.returncode, command, stderr=completed.stderr)
            if parsed is None:
                raise RuntimeError("VM download returned no header")
            self.download_progress(received)
            return parsed, b""
        from contextlib import nullcontext
        with (upload_path.open("rb") if upload_path is not None else nullcontext(None)) as upload:
            completed = self._run(command, input=request, input_stream=upload,
                env=_transport_environment(self.config.ssh_executable), timeout=self.config.operation_timeout_seconds)
        if completed.stderr and self.diagnostic_root is not None:
            preserve_log(self.diagnostic_root, "ssh-stderr", completed.stderr)
        if completed.returncode != 0:
            if self.diagnostic_root is not None:
                preserve_log(self.diagnostic_root, "ssh-stdout", completed.stdout)
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"VM transport command failed{suffix}")
        header, separator, trailing = completed.stdout.partition(b"\n")
        if not separator:
            raise RuntimeError("VM transport returned no header")
        response = json.loads(header)
        if not isinstance(response, dict):
            raise RuntimeError("VM transport response is not an object")
        return response, trailing

    def _destination(self) -> str:
        if not self.config.destination_resolver:
            return self.config.destination
        completed = self._run(
            list(self.config.destination_resolver),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_transport_environment(self.config.destination_resolver[0]),
            timeout=self.config.operation_timeout_seconds,
            check=False,
        )
        if completed.stderr and self.diagnostic_root is not None:
            preserve_log(self.diagnostic_root, "ssh-stderr", completed.stderr)
        if completed.returncode != 0:
            if self.diagnostic_root is not None:
                preserve_log(self.diagnostic_root, "ssh-stdout", completed.stdout)
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"VM destination resolver failed with exit code "
                f"{completed.returncode}{suffix}"
            )
        try:
            lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError as error:
            raise RuntimeError("VM destination resolver returned invalid text") from error
        if len(lines) != 1 or lines[0].strip() != lines[0]:
            raise RuntimeError("VM destination resolver returned an invalid address")
        try:
            address = str(ipaddress.ip_address(lines[0]))
        except ValueError as error:
            raise RuntimeError("VM destination resolver returned an invalid address") from error
        user, separator, _ = self.config.destination.rpartition("@")
        return f"{user}@{address}" if separator else address


class SSHTCADTransport:
    def __init__(
        self,
        remote: RemoteClient,
        *,
        local_result_root: Path | str,
        remote_exchange_root: Path | str = "/var/lib/scidiscovery/execution-exchange",
    ) -> None:
        self.remote = remote
        self.local_result_root = Path(local_result_root).expanduser().absolute()
        self.local_result_root.mkdir(parents=True, exist_ok=True, mode=0o770)
        self.remote_exchange_root = Path(remote_exchange_root)
        if not self.remote_exchange_root.is_absolute():
            raise ValueError("remote exchange root must be absolute")

    def handle(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        context = None
        if payload.get("collection"):
            context = CollectionContext(**payload["collection"])
        elif payload.get("deadline_monotonic") is not None:
            deadline = payload["deadline_monotonic"]
            context = CollectionContext(deadline, deadline)
        if isinstance(self.remote, SSHRemoteClient):
            self.remote.context = context
        if context and payload.get("progress_path"):
            path = Path(payload["progress_path"])
            context.report_progress = lambda value: atomic_json(path, value)
        if operation == "submission_protocol":
            return self._rpc("tcad_submission_protocol", {})
        if operation == "execution_policy":
            return self._rpc("tcad_execution_policy", {})
        if operation == "capabilities":
            if payload:
                raise ValueError("capability discovery accepts no payload")
            value = self._rpc("tcad_capabilities", {})
            snapshots = tuple(
                SolverCapabilitySnapshot.model_validate_json(
                    _canonical(item), strict=True
                )
                for item in value.get("capabilities", ())
            )
            if not snapshots:
                raise RuntimeError("remote TCAD runner returned no capabilities")
            profiles = tuple(item.profile_id for item in snapshots)
            if len(profiles) != len(set(profiles)):
                raise RuntimeError("remote TCAD capability profiles are not unique")
            return {
                "capabilities": [item.model_dump(mode="json") for item in snapshots]
            }
        if operation == "prepare":
            return self._prepare(payload)
        if operation in {"lookup_submission", "retire_submission"}:
            marker = self._read_marker(payload["submission"])
            value = self._rpc(
                "tcad_" + operation,
                {
                    "submission_id": marker["submission_id"]
                },
            )
            if value.get("found") is False:
                if operation == "retire_submission":
                    if value.get("retired") is not True or value.get("submission_id") != marker["submission_id"]:
                        raise RuntimeError("runner did not confirm retirement of the exact submission")
                    return {"found": False, "retired": True, "submission_id": marker["submission_id"]}
                return {"found": False}
            return {
                "found": True,
                "run_id": value["run_id"],
                "state": value["state"],
            }
        if operation in {"submit", "submit_retirable"}:
            marker = self._read_marker(payload["submission"])
            value = self._rpc("tcad_" + operation, {"submission": marker["remote_submission"], "authorization": payload.get("authorization")})
            return {"run_id": value["run_id"], "state": value["state"]}
        if operation == "status":
            value = self._rpc("tcad_status", {"run_id": payload["run_id"]})
            return {key: value[key] for key in ("state", "progress", "consumed_budget") if key in value}
        if operation == "cancel":
            value = self._rpc("tcad_cancel", {"run_id": payload["run_id"]})
            return {"state": value["state"]}
        if operation == "inspect_outputs":
            return self._inspect_outputs(str(payload["run_id"]), payload.get("relative_path"), payload.get("max_bytes", 32*1024*1024))
        if operation == "collect":
            destination=Path(payload.get('local_result_root',self.local_result_root))
            if not destination.is_absolute() or destination.resolve()!=destination:
                raise ValueError('collection requires its exact output directory')
            return self._collect(str(payload["run_id"]), context=context,
                output_directory=None if destination==self.local_result_root else destination)
        raise ValueError("unsupported TCAD transport operation")

    def _prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_descriptor = FileDescriptor.model_validate(payload["job_spec"], strict=True)
        archive_descriptor = FileDescriptor.model_validate(payload["archive"], strict=True)
        job_raw = _read_descriptor(job_descriptor)
        job = TCADJobSpec.model_validate_json(job_raw, strict=True)
        if job.input_archive != archive_descriptor:
            raise ValueError("job archive binding differs from prepare archive")
        request_id = job.request_id
        prefix = f"transport/{request_id}"
        remote_archive_path = str(self.remote_exchange_root / prefix / "project.tar")
        self.remote.put_file(f"{prefix}/project.tar", archive_descriptor)
        remote_archive = job.input_archive.model_copy(
            update={"local_path": remote_archive_path}
        )
        remote_job = job.model_copy(update={"input_archive": remote_archive})
        remote_job_raw = _canonical(remote_job.model_dump(mode="python"))
        self.remote.put(f"{prefix}/job.json", remote_job_raw)
        remote_submission = FileDescriptor(
            name="execution_payload",
            local_path=str(self.remote_exchange_root / prefix / "job.json"),
            size_bytes=len(remote_job_raw),
            media_type="application/json",
        )
        marker_raw = _canonical(
            {
                "schema_version": 2,
                "submission_id":request_id,
                "remote_submission": remote_submission.model_dump(mode="json"),
            }
        )
        marker_path = self.local_result_root / "submissions" / f"{request_id}.json"
        _write_immutable(marker_path, marker_raw)
        return {
            "submission": _local_descriptor(
                "execution_payload", marker_path, "application/json"
            ).model_dump(mode="json")
        }

    def _read_marker(self, value: dict[str, Any]) -> dict[str, Any]:
        descriptor = LocalFileDescriptor.model_validate(value, strict=True)
        path = Path(descriptor.local_path).absolute()
        if not _within(path, self.local_result_root):
            raise ValueError("transport submission marker is outside result root")
        raw = _read_local_descriptor(descriptor)
        marker = json.loads(raw)
        if (marker.get("schema_version") != 2
                or not isinstance(marker.get("submission_id"),str)
                or re.fullmatch(r"[0-9a-f]{32}",marker["submission_id"]) is None):
            raise ValueError("transport submission marker is invalid")
        remote=FileDescriptor.model_validate(marker.get("remote_submission"), strict=True)
        expected=self.remote_exchange_root/"transport"/marker["submission_id"]/"job.json"
        if remote.local_path!=str(expected):raise ValueError("submission marker changed its exact request owner")
        return marker

    def _inspect_outputs(self, run_id: str, relative_path: str | None, max_bytes: int) -> dict[str, Any]:
        if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise ValueError("invalid TCAD run identity")
        value = self._rpc("tcad_inspect_outputs", {"run_id": run_id, "relative_path": relative_path, "max_bytes": max_bytes})
        if value.get("status") != "available" or "file" not in value:
            return value
        descriptor = FileDescriptor.model_validate(value["file"], strict=True)
        if descriptor.size_bytes > min(max_bytes, 32 * 1024 * 1024):
            raise ValueError("inspection file exceeds limit")
        path = self.local_result_root / "inspection" / run_id / uuid.uuid4().hex
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent) as directory:
            temporary = Path(directory) / "download"
            self.remote.get_to(descriptor.local_path, temporary, min(max_bytes, 32 * 1024 * 1024))
            observed = _local_descriptor("candidate", temporary, descriptor.media_type)
            if observed.size_bytes != descriptor.size_bytes:
                return {"status": "changed_since_inspection", "reason": "download_changed"}
            os.chmod(temporary, 0o400)
            if path.exists():
                existing = _local_descriptor("candidate", path, descriptor.media_type)
                if existing.size_bytes != descriptor.size_bytes:
                    raise RuntimeError("immutable inspection copy changed")
            else:
                os.link(temporary, path)
        return {**value, "file": _local_descriptor("candidate", path, descriptor.media_type).model_dump(mode="json")}

    def _collect(self, run_id: str, *, context=None,output_directory=None) -> dict[str, Any]:
        if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
            raise ValueError("invalid TCAD run identity")
        private_directory = self.local_result_root / "runs" / run_id
        if private_directory.resolve()!=private_directory:
            raise ValueError("collection cache must not contain symlinks")
        private_directory.mkdir(parents=True, exist_ok=True)
        science_directory=None if output_directory is None else Path(output_directory)
        if science_directory is not None:
            if (not science_directory.is_absolute() or science_directory.resolve()!=science_directory
                    or science_directory==self.local_result_root
                    or science_directory.is_relative_to(self.local_result_root)):
                raise ValueError('scientific collection requires its exact separate output directory')
            if isinstance(self.remote,SSHRemoteClient) and self.remote.diagnostic_root is None:
                raise ValueError('scientific collection requires the declared private transfer spool')
            science_directory.mkdir(parents=True,exist_ok=True)
        # The control collection lock owns this execution's single writer. A
        # forced process kill cannot run finally; discard only its private temps.
        for stale in private_directory.glob(".*.download"):
            if context: context.remaining_seconds()
            stale.unlink()
        if context:
            context.report_progress({"phase": "inspect_manifest"})
        value = self._rpc("tcad_collect", {"run_id": run_id})
        descriptors=[FileDescriptor.model_validate(item,strict=True) for item in value['outputs']]
        if len({item.name for item in descriptors})!=len(descriptors):
            raise RuntimeError('remote result names are not unique')
        manifest=None
        manifest_path=None
        if science_directory is not None:
            from .project_packager import TCADRuntimeManifest
            manifests=[item for item in descriptors if item.name=='tcad_manifest']
            if len(manifests)!=1:
                raise RuntimeError('scientific collection requires one native manifest')
            descriptor=manifests[0]
            transfer_limit=getattr(getattr(self.remote,'config',None),'max_transfer_bytes',None)
            if transfer_limit is not None and descriptor.size_bytes>transfer_limit:
                raise ValueError('native manifest exceeds configured transfer bound')
            manifest_path=private_directory/'tcad_manifest'
            if manifest_path.is_symlink():
                raise ValueError('native manifest cache must not be a symlink')
            temporary=manifest_path.with_name(manifest_path.name+'.download')
            if temporary.is_symlink():raise ValueError('native manifest temporary must not be a symlink')
            try:
                if context:context.remaining_seconds()
                self.remote.get_to(descriptor.local_path,temporary,descriptor.size_bytes)
                if not temporary.is_file() or temporary.stat().st_size!=descriptor.size_bytes:
                    raise RuntimeError('downloaded native manifest differs from remote descriptor')
                os.replace(temporary,manifest_path)
            finally:
                temporary.unlink(missing_ok=True)
            manifest=TCADRuntimeManifest.model_validate_json(manifest_path.read_bytes(),strict=True)
            if {item.name for item in descriptors}!={item.name for item in manifest.outputs}|{'tcad_manifest','tcad_log'}:
                raise RuntimeError('remote results differ from native output inventory')
            records={item.name:item for item in manifest.outputs}
        outputs: list[dict[str, Any]] = []
        seen: set[str] = set()
        completed_bytes = 0
        total_bytes = sum(item["size_bytes"] for item in value["outputs"])
        def progress(**extra):
            if context is not None:
                context.report_progress({"completed_files": len(outputs), "completed_bytes": completed_bytes,
                    "total_files": len(value["outputs"]), "total_bytes": total_bytes, **extra})
        pending = []
        ordered = []
        local_names = set()
        def completed(descriptor, path, *, reused=False):
            nonlocal completed_bytes
            outputs.append({**descriptor.model_dump(mode="json"), "local_path": str(path)})
            completed_bytes += descriptor.size_bytes
            progress(phase="verify_cache" if reused else "extract_archive", current_file=None, current_file_bytes=0, reused=reused)

        def matches(candidate, descriptor):
            if not candidate.is_file() or candidate.stat().st_size != descriptor.size_bytes:
                return False
            return True

        for remote_descriptor in descriptors:
            if context is not None:
                context.remaining_seconds()
            if remote_descriptor.name in seen:
                raise RuntimeError("remote result names are not unique")
            seen.add(remote_descriptor.name)
            name = (remote_descriptor.name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote_descriptor.name)
                else "output_" + str(len(seen)))
            if name in local_names:
                raise RuntimeError("remote result names map to the same cache file")
            local_names.add(name)
            parent=private_directory
            if manifest is not None:
                record=records.get(remote_descriptor.name)
                if (record is not None and (record.size_bytes,record.media_type)
                        !=(remote_descriptor.size_bytes,remote_descriptor.media_type)):
                    raise RuntimeError('remote result differs from native manifest')
                if remote_descriptor.name!='tcad_manifest' and (record is None or record.output_class!='transport_derived'):
                    parent=science_directory
            path = parent / name
            if path.is_symlink() or parent.resolve()!=parent:
                raise ValueError("collection cache must not contain symlinks")
            ordered.append(remote_descriptor.name)
            if parent==science_directory and path.exists():
                raise RuntimeError('scientific output already exists without a saved collection')
            if matches(path, remote_descriptor):
                completed(remote_descriptor, path, reused=True)
            else:
                pending.append((remote_descriptor, path))
        progress(current_file=None, current_file_bytes=0)
        batch_download = getattr(self.remote, "get_many_to", None)
        if pending and callable(batch_download):
            batch_download(pending, on_complete=completed,
                on_progress=lambda count: progress(phase="download_archive", archive_bytes=count, current_file=None, current_file_bytes=0))
        else:
            for descriptor, path in pending:
                if context: context.remaining_seconds()
                temporary = path.with_name(path.name + ".download")
                if temporary.is_symlink():
                    raise ValueError("collection temporary file must not be a symlink")
                try:
                    self.remote.get_to(descriptor.local_path, temporary, descriptor.size_bytes)
                    if not matches(temporary, descriptor):
                        raise RuntimeError("downloaded result differs from remote descriptor")
                    if path.parent==science_directory and path.exists():
                        raise RuntimeError('scientific output appeared before completed transfer')
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                completed(descriptor, path)
        by_name = {item["name"]: item for item in outputs}
        outputs = [by_name[name] for name in ordered]
        return {"outputs": outputs}

    def _rpc(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        response = self.remote.rpc(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        if response.get("id") != request_id:
            raise RuntimeError("remote TCAD response identity differs")
        if "error" in response:
            if tool == "tcad_submission_protocol":
                raise RemoteRunnerUpgradeRequired("Remote runner cannot confirm submission/retirement protocol support; upgrade the runner before submitting.")
            if tool == "tcad_execution_policy" and response["error"].get("message") == "unknown runner tool":
                raise RemoteRunnerUpgradeRequired("Remote runner does not support execution policy discovery")
            raise RuntimeError(str(response["error"].get("message", "remote TCAD call failed")))
        try:
            value = response["result"]["structuredContent"]
        except (KeyError, TypeError) as error:
            raise RuntimeError("remote TCAD response has no structured result") from error
        if not isinstance(value, dict):
            raise RuntimeError("remote TCAD result is not an object")
        return value


def _read_descriptor(descriptor: FileDescriptor) -> bytes:
    path = Path(descriptor.local_path).absolute()
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("transport input is not a regular file")
    raw = path.read_bytes()
    if len(raw) != descriptor.size_bytes:
        raise ValueError("transport input differs from descriptor")
    return raw


def _read_local_descriptor(descriptor: LocalFileDescriptor) -> bytes:
    path = Path(descriptor.local_path).absolute()
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("local descriptor is not a regular file")
    raw = path.read_bytes()
    if len(raw) != descriptor.size_bytes:
        raise ValueError("local file differs from descriptor")
    return raw


def _write_immutable(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("existing local transport file differs")
        return
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o440)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _local_descriptor(name: str, path: Path, media_type: str) -> LocalFileDescriptor:
    metadata=path.lstat()
    if not stat.S_ISREG(metadata.st_mode):raise ValueError("local file is not regular")
    size=metadata.st_size
    return LocalFileDescriptor(
        name=name,
        local_path=str(path),
        size_bytes=size,
        media_type=media_type,
    )


def _require_ok(response: dict[str, Any], operation: str) -> dict[str, Any]:
    if response.get("schema_version") != 2 or response.get("operation") != operation:
        raise RuntimeError("VM transport response envelope differs")
    if response.get("ok") is not True or not isinstance(response.get("payload"), dict):
        raise RuntimeError("VM transport rejected operation")
    return response["payload"]


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _transport_environment(executable: str) -> dict[str, str]:
    environment = dict(os.environ)
    if executable.lower().endswith(".exe") and not environment.get("WSL_INTEROP"):
        candidates: list[tuple[int, Path]] = []
        for path in Path("/run/WSL").glob("*_interop"):
            try:
                metadata = os.stat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISSOCK(metadata.st_mode):
                candidates.append((metadata.st_mtime_ns, path))
        if not candidates:
            raise RuntimeError("no active WSL interop socket is available for ssh.exe")
        environment["WSL_INTEROP"] = str(max(candidates)[1])
    return environment


class SSHTransportConfigurationError(ValueError):
    """Local configuration failure, before any remote operation is attempted."""


class RemoteRunnerUpgradeRequired(RuntimeError):
    """The deployed remote protocol predates mandatory execution policy discovery."""


def read_transport_config(path: Path) -> SSHTCADTransportConfig:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("SSH transport configuration must be a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError("SSH transport configuration must not be group/world writable")
        from scidiscovery.plugin_configuration import read_configuration
        body = read_configuration(path)
        for field in ('ssh_executable', 'identity_file', 'known_hosts_file'):
            if isinstance(body.get(field), str):
                body[field] = str((path.absolute().parent / Path(body[field]).expanduser()).resolve())
        resolver = body.get('destination_resolver')
        if isinstance(resolver, list) and resolver and isinstance(resolver[0], str):
            resolver[0] = str((path.absolute().parent / Path(resolver[0]).expanduser()).resolve())
        return SSHTCADTransportConfig.model_validate_json(json.dumps(body), strict=True)
    except (OSError, ValueError) as error:
        raise SSHTransportConfigurationError("TCAD SSH transport configuration is invalid") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scidiscovery-tcad-transport")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    operation = "unknown"
    try:
        raw = sys.stdin.buffer.read()
        if not raw:
            raise ValueError("transport request is missing")
        request = json.loads(raw)
        if request.get("schema_version") != 2 or not isinstance(request.get("payload"), dict):
            raise ValueError("transport request envelope is invalid")
        operation = str(request.get("operation"))
        config = read_transport_config(args.config)
        transport = SSHTCADTransport(
            SSHRemoteClient(config, diagnostic_root=Path(os.environ.get(
                "SCIDISCOVERY_TCAD_RESULT_ROOT", "/var/lib/scidiscovery/transport-results"
            ))),
            local_result_root=os.environ.get(
                "SCIDISCOVERY_TCAD_RESULT_ROOT", "/var/lib/scidiscovery/transport-results"
            ),
            remote_exchange_root=config.remote_exchange_root,
        )
        payload = transport.handle(operation, request["payload"])
        response = {"schema_version": 2, "operation": operation, "ok": True, "payload": payload}
    except Exception as error:
        response = {
            "schema_version": 2,
            "operation": operation,
            "ok": False,
            "error": str(error),
            "engineering": exception_facts(error, layer="ssh_transport", action=operation),
            "payload": {},
        }
        if isinstance(error, SSHTransportConfigurationError):
            response["error_code"] = "tcad_transport_configuration_invalid"
        elif isinstance(error, RemoteRunnerUpgradeRequired):
            response["error_code"] = "tcad_runner_upgrade_required"
    sys.stdout.buffer.write(_canonical(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RemoteClient",
    "SSHRemoteClient",
    "SSHTCADTransport",
    "SSHTCADTransportConfig",
    "read_transport_config",
    "main",
]
