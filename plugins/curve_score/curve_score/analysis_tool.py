"""Bounded analysis-time scoring, shared with domain parsers without contracts."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from typing import Annotated, Any, Literal, Mapping, Callable

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator

from scidiscovery.artifact_agent.operation_tool_context import OperationToolContext
from scidiscovery.artifact_agent.schema.common import canonical_json, Identifier
from scidiscovery.plugin_runtime.calculations import CalculationResult as CalculationRecord, CalculationValidation
from scidiscovery.operation_contract import SemanticRuleViolation, contract_diagnostic, declared_violation, validation_diagnostics
from scidiscovery.operations.tooling import WorkerToolDefinition
from .schema import CurveAxis, CurveBundle, CurveComparison, CurveComparisonSpec, CurveOperatorSpec, CurveSeries, evaluate_curve_consistency

ALGORITHM_VERSION = "analysis-curve-operators.v2"
LEGACY_ALGORITHM_VERSION = "analysis-curve-operators.v1"
MAX_COMPARISONS = 16
MAX_SAMPLES = 4096
MAX_OPERATORS = 16
MAX_POINTS = 65536
MAX_REQUEST_BYTES = 12 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_WORK = 65536
MAX_INTERPOLATION_WORK = 2_000_000
SCORE_BUDGET_DESCRIPTION = (
    f"The caller's canonical request JSON is limited to {MAX_REQUEST_BYTES} UTF-8 bytes. "
    f"Each source is limited to {MAX_SOURCE_BYTES} bytes; the parsed bundle to {MAX_POINTS} points. "
    f"The sum of evaluation_points * operator count must not exceed {MAX_WORK}; "
    "the sum of that product * (reference point count + candidate point count) "
    f"must not exceed {MAX_INTERPOLATION_WORK}. Source-dependent limits are checked during execution. "
    "The calculation uses at most half the Run's remaining time. Exceeding an execution budget "
    "returns a limited calculation record; it does not require a new experiment or scoring stage."
)


class ScoreLimitError(ValueError):
    """A declared calculation or source budget was exhausted."""


class UnsupportedSourceError(NotImplementedError):
    """A source encoding is outside the parser's declared support."""


def check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("calculation_time_budget")


def admit_raw_lines(raw: bytes, *, max_lines: int = MAX_POINTS + 256) -> None:
    # Count before decode/split or domain parsers allocate per-row Python objects.
    if any(separator in raw for separator in
           (b"\x0b", b"\x0c", b"\x1c", b"\x1d", b"\x1e", b"\xc2\x85", b"\xe2\x80\xa8", b"\xe2\x80\xa9")):
        raise UnsupportedSourceError("source_line_separator_unsupported")
    if raw.count(b"\n") + raw.count(b"\r") - raw.count(b"\r\n") > max_lines:
        raise ScoreLimitError("source_line_limit")


class AnalysisSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_alias: Annotated[str, Field(min_length=1)]


class BundleSource(AnalysisSource):
    format: Literal["bundle"]


class CSVSource(AnalysisSource):
    format: Literal["csv"]
    series_key: Identifier
    case_key: Identifier
    role: Identifier
    x_axis: CurveAxis
    y_axis: CurveAxis
    x_column: str
    y_column: str


class AnalysisCurveComparison(CurveComparison):
    evaluation_points: Annotated[int, Field(ge=2, le=MAX_SAMPLES)] = 257
    operators: Annotated[tuple[CurveOperatorSpec, ...], Field(min_length=1, max_length=MAX_OPERATORS)]


class AnalysisCurveComparisonSpec(CurveComparisonSpec):
    comparisons: Annotated[tuple[AnalysisCurveComparison, ...], Field(min_length=1, max_length=MAX_COMPARISONS)]


class ScoreRequest(BaseModel):
    """Validated execution values plus the caller's unchanged JSON representation."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"description": SCORE_BUDGET_DESCRIPTION})
    sources: Annotated[tuple[AnalysisSource, ...], Field(min_length=1, max_length=40)]
    comparison_spec: AnalysisCurveComparisonSpec
    _raw_request: dict[str, Any] = PrivateAttr()

    @property
    def raw_request(self) -> dict[str, Any]:
        # Execution defaults must never enter historical receipts or request digests.
        return json.loads(canonical_json(self._raw_request))


def parse_score_request(model: type[ScoreRequest], value: Any) -> ScoreRequest:
    """Keep strict JSON array semantics while retaining the caller's raw fields."""
    if isinstance(value, model):
        return value
    raw = canonical_json(value)
    if len(raw) > MAX_REQUEST_BYTES:
        raise declared_violation(f"request exceeds {MAX_REQUEST_BYTES} canonical JSON bytes")
    parsed = model.model_validate_json(raw, strict=True)
    parsed._raw_request = json.loads(raw)
    return parsed


