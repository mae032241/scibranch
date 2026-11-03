"""Trusted-local workspace transport for one compiled Operation Run.

This module owns files only.  It does not validate scientific payloads, write
Artifact records, choose reviewers, or decide Run terminal state.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol
from ...plugin_runtime.workspace import (WorkspaceError, WorkspaceOutputError,
    write_control_workspace_file, read_control_workspace_file, remove_control_workspace_file,
    move_control_workspace_file, _open_workspace_parent,
    validate_publication_content as _validate_publication_content, media_type_for_path as _media_type)


@dataclass(frozen=True, slots=True)
class WorkspaceInput:
    name: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class SealedFile:
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SealedWorkspace:
    backend: str
    backend_version: str
    run_id: str
    digest: str
    root: Path
    files: tuple[SealedFile, ...]


@dataclass(frozen=True, slots=True)
class RecoveryDraft:
    backend: str
    backend_version: str
    digest: str
    root: Path
    files: tuple[SealedFile, ...]


@dataclass(frozen=True, slots=True)
class OpenWorkspace:
    root: Path
    assignment_path: Path
    output_directory: Path
    input_paths: Mapping[str, Path]
    domain_workspace_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_paths", MappingProxyType(dict(self.input_paths))
        )


class WorkspaceBackend(Protocol):
    backend_id: str
    backend_version: str
    capabilities: tuple[str, ...]

    def supports_operation(self, compiled: object) -> bool: ...
    def unsupported_requirements(self, compiled: object) -> tuple[str, ...]: ...
    def assignment_tool_names(self, compiled: object) -> tuple[str, ...]: ...
    def prepare(self, **values: object) -> OpenWorkspace: ...
    def open(self, run_id: str) -> OpenWorkspace: ...
    def heartbeat(self, run_id: str) -> None: ...
    def seal(self, run_id: str, **values: object) -> SealedWorkspace: ...
    def discard(self, run_id: str, **values: object) -> RecoveryDraft | None: ...


class LocalTrustedBackend:
    """Small, prompt-constrained backend for trusted local development."""

    backend_id = "local_trusted"
    backend_version = "2"
    edit_protocol = "native"
    capabilities = ("native_workspace", "immutable_candidate", "failure_isolation")

    @staticmethod
    def supports_operation(compiled: object) -> bool:
        """Local is explicitly the trusted, prompt-constrained prototype."""

        return not LocalTrustedBackend.unsupported_requirements(compiled)

    @staticmethod
    def unsupported_requirements(compiled: object) -> tuple[str, ...]:
        from ...operations.tooling import tool_evidence_ports
        if tool_evidence_ports(compiled):
            return ()
        return (
            ("agent_collection_outputs",)
            if any(port.collection is not None for port in compiled.spec.outputs)
            else ()
        )

    @staticmethod
    def assignment_tool_names(compiled: object) -> tuple[str, ...]:
        from ...operations.tooling import operation_local_worker_tool_names

        return operation_local_worker_tool_names(compiled)

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
            raise WorkspaceError("local workspace root must be a directory")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in (".bindings", "workspaces", "recovery", "quarantine"):
            (self.root / name).mkdir(mode=0o700, exist_ok=True)

    def prepare(
        self,
        *,
        run_id: str,
        inputs: tuple[WorkspaceInput, ...],
        assignment: bytes,
        result_schema: bytes,
        recovery_digest: str | None = None,
        initial_output: bytes | None = None,
    ) -> OpenWorkspace:
        binding = self._binding_path(run_id)
        if binding.exists():
            raise WorkspaceError("Run workspace already exists")
        workspace_name = "workspace_" + hashlib.sha256(run_id.encode()).hexdigest()[:32]
        run_root = self.root / "workspaces" / workspace_name
        input_root = run_root / "inputs"
        schema_root = run_root / "schema"
        output_root = run_root / "output"
        candidate_root = run_root / "candidates"
        for path in (input_root, schema_root, output_root, candidate_root):
            path.mkdir(parents=True, mode=0o700)
        input_paths: dict[str, Path] = {}
        for item in inputs:
            filename = workspace_input_filename(item.name, item.media_type)
            path = input_root / filename
            _write_new(path, item.content, mode=0o400)
            input_paths[item.name] = path
        assignment_path = run_root / "assignment.json"
        _write_new(assignment_path, assignment, mode=0o400)
        _write_new(schema_root / "result.schema.json", result_schema, mode=0o400)
        from . import input_reader, output_schema_reader, tool_contract_reader
        tool_root = run_root / "tools"
        tool_root.mkdir(mode=0o700)
        _write_new(tool_root / "read_tool_contract.py",
                   Path(tool_contract_reader.__file__).read_bytes(), mode=0o400)
        _write_new(tool_root / "read_output_schema.py",
                   Path(output_schema_reader.__file__).read_bytes(), mode=0o400)
        _write_new(tool_root / "read_input.py",
                   Path(input_reader.__file__).read_bytes(), mode=0o400)
        if initial_output is not None:
            _write_new(output_root / "result.json", initial_output, mode=0o600)
        if recovery_digest is not None:
            if re.fullmatch(r"[0-9a-f]{64}", recovery_digest) is None:
                raise WorkspaceError("recovery digest is invalid")
            recovery = self.root / "recovery" / recovery_digest
            if not recovery.is_dir() or recovery.is_symlink():
                raise WorkspaceError("recovery draft is unavailable")
            recovery_root = run_root / "recovery-draft"
            _copy_read_only_tree(recovery, recovery_root)
        try:
            _write_new(binding, workspace_name.encode("ascii"), mode=0o600)
        except Exception:
            shutil.rmtree(run_root, ignore_errors=True)
            raise
        return OpenWorkspace(
            root=run_root,
            assignment_path=assignment_path,
            output_directory=output_root,
            input_paths=input_paths,
        )

    def discard_preparation(self, run_id: str) -> None:
        """Reset an interrupted, control-fenced preparation before Worker open."""
        binding = self._binding_path(run_id)
        root = (self._run_root(run_id) if binding.exists() else
            self.root / 'workspaces' / ('workspace_' + hashlib.sha256(run_id.encode()).hexdigest()[:32]))
        if root.is_symlink():
            raise WorkspaceError("preparation workspace is unsafe")
        if root.exists():
            for directory in (root, *root.rglob('*')):
                if not directory.is_symlink() and directory.is_dir():
                    directory.chmod(directory.stat().st_mode | 0o700)
            shutil.rmtree(root, ignore_errors=False)
        binding.unlink(missing_ok=True)

    def open(self, run_id: str) -> OpenWorkspace:
        root = self._run_root(run_id)
        assignment = root / "assignment.json"
        output = root / "output"
        if (
            root.is_symlink()
            or not assignment.is_file()
            or assignment.is_symlink()
            or not output.is_dir()
            or output.is_symlink()
        ):
            raise WorkspaceError("Run workspace is unavailable")
        input_paths = {
            path.stem: path
            for path in sorted((root / "inputs").iterdir())
            if path.is_file() and not path.is_symlink()
        }
        return OpenWorkspace(
            root=root,
            assignment_path=assignment,
            output_directory=output,
            input_paths=input_paths,
            domain_workspace_path=(
                root / "domain-workspace.json"
                if (root / "domain-workspace.json").is_file()
                and not (root / "domain-workspace.json").is_symlink()
                else None
            ),
        )

    def write_domain_workspace(self, run_id: str, content: bytes) -> Path:
        """Publish one control-built, read-only domain workspace manifest."""

        workspace = self.open(run_id)
        return write_control_workspace_file(
            workspace.root,
            Path("domain-workspace.json"),
            content,
            replace=False,
            mode=0o400,
        )

    def write_primary_output(self, run_id: str, content: bytes) -> Path:
        """Publish the deterministic finalizer result, replacing only its prior result."""

        workspace = self.open(run_id)
        return write_control_workspace_file(
            workspace.root,
            Path("output/result.json"),
            content,
            replace=True,
            mode=0o600,
        )

    def heartbeat(self, run_id: str) -> None:
        self.open(run_id)

    def seal(
        self,
        run_id: str,
        *,
        max_files: int,
        max_bytes: int,
        expected_digest: str | None = None,
        snapshot: tuple[WorkspaceInput, ...] | None = None,
    ) -> SealedWorkspace:
        workspace = self.open(run_id)
        if expected_digest is not None:
            return self._sealed_candidate(
                workspace,
                run_id,
                expected_digest,
                max_files=max_files,
                max_bytes=max_bytes,
            )
        if snapshot is None:
            source_files = tuple(sorted(workspace.output_directory.rglob("*")))
            if any(path.is_symlink() for path in source_files):
                raise WorkspaceOutputError("output cannot contain symbolic links")
            if any(not path.is_file() and not path.is_dir() for path in source_files):
                raise WorkspaceOutputError("output contains a non-regular filesystem entry")
            regular = tuple(path for path in source_files if path.is_file())
            if not regular:
                raise WorkspaceOutputError("output is empty")
            if len(regular) > max_files:
                raise WorkspaceOutputError("output contains too many files")
            if sum(path.stat().st_size for path in regular) > max_bytes:
                raise WorkspaceOutputError("output exceeds the compiled byte limit")
            snapshot = tuple(WorkspaceInput(
                name=path.relative_to(workspace.output_directory).as_posix(),
                media_type=_media_type(path.name), content=_read_regular(path, max_bytes=max_bytes),
            ) for path in regular)
        if not snapshot or len(snapshot) > max_files or sum(len(item.content) for item in snapshot) > max_bytes:
            raise WorkspaceOutputError("candidate exceeds its file/byte limit or is empty")
        fingerprint = hashlib.sha256()
        contents: list[tuple[str, bytes, str]] = []
        names: set[str] = set()
        for item in sorted(snapshot, key=lambda item: item.name):
            relative, content = item.name, item.content
            path = Path(relative)
            if (not relative or path.is_absolute() or "\\" in relative
                    or any(token in {"", ".", ".."} for token in relative.split("/"))
                    or relative in names):
                raise WorkspaceOutputError("candidate path is invalid or duplicated")
            names.add(relative)
            _validate_publication_content(relative, content, item.media_type)
            digest = hashlib.sha256(content).hexdigest()
            fingerprint.update(relative.encode("utf-8") + b"\0" + digest.encode())
            contents.append((relative, content, digest))
        candidate = workspace.root / "candidates" / fingerprint.hexdigest()
        if not candidate.exists():
            candidate.mkdir(mode=0o700)
            for relative, content, _ in contents:
                target = candidate / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write_new(target, content, mode=0o400)
            for directory in sorted(
                (path for path in candidate.rglob("*") if path.is_dir()),
                reverse=True,
            ):
                directory.chmod(0o500)
            candidate.chmod(0o500)
        files = tuple(
            SealedFile(
                relative_path=relative,
                media_type=next(item.media_type for item in snapshot if item.name == relative),
                size_bytes=len(content),
                sha256=digest,
            )
            for relative, content, digest in contents
        )
        return SealedWorkspace(
            backend=self.backend_id,
            backend_version=self.backend_version,
            run_id=run_id,
            digest=fingerprint.hexdigest(),
            root=candidate,
            files=files,
        )

    def verify_recovery(self, digest: str, *, max_files: int, max_bytes: int) -> RecoveryDraft:
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise WorkspaceError("recovery digest is invalid")
        root = self.root / "recovery" / digest
        if not root.is_dir() or root.is_symlink():
            raise WorkspaceError("recovery draft is unavailable")
        files = _sealed_files(root, max_files=max_files, max_bytes=max_bytes, text_fallback=True)
        fingerprint = hashlib.sha256()
        for item in files:
            fingerprint.update(item.relative_path.encode("utf-8") + b"\0" + item.sha256.encode())
        if fingerprint.hexdigest() != digest:
            raise WorkspaceError("recovery draft no longer matches its digest")
        return RecoveryDraft(backend=self.backend_id, backend_version=self.backend_version,
                             digest=digest, root=root, files=files)

    def discard(
        self,
        run_id: str,
        *,
        preserve_digest: str | None = None,
        max_files: int = 1,
        max_bytes: int = 64 * 1024,
        retain_original: bool = False,
    ) -> RecoveryDraft | None:
        workspace_name = self._workspace_name(run_id)
        root = self.root / "workspaces" / workspace_name
        quarantine = self.root / "quarantine" / workspace_name
        if retain_original and root.exists():
            # Local native writers may still hold this cwd. Copy the immutable
            # candidate without renaming or deleting their original directory.
            quarantine = root
        elif root.exists():
            if quarantine.exists():
                raise WorkspaceError("Run has both active and isolated workspaces")
            os.replace(root, quarantine)
        elif not quarantine.exists():
            if preserve_digest is None:
                return None
            destination = self.root / "recovery" / preserve_digest
            if not destination.is_dir() or destination.is_symlink():
                raise WorkspaceError("isolated recovery candidate is unavailable")
            return self.verify_recovery(preserve_digest, max_files=max_files, max_bytes=max_bytes)
        draft: RecoveryDraft | None = None
        if preserve_digest is not None:
            source = quarantine / "candidates" / preserve_digest
            if not source.is_dir() or source.is_symlink():
                raise WorkspaceError("recovery candidate is unavailable")
            destination = self.root / "recovery" / preserve_digest
            if not destination.exists():
                staging = self.root / "recovery" / (
                    f".staging_{preserve_digest}_{uuid.uuid4().hex}"
                )
                try:
                    _copy_read_only_tree(source, staging)
                    try:
                        os.replace(staging, destination)
                    except OSError:
                        if not destination.is_dir():
                            raise
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
            files = _sealed_files(
                destination, max_files=max_files, max_bytes=max_bytes, text_fallback=True
            )
            draft = RecoveryDraft(
                backend=self.backend_id,
                backend_version=self.backend_version,
                digest=preserve_digest,
                root=destination,
                files=files,
            )
        if preserve_digest is not None:
            self.verify_recovery(preserve_digest, max_files=max_files, max_bytes=max_bytes)
        if retain_original:
            return draft
        # Controlled candidates and recovery inputs are read-only directories.
        # The verified recovery copy now permits removing this isolated tree.
        for directory in (quarantine, *quarantine.rglob("*")):
            if not directory.is_symlink() and directory.is_dir():
                directory.chmod(directory.stat().st_mode | 0o700)
        shutil.rmtree(quarantine)
        return draft

    def _sealed_candidate(
        self,
        workspace: OpenWorkspace,
        run_id: str,
        digest: str,
        *,
        max_files: int,
        max_bytes: int,
    ) -> SealedWorkspace:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise WorkspaceError("candidate digest is invalid")
        root = workspace.root / "candidates" / digest
        if not root.is_dir() or root.is_symlink():
            raise WorkspaceError("accepted candidate is unavailable")
        files = _sealed_files(root, max_files=max_files, max_bytes=max_bytes)
        fingerprint = hashlib.sha256()
        for item in files:
            fingerprint.update(
                item.relative_path.encode("utf-8") + b"\0" + item.sha256.encode()
            )
        if fingerprint.hexdigest() != digest:
            raise WorkspaceError("accepted candidate no longer matches its digest")
        return SealedWorkspace(
            backend=self.backend_id,
            backend_version=self.backend_version,
            run_id=run_id,
            digest=digest,
            root=root,
            files=files,
        )

    def _run_root(self, run_id: str) -> Path:
        return self.root / "workspaces" / self._workspace_name(run_id)

    def _workspace_name(self, run_id: str) -> str:
        binding = self._binding_path(run_id)
        try:
            workspace_name = _read_regular(binding, max_bytes=128).decode("ascii")
        except (OSError, UnicodeDecodeError) as error:
            raise WorkspaceError("Run workspace binding is unavailable") from error
        if re.fullmatch(r"workspace_[0-9a-f]{32}", workspace_name) is None:
            raise WorkspaceError("Run workspace binding is invalid")
        return workspace_name

    def exact_workspace_paths(self, run_id: str) -> tuple[Path, ...]:
        """Existing original/isolation paths from the immutable Run binding.

        This is identity evidence only, never proof that native writers stopped.
        """
        if (self.root / ".bindings").is_symlink():
            raise WorkspaceError("Run workspace binding directory is a symlink")
        name = self._workspace_name(run_id)
        paths = (self.root / "workspaces" / name, self.root / "quarantine" / name)
        for path in paths:
            if path.parent.is_symlink() or path.is_symlink():
                raise WorkspaceError("Run workspace identity is a symlink")
            if path.exists() and not path.is_dir():
                raise WorkspaceError("Run workspace identity is not a directory")
        return tuple(path for path in paths if path.exists())

    def _binding_path(self, run_id: str) -> Path:
        if not run_id.startswith("run_") or not run_id[4:].isalnum():
            raise WorkspaceError("Run identity is invalid")
        digest = hashlib.sha256(run_id.encode("ascii")).hexdigest()
        return self.root / ".bindings" / digest


def _write_new(path: Path, content: bytes, *, mode: int) -> None:
    if type(content) is not bytes:
        raise TypeError("workspace content must be bytes")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _read_regular(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
            raise WorkspaceError("sealed output file is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            value = stream.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(value) > max_bytes:
        raise WorkspaceError("sealed output file exceeds its byte limit")
    return value


def _sealed_files(
    root: Path, *, max_files: int, max_bytes: int, text_fallback: bool = False
) -> tuple[SealedFile, ...]:
    values: list[SealedFile] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise WorkspaceError("sealed candidate contains an invalid entry")
        if not path.is_file():
            continue
        if len(values) >= max_files:
            raise WorkspaceError("sealed candidate contains too many files")
        size = path.stat().st_size
        total += size
        if total > max_bytes:
            raise WorkspaceError("sealed candidate exceeds its byte limit")
        relative = path.relative_to(root).as_posix()
        content = _read_regular(path, max_bytes=min(size, max_bytes))
        media_type = _media_type(relative)
        if text_fallback and media_type == "application/octet-stream":
            media_type = "text/plain"
        _validate_publication_content(relative, content, media_type)
        values.append(
            SealedFile(
                relative_path=relative,
                media_type=media_type,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    if not values:
        raise WorkspaceError("sealed candidate is empty")
    return tuple(values)


def _copy_read_only_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, copy_function=shutil.copyfile)
    for path in sorted(destination.rglob("*"), reverse=True):
        path.chmod(0o500 if path.is_dir() else 0o400)
    destination.chmod(0o500)


def workspace_input_filename(name: str, media_type: str) -> str:
    safe = "".join(character if character.isalnum() or character in "_.-" else "_" for character in name)
    suffix = mimetypes.guess_extension(media_type.split(";", 1)[0].strip()) or ".bin"
    return safe if safe.lower().endswith(suffix.lower()) else safe + suffix


__all__ = [
    "LocalTrustedBackend",
    "OpenWorkspace",
    "RecoveryDraft",
    "SealedFile",
    "SealedWorkspace",
    "WorkspaceError",
    "WorkspaceInput",
    "WorkspaceBackend",
    "write_control_workspace_file",
    "workspace_input_filename",
]
