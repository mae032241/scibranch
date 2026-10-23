"""Deterministically package a textual TCAD project for execution."""

from __future__ import annotations

DEVELOPMENT_ARTIFACT_LIMIT_BYTES = 64 * 1024 * 1024

import base64
import uuid
import io
import json
import math
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Callable, ContextManager, Literal, Mapping

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator
from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.artifact_agent.schema.experiment import ExperimentPortfolio
from scidiscovery.operation_contract import SemanticRuleViolation, declared_violation
from scidiscovery.artifact_agent.schema.refs import ArtifactRef

from .execution_control import (
    ArchiveEntry,
    ExpectedOutput,
    FileDescriptor,
    ResourceLimits,
    SolverCapability,
    SolverCapabilitySnapshot,
    SolverKind,
    StrictModel,
    TCADJobSpec,
)


MAX_PROJECT_BYTES = 64 * 1024 * 1024
WORKBENCH_TOKEN = re.compile(r"@[A-Za-z_][A-Za-z0-9_.:-]*@")
SHELL_ENTRYPOINT_MARKERS = (
    "#!/bin/sh",
    "#!/usr/bin/env sh",
    "#!/bin/bash",
    "#!/usr/bin/env bash",
    "set -e",
    "set -u",
    "nohup ",
)


def _safe_relative_path(value: str) -> str:
    if value.startswith("/") or "\\" in value or any(
        part in {"", ".", ".."} for part in value.split("/")
    ):
        raise ValueError("unsafe relative path")
    return value


def _safe_optional_relative_path(value: str | None) -> str | None:
    return None if value is None else _safe_relative_path(value)


class DeckFile(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=8 * 1024 * 1024)

    _safe_path = field_validator("relative_path")(_safe_relative_path)


class DeclaredCaseAnchor(StrictModel):
    experiment_key: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"
    )
    case_key: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"
    )
    relative_path: str = Field(min_length=1, max_length=1024)
    locator: str = Field(min_length=1, max_length=4096)

    _safe_path = field_validator("relative_path")(_safe_relative_path)


class ProjectInputSlot(StrictModel):
    semantic_name: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    target_relative_path: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=3, max_length=255)

    _safe_target = field_validator("target_relative_path")(_safe_relative_path)


class ResolvedProjectInput(StrictModel):
    semantic_name: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    target_relative_path: str = Field(min_length=1, max_length=1024)
    artifact_ref: ArtifactRef
    media_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(ge=0, le=2**50)

    _safe_target = field_validator("target_relative_path")(_safe_relative_path)


class NativeSealRef(StrictModel):
    """An exact instance-relative original; the host must verify its bytes."""

    path: str = Field(min_length=1, max_length=4096)
    size_bytes: int = Field(ge=0)

    _safe_path = field_validator("path")(_safe_relative_path)


class NativeOriginalRef(StrictModel):
    """One sealed native material, with no CAS or Artifact identity."""

    instance_id: str = Field(min_length=1, max_length=256)
    producer_step: int = Field(gt=0)
    producer_seal: NativeSealRef
    material_path: str = Field(min_length=1, max_length=4096)
    size_bytes: int = Field(ge=0)

    _safe_material_path = field_validator("material_path")(_safe_relative_path)


class NativeResolvedProjectInput(StrictModel):
    semantic_name: str = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    target_relative_path: str = Field(min_length=1, max_length=1024)
    original: NativeOriginalRef
    media_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(ge=0, le=2**50)

    _safe_target = field_validator("target_relative_path")(_safe_relative_path)

    @model_validator(mode="after")
    def _original_size(self):
        if self.size_bytes != self.original.size_bytes:
            raise ValueError("native input size differs from its sealed original")
        return self


ProjectExpectedOutput = ExpectedOutput
ProjectResourceLimits = ResourceLimits


class ParameterBinding(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    approved_parameter_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    declared_value: str = Field(min_length=1, max_length=4096)
    unit: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=1024)
    locator: str = Field(min_length=1, max_length=4096)
    evidence_class: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    evidence_source: str | None = Field(default=None, min_length=1, max_length=4096)
    evidence_locator: str | None = Field(default=None, min_length=1, max_length=4096)
    rationale: str | None = Field(default=None, min_length=1, max_length=4096)
    requirement_keys: tuple[str, ...] = Field(default=(), max_length=64)
    materialization_kind: Literal["agent_declared", "control_generated"] = (
        "agent_declared"
    )
    source_line_start: int | None = Field(default=None, ge=1)
    source_line_end: int | None = Field(default=None, ge=1)
    derivation: str | None = Field(default=None, min_length=1, max_length=4096)

    _safe_path = field_validator("relative_path")(_safe_relative_path)

    @model_validator(mode="after")
    def _complete_evidence(self) -> ParameterBinding:
        evidence = (
            self.evidence_class,
            self.evidence_source,
            self.evidence_locator,
            self.rationale,
        )
        if any(item is not None for item in evidence) and any(
            item is None for item in evidence
        ):
            raise ValueError("parameter evidence fields must be supplied together")
        if self.materialization_kind == "control_generated":
            if (
                self.source_line_start is None
                or self.source_line_end is None
                or self.derivation is None
            ):
                raise ValueError(
                    "control-generated parameter binding requires exact source provenance"
                )
            if self.source_line_end < self.source_line_start:
                raise ValueError("parameter binding source line range is invalid")
        elif any(
            value is not None
            for value in (
                self.source_line_start,
                self.source_line_end,
                self.derivation,
            )
        ):
            raise ValueError(
                "agent-declared parameter binding cannot claim control provenance"
            )
        return self


class CaseParameterBinding(StrictModel):
    """One case-scoped realized control bound to exact solver source text."""

    experiment_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    case_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    variable_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    scientific_path: str = Field(min_length=1, max_length=4096)
    realized_value: str = Field(min_length=1, max_length=4096)
    unit: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=1024)
    locator: str = Field(min_length=1, max_length=4096)
    requirement_keys: tuple[str, ...] = Field(default=(), max_length=64)
    materialization_kind: Literal["agent_declared", "control_generated"] = (
        "agent_declared"
    )
    source_line_start: int | None = Field(default=None, ge=1)
    source_line_end: int | None = Field(default=None, ge=1)
    derivation: str | None = Field(default=None, min_length=1, max_length=4096)

    _safe_path = field_validator("relative_path")(_safe_relative_path)

    @model_validator(mode="after")
    def _materialization_evidence_is_complete(self) -> CaseParameterBinding:
        if self.materialization_kind == "control_generated":
            if (
                self.source_line_start is None
                or self.source_line_end is None
                or self.derivation is None
            ):
                raise ValueError(
                    "control-generated case binding requires exact source provenance"
                )
            if self.source_line_end < self.source_line_start:
                raise ValueError("case binding source line range is invalid")
        elif any(
            value is not None
            for value in (
                self.source_line_start,
                self.source_line_end,
                self.derivation,
            )
        ):
            raise ValueError(
                "agent-declared case binding cannot claim control provenance"
            )
        return self


