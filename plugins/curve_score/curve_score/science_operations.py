"""Curve-specific diagnosis Operations and components."""

from __future__ import annotations
from scidiscovery.operations.spec import InputDerivationSpec

from scidiscovery.operations.input_validation import parse_bound_json, prior_analysis_sources

import json
import re
from typing import Any

from scidiscovery.artifact_agent.schema.common import canonical_json, canonical_sha256
from .analysis import (
    CurveDiagnosticAnalysisPackage,
    analyze_curve_error,
    validate_curve_error_inputs,
    CurveAnalysisUnavailable,
    failed_residual_analysis_available,
    validate_curve_analysis_package,
    validate_curve_error_plot_collection,
)
from .curve_contract_compiler import validate_compiled_curve_contract, validate_curve_contract_inputs
from .diagnostic_tool import DIAGNOSTIC_GUIDANCE, DIAGNOSTIC_PLOT_OUTPUT, record_metric_report
from .analysis_files import GUIDANCE as ANALYSIS_FILES_GUIDANCE
from scidiscovery.plugin_runtime.calculations import analysis_evidence_aliases
from .schema import (
    metric_bundle_matches,
    CurveBundle,
    CurveComparisonSpec,
    CurveConsistencyReport,
    CurveExperimentContract,
    validate_curve_experiment_contract,
)
from .transform_adapter import CURVE_SCORE_OPERATION
from scidiscovery.artifact_agent.schema.experiment import (
    ExperimentPortfolio,
)
from scidiscovery.artifact_agent.schema.layered_diagnosis import (
    LayeredDiagnosisReport,
    validate_layered_diagnosis,
)
from scidiscovery.artifact_agent.schema.research_cycle import (
    ScientificReview,
    validate_scientific_review,
)
from scidiscovery.artifact_agent.schema.research_objective import (
    ResearchObjectiveContract,
)
from scidiscovery.operation_declaration import (
    OPERATION_AGENT_PREAMBLE,
    payload_validator,
    schema_resource,
    scientific_semantic_contract,
    scientific_agent_operation,
)
from scidiscovery.operation_contract import SemanticRuleViolation, declared_violation, DiagnosticError, contract_diagnostic, validate_evidence_source_aliases
from scidiscovery.operations.input_validation import OperationInvocationError
from scidiscovery.operations.spec import (
    CallableComponent,
    CollectionSpec,
    ComponentRef,
    ComponentSpec,
    ExecutorRef,
    InputPortSpec,
    InputValidationSpec,
    LimitsSpec,
    OperationDescription,
    OperationSpec,
    OutputPortSpec,
    ReviewSpec,
)


_GENERAL_SCIENCE = "general_science"
_GENERAL_RESOURCES = {
    "opaque_schema",
    "tool_evidence_schema",
    "experiment_portfolio_schema",
    "hypothesis_schema",
    "scientific_review_schema",
    "research_objective_schema",
}


def _resource_ref(name: str) -> ComponentRef:
    return ComponentRef(
        name,
        plugin_id=_GENERAL_SCIENCE if name in _GENERAL_RESOURCES else None,
    )




_SCHEMA_RESOURCES = {
    "opaque": "opaque_schema",
    "scidiscovery.experiment-portfolio.v1": "experiment_portfolio_schema",
    "scidiscovery.research-objective.v1": "research_objective_schema",
    "scidiscovery.curve-experiment-contract.v1": "curve_contract_schema",
    "scidiscovery.scientific-review.v1": "scientific_review_schema",
    "scidiscovery.curve-consistency-report.v1": "metric_report_schema",
    "scidiscovery.curve-bundle.v1": "curve_bundle_schema",
    "scidiscovery.curve-diagnostic-analysis.v1": "curve_analysis_package_schema",
    "scidiscovery.layered-diagnosis.v1": "diagnosis_schema",
    "scidiscovery.tool-evidence-manifest.v1": "tool_evidence_schema",
    "scidiscovery.hypothesis-proposal.v2": "hypothesis_schema",
}

_EVIDENCE_PATHS = {
    "scidiscovery.scientific-review.v1": ("/evidence",),
}


def _agent_marker() -> None:
    return None


