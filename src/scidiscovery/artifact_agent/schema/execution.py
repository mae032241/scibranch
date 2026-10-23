"""Domain-neutral retained execution admission and file descriptor schemas."""
from __future__ import annotations
from typing import Annotated, Literal, Any
from pydantic import Field
from .artifact import MediaType
from .common import Identifier, SchemaModel, Sha256

class ExecutionAdmission(SchemaModel):
    """Domain adapter judgment over administrator configuration and exact budget."""

    outcome: Literal["policy", "require_human_approval", "deny"]
    policy_digest: Sha256
    policy: dict[str, Any]
    budget: dict[str, int]
    allowance: dict[str, int]
    per_execution_allowance: dict[str, int] = Field(default_factory=dict)
    outside_allowance: Literal["require_human_approval", "deny"]
    budget_key: Sha256
    reason: Annotated[str, Field(min_length=1, max_length=1024)]



class LocalFileDescriptor(SchemaModel):
    name: Identifier
    local_path: Annotated[str, Field(min_length=1, max_length=4096)]
    size_bytes: Annotated[int, Field(ge=0, le=2**50)]
    media_type: MediaType
