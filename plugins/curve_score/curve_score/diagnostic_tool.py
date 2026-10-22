"""Optional same-Run residual localization using the existing curve renderer."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Annotated, Callable, Literal

from pydantic import Field

from scidiscovery.artifact_agent.operation_tool_context import OperationToolContext
from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.plugin_runtime.calculations import CalculationResult as CalculationRecord
from scidiscovery.plugin_runtime.workspace import WorkspaceError, write_control_workspace_file
from scidiscovery.operation_contract import contract_diagnostic
from scidiscovery.operations.spec import CollectionSpec, ComponentRef, OutputPortSpec
from scidiscovery.operations.tooling import WorkerToolDefinition
from .analysis import CurveErrorNumerics, compute_curve_error, render_curve_error
from .analysis_tool import (
    AnalysisCurveComparison, AnalysisCurveComparisonSpec, AnalysisScoreInput,
    AnalysisScoreRequest, ScoreLimitError, UnsupportedSourceError, _aliases,
    bundle_from_sources, check_deadline, evaluate_analysis_request,
    complete_curve_calculation,
)
from .schema import CurveConsistencyReport, CurveOperatorSpec

ALGORITHM_VERSION = "analysis-curve-diagnostics.v3"
PLOT_PORT = "tool_evidence"
MAX_DETAILS_BYTES = 4 * 1024 * 1024
MAX_PLOTS = 32


class ResidualOperator(CurveOperatorSpec):
    kind: Literal["residual_rms", "residual_max_abs", "mean_signed_difference"]


class DiagnosticComparison(AnalysisCurveComparison):
    # The bounded diagnostic grid is independent of the short report record.
    evaluation_points: Annotated[int, Field(ge=2, le=257)] = 257
    operators: Annotated[tuple[ResidualOperator, ...], Field(min_length=1, max_length=1)]


class DiagnosticComparisonSpec(AnalysisCurveComparisonSpec):
    comparisons: Annotated[tuple[DiagnosticComparison, ...], Field(min_length=1, max_length=1)]


class DiagnosticRequest(AnalysisScoreRequest):
    comparison_spec: DiagnosticComparisonSpec


CHECKPOINT_DESCRIPTION = (
    "Optional exact checkpoint evidence alias returned by this diagnostic tool. "
    "Retries rendering from saved numbers without parsing, scoring or fitting. "
    "The checkpoint must be current/adopted controlled tool evidence, with the same "
    "request, algorithm and exact source identities; workspace JSON is not accepted."
)


class DiagnosticInput(AnalysisScoreInput):
    request: DiagnosticRequest
    checkpoint_alias: Annotated[str | None, Field(min_length=1, description=CHECKPOINT_DESCRIPTION)] = None


DIAGNOSTIC_PLOT_OUTPUT = OutputPortSpec(
    name=PLOT_PORT, description="Optional tool-owned calculation records, details, images and declared analysis files.",
    kind="tool_evidence", schema="opaque", media_types=("application/json", "image/png", "text/plain", "text/csv"),
    codec=ComponentRef("opaque_codec", plugin_id="general_science"),
    schema_resource=ComponentRef("opaque_schema", plugin_id="general_science"),
    semantic_contract=ComponentRef("diagnosis_report_semantic_contract", plugin_id="curve_score"),
    min_items=0, max_items=MAX_PLOTS, max_item_bytes=16 * 1024 * 1024,
    collection=CollectionSpec(max_total_bytes=64 * 1024 * 1024),
)

DIAGNOSTIC_GUIDANCE = """
If scalar scores cannot explain a discrepancy, consider {diagnostic_tool} for
local residuals, contributions and overlay/residual images; read its contract on
selection. Diagnostics are optional, with no new mandatory review/stage. Returned
details aliases retain full traces; view task-local images when useful. Residual
segments are not gradient change points or physical interfaces; crossing/width
metrics use scoring. Distinguish exploration and record method changes.
Numerical completion does not prove successful rendering. A returned checkpoint_alias
supports render-only retry with unchanged request/sources. After continuation reuse
only control-adopted checkpoints, never edited copies; changed inputs/methods require
recomputation. Unsupported statistics must not be silently replaced by other metrics.
"""


def record_metric_report(record: CalculationRecord) -> CurveConsistencyReport:
    result = record.result
    if record.algorithm_version in {"analysis-curve-diagnostics.v1", "analysis-curve-diagnostics.v2", ALGORITHM_VERSION}:
        result = result["metric_report"]
    return CurveConsistencyReport.model_validate_json(canonical_json(result), strict=True)


def _publish_plot(context: OperationToolContext, name: str, raw: bytes, sources: tuple[str, ...]) -> dict:
    if PLOT_PORT not in context.output_collections:
        raise ValueError("diagnostic plot output is not declared")
    if len(raw) > 1024 * 1024:
        raise ScoreLimitError("diagnostic_plot_bytes")
    accepted = context.accept_evidence(raw=raw, media_type="image/png", derived_from=sources,
        metadata={"kind": "curve_diagnostic", "algorithm_version": ALGORITHM_VERSION, "plot_item": name})
    path = write_control_workspace_file(
        context.workspace, Path(".operation-tools/curve-diagnostics") / (accepted["alias"] + ".png"), raw,
        replace=False, mode=0o400, create_parents=True,
    )
    return {"path": str(path), "plot_item": name, "evidence_alias": accepted["alias"]}


def _scientific_numerics(value):
    public = json.loads(canonical_json(value))
    identity = []
    for section in ("metric_report", "numerics", "localization"):
        target = public.get(section)
        if not isinstance(target, dict):
            continue
        for key in ("curve_bundle_sha256", "comparison_spec_sha256", "validation_plan_sha256"):
            if key in target:
                identity.append(([section, key], target.pop(key)))
        if section == "localization":
            for index, item in enumerate(target.get("analyses", ())):
                if "plot_sha256" in item:
                    identity.append(([section, "analyses", index, "plot_sha256"], item.pop("plot_sha256")))
    return public, identity


def _restore_checkpoint(alias, request, context, input_digests):
    raw, numerical_identity = context.read_calculation_checkpoint(alias,
        algorithm_version=ALGORITHM_VERSION,
        tool_names=("worker_curve_diagnose", "worker_tcad_curve_diagnose"),
        sources=tuple(input_digests), max_bytes=MAX_DETAILS_BYTES)
    saved = json.loads(raw)
    if (saved["algorithm_version"] != ALGORITHM_VERSION
            or canonical_json(saved["request"]) != canonical_json(request.request.raw_request)):
        raise ValueError("diagnostic_checkpoint_scope_mismatch")
    for path, value in numerical_identity:
        target = saved
        for part in path[:-1]:
            target = target[part]
        if path[-1] in target:
            raise ValueError("checkpoint contains private identity in scientific data")
        target[path[-1]] = value
    numerics = CurveErrorNumerics.model_validate_json(canonical_json(saved["numerics"]), strict=True)
    metric = CurveConsistencyReport.model_validate_json(canonical_json(saved["metric_report"]), strict=True)
    if (numerics.curve_bundle_sha256 != metric.curve_bundle_sha256
            or numerics.comparison_spec_sha256 != metric.comparison_spec_sha256):
        raise ValueError("diagnostic_checkpoint_metric_mismatch")
    return saved["metric_report"], numerics, {"evidence_alias": alias, "reused": True}


def run_diagnostic_tool(
    request: AnalysisScoreInput, context: OperationToolContext, *,
    parse_bundle: Callable = bundle_from_sources,
) -> dict:
    """Checkpoint completed numerical work before optional rendering/publication."""
    deadline = time.monotonic() + context.remaining_seconds / 2
    sources, images, diagnostics, details, checkpoint = {}, [], (), None, None
    metric_result, numerics = None, None
    base = dict(record_key=request.record_key, request=request.request.raw_request,
                input_digests={}, algorithm_version=ALGORITHM_VERSION)
    try:
        for alias in _aliases(request.request):
            check_deadline(deadline)
            try:
                sources[alias] = context.read_evidence(alias)
            except (KeyError, ValueError):
                raise ValueError("bound_source_missing") from None
        base["input_digests"] = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
        if request.checkpoint_alias is not None:
            metric_result, numerics, checkpoint = _restore_checkpoint(
                request.checkpoint_alias, request, context, base["input_digests"])
        else:
            bundle = parse_bundle(request.request, sources, deadline=deadline)
            score = evaluate_analysis_request(record_key=request.record_key,
                request=request.request.raw_request, sources=sources, bundle=bundle,
                validated_request=request.request, deadline=deadline)
            if score.status == "computed":
                metric_result = score.result
                numerics = compute_curve_error(bundle, request.request.comparison_spec,
                    record_metric_report(score),
                    comparison_key=request.request.comparison_spec.comparisons[0].comparison_key,
                    check_budget=lambda: check_deadline(deadline))
                scientific, numerical_identity = _scientific_numerics({"algorithm_version": ALGORITHM_VERSION,
                    "request": request.request.raw_request,
                    "metric_report": metric_result, "numerics": numerics.model_dump(mode="json")})
                raw = canonical_json(scientific)
                if len(raw) > MAX_DETAILS_BYTES:
                    raise ScoreLimitError("diagnostic_checkpoint_byte_limit")
                checkpoint = context.publish_calculation_checkpoint(raw,
                    algorithm_version=ALGORITHM_VERSION, record_key=request.record_key,
                    sources=tuple(sources), numerical_identity=numerical_identity)
            else:
                record = score.model_copy(update={"algorithm_version": ALGORITHM_VERSION})
    except (TimeoutError, ScoreLimitError, UnsupportedSourceError, ValueError, OSError, WorkspaceError) as error:
        reason = ("calculation_time_budget" if isinstance(error, TimeoutError)
                  else str(error) if isinstance(error, ScoreLimitError)
                  else "diagnostic_checkpoint_unavailable" if request.checkpoint_alias else "diagnostic_unavailable")
        diagnostics = (contract_diagnostic(reason, phase="tool_execution", affected_action="tool_call",
            message=str(error)[:1000] or reason),)
        # A localization/checkpoint failure cannot erase already computed metrics.
        if metric_result is None:
            status = "error" if isinstance(error, (TimeoutError, ScoreLimitError, OSError, WorkspaceError)) else "unavailable"
            record = CalculationRecord(**base, status=status, reason_code=reason)

    if metric_result is not None:
        if numerics is not None and checkpoint is not None:
            try:
                artifacts = render_curve_error(numerics, check_budget=lambda: check_deadline(deadline))
                localization = artifacts.report.model_dump(mode="json")
                plots = []
                for item, (_, raw) in zip(localization["analyses"], artifacts.plots, strict=True):
                    name = f"curve_{request.record_key}_{len(plots)+1}.png"
                    item["plot_item"] = name
                    plots.append((name, raw))
                raw_details = canonical_json(_scientific_numerics({"metric_report": metric_result, "localization": localization})[0])
                if len(raw_details) > MAX_DETAILS_BYTES:
                    raise ScoreLimitError("diagnostic_details_byte_limit")
                details = context.publish_analysis_file(raw_details, media_type="application/json",
                    kind="calculation_details", sources=(*tuple(sources), checkpoint["evidence_alias"]), suffix=".json",
                    metadata={"record_key": request.record_key, "algorithm_version": ALGORITHM_VERSION})
                for name, raw in plots:
                    check_deadline(deadline)
                    images.append(_publish_plot(context, name, raw, (*tuple(sources), checkpoint["evidence_alias"])))
            except (TimeoutError, ValueError, OSError, WorkspaceError, ImportError, RuntimeError) as error:
                diagnostics += (contract_diagnostic("diagnostic_render_incomplete", phase="tool_execution",
                    affected_action="tool_call", repairable=True,
                    message="Numbers are retained; retry with checkpoint_alias or report the missing figure: " + str(error)[:700]),)
        record = CalculationRecord(**base, status="computed", result={
            "metric_report": metric_result,
            "checkpoint_alias": checkpoint["evidence_alias"] if checkpoint else None,
            "details_alias": details["evidence_alias"] if details else None,
            "images": [{k: v for k, v in image.items() if k != "path"} for image in images],
            "limitations": (["Numerical results are retained; the requested figure is unavailable. Retry with the checkpoint or report the missing figure."] if diagnostics else []),
        })
    if record.status != "computed" and not diagnostics:
        diagnostics = (contract_diagnostic(record.reason_code or record.status,
            phase="tool_execution", affected_action="tool_call",
            message="The diagnostic is unavailable; retain the reason and submit any supported finite analysis."),)
    return {"record": complete_curve_calculation(context, record, diagnostics=diagnostics), "images": images,
            "details": details, "checkpoint": checkpoint}


DIAGNOSTIC_TOOL = WorkerToolDefinition(
    name="worker_curve_diagnose",
    description="Optionally localize one residual comparison using bound curves or explicit CSV columns. Returns compact metrics; read record.calculation_ref with the evidence reader for the scientific record. Cite record.calculation_ref; record and receipt are retained automatically. Numbers are checkpointed (<=4 MiB) before rendering. Use checkpoint_alias for a plot-only retry with exact unchanged inputs; rendering failure preserves computed metrics. Full details (<=4 MiB) and overlay/residual images have saved evidence aliases and task-local paths. One operator, 2-257 samples; no curve contract or additional Run.",
    input_model=DiagnosticInput, capability="analysis.curve_diagnose",
    contextual_handler=run_diagnostic_tool, record_attempts=True,
    evidence_ports=("tool_evidence", "recovery_manifest_output"),
)