def _curve_contract_inputs(sources: dict[str, bytes]) -> None:
    objective = parse_bound_json(ResearchObjectiveContract, sources["research_objective"],
                                 admission_port="research_objective")
    plan = parse_bound_json(ExperimentPortfolio, sources["experiment_plan"],
                            admission_port="experiment_plan")
    parse_bound_json(CurveBundle, sources["reference_bundle"], admission_port="reference_bundle")
    try:
        validate_curve_contract_inputs(objective, plan)
    except SemanticRuleViolation as error:
        raise OperationInvocationError("input_objective_mismatch", port="research_objective",
                                       field="objective_key") from error


def _curve_contract_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    del handoff
    contract = CurveExperimentContract.model_validate_json(
        canonical_json(payload), strict=True
    )
    plan = parse_bound_json(ExperimentPortfolio, sources["experiment_plan"])
    objective = parse_bound_json(ResearchObjectiveContract, sources["research_objective"])
    reference_bundle = parse_bound_json(CurveBundle, sources["reference_bundle"])
    validate_compiled_curve_contract(
        contract,
        objective=objective,
        portfolio=plan,
        reference_bundle=reference_bundle,
    )


def _curve_contract_review_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    review = ScientificReview.model_validate_json(
        canonical_json(payload), strict=True
    )
    if review.review_target != "domain_contract":
        raise SemanticRuleViolation("curve contract review_target must be domain_contract")
    expected_verdict = "blocked" if review.verdict == "reject" else review.verdict
    if handoff.get("verdict") != expected_verdict:
        raise SemanticRuleViolation("curve contract review handoff differs from payload verdict")
    validate_evidence_source_aliases(payload, sources)


def _nonempty(raw: bytes) -> None:
    if not raw:
        raise ValueError("collection item must not be empty")


def _input(
    name: str,
    description: str,
    schema: str,
    *,
    min_items: int = 1,
    max_items: int = 1,
    max_item_bytes: int = 1024 * 1024,
    exposure: str = "full",
    usage: str = "claim_evidence",
) -> InputPortSpec:
    return InputPortSpec(
        name=name,
        description=description,
        schema=schema,
        media_types=("application/json",),
        codec=ComponentRef("json_codec", plugin_id=_GENERAL_SCIENCE),
        schema_resource=_resource_ref(_SCHEMA_RESOURCES[schema]),
        min_items=min_items,
        max_items=max_items,
        max_item_bytes=max_item_bytes,
        exposure=exposure,
        usage=usage,
    )


def _review_input(name: str, description: str) -> InputPortSpec:
    return _input(
        name,
        description,
        "scidiscovery.scientific-review.v1",
        max_item_bytes=64 * 1024,
        exposure="handoff_only",
        usage="prior_signal",
    )


def _output(
    name: str,
    description: str,
    kind: str,
    schema: str,
    validator: ComponentRef,
    *,
    max_item_bytes: int,
    media_types: tuple[str, ...] = ("application/json",),
    min_items: int = 1,
    max_items: int = 1,
    collection: CollectionSpec | None = None,
    context_validator: ComponentRef | None = None,
    context_sources: tuple[str, ...] = (),
    semantic_contract: ComponentRef | None = None,
) -> OutputPortSpec:
    opaque = schema == "opaque"
    contract = semantic_contract or ComponentRef("diagnosis_report_semantic_contract")
    payload_rule, context_rule = {
        "diagnosis_semantic_contract": (
            "curve.diagnosis.report_consistency", "curve.diagnosis.input_binding"
        ),
        "diagnosis_report_semantic_contract": (
            "curve.diagnosis.report_consistency", "curve.diagnosis.input_binding"
        ),
        "curve_contract_semantic_contract": (
            "curve.contract.case_metric_closure", "curve.contract.objective_binding"
        ),
        "curve_contract_review_contract": (
            "curve.review.verdict_consistency", "curve.review.subject_binding"
        ),
    }[contract.component_id]
    return OutputPortSpec(
        name=name,
        description=description,
        kind=kind,
        schema=schema,
        media_types=media_types,
        codec=ComponentRef(
            "opaque_codec" if opaque else "json_codec",
            plugin_id=_GENERAL_SCIENCE,
        ),
        schema_resource=_resource_ref(_SCHEMA_RESOURCES[schema]),
        min_items=min_items,
        max_items=max_items,
        max_item_bytes=max_item_bytes,
        validator=validator,
        validator_rule_id=payload_rule,
        semantic_contract=contract,
        collection=collection,
        context_validator=context_validator,
        context_rule_id=context_rule if context_validator else None,
        context_sources=context_sources,
        evidence_paths=_EVIDENCE_PATHS.get(schema, ()),
    )