class MaterializationFinding(StrictModel):
    reason_code: Literal[
        "missing_control_binding",
        "duplicate_control_anchor",
        "comment_only_anchor",
        "unreachable_assignment",
        "value_mismatch",
        "unit_mismatch",
        "missing_requirement_realization",
        "downstream_only_requirement_in_deck",
        "implicit_solver_default",
        "dead_declaration",
        "raw_output_not_declared",
        "raw_output_not_produced",
        "unsupported_syntax",
        "source_changed_after_materialization",
    ]
    severity: Literal["error", "warning"]
    message: str = Field(min_length=1, max_length=4096)
    experiment_key: str | None = Field(default=None, max_length=256)
    case_key: str | None = Field(default=None, max_length=256)
    variable_key: str | None = Field(default=None, max_length=256)
    requirement_key: str | None = Field(default=None, max_length=256)
    source_path: str | None = Field(default=None, max_length=1024)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    command_excerpt: str | None = Field(default=None, max_length=1024)
    expected: str | None = Field(default=None, max_length=4096)
    observed: str | None = Field(default=None, max_length=4096)
    suggested_fix_scope: str = Field(min_length=1, max_length=4096)


class ProjectMaterializationReport(StrictModel):
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    profile: Literal["tcad.project-materializer.declared-source.v2"]
    status: Literal["pass", "fail"]
    generated_case_bindings: int = Field(ge=0, le=100000)
    generated_parameter_bindings: int = Field(ge=0, le=100000)
    generated_requirements: int = Field(ge=0, le=100000)
    findings: tuple[MaterializationFinding, ...] = Field(default=(), max_length=4096)

    @model_validator(mode="after")
    def _status_matches_findings(self) -> ProjectMaterializationReport:
        has_error = any(item.severity == "error" for item in self.findings)
        if (self.status == "fail") != has_error:
            raise ValueError("materialization status differs from its findings")
        return self


class ProjectPreflightAttestation(StrictModel):
    """Control-owned result of one source-bound solver preflight."""

    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    profile: Literal["tcad.project-preflight.v1"] = "tcad.project-preflight.v1"
    mode: Literal["preflight"] = "preflight"
    terminal_state: Literal["succeeded", "failed", "cancelled"]
    exit_code: int
    diagnostic_layer: Literal[
        "parser",
        "initialization",
        "numerical",
        "output_contract",
        "resource_limit",
        "runtime",
        "complete",
    ]
    qualified: bool
    summary: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def _qualification_matches_result(self) -> ProjectPreflightAttestation:
        passed = (
            self.terminal_state == "succeeded"
            and self.exit_code == 0
            and self.diagnostic_layer == "complete"
        )
        if self.qualified != passed:
            raise ValueError("preflight qualification differs from its terminal result")
        return self


class ProjectInitializationAttestation(ProjectPreflightAttestation):
    """Control-owned diagnostic for the declared initialization entrypoint."""

    profile: Literal["tcad.project-initialization.v1"] = "tcad.project-initialization.v1"
    mode: Literal["initialization"] = "initialization"


class RuntimeAssertion(StrictModel):
    description: str = Field(min_length=1, max_length=4096)
    expected_output_name: str = Field(min_length=1, max_length=256)
    assertion_kind: Literal[
        "output_present",
        "file_nonempty",
        "finite_numeric_table",
        "solver_log_clean",
        "tdr_metadata",
    ] = "output_present"
    required_columns: tuple[str, ...] = Field(default=(), max_length=4096)
    min_numeric_rows: int = Field(default=1, ge=1, le=10**9)

    @model_validator(mode="after")
    def _parser_requirements_are_consistent(self) -> RuntimeAssertion:
        if self.assertion_kind != "finite_numeric_table" and self.required_columns:
            raise ValueError("required columns apply only to numeric-table assertions")
        if len(self.required_columns) != len(set(self.required_columns)):
            raise ValueError("runtime assertion required columns must be unique")
        return self


class RealizationRequirement(StrictModel):
    """One source-backed physical requirement and its exact deck realization."""

    requirement_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    category: Literal[
        "geometry",
        "material",
        "doping",
        "physics",
        "contact_boundary",
        "initial_condition",
        "numerical_protocol",
        "observable",
    ]
    requirement: str = Field(min_length=1, max_length=4096)
    evidence_class: str = Field(min_length=1, max_length=128)
    evidence_source: str = Field(min_length=1, max_length=4096)
    evidence_locator: str = Field(min_length=1, max_length=4096)
    rationale: str = Field(min_length=1, max_length=4096)
    implementation_status: Literal["implemented", "unsupported"]
    relative_path: str | None = Field(default=None, min_length=1, max_length=1024)
    locator: str | None = Field(default=None, min_length=1, max_length=4096)
    verification_mode: Literal["static_review", "runtime_log", "runtime_output"]
    expected_output_name: str | None = Field(
        default=None, min_length=1, max_length=256
    )

    @field_validator("relative_path")
    @classmethod
    def _safe_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else _safe_relative_path(value)

    @model_validator(mode="after")
    def _implementation_is_explicit(self) -> RealizationRequirement:
        if self.implementation_status == "implemented":
            if self.relative_path is None or self.locator is None:
                raise ValueError(
                    "implemented requirement needs relative_path and locator"
                )
        elif self.relative_path is not None or self.locator is not None:
            raise ValueError("unsupported requirement cannot claim a deck location")
        if self.verification_mode in {"runtime_log", "runtime_output"}:
            if self.expected_output_name is None:
                raise ValueError(
                    "runtime verification requires expected_output_name"
                )
        elif self.expected_output_name is not None:
            raise ValueError(
                "static verification cannot declare expected_output_name"
            )
        if (
            self.implementation_status == "unsupported"
            and self.verification_mode != "static_review"
        ):
            raise ValueError("unsupported requirement must use static_review")
        return self


class AttemptFile(DeckFile):
    """Control-captured source or diagnostic bytes; legacy records are UTF-8."""

    content: str = Field(max_length=DEVELOPMENT_ARTIFACT_LIMIT_BYTES)
    encoding: Literal["utf8", "base64"] = "utf8"

    def raw_bytes(self) -> bytes:
        return (base64.b64decode(self.content, validate=True)
                if self.encoding == "base64" else self.content.encode("utf-8"))

    @model_validator(mode="after")
    def _bounded_bytes(self) -> AttemptFile:
        if self.encoding == "base64" and not self.relative_path.startswith("reports/"):
            raise ValueError("binary attempt files must be diagnostic reports")
        limit = DEVELOPMENT_ARTIFACT_LIMIT_BYTES if self.relative_path.startswith("reports/") else 8 * 1024 * 1024
        if len(self.raw_bytes()) > limit:
            raise ValueError("attempt file exceeds its byte limit")
        return self


