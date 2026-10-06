"""Narrow task-private workspace hooks used by operation plugins.

The control plane owns the task root and file lifecycle.  Plugins receive only
that root, Run-local paths, and frozen source bytes for private validation. They
never receive ArtifactService, RunService, database, or repository handles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from .spec import CompiledOperation
from .input_validation import InputBindingDescriptor


WORKSPACE_HOOK_KINDS = frozenset(
    {
        "workspace_materializer",
        "workspace_file_policy",
        "workspace_finalizer",
        "workspace_snapshotter",
    }
)


class WorkspaceProtocolError(ValueError):
    """A plugin workspace rejected task-local bytes or paths."""

    def __init__(
        self, message: str, *, details: tuple[dict[str, str], ...] = ()
    ) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationRequest:
    operation_id: str
    workspace: Path
    input_paths: Mapping[str, Path]
    provisional_roots: tuple[Path, ...]
    edit_protocol: Literal["mcp", "native"] = "mcp"
    binding_descriptors: Mapping[str, InputBindingDescriptor] = field(default_factory=dict)
    # Control-only sources, including private proof; never materialize for Agents.
    input_contents: Mapping[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_paths", MappingProxyType(dict(self.input_paths))
        )
        object.__setattr__(self, "binding_descriptors", MappingProxyType(dict(self.binding_descriptors)))
        object.__setattr__(self, "input_contents", MappingProxyType(dict(self.input_contents)))


@dataclass(frozen=True, slots=True)
class WorkspaceMaterializationResult:
    manifest_name: str
    manifest: Mapping[str, Any]
    paths: Mapping[str, Any]
    read_paths: tuple[str, ...]
    patch_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", MappingProxyType(dict(self.manifest)))
        object.__setattr__(self, "paths", MappingProxyType(dict(self.paths)))
        object.__setattr__(
            self, "patch_contract", MappingProxyType(dict(self.patch_contract))
        )


@dataclass(frozen=True, slots=True)
class WorkspaceFileRequest:
    operation_id: str
    workspace: Path
    relative_path: Path


@dataclass(frozen=True, slots=True)
class WorkspaceFileRule:
    max_bytes: int
    text_required: bool = True
    removable: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceFinalizationRequest:
    operation_id: str
    workspace: Path
    input_paths: Mapping[str, Path]
    output_limit_bytes: int
    final_submission: bool = True
    output_schema_id: str = ""
    run_id: str = ""
    trusted_tool_records: Mapping[str, tuple[bytes, ...]] = field(default_factory=dict)
    binding_descriptors: Mapping[str, InputBindingDescriptor] = field(default_factory=dict)
    # Control-only sources, including private proof; never materialize for Agents.
    input_contents: Mapping[str, bytes] = field(default_factory=dict)
    sealed_material_resolver: Callable | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_paths", MappingProxyType(dict(self.input_paths))
        )
        object.__setattr__(self, "binding_descriptors", MappingProxyType(dict(self.binding_descriptors)))
        object.__setattr__(self, "input_contents", MappingProxyType(dict(self.input_contents)))
        object.__setattr__(self, "trusted_tool_records", MappingProxyType({
            key: tuple(value) for key, value in self.trusted_tool_records.items()}))


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshotFile:
    relative_path: str
    media_type: str
    content: bytes


WorkspaceMaterializer = Callable[
    [WorkspaceMaterializationRequest], WorkspaceMaterializationResult
]
WorkspaceFilePolicy = Callable[[WorkspaceFileRequest], WorkspaceFileRule | None]
@dataclass(frozen=True, slots=True)
class WorkspaceFinalizer:
    """One hook owns both its draft contract and deterministic completion.

    Plain callable finalizers remain valid and keep the complete output schema.
    Projection versions identify behavior in the compiled operation digest.
    """
    implementation: Callable[[WorkspaceFinalizationRequest], bytes]
    draft_schema: Callable[[dict, str, Mapping[str, str] | None], dict]
    projection_version: str

    def __post_init__(self) -> None:
        if not callable(self.implementation) or not callable(self.draft_schema) or not self.projection_version:
            raise ValueError("finalizer projection requires both implementations and a version")

    def __call__(self, request: WorkspaceFinalizationRequest) -> bytes:
        return self.implementation(request)

WorkspaceSnapshotter = Callable[[Path], tuple[WorkspaceSnapshotFile, ...]]


def operation_workspace_hooks(
    compiled: CompiledOperation,
) -> Mapping[str, Callable[..., Any]]:
    """Resolve the hook dependencies of this operation's workspace component."""

    template = compiled.permission_template
    if template is None:
        return MappingProxyType({})
    workspace = compiled.component_specs[template.workspace]
    hooks: dict[str, Callable[..., Any]] = {}
    for reference in workspace.resources:
        key = f"{reference.plugin_id or compiled.plugin_id}:{reference.component_id}"
        component = compiled.component_specs[key]
        if component.kind not in WORKSPACE_HOOK_KINDS:
            continue
        hooks[component.kind] = compiled.implementations[key]
    return MappingProxyType(hooks)


__all__ = [
    "WORKSPACE_HOOK_KINDS",
    "WorkspaceFileRequest",
    "WorkspaceFileRule",
    "WorkspaceFinalizationRequest",
    "WorkspaceFinalizer",
    "WorkspaceMaterializationRequest",
    "WorkspaceMaterializationResult",
    "WorkspaceProtocolError",
    "WorkspaceSnapshotFile",
    "operation_workspace_hooks",
]
