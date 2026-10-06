"""Optional scientific synthesis over an explicit, frozen comparison cohort.

The author owns judgments and explanations. Control reads numeric evidence,
checks its exact basis, and projects rankings; delivery pass is not acceptance.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_agent.schema.approval import parse_json_pointer
from .artifact_agent.schema.common import canonical_json
from .artifact_agent.schema.refs import ArtifactRef
from .general_science_agent_operations import BASE_TOOLS, _input
from .operation_contract import SemanticRuleViolation
from .operation_declaration import schema_resource, scientific_agent_operation, semantic_contract
from .operations.input_validation import OperationInvocationError
from .operations.spec import CallableComponent, CollectionSpec, ComponentRef, ComponentSpec, InputDerivationSpec, InputPortSpec, InputValidationSpec, OutputPortSpec, SemanticRuleSpec, WorkspaceContract
from .operations.tooling import WorkerToolDefinition
from .operations.workspace import WorkspaceFinalizer, WorkspaceProtocolError
from .plugin_runtime.results import finalize_result, materialize_summary_handoff, validate_finalizer_payload

SCHEMA_ID = "scidiscovery.scientific-result.v1"
CONTRACT_SCHEMA_ID = "scidiscovery.scientific-comparison-contract.v1"
RESULT_KIND = "scientific_result"


class ScientificModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Criterion(ScientificModel):
    key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    unit: str = Field(max_length=128)
    relation: Literal["le", "ge"]
    threshold: float = Field(allow_inf_nan=False)


class Ranking(ScientificModel):
    metric: str = Field(min_length=1, max_length=128)
    direction: Literal["minimize", "maximize"]


class ComparisonContract(ScientificModel):
    """Scientific criteria, frozen as an input file; no refs or mechanical forms."""
    conditions: str = Field(min_length=1, max_length=4096)
    criteria: tuple[Criterion, ...] = Field(min_length=1, max_length=16)
    ranking: Ranking | None = None
    execution_required: bool = True
    figures_required: bool = False

    @model_validator(mode="after")
    def closure(self):
        keys = [criterion.key for criterion in self.criteria]
        if len(keys) != len(set(keys)) or (self.ranking and self.ranking.metric not in keys):
            raise ValueError("Criterion keys must be unique; ranking must name a declared criterion.")
        return self


class MetricEvidence(ScientificModel):
    key: str = Field(min_length=1, max_length=128)
    source_key: str = Field(min_length=1, max_length=128,
        description="Exact sealed numeric evidence alias. Its verified original receipt must explicitly declare the candidate, frozen evaluation contract and reference evidence in derived_from/control_parents; inherited parents grant no evaluation basis. Rescore original outputs and publish with those source_aliases when earlier scores used another contract.")
    json_pointer: str = Field(max_length=1024, pattern=r"^(?:/(?:[^~]|~[01])*)?$",
        description="Location of a finite JSON number in that evidence. Control reads the number; narrative estimates are inadmissible.")


class Metric(MetricEvidence):
    value: float | None = Field(default=None, allow_inf_nan=False)
    meets_threshold: bool | None = None


class AnalysisSubmission(ScientificModel):
    candidate: str = Field(min_length=1, max_length=128,
        description="Exact current input alias from the explicit candidates port; never recent/history discovery.")
    execution: Literal["succeeded", "failed", "not_observed"]
    evidence: Literal["complete", "incomplete", "unknown"]
    scientific: Literal["accepted", "rejected", "inconclusive", "not_assessed"]
    mechanism: Literal["identified", "ambiguous", "not_assessed"]
    conclusion: str = Field(min_length=1, max_length=2048)
    metrics: tuple[MetricEvidence, ...] = Field(default=(), max_length=16)
    limitations: tuple[str, ...] = Field(default=(), max_length=16)
    analysis_outcome: Literal["completed", "inconclusive", "blocked"] = "completed"
    method_sources: tuple[str, ...] = Field(default=(), max_length=8,
        description="Optional scientific method evidence aliases when no complete-experiment implementation receipt exists; cite meaningful original methods, never hashes or logs.")


class Candidate(AnalysisSubmission):
    metrics: tuple[Metric, ...] = Field(default=(), max_length=16)
    analysis_reference: str | None = None
    evidence_readiness: Literal["complete", "incomplete"] = "incomplete"
    method_evidence: tuple[str, ...] = ()
    observation_evidence: tuple[str, ...] = ()


class Figure(ScientificModel):
    source_key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)


class ScientificResult(ScientificModel):
    summary: str = Field(min_length=1, max_length=8192)
    analyses: tuple[str, ...] = Field(min_length=1, max_length=16,
        description="Exact immutable references returned by worker_result_analysis; scientific fields are written once in that analysis.")
    candidates: tuple[Candidate, ...] = Field(default=(), max_length=16)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)
    remaining_question: str = Field(max_length=8192)
    figures: tuple[Figure, ...] = Field(default=(), max_length=16)
    main_figure: str | None = Field(default=None, max_length=128,
        description="Optional exact source_key from figures; omit when no main overlay exists.")
    comparison_contract: ComparisonContract | None = None
    best_available: tuple[str, ...] = ()
    accepted_candidates: tuple[str, ...] = ()
    disposition: Literal["accepted_model", "no_accepted_model", "inconclusive"] = "inconclusive"


def _number(raw, pointer):
    value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    for token in parse_json_pointer(pointer):
        if isinstance(value, list):
            if not token.isdecimal() or (len(token) > 1 and token.startswith("0")):
                raise ValueError("invalid array position")
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise ValueError("pointer does not resolve")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("metric must resolve to a finite JSON number")
    return float(value)


def _port(descriptors, port):
    return [item for item in descriptors.values() if item.port_name == port]


def _json_material(contents, alias, role):
    try:
        return json.loads(contents[alias])
    except (ValueError, KeyError) as error:
        raise ValueError(f"Exact {role} material {alias} is unavailable or is not JSON.") from error


def _experiment_origins(original, contents, descriptors):
    """Verify adopted method -> collected execution from automatically bound parents."""
    if original.artifact_ref.schema_id != "scidiscovery.experiment-report.v1" or original.producer_run_id is None:
        return [], []
    report = _json_material(contents, original.source_name, "experiment report")
    if report.get("outcome") != "completed":
        return [], []
    manifests = [item for item in _port(descriptors, "candidate_origins") if item.artifact_ref in original.parent_refs]
    if len(manifests) != 1:
        return [], []
    manifest = _json_material(contents, manifests[0].source_name, "experiment manifest")
    records = {item["alias"]: item for item in manifest.get("records", ())}
    adopted = [records.get(alias) for alias in report.get("adopted_stages", ())]
    if not adopted or any(item is None for item in adopted):
        return [], []
    material_by_ref = {canonical_json(item.artifact_ref.model_dump(mode="json")): item
        for item in _port(descriptors, "candidate_materials")}
    methods, observations = [], []
    implementations = {item["alias"]: item for item in adopted if item.get("metadata", {}).get("kind") == "experiment_implementation"}
    for item in adopted:
        metadata = item.get("metadata", {})
        implementation = implementations.get(metadata.get("implementation"))
        if metadata.get("kind") != "experiment_execution" or implementation is None:
            continue
        execution = material_by_ref.get(canonical_json(item["artifact_ref"]))
        method = material_by_ref.get(canonical_json(implementation["artifact_ref"]))
        if execution is None or method is None or method.artifact_ref not in execution.parent_refs:
            continue
        observation = _json_material(contents, execution.source_name, "collected execution")
        if observation.get("state") == "collected" and observation.get("terminal_state") == "succeeded":
            methods.append(method.source_name)
            observations.append(execution.source_name)
    return list(dict.fromkeys(methods)), list(dict.fromkeys(observations))


def _explicit_basis(alias, descriptors, material_resolver):
    """Declared sources in a verified original receipt, never inherited parents."""
    if material_resolver is None:
        return set()
    record = material_resolver(alias)
    descriptor = descriptors[alias]
    if record is None or ArtifactRef.model_validate(record["artifact_ref"]) != descriptor.artifact_ref:
        return set()
    declared = record.get("metadata", {}).get("derived_from", ())
    sources = record.get("source_refs", {})
    result = {ArtifactRef.model_validate(sources[name]) for name in declared if name in sources}
    result.update(ArtifactRef.model_validate(value) for value in record.get("control_parents", ()))
    if not result.issubset(set(descriptor.parent_refs)):
        raise ValueError("Original receipt declaration contradicts its immutable material parents.")
    return result


def comparison_projection(payload, contents, descriptors, material_resolver=None):
    """Pure, deterministic projection using only exact frozen materials."""
    result = validate_finalizer_payload(ScientificResult, payload).model_dump(mode="json")
    contracts = _port(descriptors, "evaluation_contract")
    if len(contracts) != 1:
        raise ValueError("One exact evaluation_contract is required.")
    contract_source = contracts[0]
    contract = ComparisonContract.model_validate_json(contents[contract_source.source_name], strict=True)
    expected = _port(descriptors, "candidates")
    result["candidates"] = []
    if len(result["analyses"]) != len(set(result["analyses"])):
        raise ValueError("Adopt each sealed analysis once.")
    references = _port(descriptors, "reference_evidence")
    required = {contract_source.artifact_ref, *(item.artifact_ref for item in references)}
    for alias in result["analyses"]:
        descriptor = descriptors.get(alias)
        if descriptor is None or dict(descriptor.labels).get("tool_name") != "worker_result_analysis":
            raise ValueError("Every candidate requires an exact sealed result analysis.")
        analysis = Candidate.model_validate_json(contents[alias], strict=True).model_dump(mode="json")
        original = descriptors.get(analysis["candidate"])
        if original is None or not {original.artifact_ref, *required}.issubset(_explicit_basis(alias, descriptors, material_resolver)):
            raise ValueError("Analysis does not retain the exact candidate/evaluation basis.")
        analysis["analysis_reference"] = alias
        result["candidates"].append(analysis)
    aliases = [item["candidate"] for item in result["candidates"]]
    if len(aliases) != len(set(aliases)) or set(aliases) != {item.source_name for item in expected}:
        raise ValueError("Assess every explicitly bound candidate exactly once.")
    # Reusing a prior comparison is allowed only under its identical original
    # basis. Re-scoring raw originals creates a new, separately frozen cohort.
    criteria = {item.key: item for item in contract.criteria}
    for candidate in result["candidates"]:
        original = descriptors[candidate["candidate"]]
        candidate["method_evidence"], candidate["observation_evidence"] = _experiment_origins(original, contents, descriptors)
        for alias in candidate["method_sources"]:
            method = descriptors.get(alias)
            if method is None or (original.artifact_ref not in method.parent_refs and method.artifact_ref not in original.parent_refs):
                raise ValueError("Method evidence must retain the exact candidate origin.")
            if alias not in candidate["method_evidence"]:
                candidate["method_evidence"].append(alias)
        if original.artifact_ref.schema_id == SCHEMA_ID:
            prior = ScientificResult.model_validate_json(contents[original.source_name], strict=True)
            if prior.comparison_contract != contract or not required.issubset(set(original.parent_refs)):
                raise ValueError("Different frozen evaluation contracts/reference evidence cannot share a ranking. Bind raw original outputs and new re-scored evidence instead.")
        seen = set()
        for metric in candidate["metrics"]:
            if metric["key"] not in criteria or metric["key"] in seen:
                raise ValueError("Metrics must name unique declared criteria.")
            seen.add(metric["key"])
            source = descriptors.get(metric["source_key"])
            if source is None or metric["source_key"] not in contents:
                raise ValueError("Metric evidence must be an exact sealed material.")
            basis = _explicit_basis(metric["source_key"], descriptors, material_resolver)
            if not {original.artifact_ref, *required}.issubset(basis):
                raise ValueError("Metric receipt lacks an explicit exact candidate/evaluation contract/reference evidence declaration. Inherited parents do not establish re-scoring; publish re-scored original outputs with the full scientific basis.")
            raw = contents[metric["source_key"]]
            if not raw and source.size_bytes:
                # Attachment inventory intentionally carries no bytes. Read only
                # this declared numeric receipt, with a bounded control resolver.
                record = material_resolver(metric["source_key"], read_content=True)
                raw = record["content"] if record else b""
            metric["value"] = _number(raw, metric["json_pointer"])
            criterion = criteria[metric["key"]]
            metric["meets_threshold"] = (metric["value"] <= criterion.threshold if criterion.relation == "le"
                else metric["value"] >= criterion.threshold)
        has_figures = any(descriptors.get(figure["source_key"]) is not None
            and original.artifact_ref in descriptors[figure["source_key"]].parent_refs
            for figure in result["figures"]) if contract.figures_required else True
        ready = bool(candidate["method_evidence"]) and (bool(candidate["observation_evidence"]) if contract.execution_required else True) and has_figures
        candidate["evidence_readiness"] = "complete" if ready else "incomplete"
        if candidate["scientific"] == "accepted" and ((contract.execution_required and candidate["execution"] != "succeeded")
                or candidate["evidence"] != "complete" or seen != set(criteria)
                or not ready or candidate["analysis_outcome"] != "completed"
                or not all(metric["meets_threshold"] for metric in candidate["metrics"])):
            raise ValueError("Accepted requires observed successful execution, complete evidence and every declared threshold. Passing thresholds alone does not establish mechanism identifiability.")
    figure_aliases = set()
    for figure in result["figures"]:
        source = descriptors.get(figure["source_key"])
        if source is None or source.media_type.split(";", 1)[0] not in {"image/png", "image/jpeg"}:
            raise ValueError("Figures must name exact sealed PNG/JPEG materials.")
        if source.artifact_ref in figure_aliases:
            raise ValueError("A figure may be cited once; preserve every distinct image.")
        figure_aliases.add(source.artifact_ref)
    if result["main_figure"] is not None and result["main_figure"] not in {item["source_key"] for item in result["figures"]}:
        raise ValueError("main_figure must name a declared figure.")
    result["comparison_contract"] = contract.model_dump(mode="json")
    accepted = [item["candidate"] for item in result["candidates"] if item["scientific"] == "accepted"]
    result["accepted_candidates"] = accepted
    result["disposition"] = ("accepted_model" if accepted else "no_accepted_model"
        if all(item["scientific"] == "rejected" for item in result["candidates"]) else "inconclusive")
    result["best_available"] = []
    if contract.ranking:
        eligible = [(item["candidate"], next((metric["value"] for metric in item["metrics"]
            if metric["key"] == contract.ranking.metric), None)) for item in result["candidates"]
            if (item["execution"] == "succeeded" or not contract.execution_required)
            and item["evidence"] == "complete" and item["analysis_outcome"] == "completed"
            and item["evidence_readiness"] == "complete" and {metric["key"] for metric in item["metrics"]} == set(criteria)]
        eligible = [(name, value) for name, value in eligible if value is not None]
        if eligible:
            best = (min if contract.ranking.direction == "minimize" else max)(value for _, value in eligible)
            result["best_available"] = [name for name, value in eligible if value == best]
    return result


def validate_inputs(sources):
    try:
        descriptor, = _port(sources.binding_descriptors, "evaluation_contract")
        ComparisonContract.model_validate_json(sources[descriptor.source_name], strict=True)
    except (ValueError, KeyError) as error:
        raise OperationInvocationError("comparison_contract_invalid", port="evaluation_contract",
            message="Freeze a scientific comparison contract JSON: conditions, criteria (key/label/unit/relation le or ge/finite threshold), optional ranking (metric/direction minimize or maximize).") from error


def validate_payload(raw):
    ScientificResult.model_validate_json(raw, strict=True)


def validate_context(payload, sources, handoff):
    try:
        projected = comparison_projection(payload, sources, sources.binding_descriptors, sources.sealed_material_record)
        if canonical_json(projected) != canonical_json(ScientificResult.model_validate_json(canonical_json(payload), strict=True).model_dump(mode="json")):
            raise ValueError("Generated comparison fields differ from exact frozen evidence.")
        if handoff.get("verdict") != "pass":
            raise ValueError("A sealed synthesis pass records delivery, not model acceptance.")
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise SemanticRuleViolation(str(error)) from error


def finalize(request):
    def project(value):
        try:
            value["payload"] = comparison_projection(value["payload"], request.input_contents, request.binding_descriptors, request.sealed_material_resolver)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise WorkspaceProtocolError(str(error), details=({"path": "$.payload", "message": str(error), "type": "comparison_binding"},)) from error
        materialize_summary_handoff(value, "pass")
    return finalize_result(request, project)


def draft_schema(envelope, schema_id, input_source_ports=None):
    envelope = deepcopy(envelope)
    payload = envelope["properties"]["payload"]
    for name in ("candidates", "comparison_contract", "best_available", "accepted_candidates", "disposition"):
        payload["properties"].pop(name, None)
        payload["required"] = [key for key in payload.get("required", ()) if key != name]
    metric = payload["$defs"]["Metric"]
    for name in ("value", "meets_threshold"):
        metric["properties"].pop(name, None)
    envelope["properties"].pop("schema_version", None)
    envelope["required"] = [key for key in envelope.get("required", ()) if key not in {"schema_version", "handoff"}]
    handoff = envelope["$defs"]["RoleHandoff"]
    for name in ("summary", "verdict"):
        handoff["properties"].pop(name, None)
        handoff["required"] = [key for key in handoff.get("required", ()) if key != name]
    return envelope


def submit_analysis(request, context):
    if request.candidate not in context.input_names_for_port("candidates"):
        raise ValueError("Choose an exact candidate input alias.")
    basis = context.input_names_for_port("evaluation_contract") + context.input_names_for_port("reference_evidence")
    materials = tuple(dict.fromkeys((request.candidate, *basis, *request.method_sources,
        *(metric.source_key for metric in request.metrics))))
    record = context.accept_evidence(raw=canonical_json(request.model_dump(mode="json")),
        media_type="application/json", derived_from=materials, metadata={"kind": "scientific_result_analysis"})
    return {"reference": record["alias"], "state": "sealed", "artifact_name": record["artifact_name"],
        "analysis_outcome": request.analysis_outcome, "scientific": request.scientific}


PROMPT = """Synthesize the explicit candidate cohort under the exact frozen evaluation
contract and reference evidence. One candidate is enough for a final scientific
result. Keep execution success, evidence completeness, scientific acceptance and
mechanism identifiability separate. completed/pass in old experiment reports is
delivery only; read original observations before judging. The contract's conditions
and thresholds are immutable. Metrics cite exact source_key/json_pointer; control
reads finite JSON numbers and computes thresholds and any declared ranking.
Never reuse old scores under a different basis: rescore the same original outputs,
retain the method, and publish JSON with worker_publish_files using the exact
candidate, evaluation_contract and all reference_evidence source_aliases. If this
cannot be done, omit the unsupported metric and record the evidence gap. An
author-published score binds origins but does not independently validate the method.
Accepted requires all thresholds plus your supported scientific judgment. A best
available candidate can remain rejected. With no declared ranking, leave candidates
unranked; tied scores stay tied. no_accepted_model concerns only the included cohort.
Cite every useful distinct figure by its exact sealed alias; identify a main overlay
only if one exists. A figure is optional. Explain what remains unresolved and every
limitation that can change the next decision. Do not scan history or select current
results: the scheduler chooses whether to adopt this immutable synthesis.
Seal each candidate's scientific assessment with worker_result_analysis, then adopt
its exact reference in analyses. Analysis completion and its scientific outcome are
separate. Control assembles the candidate fields without copying them into result.json.
A best candidate requires completed analysis, every decisive metric, exact method
evidence and applicable collected execution/figures. Missing receipts stay visible
as pending evidence, never best. Complete-experiment implementation/execution
origins are bound automatically; optional method_sources cover other research.
The control check verifies explicit original source declarations and numeric
evidence. It does not independently prove the scoring algorithm correct. Preserve
the scoring method and its limitations; copying old values with candidate-only
publication is not re-scoring under the contract even when inherited parents exist.
"""
SCHEMA = schema_resource(ScientificResult, SCHEMA_ID)
COMPARISON_SCHEMA = schema_resource(ComparisonContract, CONTRACT_SCHEMA_ID)
CONTRACT = semantic_contract(SemanticRuleSpec("results.frozen_comparison",
    "Every candidate is explicit; metric and analysis receipts must explicitly declare the exact candidate, evaluation contract and reference evidence in verified original derived_from/control_parents. Inherited parent edges never establish evaluation basis. This verifies declarations and numeric evidence, not algorithm correctness. Control parses finite numeric JSON evidence, thresholds and declared ranking; no rule grants a unique best. Scientific judgments belong to the author. Delivery pass never means accepted. Historical unstructured reports remain unassessed until scientific synthesis. A no_accepted_model conclusion covers only this included cohort."))
REF = ComponentRef
AGENT = CallableComponent("agent", lambda: None)
VALIDATOR = CallableComponent("validator", validate_payload)
INPUTS = CallableComponent("validator", validate_inputs)
CONTEXT = CallableComponent("validator", validate_context)
FINALIZER = CallableComponent("workspace_finalizer", WorkspaceFinalizer(finalize, draft_schema, "1"))
ANALYSIS_TOOL = WorkerToolDefinition(name="worker_result_analysis",
    description="Seal this candidate's scientific result analysis once. Supply judgments, decisive metric evidence locations, explanation and limitations. Control binds the exact candidate, evaluation contract and reference evidence; generated method/observation/readiness fields are ignored and assembled from original receipts. Returns an immutable analysis reference for the final synthesis.",
    input_model=AnalysisSubmission, capability="results.analysis", contextual_handler=submit_analysis, owner_only=True,
    evidence_ports=("tool_evidence", "recovery_manifest_output"))
WORKSPACE = WorkspaceContract()
COMPONENTS = (
    ComponentSpec("scientific_result_agent", "agent", __name__ + ":AGENT"),
    ComponentSpec("scientific_result_prompt", "resource", __name__ + ":PROMPT"),
    ComponentSpec("scientific_result_schema", "resource", __name__ + ":SCHEMA", public=True),
    ComponentSpec("scientific_comparison_schema", "resource", __name__ + ":COMPARISON_SCHEMA", public=True),
    ComponentSpec("scientific_result_contract", "resource", __name__ + ":CONTRACT"),
    ComponentSpec("scientific_result_validator", "validator", __name__ + ":VALIDATOR", resources=(REF("scientific_result_contract"),)),
    ComponentSpec("scientific_result_inputs", "validator", __name__ + ":INPUTS", resources=(REF("scientific_comparison_schema"),)),
    ComponentSpec("scientific_result_context", "validator", __name__ + ":CONTEXT", resources=(REF("scientific_result_contract"),)),
    ComponentSpec("scientific_result_finalizer", "workspace_finalizer", __name__ + ":FINALIZER"),
    ComponentSpec("scientific_result_workspace", "workspace", __name__ + ":WORKSPACE", resources=(REF("scientific_result_finalizer"),)),
    ComponentSpec("scientific_result_analysis_tool", "worker_tool", __name__ + ":ANALYSIS_TOOL"),
)
OPERATION = scientific_agent_operation("science.results.compare.v1",
    "Seal scientific judgments and an optional deterministic ranking for one explicit comparable cohort.",
    "Exact original candidate outputs and a frozen scientific evaluation contract/reference evidence are available; a cohort of one supplies a final scientific result.",
    "Automatic history ranking, accepting a model from completed/pass, or mixing scores from different frozen contracts.",
    decision_fields=("summary", "disposition", "best_available", "accepted_candidates", "remaining_question", "limitations"),
    agent=REF("scientific_result_agent"), prompt=REF("scientific_result_prompt"), workspace=REF("scientific_result_workspace"), tools=BASE_TOOLS + (REF("scientific_result_analysis_tool"),),
    inputs=(
        _input("research_objective", "Exact global scientific objective.", "scidiscovery.research-objective.v1"),
        _input("evaluation_contract", "Exact JSON scientific comparison contract: conditions; criteria (key,label,unit,relation le/ge,finite threshold); optional ranking (metric,direction minimize/maximize), execution_required and figures_required scientific applicability. An existing experiment Worker may publish it; freeze by file ingestion or scientific publication. Never infer a contract from old pass signals.", "*", media_types=("*/*",), exposure="material", usage="evidence_inventory"),
        _input("reference_evidence", "Exact frozen reference evidence shared by this comparison group.", "*", media_types=("*/*",), max_items=16, max_item_bytes=32*1024*1024, exposure="material", usage="evidence_inventory"),
        _input("candidates", "Explicit exact original outputs to assess; never a recent/history sample.", "*", media_types=("*/*",), max_items=16, max_item_bytes=32*1024*1024, exposure="material", usage="evidence_inventory"),
        _input("scientific_materials", "Optional exact numeric evidence, figures or methods. Old scores must retain their original evaluation basis; a changed contract requires new re-scoring of original outputs.", "*", media_types=("*/*",), min_items=0, max_items=32, max_item_bytes=32*1024*1024, exposure="material", usage="evidence_inventory"),
        InputPortSpec(name="candidate_origins", description="Control-bound original receipt manifests from the explicit candidates' exact direct parents.",
            schema="scidiscovery.tool-evidence-manifest.v1", media_types=("application/json",), codec=REF("json_codec"), schema_resource=REF("tool_evidence_schema"),
            min_items=0, max_items=16, max_item_bytes=1024*1024, agent_visible=False, exposure="on_demand", usage="evidence_inventory",
            derivation=InputDerivationSpec(anchor_port="candidates", select="parents")),
        InputPortSpec(name="candidate_materials", description="Control-bound exact original candidate method and observation materials; no history or latest lookup.",
            schema="opaque", media_types=("application/json",), codec=REF("opaque_codec"), schema_resource=REF("opaque_schema"),
            min_items=0, max_items=256, max_item_bytes=1024*1024, agent_visible=False, exposure="on_demand", usage="evidence_inventory",
            derivation=InputDerivationSpec(anchor_port="candidate_origins", select="receipt_materials")),
    ),
    outputs=(OutputPortSpec(name="scientific_result", description="Immutable scientific assessment of the explicit cohort; control-projected comparison fields.",
        schema=SCHEMA_ID, kind=RESULT_KIND, media_types=("application/json",), codec=REF("json_codec"), schema_resource=REF("scientific_result_schema"),
        max_item_bytes=64*1024, validator=REF("scientific_result_validator"), validator_rule_id="results.frozen_comparison",
        context_validator=REF("scientific_result_context"), context_rule_id="results.frozen_comparison",
        context_sources=("research_objective", "evaluation_contract", "reference_evidence", "candidates", "scientific_materials", "attachments", "tool_evidence", "candidate_origins", "candidate_materials"),
        semantic_contract=REF("scientific_result_contract")),
        OutputPortSpec(name="tool_evidence", description="Sealed candidate scientific analyses.", kind="scientific_analysis", schema="opaque",
            media_types=("application/json",), codec=REF("opaque_codec"), schema_resource=REF("opaque_schema"), min_items=0, max_items=32,
            max_item_bytes=64*1024, collection=CollectionSpec(2*1024*1024))),
    input_validation=InputValidationSpec(REF("scientific_result_inputs"), "results.comparison_inputs", "The exact evaluation contract must declare valid scientific conditions, thresholds and optional ranking."),
    timeout=1800, max_input_bytes=2_400_000_000, max_output_bytes=3*1024*1024, max_files=33, max_attempts=2, native_view_image=True)