class AnalysisScoreRequest(ScoreRequest):
    sources: Annotated[
        tuple[Annotated[BundleSource | CSVSource, Field(discriminator="format")], ...],
        Field(min_length=1, max_length=40),
    ]


class AnalysisScoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_key: Identifier
    request: AnalysisScoreRequest

    @field_validator("request", mode="before")
    @classmethod
    def _parse_request(cls, value):
        return parse_score_request(cls.model_fields["request"].annotation, value)


def _aliases(request: dict[str, Any] | ScoreRequest) -> tuple[str, ...]:
    if isinstance(request, ScoreRequest):
        return tuple(dict.fromkeys(item.input_alias for item in request.sources))
    # Persisted failed requests need not satisfy the current call schema.
    mappings = request.get("sources", [])
    if not isinstance(mappings, (list, tuple)) or len(mappings) > 40:
        raise ValueError("source_mapping_limit")
    if any(not isinstance(item, Mapping) or not isinstance(item.get("input_alias"), str) for item in mappings):
        raise ValueError("invalid_input_alias")
    names = tuple(dict.fromkeys(item["input_alias"] for item in mappings))
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("invalid_input_alias")
    return names


def _digests(request: dict[str, Any] | ScoreRequest, sources: Mapping[str, bytes], *, deadline: float | None = None) -> dict[str, str]:
    names = _aliases(request)
    digests = {}
    for name in names:
        check_deadline(deadline)
        if name in sources:
            digests[name] = hashlib.sha256(sources[name]).hexdigest()
    return digests


def csv_series(mapping: CSVSource, raw: bytes, *, deadline: float | None = None) -> CurveSeries:
    """Read only explicit columns. Original bytes remain the evidence source."""
    if len(raw) > MAX_SOURCE_BYTES:
        raise ScoreLimitError("source_byte_limit")
    check_deadline(deadline)
    admit_raw_lines(raw)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    x, y = mapping.x_column, mapping.y_column
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("csv_header_ambiguous")
    if x not in reader.fieldnames or y not in reader.fieldnames:
        raise ValueError("csv_column_missing")
    points = []
    for row in reader:
        check_deadline(deadline)
        if len(points) >= MAX_POINTS:
            raise ScoreLimitError("source_point_limit")
        try:
            points.append({"x": float(row[x]), "y": float(row[y])})
        except (ValueError, TypeError) as error:
            raise ValueError("csv_non_numeric_column") from error
    return CurveSeries.model_validate_json(canonical_json({
        **{key: getattr(mapping, key) for key in ("series_key", "case_key", "role", "x_axis", "y_axis")},
        "points": points, "availability": {"status": "available", "rationale": "Explicit CSV columns."},
        "source_locator": mapping.input_alias,
    }), strict=True)


def bundle_from_sources(request: AnalysisScoreRequest, sources: Mapping[str, bytes], *, deadline: float | None = None) -> CurveBundle:
    series = []
    for mapping in request.sources:
        check_deadline(deadline)
        raw = sources[mapping.input_alias]
        if isinstance(mapping, BundleSource):
            if len(raw) > MAX_SOURCE_BYTES:
                raise ScoreLimitError("source_byte_limit")
            series.extend(CurveBundle.model_validate_json(raw, strict=True).series)
        else:
            series.append(csv_series(mapping, raw, deadline=deadline))
        if sum(len(item.points) for item in series) > MAX_POINTS:
            raise ScoreLimitError("source_point_limit")
    return CurveBundle(source_profile="analysis_inputs", source_digests=tuple(dict.fromkeys(_digests(request, sources).values())), series=tuple(series))