class ScientificDeckImplementation(StrictModel):
    """Standalone scientific implementation shared by author schema and execution package.

    Source file paths and input-slot names/paths must be unique and disjoint.
    Control captures process logs and discovers generated output files automatically.
    Entrypoints must identify supplied files; direct solvers require a
    .cmd entrypoint without shell launchers, nested solvers, scheduler arguments
    or unresolved Workbench tokens. Combined source bytes obey x-max-source-bytes.
    Arguments cannot contain NUL bytes.
    """
    model_config = ConfigDict(json_schema_extra={"x-max-source-bytes":MAX_PROJECT_BYTES})
    execution_plan: ExperimentPortfolio | None = None
    solver_kind: SolverKind | None = None
    files: tuple[DeckFile, ...] = Field(min_length=1, max_length=4096)
    input_slots: tuple[ProjectInputSlot, ...] = Field(default=(), max_length=4096)
    entrypoint: str = Field(min_length=1, max_length=1024)
    development_initialization_entrypoint: str | None = Field(
        default=None, min_length=1, max_length=1024
    )
    arguments: tuple[Annotated[str, Field(max_length=4096, pattern=r"^[^\x00]*$")], ...] = Field(default=(), max_length=256)
    resource_limits: ProjectResourceLimits

    _safe_entrypoint = field_validator("entrypoint")(_safe_relative_path)
    _safe_initialization_entrypoint = field_validator(
        "development_initialization_entrypoint"
    )(_safe_optional_relative_path)

    @model_validator(mode="after")
    def _consistent_implementation(self):
        file_paths = tuple(item.relative_path for item in self.files)
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("project file paths must be unique")
        if self.entrypoint not in set(file_paths):
            raise ValueError("entrypoint must name one project file")
        if (
            self.development_initialization_entrypoint is not None
            and self.development_initialization_entrypoint not in set(file_paths)
        ):
            raise ValueError("development initialization entrypoint must name one project file")
        if sum(len(item.content.encode("utf-8")) for item in self.files) > MAX_PROJECT_BYTES:
            raise ValueError("project source exceeds total byte limit")

        if any(path == ".scid-capture" or path.startswith(".scid-capture/")
               for path in (*file_paths, *(item.target_relative_path for item in self.input_slots))):
            raise ValueError("project inputs must not use the control capture directory")
        slot_names = tuple(item.semantic_name for item in self.input_slots)
        slot_paths = tuple(item.target_relative_path for item in self.input_slots)
        if len(slot_names) != len(set(slot_names)) or len(slot_paths) != len(
            set(slot_paths)
        ):
            raise ValueError("project input slot names and paths must be unique")
        if set(file_paths).intersection(slot_paths):
            raise ValueError("project input slots must not overlap text files")

        contents = {item.relative_path: item.content for item in self.files}
        solver_kind = self.solver_kind
        if solver_kind is not None:
            if solver_kind in {"sprocess", "sdevice"} and Path(
                self.entrypoint
            ).suffix.lower() != ".cmd":
                raise ValueError(
                    f"direct {solver_kind} entrypoint must be a .cmd solver deck"
                )
            entrypoint_content = contents[self.entrypoint]
            if solver_kind in {"sprocess", "sdevice"} and any(
                marker in entrypoint_content for marker in SHELL_ENTRYPOINT_MARKERS
            ):
                raise ValueError(
                    f"direct {solver_kind} entrypoint contains shell-runner syntax"
                )
            if solver_kind in {"sprocess", "sdevice"} and any(
                value in {"submit", "worker", "status"}
                for value in self.arguments
            ):
                raise ValueError(
                    f"direct {solver_kind} arguments contain scheduler commands"
                )
            unresolved = {
                token
                for content in contents.values()
                for token in WORKBENCH_TOKEN.findall(content)
            }
            if solver_kind in {"sprocess", "sdevice"} and unresolved:
                raise ValueError(
                    "standalone direct-solver project contains unresolved "
                    "Workbench tokens: " + ", ".join(sorted(unresolved))
                )
            nested_solver = re.search(
                rf"(^|[;\n])\s*(?:exec\s+)?{solver_kind}\b",
                entrypoint_content,
                re.IGNORECASE,
            )
            if solver_kind in {"sprocess", "sdevice"} and nested_solver:
                raise ValueError(
                    f"direct {solver_kind} entrypoint launches a nested solver"
                )
        return self