def _inventory_input(name: str, description: str, *, min_items: int = 0, max_items: int = 4) -> InputPortSpec:
    return InputPortSpec(name=name, description=description, schema="*", media_types=("*/*",),
        codec=ComponentRef("opaque_codec", plugin_id=_GENERAL_SCIENCE),
        schema_resource=ComponentRef("wildcard_schema", plugin_id=_GENERAL_SCIENCE), min_items=min_items,
        max_items=max_items, max_item_bytes=16 * 1024 * 1024,
        exposure="on_demand", usage="evidence_inventory")


def _diagnosis_inputs(sources: dict[str, bytes]) -> None:
    prior_analysis_sources(sources)
    if "metric_report" in sources and "experiment_plan" in sources:
        plan = parse_bound_json(ExperimentPortfolio, sources["experiment_plan"], admission_port="experiment_plan")
        report = parse_bound_json(CurveConsistencyReport, sources["metric_report"], admission_port="metric_report")
        if report.validation_plan_sha256 not in {canonical_sha256(item) for item in plan.validation_plans}:
            raise OperationInvocationError("input_metric_plan_mismatch", port="metric_report", field="validation_plan_sha256",
                message="The metric report must identify a validation plan in the bound experiment.")
    for port, model in (("experiment_plan", ExperimentPortfolio), ("experiment_review", ScientificReview)):
        if port in sources:
            parse_bound_json(model, sources[port], admission_port=port)


def _diagnosis_operation(operation_id: str, purpose: str, applies_when: str, *, prompt: str) -> OperationSpec:
    return scientific_agent_operation(
        operation_id, purpose, applies_when,
        "Changing evidence, inventing unsupported metrics, or executing experiments.",
        decision_fields=("overall_verdict", "claim_allowed", "summary", "objective_assessment", "hypothesis_assessments", "next_action", "remaining_contradiction", "limitations"),
        agent=ComponentRef("diagnosis_agent"), workspace=ComponentRef("analysis_workspace"),
        prompt=ComponentRef(prompt), tools=(ComponentRef("analysis_score_tool"), ComponentRef("analysis_diagnostic_tool"), ComponentRef("analysis_files_tool")),
        native_view_image=True,
        input_validation=InputValidationSpec(ComponentRef("diagnosis_inputs"), "science.diagnosis.history_inputs",
            "Analyze selected immutable results. Plans and reviews are optional scientific context. When both a plan and precomputed metric report are supplied, their declared calculation identity must agree."),
        inputs=(
            _input("experiment_plan", "Exact experiment portfolio.", "scidiscovery.experiment-portfolio.v1", min_items=0, max_item_bytes=2 * 1024 * 1024, usage="evidence_inventory"),
            _review_input("experiment_review", "Optional scientific review and its limitations.").model_copy(update={"min_items":0, "exposure": "on_demand", "usage": "evidence_inventory"}),
            _inventory_input("experiment_results", "Exact experiment reports, observations or incomplete results.", min_items=1),
            _inventory_input("reference_material", "Optional reference bytes and explicit tables.", max_items=8),
            _inventory_input("current_progress", "Relevant prior progress and limitations."),
            _input("prior_analysis", "Optional exact prior analysis for source and calculation reuse.",
                "scidiscovery.layered-diagnosis.v1", min_items=0, max_item_bytes=128*1024, usage="evidence_inventory"),
            _input("prior_analysis_manifest", "The prior analysis's same-producer direct manifest parent. Every reused source must also be bound in this Run.",
                "scidiscovery.tool-evidence-manifest.v1", min_items=0, max_item_bytes=1024*1024, usage="evidence_inventory").model_copy(update={"agent_visible":False, "derivation":InputDerivationSpec(anchor_port="prior_analysis", producer_output_port="recovery_manifest_output")}),
            _input("metric_report", "Optional precomputed metrics, limited to actual coverage.", "scidiscovery.curve-consistency-report.v1", min_items=0, max_item_bytes=2 * 1024 * 1024),
            _input("curve_bundle", "Optional canonical curves.", "scidiscovery.curve-bundle.v1", min_items=0, max_item_bytes=8 * 1024 * 1024),
        ),
        outputs=(_output("layered_diagnosis", "Bounded analysis of exact results with optional calculations.", "layered_diagnosis", "scidiscovery.layered-diagnosis.v1", ComponentRef("diagnosis_validator"),
            max_item_bytes=128 * 1024, context_validator=ComponentRef("diagnosis_context"),
            context_sources=("experiment_plan", "experiment_review", "experiment_results", "reference_material", "current_progress", "metric_report", "curve_bundle", "recovery_manifest_output", "prior_analysis", "prior_analysis_manifest", "tool_evidence")),
            OutputPortSpec(name="recovery_manifest_output", description="Runtime-owned source and attempt receipts.",
                schema="scidiscovery.tool-evidence-manifest.v1", media_types=("application/json",),
                codec=ComponentRef("json_codec", plugin_id=_GENERAL_SCIENCE),
                schema_resource=ComponentRef("tool_evidence_schema", plugin_id=_GENERAL_SCIENCE), kind="tool_evidence_manifest",
                min_items=0, max_items=1, max_item_bytes=1024*1024,
                collection=CollectionSpec(max_total_bytes=1024*1024)), DIAGNOSTIC_PLOT_OUTPUT),
        timeout=900,
        max_input_bytes=272 * 1024 * 1024, max_output_bytes=1152 * 1024 + 64 * 1024 * 1024,
        max_files=34, consequence="scientific",
    ).model_copy(update={"version": "4"})









