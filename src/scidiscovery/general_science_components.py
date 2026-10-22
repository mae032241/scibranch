"""State-free component implementations for the general-science plugin."""

from __future__ import annotations

from scidiscovery.operations.input_validation import parse_bound_json

from .operations.input_validation import OperationInvocationError

import json
from collections.abc import Mapping
from typing import Any, Callable

from pydantic import BaseModel

from .artifact_agent.schema.approval import (
    ReviewDocument,
    ReviewDocumentItem,
    ReviewDocumentSection,
)
from .artifact_agent.schema.common import canonical_json
from .artifact_agent.schema.cognitive import (
    CriticReview,
    EvidenceAudit,
    HypothesisProposal,
    validate_critic_review,
    validate_evidence_audit,
    validate_hypothesis_proposal,
)
from .artifact_agent.schema.research_cycle import ProblemFrame, ScientificFoundation, ScientificIntake, validate_scientific_intake
from .operation_contract import SemanticRuleViolation, validate_evidence_source_aliases
from .operation_declaration import payload_validator
from .operations.invoke import ApprovalProjectorContext, ApprovalSubjectSnapshot
from .operations.spec import (CallableComponent, ComponentRef, ComponentSpec,
                              WorkspaceContract)

def _json_codec(raw: bytes) -> bytes:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("schema-bound JSON must be an object")
    return canonical_json(value)


def _opaque_codec(raw: bytes) -> bytes:
    return raw


def _critic_inputs(sources: dict[str, bytes]) -> None:
    portfolio = parse_bound_json(HypothesisProposal, sources["hypothesis_portfolio"],
                                 admission_port="hypothesis_portfolio")
    if not portfolio.hypotheses:
        raise OperationInvocationError("input_portfolio_empty", port="hypothesis_portfolio")


def _hypothesis_inputs(sources: dict[str, bytes]) -> None:
    if "subject" in sources:
        if "scientific_foundation" in sources or "problem_frame" in sources:
            raise OperationInvocationError("input_scientific_source_conflict", port="subject",
                message="Select original task text or the structured frame/foundation branch; bind additional user text as user_context.")
        from .artifact_agent.schema.research_objective import (
            ObjectiveSourceError, is_original_text_objective, validate_objective_source)
        descriptor = sources.binding_descriptors["subject"]
        if not is_original_text_objective(descriptor.artifact_ref, descriptor.media_type, descriptor.labels):
            raise OperationInvocationError("input_objective_invalid", port="subject",
                message="This hypothesis branch requires exact original user task text, not an arbitrary file or a downgraded structured object.")
        try:
            validate_objective_source(sources["subject"], ref=descriptor.artifact_ref,
                media_type=descriptor.media_type, labels=descriptor.labels)
        except ObjectiveSourceError as error:
            raise OperationInvocationError(error.reason_code, port="subject") from error
        return
    if "scientific_foundation" not in sources or "problem_frame" not in sources:
        raise OperationInvocationError("input_scientific_source_missing", port="subject",
            message="Select original task text, or both an existing problem frame and its qualified scientific foundation. Evidence extraction is optional when the task can be understood from the originals.")
    foundation = parse_bound_json(ScientificFoundation, sources["scientific_foundation"],
                                  admission_port="scientific_foundation")
    if foundation.objective_contract is None:
        raise OperationInvocationError("input_objective_missing", port="scientific_foundation", field="/objective_contract")


def _hypothesis_source_keys(sources):
    """Bound aliases and the exact foundation's existing provenance; no copied ledger."""
    allowed = set(sources) | set(getattr(sources, "binding_descriptors", {}))
    raw = sources.get("scientific_foundation")
    if raw is None and "objective_source" in sources:
        descriptor = sources.binding_descriptors["objective_source"]
        if descriptor.artifact_ref.schema_id == "scidiscovery.scientific-foundation.v1":
            raw = sources["objective_source"]
    if raw is not None:
        foundation = parse_bound_json(ScientificFoundation, raw)
        allowed.update(item.source_key for item in foundation.evidence)
        allowed.update(key for item in foundation.items for key in item.evidence_keys)
    return allowed