def evaluate_analysis_request(*, record_key: str, request: dict[str, Any], sources: Mapping[str, bytes], bundle: CurveBundle | None = None, deadline: float | None = None, validated_request: ScoreRequest | None = None, algorithm_version: str = ALGORITHM_VERSION) -> CalculationRecord:
    """Evaluate explicit request; a domain adapter may supply its freshly parsed bundle."""
    try:
        parsed = validated_request or parse_score_request(AnalysisScoreRequest, request)
    except ValidationError as error:
        return CalculationRecord(record_key=record_key, request=request, input_digests={},
            algorithm_version=algorithm_version, status="unavailable", reason_code="invalid_arguments",
            diagnostics=validation_diagnostics(error, schema=AnalysisScoreRequest.model_json_schema())[:8])
    base = dict(record_key=record_key, input_digests={}, request=parsed.raw_request, algorithm_version=algorithm_version)
    try:
        check_deadline(deadline)
        aliases = _aliases(parsed)
        base["input_digests"] = _digests(parsed, sources, deadline=deadline)
        if any(name not in sources for name in aliases):
            raise ValueError("bound_source_missing")
        if bundle is None:
            bundle = bundle_from_sources(parsed, sources, deadline=deadline)
        if sum(len(item.points) for item in bundle.series) > MAX_POINTS:
            raise ScoreLimitError("source_point_limit")
    except TimeoutError:
        return CalculationRecord(**base, status="error", reason_code="calculation_time_budget")
    except ScoreLimitError as error:
        return CalculationRecord(**base, status="error", reason_code=str(error))
    except UnsupportedSourceError as error:
        return CalculationRecord(**base, status="unsupported", reason_code=str(error))
    except (ValueError, KeyError, csv.Error) as error:
        message = str(error)
        reason = message if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", message) else "source_data_invalid"
        return CalculationRecord(**base, status="unavailable", reason_code=reason)

    spec = parsed.comparison_spec
    status, reason, result = "computed", None, None
    try:
        work = sum(item.evaluation_points * len(item.operators) for item in spec.comparisons)
        points_by_key = {item.series_key: len(item.points) for item in bundle.series}
        interpolation_work = sum(item.evaluation_points * len(item.operators) * (points_by_key.get(item.reference_series, 0) + points_by_key.get(item.candidate_series, 0)) for item in spec.comparisons)
        if work > MAX_WORK or interpolation_work > MAX_INTERPOLATION_WORK:
            raise ScoreLimitError("calculation_work_limit")
        report = evaluate_curve_consistency(bundle, spec, operator_version=algorithm_version, check_budget=lambda: check_deadline(deadline))
        check_deadline(deadline)
        # Keep aggregate/scalar metrics only; crossing samples can be very large.
        result = report.model_dump(mode="json")
        for comparison in result["comparisons"]:
            for metric in comparison["metrics"]:
                metric.pop("crossing_support", None)
        if any(item.status == "unavailable" for item in report.comparisons):
            # Preserve available calculations in a computed partial report; unavailable
            # comparisons keep their evaluator reason codes and no fabricated values.
            if all(item.status == "unavailable" for item in report.comparisons):
                reasons = [metric.reason_code for item in report.comparisons for metric in item.metrics if metric.reason_code is not None]
                status, reason, result = "unavailable", (reasons[0] if reasons else "comparisons_unavailable"), None
        if result is not None and len(canonical_json({**base, "result": result})) > 31 * 1024:
            raise ScoreLimitError("record_byte_limit")
    except TimeoutError:
        status, reason, result = "error", "calculation_time_budget", None
    except ScoreLimitError as error:
        status, reason, result = "error", str(error), None
    return CalculationRecord(**base, status=status, result=result, reason_code=reason)