class DeckProjectDraft(ScientificDeckImplementation):
    # Execution capture declarations are control-owned, absent from author forms.
    expected_outputs: tuple[ProjectExpectedOutput, ...] = Field(default=(), max_length=4096)
    collect_generated_outputs: bool = False
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    tool_profile: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    capability_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    parameter_bindings: tuple[ParameterBinding, ...] = Field(default=(), max_length=4096)
    case_parameter_bindings: tuple[CaseParameterBinding, ...] = Field(
        default=(), max_length=100000
    )
    # Control-retained declarations, independent of whether a case varies a parameter.
    # None identifies historical projects which did not retain this information.
    case_anchors: tuple[DeclaredCaseAnchor, ...] | None = Field(
        default=None, max_length=100000
    )
    runtime_assertions: tuple[RuntimeAssertion, ...] = Field(default=(), max_length=4096)
    realization_manifest: tuple[RealizationRequirement, ...] = Field(
        default=(), max_length=4096
    )
    materialization_report: ProjectMaterializationReport | None = None
    preflight_attestation: ProjectPreflightAttestation | None = None
    initialization_attestation: ProjectInitializationAttestation | None = None
    development_diagnostics: tuple[AttemptFile, ...] = Field(default=(), max_length=4096)

    @field_validator("development_diagnostics")
    @classmethod
    def _diagnostic_paths(cls, values):
        paths = [item.relative_path for item in values]
        if len(set(paths)) != len(paths) or any(not path.startswith("reports/") for path in paths):
            raise ValueError("development diagnostics require unique report paths")
        return values



    @model_validator(mode="after")
    def _consistent_project(self) -> DeckProjectDraft:
        output_names = tuple(item.name for item in self.expected_outputs)
        output_paths = tuple(item.execution_output().relative_path for item in self.expected_outputs)
        if sum(item.capture == "process_log" for item in self.expected_outputs) > 1:
            raise ValueError("project can declare at most one process-log output")
        if len(output_names) != len(set(output_names)) or len(output_paths) != len(set(output_paths)):
            raise ValueError("expected output names and paths must be unique")
        inputs = {item.relative_path for item in self.files} | {item.target_relative_path for item in self.input_slots}
        if inputs.intersection(output_paths):
            raise ValueError("expected outputs must not overwrite project inputs")
        if any(item.capture != "process_log" and (item.relative_path == ".scid-capture"
               or item.relative_path.startswith(".scid-capture/")) for item in self.expected_outputs):
            raise ValueError("solver output must not use the control capture directory")
        binding_names = tuple(item.name for item in self.parameter_bindings)
        if len(binding_names) != len(set(binding_names)):
            raise ValueError("parameter binding names must be unique")
        case_binding_keys = tuple(
            (item.experiment_key, item.case_key, item.variable_key)
            for item in self.case_parameter_bindings
        )
        if len(case_binding_keys) != len(set(case_binding_keys)):
            raise ValueError("case parameter bindings must be unique")
        contents = {item.relative_path: item.content for item in self.files}
        for binding in self.parameter_bindings:
            if binding.relative_path not in contents:
                raise ValueError("parameter binding file must exist")
            if binding.locator not in contents[binding.relative_path]:
                raise ValueError("parameter binding locator is absent from its file")
        for binding in self.case_parameter_bindings:
            if binding.relative_path not in contents:
                raise ValueError("case parameter binding file must exist")
            if contents[binding.relative_path].count(binding.locator) != 1:
                raise ValueError(
                    "case parameter binding locator must occur exactly once"
                )
            if (
                binding.materialization_kind == "agent_declared"
                and binding.realized_value not in binding.locator
            ):
                raise ValueError(
                    "case parameter binding value is not present in its exact locator"
                )

        requirement_keys = tuple(
            item.requirement_key for item in self.realization_manifest
        )
        if len(requirement_keys) != len(set(requirement_keys)):
            raise ValueError("realization requirement keys must be unique")
        known_requirements = set(requirement_keys)
        for requirement in self.realization_manifest:
            if requirement.implementation_status == "implemented":
                assert requirement.relative_path is not None
                assert requirement.locator is not None
                try:
                    source = contents[requirement.relative_path]
                except KeyError as error:
                    raise ValueError(
                        "realization requirement file must exist"
                    ) from error
                if requirement.locator not in source:
                    raise ValueError(
                        "realization requirement locator is absent from its file"
                    )
            if (
                requirement.expected_output_name is not None
                and requirement.expected_output_name not in set(output_names)
            ):
                raise ValueError(
                    "realization requirement references an unknown expected output"
                )
        for binding in self.parameter_bindings:
            if not set(binding.requirement_keys).issubset(known_requirements):
                raise ValueError(
                    "parameter binding references an unknown realization requirement"
                )
        for binding in self.case_parameter_bindings:
            if not set(binding.requirement_keys).issubset(known_requirements):
                raise ValueError(
                    "case parameter binding references an unknown realization requirement"
                )

        output_name_set = set(output_names)
        if any(
            item.expected_output_name not in output_name_set
            for item in self.runtime_assertions
        ):
            raise ValueError("runtime assertion must reference an expected output")
        if self.materialization_report is not None and self.materialization_report.status != "pass":
            raise ValueError("canonical project materialization did not pass")
        return self


class DeckRequirementReview(StrictModel):
    requirement_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    status: Literal["pass", "fail", "unknown"]
    rationale: str = Field(min_length=1, max_length=4096)


class DeckReviewFinding(StrictModel):
    finding_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    severity: Literal["blocker", "major", "minor", "note"]
    category: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=4096)
    relative_path: str | None = Field(default=None, min_length=1, max_length=1024)
    locator: str | None = Field(default=None, min_length=1, max_length=4096)
    required_action: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("relative_path")
    @classmethod
    def _safe_optional_path(cls, value: str | None) -> str | None:
        return None if value is None else _safe_relative_path(value)














class DeckReviewReport(StrictModel):
    """Independent physical and implementation review of one complete project."""

    scientific_assessment: Literal["pass", "fail", "unknown"] | None = None
    verdict: Literal["pass", "revise", "blocked"]
    capability_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    summary: str = Field(min_length=1, max_length=4096)
    rationale: str = Field(min_length=1, max_length=8192)
    physical_fidelity: Literal["pass", "fail", "unknown"]
    implementation_fidelity: Literal["pass", "fail", "unknown"]
    syntax_fidelity: Literal["pass", "fail", "unknown"] = "unknown"
    numerical_protocol_fidelity: Literal["pass", "fail", "unknown"]
    requirement_reviews: tuple[DeckRequirementReview, ...] = Field(
        default=(), max_length=4096
    )
    findings: tuple[DeckReviewFinding, ...] = Field(default=(), max_length=256)
    undeclared_defaults: tuple[str, ...] = Field(default=(), max_length=128)
    unsupported_constructs: tuple[str, ...] = Field(default=(), max_length=128)
    missing_inputs: tuple[str, ...] = Field(default=(), max_length=128)
    execution_ready: bool
    next_actions: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _verdict_matches_review(self) -> DeckReviewReport:
        if self.verdict == "pass" and self.scientific_assessment not in {None, "pass"}:
            raise ValueError("passing comprehensive review requires passing scientific assessment")
        keys = tuple(item.requirement_key for item in self.requirement_reviews)
        if len(keys) != len(set(keys)):
            raise ValueError("deck requirement reviews must be unique")
        dimensions = {
            self.physical_fidelity,
            self.implementation_fidelity,
            self.numerical_protocol_fidelity,
        }
        requirement_statuses = {item.status for item in self.requirement_reviews}
        blocking_finding = any(
            item.severity in {"blocker", "major"} for item in self.findings
        )
        clean = (
            dimensions == {"pass"}
            and self.syntax_fidelity != "fail"
            and requirement_statuses in (set(), {"pass"})
            and not blocking_finding
            and not self.undeclared_defaults
            and not self.unsupported_constructs
            and not self.missing_inputs
        )
        if self.verdict == "pass":
            if not clean or not self.execution_ready:
                raise declared_violation(
                    "passing deck review requires complete clean coverage and execution_ready",
                    path="$.verdict",
                )
        elif self.execution_ready:
            raise declared_violation("non-passing deck review cannot be execution_ready", path="$.execution_ready")
        return self






class ExecutionPackage(StrictModel):
    """An immutable execution package; optional review does not grant authorization."""

    schema_version: Annotated[int, Field(ge=2, le=2)] = 2
    project: DeckProjectDraft
    review: DeckReviewReport | None = None
    capability: SolverCapabilitySnapshot
    resolved_inputs: tuple[ResolvedProjectInput, ...] = Field(
        default=(), max_length=4096
    )
    @model_validator(mode="after")
    def _package_identity(self) -> ExecutionPackage:
        return _validate_package_identity(self)