def _critic_portfolio_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    portfolio = parse_bound_json(HypothesisProposal, sources["hypothesis_portfolio"])
    review = CriticReview.model_validate_json(canonical_json(payload), strict=True)
    validate_evidence_source_aliases(payload, _hypothesis_source_keys(sources))
    expected = tuple(item.hypothesis_key for item in portfolio.hypotheses)
    actual = tuple(item.hypothesis_key for item in review.reviews)
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise SemanticRuleViolation(
            "critic review must cover every exact portfolio hypothesis once"
        )
    expected_verdict = {
        "ready_for_experiment": "pass",
        "revise_hypothesis": "revise",
        "revise_evidence": "inconclusive",
        "design_model_counterfactual": "pass",
        "inconclusive": "inconclusive",
        "reject": "blocked",
    }[review.disposition]
    if handoff.get("verdict") != expected_verdict:
        raise SemanticRuleViolation("critic handoff verdict differs from review disposition")


def _intake_source_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    del handoff
    ScientificIntake.model_validate_json(canonical_json(payload), strict=True)
    validate_evidence_source_aliases(payload, sources)


def _hypothesis_objective_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    """Bind a role-owned stage goal to one immutable global objective."""

    del handoff
    proposal = HypothesisProposal.model_validate_json(
        canonical_json(payload), strict=True
    )
    if "scientific_foundation" not in sources:
        _hypothesis_inputs(sources)
        validate_evidence_source_aliases(payload, _hypothesis_source_keys(sources))
        if proposal.research_objective_key is not None:
            raise SemanticRuleViolation("An original-text hypothesis must not invent a structured research_objective_key.")
        return
    foundation = parse_bound_json(ScientificFoundation, sources["scientific_foundation"])
    # Qualified foundation provenance remains usable without copying its source
    # registry into every hypothesis. The exact frozen foundation owns those keys.
    validate_evidence_source_aliases(payload, _hypothesis_source_keys(sources))
    objective = foundation.objective_contract
    if proposal.research_objective_key != objective.objective_key:
        raise SemanticRuleViolation("hypothesis research_objective_key differs from global objective")


def _validate_audit_handoff_and_sources(
    audit: EvidenceAudit,
    handoff: dict[str, Any],
) -> None:
    if not audit.checks:
        raise SemanticRuleViolation("evidence audit must contain at least one exact check")
    for check in audit.checks:
        if check.status in {"pass", "fail"} and not check.evidence_keys:
            raise SemanticRuleViolation("decisive evidence checks require exact evidence keys")
    statuses = tuple(item.status for item in audit.checks)
    expected_verdict = (
        "blocked"
        if "fail" in statuses
        else "inconclusive"
        if "unknown" in statuses
        else "pass"
    )
    if handoff.get("verdict") != expected_verdict:
        raise SemanticRuleViolation("audit handoff verdict differs from evidence checks")


def _evidence_audit_context(
    payload: dict[str, Any], sources: dict[str, bytes], handoff: dict[str, Any]
) -> None:
    audit = EvidenceAudit.model_validate_json(canonical_json(payload), strict=True)
    validate_evidence_source_aliases(payload, sources)
    _validate_audit_handoff_and_sources(audit, handoff)


def _agent_marker() -> None:
    return None

def _strict_validator(model: type[BaseModel]) -> Callable[[bytes], None]:
    def validate(raw: bytes) -> None:
        model.model_validate_json(raw, strict=True)

    return validate


def _one(values: Mapping[str, tuple[bytes, ...]], name: str) -> bytes:
    items = values.get(name, ())
    if len(items) != 1:
        raise ValueError(f"transform input {name} must contain exactly one item")
    return items[0]


def _intake_split(values: Mapping[str, tuple[bytes, ...]]) -> dict[str, tuple[bytes, ...]]:
    if set(values) != {"scientific_intake", "evidence_audit"}:
        raise ValueError(
            "scientific intake split requires scientific_intake and its review lineage"
        )
    intake = ScientificIntake.model_validate_json(
        _one(values, "scientific_intake"), strict=True
    )
    return {
        "problem_frame": (intake.problem_frame.canonical_json(),),
        "scientific_foundation": (intake.scientific_foundation.canonical_json(),),
    }


