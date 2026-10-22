"""Backend-neutral capabilities exposed to one registered Operation tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, MutableMapping

from .schema.refs import ArtifactRef


@dataclass(frozen=True, slots=True)
class OperationToolContext:
    services: Mapping[str, object]
    state: MutableMapping[str, object]
    workspace: Path
    output_directory: Path
    output_collections: tuple[str, ...]
    remaining_seconds: int
    _read_input: Callable[[str], bytes]
    _input_path: Callable[[str], Path]
    _input_media_type: Callable[[str], str]
    _input_ref: Callable[[str], ArtifactRef]
    _validate_outputs: Callable[[], None]
    _record_activity: Callable[[str], None]
    _candidate_snapshot: Callable[[], tuple[str, ...]]

    run_id: str = ""
    operation_id: str = ""

    _read_evidence: Callable[[str], bytes] | None = None
    _accept_evidence: Callable[..., dict] | None = None
    _adopt_bound_evidence: Callable[..., list[dict]] | None = None
    _list_evidence: Callable[[], list[dict]] | None = None
    _list_attempts: Callable[[], list[dict]] | None = None
    _execution_scope: Callable[[str], dict] | None = None
    _recovery_authorized: Callable[[str], bool] | None = None
    _recovery_scope: Callable[[], tuple[str, tuple[str, ...], tuple[tuple[str, ArtifactRef], ...]]] | None = None
    _io_budget: Callable[..., dict] | None = None
    _source_read: Callable[[str], None] | None = None
    _source_descriptor: Callable[[str], object] | None = None
    _finish_attempt: Callable[..., tuple[dict, tuple[dict, ...]] | None] | None = None
    _prior_source_bindings: Callable[[], Mapping[str, str]] | None = None

    _input_names_for_port: Callable[[str], tuple[str, ...]] | None = None

    def input_names_for_port(self, port: str) -> tuple[str, ...]:
        return self._input_names_for_port(port) if self._input_names_for_port else ()

    _publish_files: Callable[..., dict] | None = None
    _capture_science: Callable[..., tuple] | None = None

    def capture_science(self, document, paths, declarations=()):
        if self._capture_science is None:
            return document, {}
        return self._capture_science(document, paths, declarations)

    def publish_files(self, request):
        if self._publish_files is None:
            raise ValueError("tool has no scientific attachment capability")
        return self._publish_files(request)

    _read_reference: Callable[..., dict] | None = None

    def read_reference(self, request):
        if self._read_reference is None:
            raise ValueError("tool has no reference access permission")
        return self._read_reference(request)

    _reserve_network_request: Callable[[str], None] | None = None

    def reserve_network_request(self, url: str) -> None:
        if self._reserve_network_request is None:
            raise ValueError("tool has no network permission")
        self._reserve_network_request(url)

    @property
    def prior_source_bindings(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._prior_source_bindings())) if self._prior_source_bindings else MappingProxyType({})

    def source_descriptor(self, name: str):
        if self._source_descriptor is None:
            raise ValueError("tool has no source descriptor projection")
        return self._source_descriptor(name)

    def complete_calculation(self, record, *, diagnostics=(), public_result=None) -> dict:
        from ..operations.analysis_artifacts import complete_calculation
        return complete_calculation(self, record, diagnostics=diagnostics, public_result=public_result)

    def publish_analysis_file(self, raw, *, media_type, kind, sources, suffix, metadata=None) -> dict:
        from ..operations.analysis_artifacts import publish_analysis_file
        return publish_analysis_file(self, raw, media_type=media_type, kind=kind,
            sources=sources, suffix=suffix, metadata=metadata)

    def publish_calculation_checkpoint(self, raw, *, algorithm_version, record_key, sources, numerical_identity):
        from ..operations.analysis_artifacts import publish_calculation_checkpoint
        return publish_calculation_checkpoint(self, raw, algorithm_version=algorithm_version,
            record_key=record_key, sources=sources, numerical_identity=numerical_identity)

    def read_calculation_checkpoint(self, alias, *, algorithm_version, tool_names, sources, max_bytes):
        from ..operations.analysis_artifacts import read_calculation_checkpoint
        return read_calculation_checkpoint(self, alias, algorithm_version=algorithm_version,
            tool_names=tool_names, sources=sources, max_bytes=max_bytes)

    def finish_attempt(self, **values) -> tuple[dict, tuple[dict, ...]] | None:
        return self._finish_attempt(**values) if self._finish_attempt else None

    def io_budget(self, **values):
        if self._io_budget is None:
            raise ValueError("tool has no evidence budget")
        return self._io_budget(**values)

    def tool_attempts(self) -> list[dict]:
        """Read controlled attempt receipts from this Run, including interrupted work."""
        return self._list_attempts() if self._list_attempts else []

    def evidence(self) -> list[dict]:
        from ..plugin_runtime.evidence import _record_view
        return [_record_view(record) for record in self._list_evidence()] if self._list_evidence else []

    def read_evidence(self, name: str) -> bytes:
        raw = self._read_evidence(name) if self._read_evidence else self.read_input(name)
        if self._source_read: self._source_read(name)
        return raw

    def accept_evidence(self, **values) -> dict:
        if self._accept_evidence is None:
            raise ValueError("tool has no evidence collection permission")
        return self._accept_evidence(**values)

    def adopt_bound_evidence(self, manifest_alias: str) -> list[dict]:
        """Reuse an exact bound completed tool family without rewriting its receipts."""
        if self._adopt_bound_evidence is None:
            raise ValueError("tool has no evidence collection permission")
        return self._adopt_bound_evidence(manifest_alias=manifest_alias)

    def execution_scope(self, alias: str) -> dict:
        if self._execution_scope is None:
            raise ValueError("tool has no execution inspection permission")
        return self._execution_scope(alias)

    def recovery_authorized(self, source_run_id: str) -> bool:
        return source_run_id == self.run_id or bool(self._recovery_authorized and self._recovery_authorized(source_run_id))

    def recovery_scope(self) -> tuple[str, tuple[str, ...], tuple[tuple[str, ArtifactRef], ...]]:
        """Sealed task origin, family identities and frozen input anchors."""
        return self._recovery_scope() if self._recovery_scope else (self.run_id, (self.run_id,), ())

    def __post_init__(self) -> None:
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))

    def require_service(self, name: str) -> object:
        try:
            return self.services[name]
        except KeyError as error:
            raise RuntimeError(
                f"registered Operation service is unavailable: {name}"
            ) from error

    def read_input(self, name: str) -> bytes:
        raw = self._read_input(name)
        if self._source_read: self._source_read(name)
        return raw

    def input_path(self, name: str) -> Path:
        return self._input_path(name)

    def input_media_type(self, name: str) -> str:
        return self._input_media_type(name)

    def input_ref(self, name: str) -> ArtifactRef:
        """Return the immutable scientific identity of one declared input."""

        return self._input_ref(name)

    def validate_outputs(self) -> None:
        self._validate_outputs()

    def record_activity(self, activity: str) -> None:
        self._record_activity(activity)

    def candidate_snapshot(self) -> tuple[str, ...]:
        return self._candidate_snapshot()


__all__ = ["OperationToolContext"]
