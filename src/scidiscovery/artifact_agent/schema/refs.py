"""Content-bound reference schemas."""

from __future__ import annotations

from .common import Identifier, SchemaModel


class ArtifactRef(SchemaModel):
    """Complete identity required to resolve one immutable artifact payload."""

    artifact_id: Identifier
    kind: Identifier
    schema_id: Identifier