class NativeExecutionPackage(StrictModel):
    """Host-compiled native inputs; formal qualification remains control-owned."""

    schema_version: Annotated[int, Field(ge=3, le=3)] = 3
    project: DeckProjectDraft
    review: DeckReviewReport | None = None
    capability: SolverCapabilitySnapshot
    resolved_inputs: tuple[NativeResolvedProjectInput, ...] = Field(default=(), max_length=4096)

    @model_validator(mode="after")
    def _package_identity(self):
        return _validate_package_identity(self)


def _validate_package_identity(package):
    """Share scientific/slot checks without converting native refs to Artifacts."""
    if package.project.solver_kind is None:
        raise ValueError("execution package requires an explicit solver_kind")
    if (
        package.project.capability_sha256 != package.capability.capability_sha256
        or package.review is not None and package.review.capability_sha256
        not in {None, package.capability.capability_sha256}
    ):
        raise ValueError("execution package project or review capability digest differs")
    if (
        package.capability.profile_id != package.project.tool_profile
        or package.capability.solver_kind != package.project.solver_kind
    ):
        raise ValueError("execution package capability differs from its project")
    slots = {item.semantic_name: item for item in package.project.input_slots}
    resolved = {item.semantic_name: item for item in package.resolved_inputs}
    if len(resolved) != len(package.resolved_inputs) or set(resolved) != set(slots):
        raise ValueError("execution package must resolve every project input slot")
    for name, slot in slots.items():
        item = resolved[name]
        if item.target_relative_path != slot.target_relative_path or item.media_type != slot.media_type:
            raise ValueError("resolved project input differs from its declared slot")
    return package


def validate_execution_package_eligibility(reviewed: ExecutionPackage | NativeExecutionPackage) -> None:
    """Retain source/slot/capability integrity; review is an optional assessment.

    Formal review qualification is owned by its immutable control record. Merely
    embedding a passing report here cannot authorize execution.
    """
    model = NativeExecutionPackage if isinstance(reviewed, NativeExecutionPackage) else ExecutionPackage
    model.model_validate_json(canonical_json(reviewed.model_dump(mode="json")), strict=True)


class RuntimeOutputRecord(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    relative_path: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(ge=0, le=2**50)
    output_class: Literal[
        "solver_native", "transport_derived", "parser_derived"
    ] = "solver_native"

    _safe_path = field_validator("relative_path")(_safe_relative_path)


class TCADRuntimeManifest(StrictModel):
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    solver_exit_code: int | None = None
    collection_errors: tuple[str, ...] = ()
    started_at: str | None = Field(min_length=1, max_length=128)
    completed_at: str = Field(min_length=1, max_length=128)
    terminal_state: Literal["succeeded", "failed", "cancelled"]
    exit_code: int
    error: str
    outputs: tuple[RuntimeOutputRecord, ...]

    @model_validator(mode="after")
    def _outputs_are_unique(self) -> TCADRuntimeManifest:
        names = tuple(item.name for item in self.outputs)
        paths = tuple(item.relative_path for item in self.outputs)
        if len(names) != len(set(names)) or len(paths) != len(set(paths)):
            raise ValueError("runtime output names and paths must be unique")
        return self


class RuntimeContractCheck(StrictModel):
    check_key: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    )
    status: Literal["pass", "fail"]
    rationale: str = Field(min_length=1)
    gate_kind: Literal["transport", "format", "numeric", "runtime_contract"] = (
        "runtime_contract"
    )
    provider_id: str | None = Field(default=None, min_length=1, max_length=256)


class RuntimeAttestation(StrictModel):
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    verdict: Literal["pass", "fail"]
    terminal_state: Literal["succeeded", "failed", "cancelled"]
    exit_code: int
    checks: tuple[RuntimeContractCheck, ...] = Field(min_length=1)
    missing_required_outputs: tuple[str, ...] = ()
    solver_native_output_count: int = Field(ge=0)
    transport_derived_output_count: int = Field(ge=0)
    parser_derived_output_count: int = Field(ge=0)
    scope: Literal["execution_contract_only"] = "execution_contract_only"
    rationale: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _verdict_matches_checks(self) -> RuntimeAttestation:
        failed = any(item.status == "fail" for item in self.checks)
        if (self.verdict == "fail") != failed:
            raise ValueError("runtime attestation verdict differs from its checks")
        return self


@dataclass(frozen=True)
class PackagedTCADProject:
    package_id: str
    package_directory: Path
    archive: FileDescriptor
    job_spec_file: FileDescriptor
    job_spec: TCADJobSpec


class PackagerError(RuntimeError):
    pass


def deck_project_diff(
    base: DeckProjectDraft, revised: DeckProjectDraft
) -> dict[str, object]:
    base_files = {item.relative_path: item.content for item in base.files}
    revised_files = {item.relative_path: item.content for item in revised.files}
    paths = sorted(set(base_files) | set(revised_files))
    file_changes = []
    for path in paths:
        if path not in base_files:
            operation = "add"
        elif path not in revised_files:
            operation = "delete"
        elif base_files[path] != revised_files[path]:
            operation = "replace"
        else:
            continue
        file_changes.append({"relative_path": path, "operation": operation})
    base_bindings = {item.name: item.model_dump(mode="json") for item in base.parameter_bindings}
    revised_bindings = {
        item.name: item.model_dump(mode="json") for item in revised.parameter_bindings
    }
    binding_changes = [
        name
        for name in sorted(set(base_bindings) | set(revised_bindings))
        if base_bindings.get(name) != revised_bindings.get(name)
    ]
    base_case_bindings = {
        (item.experiment_key, item.case_key, item.variable_key): item.model_dump(
            mode="json"
        )
        for item in base.case_parameter_bindings
    }
    revised_case_bindings = {
        (item.experiment_key, item.case_key, item.variable_key): item.model_dump(
            mode="json"
        )
        for item in revised.case_parameter_bindings
    }
    case_binding_changes = [
        "/".join(key)
        for key in sorted(set(base_case_bindings) | set(revised_case_bindings))
        if base_case_bindings.get(key) != revised_case_bindings.get(key)
    ]
    base_requirements = {
        item.requirement_key: item.model_dump(mode="json")
        for item in base.realization_manifest
    }
    revised_requirements = {
        item.requirement_key: item.model_dump(mode="json")
        for item in revised.realization_manifest
    }
    realization_requirement_changes = [
        name
        for name in sorted(set(base_requirements) | set(revised_requirements))
        if base_requirements.get(name) != revised_requirements.get(name)
    ]
    base_outputs = {
        item.name: item.model_dump(mode="json") for item in base.expected_outputs
    }
    revised_outputs = {
        item.name: item.model_dump(mode="json") for item in revised.expected_outputs
    }
    expected_output_changes = [
        name
        for name in sorted(set(base_outputs) | set(revised_outputs))
        if base_outputs.get(name) != revised_outputs.get(name)
    ]
    base_assertions: dict[str, list[dict[str, object]]] = {}
    revised_assertions: dict[str, list[dict[str, object]]] = {}
    for item in base.runtime_assertions:
        base_assertions.setdefault(item.expected_output_name, []).append(
            item.model_dump(mode="json")
        )
    for item in revised.runtime_assertions:
        revised_assertions.setdefault(item.expected_output_name, []).append(
            item.model_dump(mode="json")
        )
    runtime_assertion_output_changes = [
        name
        for name in sorted(set(base_assertions) | set(revised_assertions))
        if base_assertions.get(name) != revised_assertions.get(name)
    ]
    frozen_fields = (
        "tool_profile",
        "solver_kind",
        "capability_sha256",
        "input_slots",
        "entrypoint",
        "development_initialization_entrypoint",
        "arguments",
        "collect_generated_outputs",
        "resource_limits",
    )
    return {
        "file_changes": file_changes,
        "parameter_binding_changes": binding_changes,
        "case_parameter_binding_changes": case_binding_changes,
        "realization_requirement_changes": realization_requirement_changes,
        "expected_output_changes": expected_output_changes,
        "runtime_assertion_output_changes": runtime_assertion_output_changes,
        "frozen_field_changes": [
            field
            for field in frozen_fields
            if getattr(base, field) != getattr(revised, field)
        ],
    }