def _subjects_by_port(
    context: ApprovalProjectorContext,
) -> dict[str, tuple[ApprovalSubjectSnapshot, ...]]:
    grouped: dict[str, list[ApprovalSubjectSnapshot]] = {}
    refs = [item.ref for item in context.subjects]
    if len(refs) != len(set(refs)):
        raise OperationInvocationError("approval_subject_invalid", message="approval subjects must be distinct exact artifacts")
    for item in context.subjects:
        grouped.setdefault(item.port_name, []).append(item)
    return {name: tuple(items) for name, items in grouped.items()}


def _one_subject(
    grouped: dict[str, tuple[ApprovalSubjectSnapshot, ...]], name: str
) -> ApprovalSubjectSnapshot:
    values = grouped.get(name, ())
    if len(values) != 1:
        raise OperationInvocationError("approval_subject_invalid", message=f"approval port {name} requires exactly one subject")
    return values[0]


def _parse_subject(
    subject: ApprovalSubjectSnapshot, model: type[BaseModel]
) -> BaseModel:
    return parse_bound_json(model, subject.content, admission_port=subject.port_name)


def _labels(subject: ApprovalSubjectSnapshot) -> dict[str, str]:
    return dict(subject.labels)


def _require_transform_output(
    subject: ApprovalSubjectSnapshot,
    *,
    operation_id: str,
    port_name: str,
    parent_refs: tuple[object, ...],
) -> None:
    labels = _labels(subject)
    if (
        labels.get("operation_id") != operation_id
        or labels.get("transform_profile") != operation_id
        or labels.get("operation_output_port") != port_name
        or subject.parent_refs != parent_refs
    ):
        raise OperationInvocationError("approval_subject_invalid", message="approval subject has an invalid deterministic lineage")


def _unique_family_source_refs(family: object) -> tuple[object, ...]:
    return tuple(
        dict.fromkeys(
            source.ref for source in family.evidence_sources
        )
    )


def _require_passing_audit(
    audit_subject: ApprovalSubjectSnapshot,
    *,
    expected_operations: frozenset[str] | None,
    required_parent_refs: set[object],
    required_checks: frozenset[str] = frozenset(),
) -> EvidenceAudit:
    labels = _labels(audit_subject)
    operation_id = labels.get("operation_id")
    if expected_operations is not None and operation_id not in expected_operations:
        raise OperationInvocationError("approval_subject_invalid", message="independent audit producer is not the required operation")
    if audit_subject.handoff_verdict != "pass":
        raise OperationInvocationError("approval_subject_invalid", message="independent audit does not have a passing handoff")
    if not required_parent_refs.issubset(set(audit_subject.parent_refs)):
        raise OperationInvocationError("approval_subject_invalid", message="independent audit did not bind the complete exact review set")
    audit = parse_bound_json(EvidenceAudit, audit_subject.content, admission_port=audit_subject.port_name)
    checks = {item.check_key: item for item in audit.checks}
    if not required_checks.issubset(checks) or any(
        checks[key].status != "pass" for key in required_checks
    ):
        raise OperationInvocationError("approval_subject_invalid", message="independent audit lacks a required passing check")
    return audit


def _review_document(
    context: ApprovalProjectorContext,
    *,
    title: str,
    description: str,
) -> ReviewDocument:
    items = tuple(
        ReviewDocumentItem(
            kind=("json_tree" if subject.media_type == "application/json" else "download"),
            label=f"{subject.port_name}（第 {subject.item_index + 1} 项）",
            subject_index=subject.subject_index,
            json_pointer="" if subject.media_type == "application/json" else None,
        )
        for subject in context.subjects
    )
    return ReviewDocument(
        title=title,
        description=description,
        sections=(
            ReviewDocumentSection(
                title="完整冻结审查对象",
                description="以下内容按已编译端口顺序展示，审批不会修改任何科学对象。",
                items=items,
            ),
        ),
    )


