"""Execution preferences; independent of scientific contracts and qualifications."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["zh-CN", "en"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max", "ultra"]


def canonical_model_id(value: str | None) -> str | None:
    """Use the platform's case-insensitive model identity without changing punctuation."""
    return value.casefold() if value is not None else None

# These exact ALTER definitions also delimit old archive compatibility.
EXECUTION_SETTINGS_COLUMNS = {
    "scheduler_instances": {
        "agent_settings_json": ("TEXT DEFAULT NULL", None),
        "agent_settings_revision": ("INTEGER DEFAULT 0", 0),
        "agent_settings_updated_at": ("TEXT DEFAULT NULL", None),
    },
    "runs": {"execution_profile_json": ("TEXT DEFAULT NULL", None)},
}


class SettingsValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class OperationSettings(SettingsValue):
    model: str | None = Field(default=None, min_length=1, max_length=256)
    reasoning_effort: ReasoningEffort | None = None
    max_attempts: int | None = Field(default=None, ge=1)


class DefaultSettings(OperationSettings):
    narrative_language: Language | None = None


class HelperProfileSettings(SettingsValue):
    model: str | None = Field(default=None, min_length=1, max_length=256)
    reasoning_effort: ReasoningEffort | None = None
    max_calls: int | None = Field(default=None, ge=0, le=32)
    max_input_bytes: int | None = Field(default=None, ge=512, le=262144)


def packaged_helper_profiles() -> dict:
    source = Path(__file__).with_name("default_agent_settings.json")
    values = json.loads(source.read_text(encoding="utf-8"))["helpers"]["profiles"]
    return {name: HelperProfileSettings.model_validate(value) for name, value in values.items()}


class HelperSettings(SettingsValue):
    """Run tool admissions and frozen model choices, not native token hard limits."""
    default_profile: Literal["inherit", "lightweight"] = "inherit"
    profiles: dict[Literal["lightweight"], HelperProfileSettings] = Field(default_factory=packaged_helper_profiles)
    max_depth: Literal[0, 1] = 1
    max_active: Literal[0, 1] = 1
    max_calls: int = Field(default=4, ge=0, le=32)
    max_input_bytes: int = Field(default=32768, ge=512, le=262144)


def merge_helper_settings(base: dict, override: dict) -> dict:
    """Instance profile fields inherit global values; explicit empty profiles disables them."""
    result = {**base, **{key:value for key,value in override.items() if key != "profiles"}}
    if "profiles" in override:
        profiles = dict(base.get("profiles", {})) if override["profiles"] else {}
        for name, value in override["profiles"].items():
            profiles[name] = {**profiles.get(name, {}), **value}
        result["profiles"] = profiles
    return HelperSettings.model_validate(result).model_dump()


class ExecutionIOSettings(SettingsValue):
    """Transfer limits frozen per Run; separate from solver authorization and RAM."""
    max_export_bytes: int = Field(default=2_000_000_000, ge=1)
    read_page_bytes: int = Field(default=16384, ge=512, le=262144)
    collection_timeout_seconds: int = Field(default=600, ge=1)
    file_timeout_seconds: int = Field(default=120, ge=1)
    idle_timeout_seconds: int = Field(default=30, ge=1)
    submission_response_wait_seconds: float = Field(default=0.25, ge=0, le=2, allow_inf_nan=False)
    approval_wait_budget_seconds: int = Field(default=86400, ge=0)


class BudgetExtensionSettings(SettingsValue):
    """Administrator limits on new human time grants; historical decisions persist."""
    max_seconds_per_decision: int = Field(default=21600, ge=0, le=2**53-1)
    max_total_seconds_per_pool: int = Field(default=86400, ge=0, le=2**53-1)


class SchedulingSettings(SettingsValue):
    """Global queued/running task capacity, independent of Operation identity."""
    max_active_runs: int = Field(default=32, ge=1, le=4096)


class LifecycleSettings(SettingsValue):
    """Control daemon reconciliation; global settings, no model calls or submissions."""
    interval_seconds: int = Field(default=30, ge=1, le=3600)
    batch_size: int = Field(default=32, ge=1, le=256)
    terminal_observation_failure_limit: int = Field(default=3, ge=1, le=1000)


class MaterialInputSettings(SettingsValue):
    """Stored material budgets are separate from in-memory delivery thresholds."""
    max_item_bytes: int = Field(default=2_000_000_000, ge=1)
    max_total_bytes: int = Field(default=2_000_000_000, ge=1)
    inline_max_bytes: int = Field(default=1048576, ge=1)
    inline_total_bytes: int = Field(default=8388608, ge=1)
    transfer_chunk_bytes: int = Field(default=1048576, ge=1, le=8388608)


class AttachmentSettings(SettingsValue):
    """Streamed scientific deliverables, independent of prompt and solver budgets."""
    max_item_bytes: int = Field(default=2_000_000_000, ge=1)
    max_total_bytes: int = Field(default=2_000_000_000, ge=1)
    max_files: int = Field(default=64, ge=1, le=128)
    chunk_bytes: int = Field(default=1048576, ge=4096, le=8388608)