def package_deck_project(
    project: DeckProjectDraft,
    *,
    capability: SolverCapability | SolverCapabilitySnapshot,
    resolved_inputs: tuple[ResolvedProjectInput | NativeResolvedProjectInput, ...] = (),
    input_payloads: Mapping[str, bytes | Path] | None = None,
    output_root: Path | str,
    context=None,
) -> PackagedTCADProject:
    """Create one reproducible package without interpreting its physics."""

    snapshot = (
        capability.public_snapshot()
        if isinstance(capability, SolverCapability)
        else capability
    )
    if project.solver_kind is None:
        raise PackagerError("execution packaging requires an explicit solver_kind")
    if (
        snapshot.profile_id != project.tool_profile
        or snapshot.solver_kind != project.solver_kind
    ):
        raise PackagerError("execution capability differs from the deck project")
    if (
        project.capability_sha256 is not None
        and project.capability_sha256 != snapshot.capability_sha256
    ):
        raise PackagerError("execution capability digest differs from the deck project")
    slots = {item.semantic_name: item for item in project.input_slots}
    resolved = {item.semantic_name: item for item in resolved_inputs}
    if len(resolved) != len(resolved_inputs) or set(resolved) != set(slots):
        raise PackagerError("execution packaging requires every project input slot")
    payloads = dict(input_payloads or {})
    if set(payloads) != set(slots):
        raise PackagerError("execution input payloads must cover the exact project slots")
    binary_files: dict[str, bytes | Path] = {}
    for name, slot in slots.items():
        item = resolved[name]
        if (
            item.target_relative_path != slot.target_relative_path
            or item.media_type != slot.media_type
        ):
            raise PackagerError("resolved execution input differs from its project slot")
        raw = payloads[name]
        if isinstance(raw, Path):
            size = _file_size(raw,context)
        elif type(raw) is bytes:
            size = len(raw)
        else:
            raise PackagerError("execution input must be verified bytes or a staged file")
        if size != item.size_bytes:
            raise PackagerError("resolved execution input differs from its exact original")
        binary_files[item.target_relative_path] = raw
    package_id = uuid.uuid4().hex
    root = Path(output_root).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=True, mode=0o770)
    if root.is_symlink() or not root.is_dir():
        raise PackagerError("package root must be a non-symlink directory")
    target = root / f"pkg_{package_id}"
    temporary: Path | None = Path(tempfile.mkdtemp(prefix=".package-", dir=root))
    try:
        assert temporary is not None
        archive_path = temporary / "project.tar"
        entries = _write_archive(project.files, binary_files, archive_path,context=context)
        final_archive_path = target / archive_path.name
        archive_descriptor = _descriptor(
            "input_archive",
            archive_path,
            "application/x-tar",
            reported_path=final_archive_path,
            context=context,
        )
        job = TCADJobSpec(
            execution_purpose="production",
            tool_profile=project.tool_profile,
            solver_kind=project.solver_kind,
            capability_sha256=snapshot.capability_sha256,
            input_archive=archive_descriptor,
            archive_entries=entries,
            arguments=(project.entrypoint, *project.arguments),
            expected_outputs=tuple(item.execution_output() for item in project.expected_outputs),
            collect_generated_outputs=project.collect_generated_outputs,
            limits=project.resource_limits,
        )
        job_path = temporary / "job.json"
        job_path.write_bytes(_canonical(job.model_dump(mode="python")))
        os.chmod(archive_path, 0o440)
        os.chmod(job_path, 0o440)

        os.rename(temporary, target)
        temporary = None

        final_archive = target / "project.tar"
        final_job = target / "job.json"
        return PackagedTCADProject(
            package_id=package_id,
            package_directory=target,
            archive=_descriptor("input_archive", final_archive, "application/x-tar",context=context),
            job_spec_file=_descriptor(
                "execution_payload", final_job, "application/json",context=context
            ),
            job_spec=job,
        )
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def package_execution_package_json(
    raw: bytes,
    *,
    input_payloads: Mapping[str, bytes | Path] | None = None,
    output_root: Path | str,
    preparation_profile: str = 'tcad.execution-package.v2',
    context=None,
) -> PackagedTCADProject:
    """Validate one canonical execution package before preparing execution."""

    reviewed = validate_execution_package_json(raw, preparation_profile=preparation_profile)
    return package_deck_project(
        reviewed.project,
        capability=reviewed.capability,
        resolved_inputs=reviewed.resolved_inputs,
        input_payloads=input_payloads,
        output_root=output_root,
        context=context,
    )


def validate_execution_package_json(raw: bytes, *, preparation_profile: str = 'tcad.execution-package.v2') -> ExecutionPackage | NativeExecutionPackage:
    """Validate a canonical execution package without creating execution files."""

    try:
        if preparation_profile == 'tcad.execution-package.v2':
            model = ExecutionPackage
        elif preparation_profile == 'tcad.execution-package.v3':
            model = NativeExecutionPackage
        else:
            raise ValueError('Explicit TCAD execution package profile required')
        reviewed = model.model_validate_json(raw, strict=True)
        validate_execution_package_eligibility(reviewed)
    except ValueError as error:
        raise PackagerError("execution package artifact is invalid") from error
    canonical = _canonical(reviewed.model_dump(mode="python"))
    if raw not in {canonical, canonical + b"\n"}:
        raise PackagerError("execution package artifact must use canonical JSON")
    return reviewed




_SOLVER_NATIVE_OUTPUT_SUFFIXES = {
    "sprocess": frozenset({".tdr", ".plx", ".plt", ".log"}),
    "sdevice": frozenset({".tdr", ".plx", ".plt", ".log"}),
}