def _validate_evidence_qualification(
    context: ApprovalProjectorContext,
) -> None:
    grouped = _subjects_by_port(context)
    foundation_subject = _one_subject(grouped, "scientific_foundation")
    primary_subject = _one_subject(grouped, "extraction_primary")
    audit_subject = _one_subject(grouped, "evidence_audit")
    matching_families = tuple(
        item
        for item in context.producer_families
        if item.primary_ref == primary_subject.ref
    )
    if len(matching_families) != 1:
        raise OperationInvocationError("approval_subject_invalid", message="qualification primary is not the exact producer primary")
    family = matching_families[0]
    members = family.members
    if not members or members[0].ref != primary_subject.ref:
        raise OperationInvocationError("approval_subject_invalid", message="producer family has no unique leading primary")
    bound_siblings = tuple(item.ref for item in grouped.get("producer_outputs", ()))
    expected_siblings = tuple(item.ref for item in members[1:])
    if (
        len(bound_siblings) != len(set(bound_siblings))
        or set(bound_siblings) != set(expected_siblings)
    ):
        raise OperationInvocationError("approval_subject_invalid", message="qualification must bind the complete producer sibling family")
    frozen_refs = _unique_family_source_refs(family)
    if tuple(item.ref for item in grouped.get("frozen_sources", ())) != frozen_refs:
        raise OperationInvocationError("approval_subject_invalid", message="qualification omits or adds a frozen producer source")
    intake = parse_bound_json(ScientificIntake, primary_subject.content, admission_port=primary_subject.port_name)
    foundation = parse_bound_json(ScientificFoundation, foundation_subject.content, admission_port=foundation_subject.port_name)
    if foundation != intake.scientific_foundation:
        raise OperationInvocationError("approval_subject_invalid", message="split foundation differs from the extraction primary")
    _require_transform_output(
        foundation_subject,
        operation_id="science.intake.split.v1",
        port_name="scientific_foundation",
        parent_refs=(primary_subject.ref, audit_subject.ref),
    )
    if (
        family.reviewer_operation is None
        or members[0].port_name not in family.review_subject_outputs
    ):
        raise OperationInvocationError("approval_subject_invalid", message="evidence producer has no compiled independent-review edge")
    audit_inputs = {item.ref for item in members}
    audit_inputs.update(frozen_refs)
    _require_passing_audit(
        audit_subject,
        expected_operations=frozenset({family.reviewer_operation}),
        required_parent_refs=audit_inputs,
    )


def _evidence_qualification_document(context: ApprovalProjectorContext) -> ReviewDocument:
    _validate_evidence_qualification(context)
    return _review_document(
        context,
        title="科学证据资格审查",
        description="审查完整提取输出族、冻结来源、确定性校验和独立证据审核。",
    )


class Components:
    critic_inputs = CallableComponent("validator", _critic_inputs)
    hypothesis_inputs = CallableComponent("validator", _hypothesis_inputs)
    json_codec = CallableComponent("codec", _json_codec)
    opaque_codec = CallableComponent("codec", _opaque_codec)
    intake_validator = CallableComponent("validator", payload_validator(validate_scientific_intake))
    intake_source_context = CallableComponent("validator", _intake_source_context)
    hypothesis_validator = CallableComponent("validator", payload_validator(validate_hypothesis_proposal))
    hypothesis_objective_context = CallableComponent("validator", _hypothesis_objective_context)
    critic_validator = CallableComponent("validator", payload_validator(validate_critic_review))
    audit_validator = CallableComponent("validator", payload_validator(validate_evidence_audit))
    critic_portfolio_context = CallableComponent("validator", _critic_portfolio_context)
    evidence_audit_context = CallableComponent("validator", _evidence_audit_context)
    evidence_agent = CallableComponent("agent", _agent_marker)
    ideator_agent = CallableComponent("agent", _agent_marker)
    critic_agent = CallableComponent("agent", _agent_marker)
    auditor_agent = CallableComponent("agent", _agent_marker)
    evidence_qualification_projector = CallableComponent("projector", _evidence_qualification_document)

    intake_split = CallableComponent("transform", _intake_split)
    problem_frame_validator = CallableComponent("validator", _strict_validator(ProblemFrame))
    foundation_validator = CallableComponent("validator", _strict_validator(ScientificFoundation))


from .plugin_runtime.results import finalize_general_result, result_draft_schema, RESULT_PROJECTION_VERSION
from .operations.workspace import WorkspaceFinalizer
RESULT_FINALIZER = CallableComponent("workspace_finalizer", WorkspaceFinalizer(
    finalize_general_result, result_draft_schema, RESULT_PROJECTION_VERSION,
    predecessor=("scidiscovery.general_science_components:RESULT_FINALIZER", "4")))
WORKSPACE = WorkspaceContract()