AGENT_OPERATIONS = (
    _diagnosis_operation(
        "science.result.diagnose.v1",
        "Analyze exact experiment results with optional deterministic calculations.",
        "Reviewed experiment results are available, including incomplete or failed results.",
        prompt="diagnosis_prompt",
    ),
)


def validate_analysis_report(diagnosis: LayeredDiagnosisReport, portfolio: ExperimentPortfolio,
                             metric_report: CurveConsistencyReport | None = None, *,
                             calculations=None) -> None:
    """Check references to recorded work; the analyst owns scientific conclusions."""
    plans = tuple(item for item in portfolio.validation_plans
                  if item.experiment_key == diagnosis.experiment_key and item.plan_key == diagnosis.plan_key)
    if len(plans) != 1 or diagnosis.study_kind != portfolio.study_kind:
        raise declared_violation("analysis must identify the exact study and validation plan")
    assessment = diagnosis.objective_assessment
    if assessment is not None and assessment.objective_key != portfolio.objective_key:
        raise declared_violation("analysis objective differs from plan", path="$.objective_assessment")
    records = () if calculations is None else calculations
    comparisons = set()
    for record in records:
        if record.status == "computed":
            report = record_metric_report(record)
            comparisons.update(item.comparison_key for item in report.comparisons)
    if metric_report is not None and metric_report.validation_plan_sha256 == canonical_sha256(plans[0]):
        comparisons.update(item.comparison_key for item in metric_report.comparisons)
    if assessment is not None and not set(assessment.comparison_keys).issubset(comparisons):
        raise declared_violation("objective assessment references an unknown comparison", path="$.objective_assessment")


def _diagnosis_context(payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]) -> None:
    del handoff
    diagnosis = LayeredDiagnosisReport.model_validate_json(canonical_json(payload), strict=True)
    portfolio = parse_bound_json(ExperimentPortfolio, sources["experiment_plan"]) if "experiment_plan" in sources else None
    calculations = sources.verified_calculations(diagnosis)
    metric_report = parse_bound_json(CurveConsistencyReport, sources["metric_report"]) if "metric_report" in sources else None
    if portfolio is not None:
        validate_analysis_report(diagnosis, portfolio, metric_report, calculations=calculations)
    _validate_analysis_evidence(diagnosis, sources)
    _validate_diagnosis_references(diagnosis, sources)


def _validate_diagnosis_references(diagnosis, sources):
    # Locally named citations have already been resolved by the domain checker.
    # Direct input/tool aliases need no second entry in the evidence table.
    validate_evidence_source_aliases(diagnosis.model_dump(mode="json"),
        set(sources) | {item.source_key for item in diagnosis.evidence}
        | {item.source_key for item in diagnosis.source_references}
        )