_DOWNSTREAM_COMPARISON_PATH_PREFIXES = (
    "analysis.",
    "diagnosis.",
    "implementation.curve_score",
    "implementation.reference_input",
    "scoring.",
)










def attest_runtime_contract(
    reviewed: ExecutionPackage,
    manifest: TCADRuntimeManifest,
    *,
    output_payloads: Mapping[str, bytes | Path] | None = None,
) -> RuntimeAttestation:
    """Verify execution facts without interpreting physical correctness."""

    checks: list[RuntimeContractCheck] = []
    terminal_pass = manifest.terminal_state == "succeeded" and manifest.exit_code == 0
    checks.append(
        RuntimeContractCheck(
            check_key="terminal_state",
            status="pass" if terminal_pass else "fail",
            rationale=(
                "The solver completed successfully with exit code zero."
                if terminal_pass
                else (
                    f"The solver ended as {manifest.terminal_state} with exit code "
                    f"{manifest.exit_code}: {manifest.error or 'no error detail'}"
                )
            ),
        )
    )
    observed = {item.name: item for item in manifest.outputs}
    payloads = dict(output_payloads or {})
    if not set(payloads).issubset(observed):
        raise ValueError("runtime payload names must exist in the runtime manifest")
    for name, raw in payloads.items():
        if type(raw) is not bytes:
            raise TypeError("runtime output payload must be bytes")
        descriptor = observed[name]
        passed = (
            len(raw) == descriptor.size_bytes
        )
        checks.append(
            RuntimeContractCheck(
                check_key=f"transport_payload_{name}",
                status="pass" if passed else "fail",
                rationale=(
                    f"Collected payload {name} matches its immutable descriptor."
                    if passed
                    else f"Collected payload {name} differs from its immutable descriptor."
                ),
                gate_kind="transport",
            )
        )
    declared = {item.name: item for item in reviewed.project.expected_outputs}
    missing: list[str] = []
    for index, expected in enumerate(reviewed.project.expected_outputs, start=1):
        actual = observed.get(expected.name)
        if actual is None:
            passed = not expected.required
            if expected.required:
                missing.append(expected.name)
            rationale = (
                "The optional output was not produced."
                if passed
                else f"Required output is missing: {expected.name}."
            )
        else:
            passed = (
                actual.relative_path == expected.execution_output().relative_path
                and actual.media_type == expected.media_type
                and actual.size_bytes <= expected.max_bytes
                and (actual.size_bytes > 0 or not expected.required)
            )
            rationale = (
                f"Collected output {expected.name} matches its declared contract."
                if passed
                else f"Collected output {expected.name} differs from its declared contract."
            )
        checks.append(
            RuntimeContractCheck(
                check_key=f"expected_output_{index:04d}",
                status="pass" if passed else "fail",
                rationale=rationale,
            )
        )
    undeclared = sorted(
        name
        for name, item in observed.items()
        if item.output_class == "solver_native" and name not in declared
        and not (
            reviewed.project.collect_generated_outputs
            and name == "generated_" + str(list(manifest.outputs).index(item))
        )
    )
    generated_count = sum(
        item.output_class == "solver_native"
        and item.name.startswith("generated_")
        and item.name not in declared
        for item in manifest.outputs
    )
    checks.append(
        RuntimeContractCheck(
            check_key="undeclared_outputs",
            status="pass" if not undeclared and (
                not reviewed.project.collect_generated_outputs or generated_count
            ) else "fail",
            rationale=(
                "Generated solver outputs match their manifest paths and hashes."
                if not undeclared and reviewed.project.collect_generated_outputs and generated_count
                else "No undeclared solver outputs were collected."
                if not undeclared and not reviewed.project.collect_generated_outputs
                else "Generated-output collection contains no solver files."
                if not undeclared
                else "Undeclared outputs were collected: " + ", ".join(undeclared)
            ),
        )
    )
    for index, assertion in enumerate(reviewed.project.runtime_assertions, start=1):
        passed, rationale, gate_kind, provider_id = _evaluate_runtime_assertion(
            assertion,
            observed.get(assertion.expected_output_name),
            payloads.get(assertion.expected_output_name),
        )
        checks.append(
            RuntimeContractCheck(
                check_key=f"runtime_assertion_{index:04d}",
                status="pass" if passed else "fail",
                rationale=f"{assertion.description} {rationale}",
                gate_kind=gate_kind,
                provider_id=provider_id,
            )
        )
    verdict = "fail" if any(item.status == "fail" for item in checks) else "pass"
    return RuntimeAttestation(
        verdict=verdict,
        terminal_state=manifest.terminal_state,
        exit_code=manifest.exit_code,
        checks=tuple(checks),
        missing_required_outputs=tuple(missing),
        solver_native_output_count=sum(
            item.output_class == "solver_native" for item in manifest.outputs
        ),
        transport_derived_output_count=sum(
            item.output_class == "transport_derived" for item in manifest.outputs
        ),
        parser_derived_output_count=sum(
            item.output_class == "parser_derived" for item in manifest.outputs
        ),
        rationale=(
            "This attestation covers only solver termination and the captured output "
            "contract; it does not establish physical correctness or agreement with data."
        ),
    )


def _evaluate_runtime_assertion(
    assertion: RuntimeAssertion,
    output: RuntimeOutputRecord | None,
    raw: bytes | None,
) -> tuple[bool, str, Literal["format", "numeric", "runtime_contract"], str | None]:
    if output is None:
        return (
            False,
            f"Evidence output is missing: {assertion.expected_output_name}.",
            "runtime_contract",
            None,
        )
    if assertion.assertion_kind == "output_present":
        return True, "The declared output is present.", "runtime_contract", None
    if assertion.assertion_kind == "file_nonempty":
        passed = output.size_bytes > 0
        return (
            passed,
            "The declared output is non-empty."
            if passed
            else "The declared output is empty.",
            "format",
            "tcad.file-nonempty.v1",
        )
    if raw is None:
        return (
            False,
            "Parser input bytes were not bound to the runtime attestation.",
            "format",
            None,
        )
    if assertion.assertion_kind == "tdr_metadata":
        return (
            False,
            "No qualified TDR metadata provider is configured.",
            "format",
            None,
        )
    if assertion.assertion_kind == "solver_log_clean":
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False, "Solver log is not valid UTF-8.", "format", "tcad.log.v1"
        lowered = text.lower()
        passed = not any(
            marker in lowered
            for marker in ("fatal error", "segmentation fault", "aborted")
        )
        return (
            passed,
            "Solver log has no declared fatal marker."
            if passed
            else "Solver log contains a fatal marker.",
            "format",
            "tcad.log.v1",
        )
    return _parse_finite_numeric_table(assertion, output, raw)


