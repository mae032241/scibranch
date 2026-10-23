"""Administrator-owned execution policy; never accepted from Worker arguments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scidiscovery.artifact_agent.schema.common import canonical_sha256
from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class AgentExecutionPolicy(PolicyModel):
    enabled: bool
    max_storage_bytes: int = Field(ge=1)
    max_wall_time_seconds: int = Field(ge=1)
    max_total_wall_time_seconds: int = Field(ge=1)
    outside_limits: Literal["require_human_approval", "deny"]


class RunnerPolicy(PolicyModel):
    sample_interval_seconds: float = Field(gt=0)
    terminate_grace_seconds: float = Field(gt=0)
    transfer_chunk_bytes: int = Field(ge=1)
    max_transfer_bytes: int = Field(ge=1)
    max_preview_bytes: int = Field(ge=1)
    max_manifest_bytes: int = Field(ge=1)
    max_log_bytes: int = Field(ge=1)
    max_diagnostic_files: int = Field(ge=1)
    collection_total_seconds: float = Field(gt=0)
    collection_file_seconds: float = Field(gt=0)
    collection_idle_seconds: float = Field(gt=0)


class DebugModePolicy(PolicyModel):
    wall_time_seconds: int = Field(ge=1)
    max_output_bytes: int = Field(ge=1)


class DebugPolicy(PolicyModel):
    budget_scope: Literal["task", "scientific_subject"]
    preflight: DebugModePolicy
    smoke: DebugModePolicy
    initialization: DebugModePolicy
    max_runs: int = Field(ge=1)
    total_wall_seconds: int = Field(ge=1)
    max_memory_bytes: int = Field(ge=1)
    max_storage_bytes: int = Field(ge=1)
    max_output_file_bytes: int = Field(ge=1)
    max_output_files: int = Field(ge=1)
    max_log_bytes: int = Field(ge=1)
    max_manifest_bytes: int = Field(ge=1)
    max_response_log_chars: int = Field(ge=1)
    max_response_bytes: int = Field(ge=1)


class ExecutionPolicySnapshot(PolicyModel):
    agent_execution_policy: AgentExecutionPolicy
    runner: RunnerPolicy
    debug: DebugPolicy

    def admission(self, *, budget: dict[str, int], budget_key: str) -> ExecutionAdmission:
        policy = self.agent_execution_policy
        if any(type(value) is not int or value < 1 for value in budget.values()):
            raise ValueError("execution budget must contain positive integers")
        if set(budget) != {"max_storage_bytes", "wall_time_seconds"}:
            raise ValueError("execution budget is incomplete")
        if self.runner.max_transfer_bytes < budget["max_storage_bytes"]:
            from scidiscovery.operation_contract import ToolInputError
            raise ToolInputError("execution_storage_capability_exceeded",
                f"Runner transfer capability is {self.runner.max_transfer_bytes} bytes, smaller than the requested "
                f"storage budget {budget['max_storage_bytes']} bytes. Reduce resource_limits.max_storage_bytes "
                "and its dependent output budget, or ask the administrator to increase runner capacity before sealing the implementation.",
                path="$.resource_limits.max_storage_bytes")
        allowed = (policy.enabled
            and budget["max_storage_bytes"] <= policy.max_storage_bytes
            and budget["wall_time_seconds"] <= policy.max_wall_time_seconds)
        projection = self.model_dump(mode="json")
        return ExecutionAdmission(
            outcome="policy" if allowed else policy.outside_limits,
            policy_digest=canonical_sha256(projection), policy=projection,
            budget=budget, budget_key=budget_key,
            allowance={"max_storage_bytes": policy.max_storage_bytes,
                       "wall_time_seconds": policy.max_total_wall_time_seconds},
            per_execution_allowance={"max_storage_bytes": policy.max_storage_bytes,
                                     "wall_time_seconds": policy.max_wall_time_seconds},
            outside_allowance=policy.outside_limits,
            reason="within_configured_limits" if allowed else "autonomy_disabled_or_budget_exceeded",
        )


def execution_admission(snapshot: ExecutionPolicySnapshot, reviewed) -> ExecutionAdmission:
    limits = reviewed.project.resource_limits
    # This request fingerprint is replaced by the control-derived scientific
    # budget owner before authorization. It never grants a fresh revision pool.
    return snapshot.admission(
        budget={"max_storage_bytes": limits.max_storage_bytes,
                "wall_time_seconds": limits.wall_time_seconds},
        budget_key=canonical_sha256({"kind":"unbound_execution_budget",
            "tool_profile":reviewed.project.tool_profile,
            "max_storage_bytes":limits.max_storage_bytes,"wall_time_seconds":limits.wall_time_seconds}),
    )


def collection_context(context, policy: RunnerPolicy):
    from dataclasses import replace
    import time
    return replace(context,
        deadline_monotonic=min(context.deadline_monotonic, time.monotonic() + policy.collection_total_seconds),
        file_timeout_seconds=min(context.file_timeout_seconds, policy.collection_file_seconds),
        idle_timeout_seconds=min(context.idle_timeout_seconds, policy.collection_idle_seconds))
