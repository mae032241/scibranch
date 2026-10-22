"""A complete scientific experiment, with immutable stage evidence in one Run."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_agent.schema.common import canonical_json
from .artifact_agent.schema.experiment import PredictionTest, ValidationCheck
from .artifact_agent.schema.validation import ValidationCheckResult
from .plugin_runtime.results import materialize_summary_handoff, finalize_result, validate_finalizer_payload
from .general_science_agent_operations import _input
from .operation_declaration import schema_resource, scientific_agent_operation, semantic_contract
from .operations.spec import (ArtifactCaptureSpec, CaptureSources, CallableComponent, CollectionSpec, ComponentRef,
    ComponentSpec, InputDerivationSpec, InputPortSpec, InputValidationSpec, OutputPortSpec, SemanticRuleSpec, WorkspaceContract, ScientificProgressSpec)
from .operations.tooling import WorkerToolDefinition
from .agent_execution_settings import MaterialInputSettings
from .operations.workspace import WorkspaceProtocolError
from .operation_contract import SemanticRuleViolation, DiagnosticError, ToolInputError


class ExperimentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    summary: str = Field(min_length=1, max_length=8192)
    outcome: Literal["completed", "inconclusive", "blocked"]
    limitations: tuple[str, ...] = Field(default=(), max_length=32)
    remaining_question: str = Field(max_length=8192)
    adopted_stages: tuple[str, ...] = Field(default=(), max_length=32,
        description="Tool-returned references or current input names of sealed materials adopted in this report. Control verifies their original receipts.")


MaterialAlias = Annotated[str, Field(min_length=1, max_length=256)]


class PredictionDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    prediction: PredictionTest
    criterion: ValidationCheck
    conditions: Annotated[tuple[MaterialAlias, ...], Field(min_length=1, max_length=32,
        description="Actual accessible input/material aliases defining the prediction's conditions; control reads and freezes exact originals, each at most 65536 bytes. Larger materials remain usable through ordinary stage materials.")]

    @model_validator(mode="after")
    def unique_conditions(self):
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("prediction conditions must be unique")
        return self


class PredictionComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    prediction: MaterialAlias = Field(description="Accessible alias of the exact sealed prediction declaration, at most 65536 bytes, not a revised fit or copied JSON.")
    observations: Annotated[tuple[MaterialAlias, ...], Field(min_length=1, max_length=32,
        description="Actual observations used in this comparison; result.evidence_keys names these exact aliases. This optional binding reads each original, at most 65536 bytes; larger data remain usable through ordinary stage materials.")]
    result: ValidationCheckResult

    @model_validator(mode="after")
    def actual_evidence(self):
        if len(set(self.observations)) != len(self.observations):
            raise ValueError("comparison observations must be unique")
        if not self.result.evidence_keys or not set(self.result.evidence_keys).issubset(self.observations):
            raise ValueError("comparison evidence_keys must name actual observations")
        return self


class NativeExecution(BaseModel):
    """Worker-reported native execution; sealed bytes are not an attestation."""
    model_config = ConfigDict(extra="forbid", strict=True)
    implementation: MaterialAlias
    state: Literal["succeeded", "failed", "running", "not_started"]
    logs: Annotated[tuple[MaterialAlias, ...], Field(min_length=1, max_length=16)]
    results: tuple[MaterialAlias, ...] = Field(default=(), max_length=16)


class StageSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stage: Literal["design", "implementation", "debug", "execution", "validity"]
    conclusion: str = Field(min_length=1, max_length=16384)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)
    remaining_question: str = Field(default="", max_length=8192,
        description="Unresolved question or blocker, when there is one.")
    materials: tuple[str, ...] = Field(default=(), max_length=32,
        description="Exact current input or sealed aliases, or file:scratch/artifacts/path for this seal; file@<sealed-stage-reference>:path selects that immutable version. run: is not a scientific source.")
    capture_sources: tuple[CaptureSources, ...] = Field(default=(), max_length=4)
    prediction_declaration: PredictionDeclaration | None = Field(default=None,
        description="Optional design-stage scientific precommitment: prediction, criterion and actual conditions. Sealing fixes bytes and references, not scientific correctness or final task completion.")
    comparison: PredictionComparison | None = Field(default=None,
        description="Optional validity-stage comparison against that exact declaration. Handler reads/binds originals; final adoption validation checks the declaration's unique original tool receipt. A preview is not a completed validation; not_run means no test.")
    native_execution: NativeExecution | None = Field(default=None,
        description="Execution stage only: Worker-reported native shell execution, exact sealed implementation and published logs/results. Control binds file identities, not execution truth. Observation wrappers are optional; retain provenance limitations.")

    @model_validator(mode="after")
    def scientific_roles(self):
        if self.prediction_declaration is not None and self.stage != "design":
            raise ValueError("prediction_declaration belongs to design")
        if self.comparison is not None and self.stage != "validity":
            raise ValueError("comparison belongs to validity")
        if self.native_execution is not None and self.stage != "execution":
            raise ValueError("native_execution belongs to execution")
        return self


class DraftValidationCheckResult(ValidationCheckResult):
    evidence_keys: Annotated[tuple[MaterialAlias, ...], Field(max_length=64)] = ()


class DraftPredictionComparison(PredictionComparison):
    result: DraftValidationCheckResult


class DraftStageSubmission(StageSubmission):
    comparison: DraftPredictionComparison | None = Field(default=None,
        description=StageSubmission.model_fields['comparison'].description)


def _same_criterion(declaration, comparison):
    if (comparison.result.check_key != declaration.criterion.check_key
            or comparison.result.evaluation_mode != declaration.criterion.evaluation_mode):
        raise SemanticRuleViolation("Comparison must retain the exact declaration's check_key and evaluation_mode.")


def _read_stage_source(context, alias):
    source = context.source_descriptor(alias)
    if source.size_bytes > 65536:
        raise ToolInputError("experiment_scientific_source_too_large",
            "This optional prediction/comparison binding reads each original up to 65536 bytes; use ordinary stage materials for larger data.")
    raw = context.read_evidence(alias)
    if hashlib.sha256(raw).hexdigest() != source.artifact_ref.sha256:
        raise SemanticRuleViolation("Scientific source differs from its frozen original.")
    return raw


def submit_stage(request, context):
    raw, capture = context.capture_science(request.model_dump(mode="json"),
        ("materials/*", "prediction_declaration/conditions/*", "comparison/prediction", "comparison/observations/*",
         "comparison/result/evidence_keys/*", "native_execution/implementation", "native_execution/logs/*", "native_execution/results/*"),
        [item.model_dump(mode="json") for item in request.capture_sources])
    request = StageSubmission.model_validate_json(canonical_json(raw), strict=True)
    aliases = list(request.materials)
    metadata = {"kind": "experiment_stage", "stage": request.stage, **capture}
    if request.native_execution is not None:
        native = request.native_execution
        aliases.extend((native.implementation, *native.logs, *native.results))
        metadata.update(origin="worker_native", execution_attestation="unavailable",
            implementation=native.implementation)
    if request.prediction_declaration is not None:
        aliases.extend(request.prediction_declaration.conditions)
        for alias in request.prediction_declaration.conditions:
            _read_stage_source(context, alias)
    if request.comparison is not None:
        comparison = request.comparison
        declaration = StageSubmission.model_validate_json(_read_stage_source(context, comparison.prediction), strict=True)
        if declaration.prediction_declaration is None:
            raise SemanticRuleViolation("Comparison needs an exact sealed prediction declaration.")
        _same_criterion(declaration.prediction_declaration, comparison)
        for alias in comparison.observations:
            _read_stage_source(context, alias)
        aliases.extend((comparison.prediction, *comparison.observations))
    record = context.accept_evidence(raw=canonical_json(request.model_dump(mode="json")),
        media_type="application/json", derived_from=tuple(dict.fromkeys(("objective_source", *aliases))),
        metadata=metadata)
    return {"stage": request.stage, "reference": record["alias"], "state": "sealed",
        "artifact_name": record["artifact_name"],
        **({'capture':{'reference':capture['capture_index'],'coverage':capture['capture_coverage'],
             'counts':capture['capture_counts'],'attempt_inventory':capture['attempt_inventory']}} if capture else {})}


class ExecuteExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["capabilities", "review", "start", "advance", "collect", "status", "logs", "read", "export", "cancel"]
    name: str = Field(default="experiment", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    implementation: str | None = Field(default=None, max_length=128,
        description="Sealed implementation created in or explicitly bound to this task; required for start or review. review records a wait for the exact independent review and returns its invocation for the scheduler. When the review finishes, the same action returns its complete sealed scientific outcome. Controlled work waits for the review outcome; reads/status/cancel remain available. It creates no execution and dispatches no Agent.")
    output_name: str | None = Field(default=None, max_length=1024,
        description="read/export: use an exact output name or verified file_name returned by status after collection completes; never guess a generated identifier.")
    offset: int = Field(default=0, ge=0, description="read only: byte offset; continue at the returned next_offset. For logs use direction/cursor.")
    direction: Literal["tail", "before", "after"] = "tail"
    cursor: str | None = Field(default=None, max_length=2048, description="logs only: pass the returned before/after cursor unchanged; tail needs no cursor.")
    log_index: int = Field(default=0, ge=0, description="logs only: select 0 <= log_index < status log_count.")


def execute(request, context):
    return context.require_service("experiment.execution").command(context, request)



def validate_report(raw):
    ExperimentReport.model_validate_json(raw, strict=True)


def validate_goal_inputs(sources):
    """Validate the goal at its exact source; no fabricated standalone artifact."""
    from .artifact_agent.schema.cognitive import HypothesisProposal
    from .operations.input_validation import parse_bound_json
    from .artifact_agent.schema.research_objective import ObjectiveSourceError, validate_objective_source
    from .operations.invoke import OperationInvocationError
    hypothesis = sources.binding_descriptors["hypothesis_portfolio"]
    labels = dict(hypothesis.labels)
    if (hypothesis.producer_run_id is None
            or labels.get("operation_id") != "science.hypothesis.propose.v1"
            or labels.get("operation_output_port") != "hypothesis_portfolio"):
        raise OperationInvocationError("input_origin_unavailable", port="subject",
            message="Select an exact completed hypothesis task output, not an uploaded portfolio or copied JSON.")
    proposal = parse_bound_json(HypothesisProposal, sources["hypothesis_portfolio"],
        admission_port="subject")
    if not proposal.hypotheses:
        raise OperationInvocationError("input_portfolio_empty", port="subject",
            message="An experiment requires a nonempty sealed hypothesis portfolio. Return to hypothesis formulation when no testable hypothesis is available.")
    descriptor = sources.binding_descriptors["objective_source"]
    try:
        goal = validate_objective_source(sources["objective_source"], ref=descriptor.artifact_ref,
            media_type=descriptor.media_type, labels=descriptor.labels)
    except ObjectiveSourceError as error:
        message = {
            "input_objective_invalid": "The selected goal source is unreadable, has incompatible original-text metadata, or declares an invalid structured goal; repair its original content or type.",
            "input_objective_missing": "The selected structured scientific material does not declare the research intent and completion criteria; repair that original structured goal rather than downgrading its type.",
            "input_objective_conflict": "The selected material's goal statement conflicts with its declared objective; resolve the scientific conflict at its source.",
        }[error.reason_code]
        raise OperationInvocationError(error.reason_code, port="subject", message=message) from error
    if proposal.research_objective_key != (goal.objective_key if goal is not None else None):
        raise OperationInvocationError("input_objective_conflict", port="subject",
            message="The hypothesis objective must match its exact original goal; original text has no structured objective key.")


def validate_completion(payload, sources, handoff):
    report = ExperimentReport.model_validate_json(canonical_json(payload), strict=True)
    records = {item["alias"]: item for item in json.loads(sources.tool_snapshot or b"{}").get("records", ())}
    for alias in report.adopted_stages:
        resolved = sources.sealed_material_record(alias)
        if resolved is not None:
            records[alias] = resolved
    def repair(message):
        for alias in sources:
            if alias not in records:
                resolved = sources.sealed_material_record(alias)
                if resolved is not None:
                    records[alias] = resolved
        groups = {}
        for alias, item in records.items():
            metadata = item.get('metadata', {})
            kind = metadata.get('kind')
            label = {'experiment_implementation': 'implementation', 'experiment_execution': 'collected_execution'}.get(kind)
            if kind == 'experiment_stage' and metadata.get('stage') in {'implementation', 'execution', 'validity'}:
                label = metadata['stage']
            if label:
                groups.setdefault(label, []).append(alias)
        candidates = '; '.join(f'{key}={values[:6]}' + (f' ({len(values)-6} more)' if len(values)>6 else '')
            for key, values in groups.items())
        raise SemanticRuleViolation(message + ' Available exact material aliases: ' + (candidates or 'none')
            + '. Adopt each exact implementation, its collected execution or native execution materials, and validity citing that execution. Native materials retain Worker provenance; they are not a collected execution receipt.')
    unknown = [alias for alias in report.adopted_stages if alias not in records]
    if unknown:
        repair('Unknown adopted_stages: ' + repr(unknown[:6]) + '.')
    selected = [records[alias] for alias in report.adopted_stages]
    for alias, record in zip(report.adopted_stages, selected):
        if record.get("tool_name") != "worker_experiment_stage":
            continue
        stage = StageSubmission.model_validate_json(sources.stage_source(alias), strict=True)
        if stage.comparison is None:
            continue
        # Body aliases belong to the original producer, even after another task
        # binds this comparison under a different name.
        source_refs = record.get("source_refs") or {
            name: descriptor.artifact_ref for name, descriptor in sources.binding_descriptors.items()}
        prediction_ref = source_refs.get(stage.comparison.prediction)
        prediction_alias = next((name for name, descriptor in sources.binding_descriptors.items()
            if descriptor.artifact_ref == prediction_ref), None)
        declaration_record = sources.sealed_material_record(prediction_alias) if prediction_alias else None
        if declaration_record is None or declaration_record.get("tool_name") != "worker_experiment_stage":
            raise SemanticRuleViolation("Adopted comparison requires the declaration's unique original stage receipt; bind that exact material.")
        declaration = StageSubmission.model_validate_json(sources.stage_source(prediction_alias), strict=True)
        if declaration.prediction_declaration is None:
            raise SemanticRuleViolation("The exact declaration receipt has no scientific prediction.")
        _same_criterion(declaration.prediction_declaration, stage.comparison)
        for name in declaration.prediction_declaration.conditions:
            if name not in declaration_record["source_refs"]:
                raise SemanticRuleViolation("Prediction condition is missing from its original producer namespace.")
        for name in stage.comparison.observations:
            if name not in source_refs:
                raise SemanticRuleViolation("Comparison observation is missing from its original producer namespace.")
    if report.outcome != "completed":
        return
    def identity(item):
        return canonical_json(item["artifact_ref"]) if "artifact_ref" in item else item["alias"]
    def source_identity(item, alias):
        if "source_refs" in item:
            ref = item["source_refs"].get(alias)
            return canonical_json(ref) if ref is not None else None
        return identity(records[alias]) if alias in records else alias
    def stage_is(item, stage):
        return (item.get("tool_name") == "worker_experiment_stage"
            and item["metadata"].get("kind") == "experiment_stage"
            and item["metadata"].get("stage") == stage)
    def published_file(item, alias):
        target = source_identity(item, alias)
        for name, descriptor in sources.binding_descriptors.items():
            if canonical_json(descriptor.artifact_ref.model_dump(mode="json")) == target and name not in records:
                resolved = sources.sealed_material_record(name)
                if resolved is not None:
                    records[name] = resolved
        for name, record in tuple(records.items()):
            if (identity(record) != target or record.get("output_port") != "attachments"
                    or record["metadata"].get("kind") != "scientific_attachment"
                    or record["metadata"].get("provenance_status") == "unknown"):
                continue
            original = sources.sealed_material_record(name)
            if (original is not None and identity(original) == target
                    and original.get("tool_name") == "worker_publish_files"
                    and original.get("output_port") == "attachments"):
                return True
        return False
    implementations = {}
    for item in selected:
        if item["metadata"].get("kind") == "experiment_implementation":
            implementations[identity(item)] = "remote"
        elif stage_is(item, "implementation"):
            materials = StageSubmission.model_validate_json(sources.stage_source(item["alias"]), strict=True).materials
            if not any(published_file(item, alias) for alias in materials):
                repair("Native implementation requires its exact published source files.")
            implementations[identity(item)] = "native"
    executions = set()
    for item in selected:
        remote = item["metadata"].get("kind") == "experiment_execution"
        native = stage_is(item, "execution")
        if not remote and not native:
            continue
        implementation = source_identity(item, item["metadata"].get("implementation"))
        if implementation not in implementations:
            repair("Adopted execution is not linked to a selected implementation.")
        if remote:
            if item.get("tool_name") != "worker_experiment_execute" or implementations[implementation] != "remote":
                repair("Remote execution requires its original collected-execution receipt and implementation.")
            receipt = json.loads(sources[item["alias"]])
            if receipt.get("state") != "collected" or receipt.get("terminal_state") != "succeeded":
                repair("Completed requires successful collected execution; retain failed or unfinished work as blocked/inconclusive.")
        else:
            declaration = StageSubmission.model_validate_json(sources.stage_source(item["alias"]), strict=True).native_execution
            if (declaration is None or implementations[implementation] != "native"
                    or declaration.state != "succeeded" or not declaration.results
                    or item["metadata"].get("origin") != "worker_native"
                    or item["metadata"].get("execution_attestation") != "unavailable"
                    or source_identity(item, declaration.implementation) != implementation
                    or not all(published_file(item, alias) for alias in (*declaration.logs, *declaration.results))):
                repair("Completed native execution requires its exact implementation, published logs/results and Worker-reported succeeded state; this is not execution attestation.")
        executions.add(identity(item))
    validity = [item for item in selected if item["metadata"].get("kind") == "experiment_stage"
        and item["metadata"].get("stage") == "validity"
        and executions.intersection(source_identity(item, alias) for alias in item["metadata"].get("derived_from", ()))]
    if not implementations or not executions or not validity:
        missing = ('implementation' if not implementations else 'execution linked to selected implementation' if not executions
            else 'validity citing selected execution')
        repair('Completed experiment is missing ' + missing + '.')
    covered = {source_identity(item, alias) for item in validity
        for alias in item["metadata"].get("derived_from", ())}
    if executions - covered:
        repair("Every adopted execution requires validity citing that exact execution.")


def finalize(request):
    def project(value):
        if request.output_schema_id == "scidiscovery.experiment-review.v1":
            from .general_science_experiment_review import ExperimentReview
            review = validate_finalizer_payload(ExperimentReview, value["payload"])
            materialize_summary_handoff(value, review.verdict)
            return
        report = validate_finalizer_payload(ExperimentReport, value["payload"])
        if any(alias not in request.binding_descriptors for alias in report.adopted_stages):
            raise WorkspaceProtocolError("Adopted stages must name sealed material available in this task.")
        materialize_summary_handoff(value, {"completed": "pass", "inconclusive": "inconclusive",
            "blocked": "blocked"}[report.outcome])
    return finalize_result(request, project)


_REFERENCE_TEXT = """Own one bounded experiment for one answerable scientific question:
design, implement, debug when useful, execute locally with the native shell or
available remote tools, retain observations and judge validity. Test the exact
sealed hypotheses in hypothesis_portfolio; own feasibility and test design.
Within H's declared mechanisms, key assumptions, allowed data and validation
boundaries, the assigned question, original criteria and user constraints,
continuously try related parameters, implementations, numerical
methods, repetitions, robustness checks and baselines. A script run or fixed trial
count does not force a new task. Retain each attempt's results and limitations and
the best eligible candidate under the original selection criterion.
Use a fixed grid or preplanned comparison for initial screening when useful.
After observations, choose the next trial from the remaining scientific question:
refine a promising route inside current H, stop a weak route, or seal evidence for
A when the answer or a boundary calls for independent analysis. Explain how exact
observations support that choice; do not continue an unchanged list just to fill
run counts. Weak controls can still answer a useful contrast after strong/weak
separation, but a preplanned weak-control tail with no improvement does not show
that the promising route is exhausted or that global exploration should stop.
Reuse design/result narratives, logs and existing stage conclusion,
remaining_question and materials to retain the question, change, observation and
its effect on the next choice. This adds no per-run form, approval or fixed trial
limit; design stages remain optional. A judges global exploration and closeout;
W does not own global run-count completion or the overall delivery floor.
If observations challenge a key assumption or proxy metrics conflict with robust
evidence, an affordable discriminating test may continue within those boundaries.
Changing a mechanism, key assumption, allowed data or validation boundary requires
sealing the bounded observations for A, then H revision before executing the new
direction. Genuine original user constraints and control permissions remain
binding; method freedom does not relax them. H-derived scientific restrictions
also govern the current assignment until H explicitly revises them. You may report an idea outside scope or already-observed anomalous evidence,
with its limits, but must not execute a new direction on that basis.
When resolving a conflict requires changing the assigned question or original
criteria, likewise return it through A to H; do not make the global scientific
decision yourself. Seal also when sufficiently answered, without
further scientific progress, or at budget/permission boundaries. Cheap computation
does not remove these limits. New exploratory criteria may be declared with their
evidence limits inside the declared boundaries; they cannot replace original
criteria or authorize execution outside H's hypotheses. A and H own revisions.
Read subject for the current question, stage objective and conclusions, and
objective_source for the frozen global goal and completion criteria: original user
text, research objective, the foundation's objective_contract, or the intake's
scientific_foundation.objective_contract. Reuse them in place; distinguish supplied
requirements from working assumptions and criteria. Control does not invent these
from text. Global completion conditions and trial/stopping history accumulate
across sealed tasks, not as this Worker's delivery floor. Carry original requirements,
unmet conditions and exact history; bounded delivery need not finish the overall
goal. Do not expand budgets or retries.
Use the assigned handoff target to plan within the current controlled time budget,
leaving independent analysis and Root closeout time. Retain and check deliverable
materials during the trials, rather than postponing all publication and validation
to the end. Reassess remaining time before further work; when the bounded answer
or a stopping boundary is reached, deliver the evidence and limitations promptly.
Planning targets do not cancel accepted executions or extend their authority.
Reuse exact supplied materials, sealed conclusions, limitations and engineering
handoff. Start with documented entry points/help, data schema/shape and selected
statistics, not whole datasets or CLI implementations. Delegate bulk code/log
inspection when helpers are available; otherwise read needed ranges or report the
gap. Inspect specific ranges for interface gaps, failures or risks. Keep corrections
here; request missing facts or deliver blocked/inconclusive with the remaining question.
Unavailable or unperformed execution cannot support completed. Keep stage conclusions
concise; cite already-published exact materials when their provenance still applies.
Publish new records/logs as attachments; new execution observations retain their
own provenance even when bytes match. Reference originals instead of copying them
into each narrative. Keep complete logs on disk; show counts, key measurements,
failures and exact locators. Batch deterministic preparation/collection until the
next scientific decision or required seal; preserve all failed attempts and stop
on a boundary. Do not batch past observations that should change the next trial.
Cite exact sealed materials with worker_experiment_stage.
When useful, a design stage may include prediction_declaration with a prediction,
criterion and aliases of the actual conditions. A validity stage may include a
comparison naming that exact sealed declaration and observation aliases; result
evidence_keys name those observation aliases. These optional bounded declarations
fix references, not scientific truth. Sealing reads/binds the available originals;
final adoption additionally verifies the declaration's original stage receipt.
Bind a previous declaration as scientific_materials for that validation. Retain
the original prediction, criterion and threshold when comparing held-out observations;
never overwrite them to turn observed results into prior validation. Exploratory
revisions seal a new declaration/version with evidence limits as above. not_run
is no test.
Include execution interpretation when useful: executor facts do not supply your
scientific conclusion. Rework seals a new version; retain the exact versions you
adopt in the final report. Stage sealing is neither review nor final completion.
Sealed public materials return artifact_name for exact handoff before this Run ends.
Use existing handoff.next_actions for a complete recommended scientific next
question, role and exact source names, with limitations in handoff.assumptions
or missing_inputs and the formal report. Put the primary direction first and
keep each statement within 1024 UTF-8 bytes so Root relays it without reconstructing
science; this adds no schema, gate or permission. A owns independent outcome
interpretation and scientific followup/closeout.
Keep reusable engineering handoff in existing narratives: exact published source
aliases, runnable entry/parameters, formats, checked constraints, unchecked risks
and original locations. If delivery includes saved code or a model, check a minimal
save/load or fresh-process entry early; retain dependencies and recoverable materials
for the best eligible candidate. Reuse an unchanged verified chain when sufficient;
handoff does not attest execution or replace original contracts.
Choose local or remote execution independently at each step; the same task may
move from local to remote and back to local. Local computation uses your declared
native shell permissions and installed environment. Reuse exact bound published
source references or publish new/changed source files with worker_publish_files.
Seal an implementation stage citing them, run the exact source locally, then publish
this execution's logs and results. Seal an execution stage
with native_execution naming that implementation, its state and exact published
logs/results. Seal validity citing that execution. Control computes file identity
and provenance; never author hashes or internal bindings. Native execution claims
and optional process observations remain Worker reports, without independent
execution attestation; carry that limitation into the report. No special wrapper
is required. A missing local environment supports blocked/inconclusive.
Independent review is optional unless an applicable declared policy requires it.
When worker_experiment_execute is available, read its complete contract before
remote work; it owns the applicable review, authorization and recovery rules.
"""
from .artifact_agent.schema.role_result import (BOUNDARY_GUIDANCE, reference_sections,
    with_reference)
_REFERENCE_TEXT += BOUNDARY_GUIDANCE + """All current boundaries in the exact assigned H portfolio remain applicable to W,
including derived or source-unverified ones. Choose methods inside them. Report
ideas or source doubts through A; a proposed change requires H's explicit sealed
revision before executing the changed direction. Your declarations describe your
findings or proposals, not a replacement H boundary or control policy.
"""
CORE_PROMPT = """Safety (always): use only the data and methods H and the user allow; never use hidden tests or held-out
targets outside the allowed data, never leak evaluation data into fitting, never invent success;
user constraints and control permissions stay binding.
W decision checklist (core contract). Detailed rules: guide role_reference, section named in [].
1. Own one bounded experiment for the assigned question inside the exact sealed
hypothesis_portfolio: its mechanisms, key assumptions, allowed data and validation boundaries
(all current H boundaries apply, including derived or source-unverified ones), the original
criteria and user constraints. You own feasibility, test design, implementation, debugging,
local or remote execution, retained observations and validity judgment. [w_scope]
2. Read subject (current question, stage objective, conclusions) and objective_source (frozen
global goal and completion criteria); distinguish supplied requirements from working assumptions.
Global completion and run counts are not your delivery floor; A owns global exploration and
closeout. [w_inputs]
3. Before optimizing a metric, state in the design or conclusions what it observes and misses,
the proxy/target distinction and the assumptions the proxy needs; execution success does not
establish objective support.
4. Iterate inside this task: related parameters, implementations, methods, repetitions,
robustness checks and baselines; a fixed grid may screen. After each observation choose the next
trial from the remaining question (refine, stop a weak route, or seal for A) and explain why;
never run an unchanged list to fill counts; a weak-control tail does not prove exhaustion. Retain
each attempt's results and limitations and the best eligible candidate under the original
criterion. [w_iteration]
5. Changing a mechanism, key assumption, allowed data, validation boundary, the assigned question
or original criteria: seal the bounded observations for A, then H revision before executing.
Report out-of-scope ideas or anomalies without executing them. Seal when answered, without
progress, or at a budget/permission boundary; user constraints and control permissions stay
binding; never expand budgets or retries. [w_revision_route]
6. Plan within the controlled time budget, leaving analysis and closeout time; check deliverables
during trials. Use help/schema/selected statistics before bulk data or CLI source; delegate bulk
inspection when available, otherwise read needed ranges. Keep full logs on disk and return
bounded results/failures; batch only until the next scientific decision or required seal.
Unperformed execution cannot support completed. [w_budget, w_materials]
7. Execution chain: seal implementation, run the exact source, publish logs/results, seal
execution with native_execution, seal validity. Native claims stay unattested Worker reports; a
missing environment supports blocked/inconclusive. Optional design prediction_declaration and
validity comparison fix references, not truth; never overwrite a prediction after observing;
not_run is no test. Before remote work read worker_experiment_execute. [w_execution, w_predictions]
8. Report: cite exact sealed materials, keep conclusions concise, retain adopted versions,
failed/inconclusive attempts and limitations; handoff.next_actions gives the next question, role
and exact sources (primary first; relay byte limits in [w_reporting]); keep reusable engineering handoff in
narratives. [w_reporting, w_engineering]
9. Your optional boundary_declarations are findings or proposals, not replacement H boundaries; a
matched quote proves only its bytes, never interpretation or authority. [boundary_declarations, w_boundaries]
"""
PROMPT = with_reference(CORE_PROMPT, reference_sections(_REFERENCE_TEXT, (
    ("w_scope", "Own one bounded experiment for one answerable"),
    ("w_iteration", "Use a fixed grid or preplanned comparison"),
    ("w_revision_route", "If observations challenge a key assumption"),
    ("w_inputs", "Read subject for the current question"),
    ("w_budget", "Use the assigned handoff target to plan"),
    ("w_materials", "Reuse exact supplied materials, sealed conclusions"),
    ("w_predictions", "When useful, a design stage may include prediction_declaration"),
    ("w_reporting", "Include execution interpretation when useful"),
    ("w_engineering", "Keep reusable engineering handoff in existing narratives"),
    ("w_execution", "Choose local or remote execution independently"),
    ("boundary_declarations", "Boundary declarations separate claim origin"),
    ("w_boundaries", "All current boundaries in the exact assigned H portfolio"),
), remove=(
    # Enforced by StageSubmission(extra="forbid") and control-side publication, which assigns identities.
    " Control computes file identity\nand provenance; never author hashes or internal bindings.",
)))
REMOTE_EXECUTION_GUIDANCE = """For remote work, if its policy requires independent implementation review, send prepare's review.request
to the scheduler, or obtain it with worker_experiment_execute(action="review",
implementation=<sealed reference>). The request suspends controlled experimental work while review is pending.
Wait for the scheduler notification; read the sealed outcome with that same review action, then continue
this same task; a negative review calls for scientific correction, not execution.
When that review is required, obtain a sealed pass before remote execution;
never finish merely to make the subject addressable.
worker_experiment_execute is present only for the configured remote provider.
Use the following submission/review guidance only for that remote tool.
Submission runs in the control background. submission.state=pending acknowledges
the original request, not solver success. Poll status with the same name. An
unknown submission is reconciled by read-only lookup, never by creating another
request; submission.error provides a bounded diagnostic reference. A started
submission can be stopped with cancel: control retires an uncertain new-protocol
request remotely before releasing its reservation, or cancels its existing job.
An unreachable or older backend cannot prove permanent absence. A started
or successful solver is not by itself a valid scientific result. Read the collected
facts and deliver an ExperimentReport with the adopted sealed stage references.
Use status for fresh remote state and bounded progress of a submitted execution;
it never starts, cancels or collects work. advance can continue execution after
authorization; collect transfers result files. Status caches log tails but returns no
log text; logs defaults to the newest cached page without a remote call. Use its
before cursor to read older text or after cursor to follow new text. A gap marks
lost continuity. Inspect or delegate log diagnosis only when it can change a decision.
Report elapsed time, log update time and any
refresh failure when relevant. A running state or quiet log does not prove progress
or a stall; do not resubmit an existing execution to obtain an update.
Independent review is optional unless an applicable declared policy requires it;
no fixed preflight, initialization or reviewer checklist is a submission condition.
"""
SCHEMA = schema_resource(ExperimentReport, "scidiscovery.experiment-report.v1")
CONTRACT = semantic_contract(SemanticRuleSpec("experiment.sealed_material",
    "Adopted stages must identify sealed materials created in or explicitly bound to this task; original receipts and authorship are retained. Completed requires every adopted execution to cite its selected exact implementation and a validity assessment citing that execution. Remote work requires successful collected receipts. Native work requires published implementation files, Worker-reported successful execution and published logs/results; file identities do not attest execution. Finish or cancel outstanding execution and diagnostics before final delivery. Design and debug stages are optional. Otherwise report blocked or inconclusive; scientific validity belongs to the author. A fixed grid may screen initially; subsequent choices use observed evidence to continue within current H, stop a weak route or hand off to A. A preplanned weak-control tail does not establish exhaustion of a promising route. Reuse existing stage/result narratives and exact materials for the question, change, observation and next choice; no per-run form, approval or fixed trial limit is added. Mechanism, key-assumption, allowed-data or validation-boundary changes require A then H revision before execution; actual constraints and permissions remain binding. W does not own global run-count completion or overall closeout."))
PROTOCOL = "scidiscovery.complete-experiment.v1"
WORKSPACE = WorkspaceContract()
AGENT = CallableComponent("agent", lambda: None)
VALIDATOR = CallableComponent("validator", validate_report)
COMPLETION_VALIDATOR = CallableComponent("validator", validate_completion)
GOAL_VALIDATOR = CallableComponent("validator", validate_goal_inputs)
from .operations.workspace import WorkspaceFinalizer
from .plugin_runtime.results import result_draft_schema, RESULT_PROJECTION_VERSION
FINALIZER = CallableComponent("workspace_finalizer", WorkspaceFinalizer(
    finalize, result_draft_schema, RESULT_PROJECTION_VERSION,
    predecessor=("scidiscovery.general_science_experiment_task:FINALIZER", "4")))
STAGE_TOOL = WorkerToolDefinition(name="worker_experiment_stage",
    description="Seal a stage conclusion and exact scientific materials. Reuse conclusion, remaining_question and materials to explain the question, change, observation and its effect on the next trial or handoff; no submission is required for each exp run. Optional design prediction_declaration freezes a prediction/criterion/conditions; optional validity comparison reads that exact declaration and actual observations. Handler checks structure and exact bindings; original declaration receipt is additionally checked when the final report adopts the comparison (bind the declaration as scientific_materials). Sealing alone is a preview, not scientific correctness or final validation; not_run does not test a prediction. Returns a local reference and immutable artifact_name; continue working after sealing.",
    input_model=DraftStageSubmission, capability="experiment.stage", contextual_handler=submit_stage, owner_only=True,
    evidence_ports=("tool_evidence", "recovery_manifest_output", "attachments"))
EXECUTE_TOOL = WorkerToolDefinition(name="worker_experiment_execute",
    description="Request, advance, collect or cancel this experiment's formal execution. start may return submission.state=pending while control submits in the background; query status with the same name. unknown is not proof of non-submission; never create a replacement. Async failures are readable in submission.error. review returns an exact public subject and operation_invoke request for independent review; it starts neither a reviewer nor execution. status refreshes remote state and bounded progress of a submitted execution without submitting, cancelling or collecting. advance can continue an authorized execution; collect transfers result files. Confirmed terminal states require no remote query. Status returns timing, log metadata and refresh failure only, never log text. Use logs for the newest cached page, direction=before with its before cursor for older text, or direction=after with its after cursor for new text. No change returns empty text; gap=snapshot_replaced explicitly marks lost continuity and returns a fresh newest page. It performs no remote query. After successful collection read/export the collected log outputs. Read text or export exact collected scientific files into scratch for local analysis. Uses configured authorization and the original scientific budget.\n" + REMOTE_EXECUTION_GUIDANCE,
    input_model=ExecuteExperiment, capability="experiment.execute", contextual_handler=execute,
    required_services=("experiment.execution",), evidence_ports=("tool_evidence", "recovery_manifest_output"))


_REF = ComponentRef
COMPONENTS = (
    ComponentSpec("experiment_task_protocol", "resource", __name__ + ":PROTOCOL", public=True),
    ComponentSpec("experiment_task_agent", "agent", __name__ + ":AGENT"),
    ComponentSpec("experiment_task_prompt", "resource", __name__ + ":PROMPT"),
    ComponentSpec("experiment_task_schema", "resource", __name__ + ":SCHEMA", public=True),
    ComponentSpec("experiment_task_contract", "resource", __name__ + ":CONTRACT"),
    ComponentSpec("experiment_task_validator", "validator", __name__ + ":VALIDATOR",
        resources=(_REF("experiment_task_contract"),)),
    ComponentSpec("experiment_completion_validator", "validator", __name__ + ":COMPLETION_VALIDATOR",
        resources=(_REF("experiment_task_contract"),)),
    ComponentSpec("experiment_goal_inputs", "validator", __name__ + ":GOAL_VALIDATOR"),
    ComponentSpec("experiment_task_finalizer", "workspace_finalizer", __name__ + ":FINALIZER"),
    ComponentSpec("experiment_task_workspace", "workspace", __name__ + ":WORKSPACE",
        resources=(_REF("experiment_task_finalizer"),)),
    ComponentSpec("experiment_stage_tool", "worker_tool", __name__ + ":STAGE_TOOL"),
    ComponentSpec("experiment_execute_tool", "worker_tool", __name__ + ":EXECUTE_TOOL", public=True),
)
OPERATION = scientific_agent_operation(
    "science.experiment.v1", "Test an assigned scientific question within sealed hypotheses through related trials, execution and validity judgment.",
    "Sealed hypotheses need test design and related parameter, implementation or numerical trials within the assigned question and user constraints. Local design changes may continue; retain attempts and the original-criterion best for independent analysis.",
    "Open research without sealed hypotheses, changing or introducing hypotheses, departing the bound hypotheses/question or user-authorized scope, or independent result interpretation. Reuse sufficient materials; prepare missing originals when needed.",
    decision_fields=("outcome", "summary", "remaining_question", "limitations"),
    scientific_progress=ScientificProgressSpec(stage="experiment", subject_port="hypothesis_portfolio", objective_port="objective_source"),
    artifact_capture=ArtifactCaptureSpec(required_source_ports=("objective_source",)),
    agent=_REF("experiment_task_agent"), prompt=_REF("experiment_task_prompt"),
    workspace=_REF("experiment_task_workspace"),
    input_validation=InputValidationSpec(_REF("experiment_goal_inputs"), "science.experiment.v1.goal",
        "Select a sealed nonempty hypothesis portfolio or an exact prior experiment carrying one. Control resolves the original hypothesis and global goal from immutable producer inputs; original task text or intake alone cannot start an experiment. Root does not bind internal goal objects."),
    tools=(_REF("experiment_stage_tool"),),
    inputs=(
        _input("subject", "Exact sealed hypothesis portfolio or prior experiment carrying that hypothesis. Select its instance name; control retrieves its original scientific sources. Original task text, intake, foundation or a goal alone is insufficient.", "*",
            media_types=("*/*",), exposure="on_demand", usage="evidence_inventory"),
        _input("hypothesis_portfolio", "Control-bound exact sealed hypotheses tested by this experiment.",
            "scidiscovery.hypothesis-proposal.v2", exposure="on_demand", usage="evidence_inventory").model_copy(update={
            "derivation": InputDerivationSpec(anchor_port="subject",
                source_paths=((), ("hypothesis_portfolio",), ("subject",)),
                source_schemas=("scidiscovery.hypothesis-proposal.v2",))}),
        _input("objective_source", "Control-bound exact scientific source of the global goal and completion criteria.", "*",
            media_types=("*/*",), exposure="on_demand", usage="evidence_inventory").model_copy(update={
            "derivation": InputDerivationSpec(anchor_port="hypothesis_portfolio", source_original_text=True,
                source_paths=((), ("scientific_foundation",), ("hypothesis_portfolio", "scientific_foundation"),
                              ("objective_source",), ("research_objective",)),
                source_schemas=("scidiscovery.research-objective.v1", "scidiscovery.scientific-foundation.v1",
                                "scidiscovery.scientific-intake.v1"))}),
        _input("scientific_materials", "Exact hypothesis, evidence, parameters or feedback needed for this experiment.", "*",
            media_types=("*/*",), min_items=0, max_items=16, exposure="material", usage="evidence_inventory",
            max_item_bytes=MaterialInputSettings().max_item_bytes),
        InputPortSpec(name="prior_experiment", description="Optional exact previous experiment whose feedback this task addresses.",
            schema="scidiscovery.experiment-report.v1", media_types=("application/json",), codec=_REF("json_codec"),
            schema_resource=_REF("experiment_task_schema"), min_items=0, exposure="on_demand", usage="prior_signal"),
        InputPortSpec(name="scientific_files", description="Optional exact files of any registered type (PDF, image, table, archive or simulation grid); streamed on request. These may also be supplied as scientific_materials.",
            schema="*", media_types=("*/*",), codec=_REF("opaque_codec"),
            schema_resource=_REF("wildcard_schema"), min_items=0, max_items=16,
            max_item_bytes=MaterialInputSettings().max_item_bytes,
            exposure="file_reference", usage="evidence_inventory"),
    ),
    outputs=(
        OutputPortSpec(name="experiment", description="Complete experiment conclusion and adopted sealed stages.",
            schema="scidiscovery.experiment-report.v1", media_types=("application/json",), codec=_REF("json_codec"),
            schema_resource=_REF("experiment_task_schema"), kind="experiment_report", max_item_bytes=65536,
            validator=_REF("experiment_task_validator"), validator_rule_id="experiment.sealed_material",
            context_validator=_REF("experiment_completion_validator"), context_rule_id="experiment.sealed_material",
            context_sources=("tool_evidence", "scientific_materials"),
            semantic_contract=_REF("experiment_task_contract")),
        OutputPortSpec(name="tool_evidence", description="Sealed stages, implementations and execution observations.",
            schema="opaque", media_types=("application/json", "application/octet-stream", "text/plain", "text/csv"),
            codec=_REF("opaque_codec"), schema_resource=_REF("opaque_schema"), kind="experiment_material",
            min_items=0, max_items=64, max_item_bytes=32*1024*1024, collection=CollectionSpec(128*1024*1024)),
        OutputPortSpec(name="recovery_manifest_output", description="Control-retained provenance of sealed materials.",
            schema="scidiscovery.tool-evidence-manifest.v1", media_types=("application/json",), codec=_REF("json_codec"),
            schema_resource=_REF("tool_evidence_schema"), kind="tool_evidence_manifest", min_items=0, max_items=1,
            agent_visible=False,
            max_item_bytes=1024*1024, collection=CollectionSpec(1024*1024)),
    ), timeout=7200, max_input_bytes=32_100_000_000, max_output_bytes=130*1024*1024,
    max_files=66, max_attempts=2, native_view_image=True,
)
OPERATION = OPERATION.model_copy(update={"executor": OPERATION.executor.model_copy(update={
    "capability": _REF("experiment_task_protocol")})})