def component_specs() -> tuple[ComponentSpec, ...]:
    values: list[ComponentSpec] = []
    semantic_resources = {
        "intake_validator": ("intake_semantic_contract",),
        "intake_source_context": ("intake_semantic_contract",),
        "hypothesis_validator": ("hypothesis_semantic_contract",),
        "hypothesis_objective_context": ("hypothesis_semantic_contract",),
        "critic_validator": ("critic_semantic_contract",),
        "audit_validator": ("evidence_audit_semantic_contract",),
        "critic_portfolio_context": ("critic_semantic_contract",),
        "evidence_audit_context": ("evidence_audit_semantic_contract",),
    }
    for name in (
        "critic_inputs", "hypothesis_inputs",
        "json_codec", "opaque_codec", "intake_validator", "intake_source_context",
        "hypothesis_validator", "hypothesis_objective_context",
        "critic_validator", "audit_validator",
        "critic_portfolio_context", "evidence_audit_context",
        "evidence_agent", "ideator_agent", "critic_agent",
        "auditor_agent", "evidence_qualification_projector",
    ):
        values.append(ComponentSpec(
            name,
            getattr(Components, name).kind,
            f"scidiscovery.general_science_components:Components.{name}",
            resources=tuple(ComponentRef(resource) for resource in semantic_resources.get(name, ())),
            configuration_identity=("hypothesis-feedback-sources:v1" if name in {"critic_portfolio_context", "hypothesis_objective_context"} else None),
            public=name in {
                "json_codec", "opaque_codec",
                "intake_validator", "intake_source_context",
                "audit_validator", "evidence_audit_context",
                "evidence_agent", "auditor_agent",
            },
        ))
    for name in ("intake_split",):
        values.append(ComponentSpec(name, "transform", f"scidiscovery.general_science_components:Components.{name}", configuration_identity=f"general-transform:{name}:v1"))

    for name in (
        "problem_frame_validator", "foundation_validator",
    ):
        values.append(ComponentSpec(name, "validator", f"scidiscovery.general_science_components:Components.{name}", configuration_identity=f"general-transform:{name}:v1"))
    values.append(ComponentSpec("result_finalizer", "workspace_finalizer", "scidiscovery.general_science_components:RESULT_FINALIZER", configuration_identity="general.result-finalizer.v4:boundary-attribution"))
    values.append(ComponentSpec("workspace", "workspace", "scidiscovery.general_science_components:WORKSPACE", public=True, resources=(ComponentRef("result_finalizer", plugin_id="general_science"),)))
    values.append(ComponentSpec(
        "pdf_extract_tool",
        "worker_tool",
        "scidiscovery.operations.worker_protocol:EXTRACT_PDF_TEXT_TOOL",
        public=True,
    ))
    values.extend((
        ComponentSpec("source_capture_tool", "worker_tool", "scidiscovery.source_capture:SOURCE_CAPTURE_TOOL"),
        ComponentSpec("tool_evidence_schema", "resource", "scidiscovery.general_science_resources:TOOL_EVIDENCE_SCHEMA", public=True),
    ))
    for name in (
        "opaque_schema", "intake_semantic_contract", "hypothesis_semantic_contract",
        "critic_semantic_contract", "evidence_audit_semantic_contract",
        "scientific_intake_schema", "scientific_foundation_schema",
        "problem_frame_schema", "hypothesis_schema", "critic_review_schema",
        "evidence_audit_schema", "evidence_prompt", "ideator_prompt",
        "critic_prompt", "auditor_prompt",
    ):
        values.append(ComponentSpec(
            name,
            "resource",
            f"scidiscovery.general_science_resources:Resources.{name}",
            public=name in {
                "scientific_intake_schema", "scientific_foundation_schema",
                "hypothesis_schema", "critic_review_schema", "evidence_audit_schema",
                "opaque_schema",
                "intake_semantic_contract", "evidence_audit_semantic_contract",
                "auditor_prompt",
            },
        ))
    for name in ("wildcard_schema",):
        values.append(ComponentSpec(
            name,
            "resource",
            f"scidiscovery.general_science_resources:TransformResources.{name}",
            public=True,
        ))
    return tuple(values)


COMPONENTS = component_specs()

__all__ = ["COMPONENTS", "Components", "WORKSPACE"]