def replay_calculation(record: CalculationRecord, sources: Mapping[str, bytes], *, bundle: CurveBundle | None = None, deadline: float | None = None) -> None:
    try:
        replay_sources = ({key: sources[key] for key in record.input_digests if key in sources}
                          if record.status == "error" else sources)
        digests = _digests(record.request, replay_sources, deadline=deadline)
    except TimeoutError as error:
        raise SemanticRuleViolation("calculation replay budget exhausted; remove this calculation and submit limited analysis") from error
    except (ValueError, KeyError, TypeError):
        digests = {}
    matches = (all(digests.get(name) == digest for name, digest in record.input_digests.items())
               if record.status == "error" else record.input_digests == digests)
    if record.algorithm_version not in {ALGORITHM_VERSION, LEGACY_ALGORITHM_VERSION} or not matches:
        raise SemanticRuleViolation("calculation algorithm or exact source bytes differ")
    if record.status == "error":
        if record.result is not None:
            raise SemanticRuleViolation("unfinished calculation cannot supply numeric evidence")
        return
    replay = evaluate_analysis_request(record_key=record.record_key, request=record.request, sources=sources, bundle=bundle, deadline=deadline, algorithm_version=record.algorithm_version)
    if replay.status == "error":
        raise SemanticRuleViolation("calculation replay budget exhausted; remove this calculation and submit limited analysis")
    def same_record(candidate):
        return canonical_json(record.model_dump(mode="json", exclude={"attempt", "diagnostics", "validation"})) == canonical_json(candidate.model_dump(mode="json", exclude={"attempt", "diagnostics", "validation"}))
    if same_record(replay):
        return
    if record.algorithm_version == LEGACY_ALGORITHM_VERSION and replay.result is not None:
        # V1 shipped in two wire layouts. Accept a historical digest only after
        # recomputing every metric and verifying all original source bytes. Never
        # drop nonempty shared-support declarations or ignore numeric differences.
        from .schema import pre_shared_support_digest
        actual_bundle = bundle or bundle_from_sources(parse_score_request(AnalysisScoreRequest, record.request), sources, deadline=deadline)
        old_digest = pre_shared_support_digest(actual_bundle)
        if old_digest is not None:
            result = {**replay.result, "curve_bundle_sha256": old_digest}
            if same_record(replay.model_copy(update={"result": result})):
                return
    raise SemanticRuleViolation("calculation record differs from deterministic replay")


def complete_curve_calculation(context, record, *, diagnostics=()):
    """Keep curve identity in the sealed receipt and publish bounded metrics."""
    public_result = json.loads(canonical_json(record.result))
    if isinstance(public_result, dict):
        for target in (public_result, public_result.get("metric_report"), public_result.get("localization")):
            if isinstance(target, dict):
                for key in ("curve_bundle_sha256", "comparison_spec_sha256", "validation_plan_sha256"):
                    target.pop(key, None)
    response = context.complete_calculation(record, diagnostics=diagnostics, public_result=public_result)
    result = {key: response[key] for key in
              ("record_key", "status", "reason_code", "algorithm_version", "diagnostics") if key in response}
    report = response.get("result") or {}
    metric_report = report.get("metric_report", report)
    comparisons = metric_report.get("comparisons", [])
    result["summary"] = {
        "aggregate_status": metric_report.get("aggregate_status"),
        "comparison_count": len(comparisons),
        "comparisons": [{"comparison_key": item["comparison_key"], "status": item["status"],
            "metrics": [{key: metric[key] for key in ("operator_key", "kind", "status", "value", "unit", "reason_code") if key in metric}
                        for metric in item.get("metrics", [])[:4]],
            "omitted_metrics": max(0, len(item.get("metrics", []))-4)} for item in comparisons[:4]],
        "omitted_comparisons": max(0, len(comparisons)-4),
        "interpretation_boundary": "deterministic_metrics_only_no_physical_interpretation",
        "limitations": report.get("limitations", []),
    }
    return {**result, "calculation_ref": response["calculation_ref"]}


def run_score_tool(request: AnalysisScoreInput, context: OperationToolContext,
                   evaluate: Callable[..., CalculationRecord]) -> dict[str, Any]:
    deadline = time.monotonic() + context.remaining_seconds / 2
    sources = {}
    aliases = _aliases(request.request)
    raw_request = request.request.raw_request
    try:
        for name in aliases:
            check_deadline(deadline)
            try:
                sources[name] = context.read_evidence(name)
            except (KeyError, ValueError):
                continue
        check_deadline(deadline)
        result = evaluate(record_key=request.record_key, request=raw_request,
                          sources=sources, deadline=deadline, validated_request=request.request)
        check_deadline(deadline)
    except TimeoutError:
        result = CalculationRecord(record_key=request.record_key, request=raw_request,
            input_digests={}, algorithm_version=ALGORITHM_VERSION, status="error",
            reason_code="calculation_time_budget")
    if result.status == 'computed':
        validation = _planned_validation(context, result)
        if validation is not None:
            candidate = result.model_copy(update={'validation': validation})
            if len(canonical_json(candidate)) <= 32 * 1024:
                result = candidate
    diagnostics = () if result.status == "computed" else (contract_diagnostic(
        result.reason_code or result.status, phase="tool_execution", affected_action="tool_call",
        message="The tool returned no numeric result; retain its specific reason and limitations.",
    ),)
    return complete_curve_calculation(context, result, diagnostics=diagnostics)