def _parse_finite_numeric_table(
    assertion: RuntimeAssertion,
    output: RuntimeOutputRecord,
    raw: bytes,
) -> tuple[bool, str, Literal["numeric"], str]:
    suffix = Path(output.relative_path).suffix.lower()
    provider_id = (
        "tcad.plx-text-table.v1" if suffix == ".plx" else "tcad.plt-text-table.v1"
    )
    if suffix not in {".plx", ".plt"}:
        return (
            False,
            "No numeric-table provider matches the output path.",
            "numeric",
            provider_id,
        )
    try:
        lines = [
            line.split() for line in raw.decode("utf-8").splitlines() if line.strip()
        ]
    except UnicodeDecodeError:
        return False, "Numeric table is not valid UTF-8.", "numeric", provider_id
    if not lines:
        return False, "Numeric table is empty.", "numeric", provider_id
    first_is_numeric = all(_finite_number(token) for token in lines[0])
    header = () if first_is_numeric else tuple(lines.pop(0))
    if assertion.required_columns and not set(assertion.required_columns).issubset(
        header
    ):
        return (
            False,
            "Numeric table is missing required columns.",
            "numeric",
            provider_id,
        )
    width = len(header) if header else len(lines[0]) if lines else 0
    passed = (
        len(lines) >= assertion.min_numeric_rows
        and width > 0
        and all(
            len(row) == width and all(_finite_number(item) for item in row)
            for row in lines
        )
    )
    return (
        passed,
        "Numeric table has the required columns, rows, and finite values."
        if passed
        else "Numeric table is truncated, non-finite, or structurally inconsistent.",
        "numeric",
        provider_id,
    )


def _finite_number(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _file_size(path: Path,context=None) -> int:
    if context is not None:context.remaining_seconds()
    if path.is_symlink() or not path.is_file():
        raise PackagerError("execution input must be a regular file")
    return path.stat().st_size


def materialize_execution_inputs(
    reviewed: ExecutionPackage | NativeExecutionPackage,
    artifacts,
    exchange_directory: Path,
    chunk_bytes: int,
    *,
    input_metadata: Callable[[ResolvedProjectInput | NativeResolvedProjectInput], tuple[int, str]] | None = None,
    open_input: Callable[[ResolvedProjectInput | NativeResolvedProjectInput], ContextManager[BinaryIO]] | None = None,
    context=None,
) -> dict[str, Path]:
    """Copy exact originals; the host owns frozen bindings, not source discovery."""
    if type(chunk_bytes) is not int or chunk_bytes <= 0:
        raise PackagerError("input transfer chunk must be a positive integer")
    if (input_metadata is None) != (open_input is None):
        raise PackagerError("input metadata and original opener must be supplied together")
    if artifacts is not None:
        if input_metadata is not None:
            raise PackagerError("Artifact service and input callbacks cannot be combined")
        if isinstance(reviewed, NativeExecutionPackage):
            raise PackagerError('Native originals require their exact host callbacks, not an Artifact service')

        def input_metadata(item):
            envelope = artifacts.catalog(item.artifact_ref)
            return envelope.size_bytes, envelope.media_type

        def open_input(item):
            return artifacts.open_original(item.artifact_ref)

    total = sum(len(item.content.encode("utf-8")) for item in reviewed.project.files)
    total += sum(item.size_bytes for item in reviewed.resolved_inputs)
    if total > reviewed.project.resource_limits.max_storage_bytes:
        raise PackagerError("declared inputs exceed task storage budget")
    if not reviewed.resolved_inputs:
        return {}
    if input_metadata is None or open_input is None:
        raise PackagerError("scientific inputs require exact original callbacks or Artifact service")
    exchange_directory = Path(exchange_directory).absolute()
    if exchange_directory.is_symlink() or not exchange_directory.is_dir():
        raise PackagerError("execution exchange must be a regular directory")
    exchange_root = exchange_directory.resolve(strict=True)
    directory = exchange_directory / "scientific-inputs"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise PackagerError("scientific input directory must be a regular directory")
    directory.mkdir(exist_ok=True)
    if directory.resolve(strict=True).parent != exchange_root:
        raise PackagerError("scientific inputs are outside the execution exchange")
    inputs = {}
    for item in reviewed.resolved_inputs:
        if context is not None:context.remaining_seconds()
        size_bytes, media_type = input_metadata(item)
        if (type(size_bytes) is not int or not isinstance(media_type, str)
                or size_bytes != item.size_bytes or media_type != item.media_type):
            raise PackagerError("resolved input metadata differs from exact original")
        target = directory / item.semantic_name
        if target.exists() or target.is_symlink():
            raise PackagerError("scientific input staging slot already exists; reconcile original preparation")
        else:
            created = False
            try:
                actual_size = 0
                with open_input(item) as source, target.open("xb") as output:
                    created = True
                    for block in iter(lambda: source.read(chunk_bytes), b""):
                        if context is not None:context.remaining_seconds()
                        actual_size += len(block)
                        if actual_size > item.size_bytes:
                            raise PackagerError("original exceeds its declared input size")
                        output.write(block)
                    if actual_size != item.size_bytes:
                        raise PackagerError("input bytes differ from exact original")
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(target, 0o440)
            except BaseException:
                if created:
                    target.unlink()
                raise
        inputs[item.semantic_name] = target
    return inputs


def _write_archive(
    files: tuple[DeckFile, ...],
    binary_files: Mapping[str, bytes | Path],
    archive_path: Path,
    *, context=None,
) -> tuple[ArchiveEntry, ...]:
    entries: list[ArchiveEntry] = []
    contents = {
        item.relative_path: item.content.encode("utf-8") for item in files
    }
    contents.update(binary_files)
    with tarfile.open(archive_path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for relative_path, raw in sorted(contents.items()):
            info = tarfile.TarInfo(relative_path)
            size = _file_size(raw,context) if isinstance(raw, Path) else len(raw)
            info.size = size
            info.mode = 0o440
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with (raw.open("rb") if isinstance(raw, Path) else io.BytesIO(raw)) as source:
                archive.addfile(info, fileobj=_ContextReader(source,context) if context is not None else source)
            entries.append(
                ArchiveEntry(
                    relative_path=relative_path,
                    size_bytes=size,
                )
            )
    return tuple(entries)


class _ContextReader:
    def __init__(self,source,context):self.source,self.context=source,context
    def read(self,size=-1):
        self.context.remaining_seconds()
        value=self.source.read(size)
        self.context.remaining_seconds()
        return value


def _descriptor(
    name: str,
    path: Path,
    media_type: str,
    *,
    reported_path: Path | None = None,
    context=None,
) -> FileDescriptor:
    size = _file_size(path,context)
    return FileDescriptor(
        name=name,
        local_path=str(reported_path or path),
        size_bytes=size,
        media_type=media_type,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
