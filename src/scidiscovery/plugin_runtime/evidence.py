"""Read detached records from an already bound control evidence manifest."""
from typing import Any

from ..artifact_agent.schema.tool_evidence import ToolEvidenceManifest as _Manifest

__all__ = ["read_evidence_records"]
_RECORD_FIELDS = ("alias", "artifact_ref", "source_ref", "media_type", "size_bytes", "metadata", "tool_name")


def read_evidence_records(raw: bytes) -> tuple[dict[str, Any], ...]:
    """Validate manifest wire shape and return its bounded record projection.

    Parsing does not authenticate the bytes or authorize their use. The caller
    must obtain the exact manifest through its compiled input/tool context and
    validate the domain's source/file relationships. Attempts, recovery proof and
    input bindings stay control-owned and are not part of this plugin view.
    Each record exposes alias, artifact_ref, source_ref, media_type, size_bytes,
    tool_name and domain metadata only. Domain metadata can contain source-bound
    restoration data, so this is a trusted component API, not an Agent projection.
    Returned dictionaries are detached values, never mutable service state.
    """
    manifest = _Manifest.model_validate_json(raw, strict=True)
    return tuple(_record_view(record) for record in manifest.records)


def _record_view(record):
    """Project shared record fields, excluding core-owned calculation proof metadata."""
    value = {key: record[key] for key in _RECORD_FIELDS if key in record}
    value["metadata"] = {key: item for key, item in record.get("metadata", {}).items()
                         if key not in {"calculation_proof", "calculation_public_sha256", "checkpoint_proof"}}
    return value