class PresentationSettings(SettingsValue):
    """Optional, serial post-seal copy editing; never scientific admission."""
    enabled: bool = True
    command: list[str] = Field(default_factory=lambda: ["codex"], min_length=1, max_length=16)
    codex_home: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    interval_seconds: int = Field(default=5, ge=1, le=3600)
    max_source_bytes: int = Field(default=1048576, ge=1024, le=8388608)
    max_input_bytes: int = Field(default=16384, ge=1024, le=262144)
    max_output_bytes: int = Field(default=32768, ge=1024, le=262144)
    memory_limit_bytes: int = Field(default=2147483648, ge=268435456)


class MCPReadSettings(SettingsValue):
    """Global JSON page budgets for Root task and scientific-object lists."""
    summary_max_bytes: int = Field(default=4096, ge=1024, le=262144)
    detail_max_bytes: int = Field(default=32768, ge=1024, le=1048576)


class DiagnosticsSettings(SettingsValue):
    """Retention of expendable engineering traces, never sealed scientific evidence."""
    max_records: int = Field(default=1024, ge=1)
    max_total_bytes: int = Field(default=67108864, ge=1024)
    retention_seconds: int = Field(default=604800, ge=1)


class AgentSettings(SettingsValue):
    schema_version: Literal[1] = 1
    defaults: DefaultSettings = Field(default_factory=DefaultSettings)
    operations: dict[str, OperationSettings] = Field(default_factory=dict)
    helpers: HelperSettings = Field(default_factory=HelperSettings)
    execution_io: ExecutionIOSettings = Field(default_factory=ExecutionIOSettings)
    budget_extensions: BudgetExtensionSettings = Field(default_factory=BudgetExtensionSettings)
    scheduling: SchedulingSettings = Field(default_factory=SchedulingSettings)
    lifecycle: LifecycleSettings = Field(default_factory=LifecycleSettings)
    diagnostics: DiagnosticsSettings = Field(default_factory=DiagnosticsSettings)
    input_materials: MaterialInputSettings = Field(default_factory=MaterialInputSettings)
    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    presentation: PresentationSettings = Field(default_factory=PresentationSettings)
    mcp_read: MCPReadSettings = Field(default_factory=MCPReadSettings)

    def sparse(self) -> dict:
        return self.model_dump(exclude_none=True, exclude_unset=True)


class ExecutionProfile(SettingsValue):
    model: str = Field(min_length=1, max_length=256)
    reasoning_effort: ReasoningEffort
    narrative_language: Language

    @field_validator("model")
    @classmethod
    def normalize_model_identity(cls, value: str) -> str:
        return canonical_model_id(value) or value


def parse_settings(value: dict, *, label: str = "agent settings") -> AgentSettings:
    """Missing fields inherit; explicit null is never another spelling of missing."""
    def reject_null(item, path):
        if item is None:
            raise ValueError(f"{label}:{path}: omit an override to inherit; null is invalid")
        if isinstance(item, dict):
            for key, child in item.items():
                reject_null(child, f"{path}.{key}")
    reject_null(value, "$")
    try:
        return AgentSettings.model_validate(value)
    except ValueError as error:
        raise ValueError(f"{label}: {error}") from error


def load_settings(path: Path | str | None) -> AgentSettings:
    if path is None:
        return AgentSettings()
    source = Path(path).expanduser()
    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AgentSettings()
    except UnicodeError as error:
        raise ValueError(f"{source}: {error}") from error
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{source}: {error}") from error
    return parse_settings(value, label=str(source))


@lru_cache(maxsize=1)
def packaged_default_model() -> str:
    source = Path(__file__).with_name("default_agent_settings.json")
    settings = parse_settings(json.loads(source.read_text(encoding="utf-8")), label=str(source))
    if settings.defaults.model is None:
        raise ValueError(f"{source}: defaults.model is required")
    return settings.defaults.model


def resolve_settings(global_settings: AgentSettings, instance_settings: AgentSettings,
                     *, operation_id: str, operation_model: str | None,
                     operation_max_attempts: int) -> dict:
    values = dict(model=operation_model or packaged_default_model(), reasoning_effort="medium",
                  narrative_language="en", max_attempts=operation_max_attempts)
    sources = dict(model="operation_default" if operation_model else "package_default",
                   reasoning_effort="compatibility_default",
                   narrative_language="compatibility_default", max_attempts="operation_default")
    for source, settings in (("global", global_settings), ("instance", instance_settings)):
        for suffix, item in (("defaults", settings.defaults),
                             ("operation", settings.operations.get(operation_id))):
            if item is not None:
                for key, value in item.model_dump(exclude_none=True).items():
                    values[key] = value
                    sources[key] = source + "." + suffix
    return {"profile": ExecutionProfile(**{key: values[key] for key in ExecutionProfile.model_fields}),
            "max_attempts": values["max_attempts"], "sources": sources}


def narrative_instruction(profile: dict | None) -> str | None:
    if profile is None:
        return None
    language = "简体中文" if profile["narrative_language"] == "zh-CN" else "English"
    return (f"Write newly authored goals, summaries, conclusions, reasons, limitations and suggestions in {language}. "
            "Preserve field names, enums, identifiers, formulas, code, units, quotations and any text "
            "the scientific contract requires copying exactly. Do not translate historical inputs or "
            "produce a second report. Language is a writing preference, not an output acceptance rule.")