def _planned_validation(context, record):
    """Optional existing input contract; ordinary scoring remains available."""
    from scidiscovery.artifact_agent.schema.experiment import ExperimentPortfolio
    from scidiscovery.artifact_agent.schema.validation import ValidationCheckResult
    from .schema import CurveExperimentContract, validate_curve_experiment_contract
    plans = context.input_names_for_port('experiment_plan')
    if len(plans) != 1:
        return None
    candidates = [alias for alias in context.input_names_for_port('reference_material')
        if context.source_descriptor(alias).artifact_ref.schema_id == 'scidiscovery.curve-experiment-contract.v1']
    if len(candidates) != 1:
        return None
    plan_alias, condition_alias = plans[0], candidates[0]
    try:
        portfolio = ExperimentPortfolio.model_validate_json(context.read_input(plan_alias), strict=True)
        contract = CurveExperimentContract.model_validate_json(context.read_input(condition_alias), strict=True)
        if context.source_descriptor(plan_alias).artifact_ref not in context.source_descriptor(condition_alias).parent_refs:
            return None
        if canonical_json(contract.comparison_spec) != canonical_json(record.request['comparison_spec']):
            return None
        covered = validate_curve_experiment_contract(contract, portfolio,
            expected_evaluator_profile='scidiscovery.curve-score.v1')
        plan = next(p for p in portfolio.validation_plans if p.experiment_key == contract.experiment_key)
        results = []
        declarations = {c.comparison_key: c for c in contract.comparison_spec.comparisons}
        for comparison in record.result['comparisons']:
            operators = {o.operator_key: o for o in declarations[comparison['comparison_key']].operators}
            metrics = {m['operator_key']: m for m in comparison['metrics']}
            for tested in comparison['checks']:
                operator = operators[tested['operator_key']]
                if operator.validation_check_key not in covered or tested['status'] not in {'pass', 'fail'}:
                    continue
                metric = metrics[tested['operator_key']]
                observed = abs(tested['observed_value']) if operator.threshold.comparison == 'abs_le' else tested['observed_value']
                results.append(ValidationCheckResult(check_key=operator.validation_check_key,
                    status=tested['status'], evaluation_mode='deterministic_threshold',
                    observed_value=observed, observed_text=f"{observed} {metric['unit']}", unit=metric['unit'], evidence_keys=tuple(record.input_digests),
                    rationale='Registered curve evaluator applied the exact predeclared comparison contract.'))
        if not results or len({r.check_key for r in results}) != len(results):
            return None
        return CalculationValidation(plan_source=plan_alias, conditions_source=condition_alias,
            result_sources=tuple(record.input_digests), evaluator_profile='scidiscovery.curve-score.v1',
            experiment_key=plan.experiment_key, plan_key=plan.plan_key, results=tuple(results))
    except (ValidationError, SemanticRuleViolation, ValueError):
        return None  # Missing optional progress support is not a scoring gate.


def score_tool(request: AnalysisScoreInput, context: OperationToolContext) -> dict[str, Any]:
    return run_score_tool(request, context, evaluate_analysis_request)


CURVE_SCORE_TOOL = WorkerToolDefinition(name="worker_curve_score", description="Optionally score bound bundle/explicit CSV columns. Returns compact metrics; read calculation_ref with the evidence reader for the scientific record. Cite the returned calculation_ref; the complete record and receipt are saved automatically. Unsupported metrics are never substituted. When experiment_plan and exactly one predeclared curve contract in reference_material match the unchanged comparison_spec, the registered evaluator retains an optional ValidationCheckResult projection. At verified-progress consumption, prior sealing requires the exact frozen plan and this calculation's exact conditions to be immutable ancestors of every observed result. Each sealing proof checks at most 64 distinct original envelopes; absent, damaged or incompletely covered lineage leaves progress unknown. Registration timestamps are not order proof. Sealing and controlled evaluation do not prove original generation time, source novelty or scientific authenticity. Ordinary scoring remains available without verified progress.", input_model=AnalysisScoreInput, capability="analysis.curve_score", contextual_handler=score_tool, record_attempts=True, evidence_ports=("tool_evidence", "recovery_manifest_output"))