def _validate_analysis_evidence(diagnosis, sources, *, package=None) -> None:
    references = {}
    for index, reference in enumerate(diagnosis.source_references):
        if reference.source_key in references and reference != references[reference.source_key]:
            raise declared_violation("analysis source key has conflicting source mappings", path=f"$.source_references[{index}].source_key")
        references[reference.source_key] = reference
    for reference in references.values():
        if reference.input_alias not in sources:
            raise declared_violation("analysis source reference is not a bound input", path="$.source_references")
    aliases = analysis_evidence_aliases(diagnosis.evidence, diagnosis.source_references, sources)
    for evidence in diagnosis.evidence:
        alias = aliases[evidence.source_key]
        if alias not in sources:
            raise declared_violation("raw analysis evidence requires a bound input locator", path="$.evidence")
        if package is not None and alias == "curve_analysis_package" and evidence.locator != alias:
            pointer = evidence.locator[len(alias) + 1:] if evidence.locator.startswith(alias + ":") else evidence.locator
            value = package
            if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
                raise declared_violation("precomputed evidence requires a valid package JSON pointer", path="$.evidence")
            try:
                for part in pointer[1:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    if isinstance(value, list):
                        if not key.isdigit() or str(int(key)) != key:
                            raise KeyError(key)
                        value = value[int(key)]
                    else:
                        value = value[key]
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise declared_violation("precomputed evidence pointer is outside the exact package", path="$.evidence") from error


def _curve_diagnosis_inputs(sources: dict[str, bytes]) -> None:
    package = parse_bound_json(CurveDiagnosticAnalysisPackage, sources["curve_analysis_package"],
                              admission_port="curve_analysis_package")
    try:
        covered = validate_curve_experiment_contract(
            package.curve_contract, package.experiment_plan,
            expected_evaluator_profile=CURVE_SCORE_OPERATION,
        )
    except SemanticRuleViolation as error:
        raise OperationInvocationError("input_curve_contract_mismatch", port="curve_analysis_package") from error
    report = package.metric_report
    if report.validation_scope != "complete_plan":
        raise OperationInvocationError("input_metric_scope_invalid", port="curve_analysis_package")
    if report.covered_validation_check_keys != covered:
        raise OperationInvocationError("input_metric_coverage_mismatch", port="curve_analysis_package")
    try:
        validate_curve_analysis_package(package)
    except ValueError as error:
        raise OperationInvocationError("input_curve_analysis_mismatch", port="curve_analysis_package") from error


def _curve_diagnosis_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    del handoff
    package = parse_bound_json(CurveDiagnosticAnalysisPackage, sources["curve_analysis_package"])
    diagnosis = LayeredDiagnosisReport.model_validate_json(
        canonical_json(payload), strict=True
    )
    _validate_analysis_evidence(diagnosis, sources, package=json.loads(sources["curve_analysis_package"]))
    _validate_diagnosis_references(diagnosis, sources)
    _validate_diagnosis_against(
        diagnosis,
        package.experiment_plan,
        package.curve_contract,
        package.metric_report,
    )


def _validate_diagnosis_against(
    diagnosis: LayeredDiagnosisReport,
    portfolio: ExperimentPortfolio,
    contract: CurveExperimentContract,
    metric_report: CurveConsistencyReport,
) -> None:
    if diagnosis.experiment_key != contract.experiment_key:
        raise declared_violation("diagnosis differs from the exact contract experiment", path="$.experiment_key")
    validate_analysis_report(diagnosis, portfolio, metric_report)


def _curve_error_inputs(sources: dict[str, bytes]) -> None:
    plan, contract, report, bundle = (
        parse_bound_json(model, sources[name], admission_port=name)
        for name, model in (("experiment_plan", ExperimentPortfolio), ("curve_contract", CurveExperimentContract),
                            ("metric_report", CurveConsistencyReport), ("curve_bundle", CurveBundle))
    )
    if not metric_bundle_matches(bundle, report):
        raise OperationInvocationError("input_metric_bundle_mismatch", port="metric_report", field="curve_bundle_sha256",
                                       message="The metric report must identify the exact bound curve bundle.")
    try:
        validate_curve_error_inputs(plan, contract, report, bundle)
    except ValueError as error:
        raise OperationInvocationError("input_curve_analysis_mismatch", port="metric_report",
                                       message="The report, contract and validation plan must identify the same comparison.") from error


def _curve_error_analysis(
    values: dict[str, tuple[bytes, ...]],
) -> dict[str, tuple[bytes, ...]]:
    required = (
        "experiment_plan",
        "experiment_review",
        "curve_contract",
        "curve_contract_review",
        "metric_report",
        "curve_bundle",
    )
    if set(values) != set(required) or any(len(values[name]) != 1 for name in required):
        raise ValueError("curve error analysis inputs violate their cardinality")
    portfolio = ExperimentPortfolio.model_validate_json(
        values["experiment_plan"][0], strict=True
    )
    contract = CurveExperimentContract.model_validate_json(
        values["curve_contract"][0], strict=True
    )
    metric_report = CurveConsistencyReport.model_validate_json(
        values["metric_report"][0], strict=True
    )
    bundle = CurveBundle.model_validate_json(values["curve_bundle"][0], strict=True)
    try:
        artifacts = analyze_curve_error(portfolio, contract, metric_report, bundle)
    except CurveAnalysisUnavailable as error:
        raise DiagnosticError("curve_analysis_unavailable", details=(contract_diagnostic(
            "curve_analysis_unavailable", phase="tool_execution", affected_action="invoke",
            message="No failed or unavailable residual comparison can be analyzed by this helper."),)) from error
    package = CurveDiagnosticAnalysisPackage(
        experiment_plan=portfolio,
        curve_contract=contract,
        metric_report=metric_report,
        curve_bundle=bundle,
        curve_analysis=artifacts.report,
    )
    plots = dict(artifacts.plots)
    if sum(len(content) for content in plots.values()) > 8 * 1024 * 1024:
        raise ValueError("curve analysis plot collection exceeds its declared limit")
    validate_curve_error_plot_collection(
        package.model_dump(mode="json"), plots
    )
    return {
        "curve_analysis_package": (package.canonical_json(),),
        "curve_analysis_plots": tuple(plots.values()),
    }


DIAGNOSIS_PROMPT = """Return one RoleResultEnvelope with LayeredDiagnosisReport payload.
Analyze the exact plan and bound results, including failures and missing observations.
Scoring is optional: worker_curve_score accepts explicit sources and comparison_spec.
Do not invent metric implementations, replace unsupported statistics with RMS, or
change methods after seeing results without recording analysis_method and method_changes.
Include evidence references only for calculations used in the report.
Use bound input/tool aliases directly in evidence_keys; no separate citation ledger
is required. Optional evidence entries may use the bound alias as source_key with a
local locator, or a local source_key resolved by source_references.input_alias or an
alias-prefixed locator. Multiple locators for the same source are allowed.
Cite returned calculation_ref aliases for new calculations. Control retains their records and diagnostics.
Submission verifies controlled receipts without rerunning calculations. Do not copy
a calculation's source mappings into source_references; cite its record instead.
Unavailable/unsupported/error records explain limits and cannot support numeric success.
No score is a normal path: report valid findings, missing conditions, current objective
limits, remaining overall targets, and next steps, according to the evidence and the
plan's actual dependencies. A missing comparison may leave the overall
result inconclusive while other evidence supports finite findings or a local objective.
Use the exact experiment_plan as the report scope. When an optional
objective_assessment is supplied, it must use that plan’s exact non-null objective_key; pass,
fail, inconclusive, and not_evaluable remain scientific choices. When the key is null,
the assessment remains optional and must not be invented. next_action remains optional
Worker advice, not a scheduling command.
A partial metric pass does not establish unperformed comparisons or overall closure.
Link computed operators to the selected plan using exact validation_check_key values,
and cite their saved calculation_ref in evidence.
Explain which planned checks were completed and how missing or failed checks limit each conclusion.
The reviewer assesses scientific sufficiency; submission does not derive the verdict from gate statuses.
The optional curve-error helper is never a required next stage.
""" + DIAGNOSTIC_GUIDANCE.format(diagnostic_tool="worker_curve_diagnose") + ANALYSIS_FILES_GUIDANCE


class Resources:
    diagnosis_schema = schema_resource(
        LayeredDiagnosisReport, "scidiscovery.layered-diagnosis.v1"
    )
    diagnosis_report_semantic_contract = scientific_semantic_contract(
        "curve.diagnosis",
        "Bind analysis to the exact plan, results, evidence and optional calculations.",
        "Report findings and limitations; verify controlled calculation receipts without rerunning scoring.",
        context_constraint=(
            "Output identities and references must agree with the exact declared context sources that are present. "
            "When an optional objective assessment is supplied, it uses the exact selected plan objective_key. A null key does not authorize "
            "inventing an assessment identity. A source key must resolve to one bound input or calculation record. "
            "Cite the calculation_ref returned by the tool; the service resolves the registered scientific record and private receipt. "
            "A bound input alias retains its identity even in an optional source mapping. Repeated citations and local "
            "locators are allowed; an explicit locator naming another bound input conflicts with that mapping."
        ),
        payload_rule_id="curve.diagnosis.report_consistency",
        context_rule_id="curve.diagnosis.input_binding",
    )
    diagnosis_prompt = OPERATION_AGENT_PREAMBLE + DIAGNOSIS_PROMPT


class Components:
    diagnosis_inputs = CallableComponent("validator", _diagnosis_inputs)
    diagnosis_validator = CallableComponent(
        "validator", payload_validator(validate_layered_diagnosis)
    )
    diagnosis_context = CallableComponent("validator", _diagnosis_context)
    diagnosis_agent = CallableComponent("agent", _agent_marker)




TRANSFORM_OPERATIONS = ()


def component_specs() -> tuple[ComponentSpec, ...]:
    values = [
        ComponentSpec("analysis_workspace", "workspace", "curve_score.analysis_workspace:WORKSPACE", public=True,
            resources=(ComponentRef("analysis_materializer"), ComponentRef("analysis_snapshotter"), ComponentRef("analysis_finalizer"))),
        ComponentSpec("analysis_finalizer", "workspace_finalizer", "curve_score.analysis_workspace:FINALIZER"),
        ComponentSpec("analysis_materializer", "workspace_materializer", "curve_score.analysis_workspace:MATERIALIZER", configuration_identity="analysis.scientific-context:v2"),
        ComponentSpec("analysis_snapshotter", "workspace_snapshotter", "curve_score.analysis_workspace:SNAPSHOTTER"),
        ComponentSpec("diagnosis_inputs", "validator", "curve_score.science_operations:Components.diagnosis_inputs", configuration_identity="historical-analysis-r4:v1"),
        ComponentSpec("analysis_score_tool", "worker_tool", "curve_score.analysis_tool:CURVE_SCORE_TOOL", configuration_identity="analysis.scientific-receipt:v3"),
        ComponentSpec("analysis_diagnostic_tool", "worker_tool", "curve_score.diagnostic_tool:DIAGNOSTIC_TOOL", configuration_identity="analysis.diagnostic-checkpoint:v5"),
        ComponentSpec("analysis_files_tool", "worker_tool", "curve_score.analysis_files:TOOL", public=True),
        ComponentSpec("diagnosis_validator", "validator", "curve_score.science_operations:Components.diagnosis_validator", resources=(ComponentRef("diagnosis_report_semantic_contract"),)),
        ComponentSpec("diagnosis_context", "validator", "curve_score.science_operations:Components.diagnosis_context", configuration_identity="analysis-private-receipt:v3", resources=(ComponentRef("diagnosis_report_semantic_contract"),)),
        ComponentSpec("diagnosis_agent", "agent", "curve_score.science_operations:Components.diagnosis_agent"),
    ]
    values.extend(ComponentSpec(name, "resource", f"curve_score.science_operations:Resources.{name}", public=name == "diagnosis_schema")
        for name in ("diagnosis_schema", "diagnosis_report_semantic_contract", "diagnosis_prompt"))
    return tuple(values)


_NONEMPTY_COMPONENT = CallableComponent("validator", _nonempty)
COMPONENT_SPECS = component_specs()
OPERATIONS = AGENT_OPERATIONS + TRANSFORM_OPERATIONS

__all__ = ["COMPONENT_SPECS", "OPERATIONS"]
