"""Frozen, state-free declarations for plugin-provided operations."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Literal, Mapping
from types import MappingProxyType
from pydantic import BaseModel, ConfigDict, Field
PLUGIN_PROTOCOL_VERSION = "1"
OPERATION_ABI_VERSION = "23"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_JSON_POINTER = re.compile(r"^/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*$")
_DOMAIN = re.compile(r"^(?:\*\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                     r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_DECISIONS = frozenset({"accept", "reject", "revise", "accept_with_exception"})
class FrozenSpec(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, populate_by_name=True
    )
    def __init__(self, *values: Any, **data: Any) -> None:
        names = tuple(type(self).model_fields)
        if len(values) > len(names) or any(name in data for name in names[:len(values)]):
            raise TypeError("invalid positional declaration fields")
        super().__init__(**dict(zip(names, values)), **data)
class ComponentRef(FrozenSpec):
    component_id: str; plugin_id: str | None = None
class ComponentSpec(FrozenSpec):
    component_id: str
    kind: str
    implementation: str
    protocol_version: str = "1"
    resources: tuple[ComponentRef, ...] = ()
    public: bool = False
    configuration_identity: str | None = None
    # A provider may implement an explicitly declared host capability. Selection
    # is performed once by the catalog compiler, before permissions are frozen.
    extends: ComponentRef | None = None


class SemanticRuleSpec(FrozenSpec):
    """One Worker-visible rule that cannot be expressed by JSON Schema alone."""

    rule_id: str
    description: str
    output_paths: tuple[str, ...] = ("/",)
    required_inputs: tuple[str, ...] = ()

    def issue(self) -> str | None:
        if (
            _ID.fullmatch(self.rule_id) is None
            or not self.description.strip()
            or not self.output_paths
            or len(self.output_paths) != len(set(self.output_paths))
            or any(_JSON_POINTER.fullmatch(path) is None for path in self.output_paths)
            or len(self.required_inputs) != len(set(self.required_inputs))
            or any(_ID.fullmatch(name) is None for name in self.required_inputs)
        ):
            return "semantic_rule_invalid"
        return None


class SemanticContractSpec(FrozenSpec):
    schema_version: Literal[1] = 1
    rules: tuple[SemanticRuleSpec, ...]

    def issue(self) -> str | None:
        if not self.rules:
            return "semantic_contract_invalid"
        identifiers = tuple(rule.rule_id for rule in self.rules)
        if len(identifiers) != len(set(identifiers)):
            return "semantic_rule_duplicate"
        return next((issue for rule in self.rules if (issue := rule.issue())), None)


@dataclass(frozen=True, slots=True)
class CallableComponent:
    kind: str; implementation: Any
class WorkspaceContract(FrozenSpec):
    input_directory: str = "inputs"; output_directory: str = "output"
    scratch_directory: str = "scratch"
class CollectionSpec(FrozenSpec):
    max_total_bytes: int; bundle_validator: ComponentRef | None = None
class OperationDescription(FrozenSpec):
    purpose: str; applies_when: str
    not_for: str
class PortSpec(FrozenSpec):
    name: str
    description: str
    schema_id: str = Field(alias="schema")
    media_types: tuple[str, ...]
    codec: ComponentRef
    schema_resource: ComponentRef
    min_items: int = 1
    max_items: int = 1
    max_item_bytes: int = 1024 * 1024
    agent_visible: bool = True
    def issue(self) -> str | None:
        if (not _ID.fullmatch(self.name) or not all((self.description, self.schema_id))
                or not self.media_types or len(self.media_types) != len(set(self.media_types))
                or any(not item for item in self.media_types)):
            return "port_invalid"
        if self.min_items < 0 or self.max_items < max(1, self.min_items):
            return "port_cardinality_invalid"
        return "port_size_invalid" if self.max_item_bytes <= 0 else None
class InputDerivationSpec(FrozenSpec):
    """Exact producer traversal or typed direct parents; never global artifact search."""
    anchor_port: str
    producer_input_path: tuple[str, ...] = ()
    select: Literal["subject", "siblings", "sources", "parents", "receipt_materials"] = "subject"
    producer_output_port: str | None = None


class InputPortSpec(PortSpec):
    derivation: InputDerivationSpec | None = None
    usage: str = "scientific_input"
    exposure: str = "handoff_only"
    require_current: bool = False
    # Top-level JSON fields that must be present and non-null before invocation.
    required_non_null_fields: tuple[str, ...] = ()
    def issue(self) -> str | None:
        if self.exposure in {"material", "file_reference"} and self.required_non_null_fields:
            return "file_reference_contract_invalid"
        if self.exposure == "material" and (self.schema_id != "*" or self.usage != "evidence_inventory"):
            return "input_wildcard_invalid"
        if self.required_non_null_fields and (
            self.media_types != ("application/json",)
            or len(self.required_non_null_fields) != len(set(self.required_non_null_fields))
            or any(not name.strip() for name in self.required_non_null_fields)
        ):
            return "input_content_requirement_invalid"
        wildcard = self.schema_id == "*" or self.media_types == ("*/*",)
        if wildcard and not (
            self.schema_id == "*"
            and self.media_types == ("*/*",)
            and self.exposure in {"handoff_only", "on_demand", "material", "file_reference"}
            and self.usage == "evidence_inventory"
        ):
            return "input_wildcard_invalid"
        issue = super().issue() or (None if self.exposure in {
            "full", "on_demand", "handoff_only", "file_reference", "material"
        } else "input_exposure_invalid")
        if issue is not None:
            return issue
        return None
class OutputPortSpec(PortSpec):
    kind: str
    payload_schema_version: int = Field(default=1, ge=1)
    validator: ComponentRef | None = None
    validator_rule_id: str | None = None
    semantic_contract: ComponentRef | None = None
    collection: CollectionSpec | None = None
    context_validator: ComponentRef | None = None
    context_rule_id: str | None = None
    context_sources: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    def issue(self) -> str | None:
        issue = super().issue() or (None if self.kind else "port_invalid")
        if issue is not None:
            return issue
        if (
            len(self.evidence_paths) != len(set(self.evidence_paths))
            or any(_JSON_POINTER.fullmatch(path) is None for path in self.evidence_paths)
        ):
            return "output_evidence_path_invalid"
        if (
            (self.validator_rule_id is not None and self.validator is None)
            or (self.context_rule_id is not None and self.context_validator is None)
            or any(
                value is not None and _ID.fullmatch(value) is None
                for value in (self.validator_rule_id, self.context_rule_id)
            )
        ):
            return "output_checker_rule_invalid"
        return None
class NativeToolPolicy(FrozenSpec):
    shell: str = "none"
    view_image: bool = False
    web_search: str = "disabled"
    def issue(self) -> str | None:
        return (
            None
            if (self.shell in {"none", "sandboxed", "inherited_prototype"}
                and self.web_search in {"disabled", "cached", "live"})
            else "native_tool_policy_unsupported"
        )
class ExecutorRef(FrozenSpec):
    kind: str
    component: ComponentRef
    preparation: ComponentRef | None = None
    workspace: ComponentRef | None = None
    tools: tuple[ComponentRef, ...] = ()
    resources: tuple[ComponentRef, ...] = ()
    prompt: ComponentRef | None = None
    model: str | None = None
    native_tools: NativeToolPolicy = NativeToolPolicy()
    capability: ComponentRef | None = None
class NetworkPolicy(FrozenSpec):
    mode: str = "none"
    allowed_domains: tuple[str, ...] = ()
    max_requests: int = 0
    def issue(self) -> str | None:
        if self.mode == "none":
            return "network_not_default_deny" if self.allowed_domains or self.max_requests else None
        if self.mode == "public_web":
            return None if not self.allowed_domains and self.max_requests > 0 else "network_scope_invalid"
        if self.mode != "restricted":
            return "network_mode_invalid"
        domains = self.allowed_domains
        if (not domains or self.max_requests <= 0 or len(domains) != len(set(domains))
                or any(not _DOMAIN.fullmatch(domain) for domain in domains)):
            return "network_scope_invalid"
        return None
class LimitsSpec(FrozenSpec):
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int
    max_files: int
    max_attempts: int = 1
    network: NetworkPolicy = NetworkPolicy()
    def issue(self) -> str | None:
        values = (self.timeout_seconds, self.max_input_bytes, self.max_output_bytes,
                  self.max_files, self.max_attempts)
        return "limits_invalid" if min(values) <= 0 or self.max_attempts > 3 else self.network.issue()
class ApprovalOption(FrozenSpec):
    label: str; decision: str
    requires_reason: bool = False
    description: str | None = None
class ApprovalContract(FrozenSpec):
    subject_ports: tuple[str, ...]
    question: str
    options: tuple[ApprovalOption, ...]
    projector: ComponentRef | None = None
    kind: str = "execution_authorization"
    allow_policy_authorization: bool = False
    def issue(self, port_names: set[str], external: bool) -> str | None:
        subjects = set(self.subject_ports)
        if (not subjects or len(subjects) != len(self.subject_ports)
                or not subjects <= port_names):
            return "approval_subject_missing"
        decision_set = {item.decision for item in self.options}
        if external and (
            "reject" not in decision_set
            or not decision_set.intersection({"accept", "accept_with_exception"})
        ):
            return "external_approval_options_invalid"
        if (
            not 1 <= len(self.question) <= 16384
            or not 2 <= len(self.options) <= 32
            or self.projector is None
            or _ID.fullmatch(self.kind) is None
        ):
            return "approval_contract_invalid"
        labels = [item.label for item in self.options]
        decisions = [item.decision for item in self.options]
        if (
            len(labels) != len(set(labels))
            or len(decisions) != len(set(decisions))
            or any(
                not 1 <= len(item.label) <= 256
                or item.decision not in _DECISIONS
                or not 1 <= len(item.description or item.label) <= 4096
                for item in self.options
            )
        ):
            return "approval_option_invalid"
        if any(item.decision == "accept_with_exception" and not item.requires_reason
               for item in self.options):
            return "approval_reason_required"
        return None
class ReviewSpec(FrozenSpec):
    reviewer_operation: str | None = None; reviewer_input_port: str | None = None
    subject_outputs: tuple[str, ...] = ()
    accepted_verdicts: tuple[Literal["pass", "inconclusive"], ...] = ("pass",)
    approval: ApprovalContract | None = None
    max_revisions: int = 0
    progress_fingerprint: ComponentRef | None = None
    def issue(self) -> str | None:
        if self.max_revisions < 0 or self.max_revisions > 3:
            return "review_revision_limit_invalid"
        if (self.max_revisions == 0) != (self.progress_fingerprint is None):
            return "review_revision_policy_incomplete"
        return None
class InputAdmissionSpec(FrozenSpec):
    """One immutable all-or-none input group and its optional approval gate."""

    cohort_id: str
    member_ports: tuple[str, ...]
    approval_subject_ports: tuple[str, ...] = ()
    approval_kind: str | None = None
    accepted_options: tuple[str, ...] = ()
    accepted_provider_operations: tuple[str, ...] = ()
    def issue(self) -> str | None:
        members = set(self.member_ports)
        subjects = set(self.approval_subject_ports)
        if (
            _ID.fullmatch(self.cohort_id) is None
            or not members
            or len(members) != len(self.member_ports)
            or any(_ID.fullmatch(item) is None for item in members)
            or len(subjects) != len(self.approval_subject_ports)
            or not subjects <= members
        ):
            return "input_admission_invalid"
        if self.approval_kind is None:
            return (
                "input_admission_invalid"
                if subjects
                or self.accepted_options
                or self.accepted_provider_operations
                or len(members) < 2
                else None
            )
        if (
            _ID.fullmatch(self.approval_kind) is None
            or not subjects
            or not self.accepted_options
            or len(self.accepted_options) != len(set(self.accepted_options))
            or any(_ID.fullmatch(item) is None for item in self.accepted_options)
            or not self.accepted_provider_operations
            or len(self.accepted_provider_operations)
            != len(set(self.accepted_provider_operations))
            or any(
                _ID.fullmatch(item) is None
                for item in self.accepted_provider_operations
            )
        ):
            return "input_admission_approval_invalid"
        return None


class CompleteTransformFamilySpec(FrozenSpec):
    """One consumer-declared complete transform family and its exact inputs."""

    output_ports: tuple[str, ...]
    input_ports: tuple[str, ...]

    def issue(self) -> str | None:
        names = (*self.output_ports, *self.input_ports)
        if (
            not self.output_ports
            or not self.input_ports
            or len(names) != len(set(names))
            or any(_ID.fullmatch(name) is None for name in names)
        ):
            return "complete_transform_family_invalid"
        return None


class InputValidationSpec(FrozenSpec):
    """One pure checker for the exact declared input bytes, before Run creation."""

    validator: ComponentRef
    rule_id: str
    description: str

    def issue(self) -> str | None:
        if _ID.fullmatch(self.rule_id) is None or not self.description.strip():
            return "input_validation_invalid"
        return None


def input_validation_projection(spec: OperationSpec) -> dict[str, Any] | None:
    validation = spec.input_validation
    if validation is None:
        return None
    return {
        "phase": "input_admission",
        "rule_id": validation.rule_id,
        "description": validation.description,
        "required_inputs": [port.name for port in spec.inputs if port.min_items > 0 and port.agent_visible and port.derivation is None],
        "optional_inputs": [port.name for port in spec.inputs if port.min_items == 0 and port.agent_visible and port.derivation is None],
    }


class OperationSpec(FrozenSpec):
    operation_id: str
    version: str
    catalog_scope: Literal["public", "support", "internal"]
    description: OperationDescription
    executor: ExecutorRef
    inputs: tuple[InputPortSpec, ...]
    outputs: tuple[OutputPortSpec, ...]
    consequence: str
    decision_fields: tuple[str, ...] = ("summary", "conclusion", "limitations", "remaining_question", "remaining_contradiction")
    independent_review_ports: tuple[str, ...] = ()
    input_admission: InputAdmissionSpec | None = None
    input_validation: InputValidationSpec | None = None
    complete_transform_family: CompleteTransformFamilySpec | None = None
    review: ReviewSpec | None = None
    guards: tuple[ComponentRef, ...] = ()
    limits: LimitsSpec | None = None
    def bounds_issue(self) -> str | None:
        if self.limits is None:
            return "limits_invalid"
        # Core attachments stream directly to CAS under frozen runtime settings;
        # they never consume the candidate workspace's in-memory/file allowance.
        output_bytes = sum(
            p.collection.max_total_bytes if p.collection else p.max_items * p.max_item_bytes
            for p in self.outputs
            if not (self.executor.kind == "agent" and p.name == "attachments")
        )
        if any(
            p.collection and p.collection.max_total_bytes <= 0 for p in self.outputs
        ):
            return "input_or_collection_limit_invalid"
        if (output_bytes > self.limits.max_output_bytes
                or sum(p.max_items for p in self.outputs if not (self.executor.kind == "agent" and p.name == "attachments")) > self.limits.max_files):
            return "output_limits_inconsistent"
        return None
class PluginDependency(FrozenSpec):
    plugin_id: str; version: str | None = None
class PluginDefinition(FrozenSpec):
    plugin_id: str
    version: str
    protocol_version: str
    components: tuple[ComponentSpec, ...]
    operations: tuple[OperationSpec, ...]
    dependencies: tuple[PluginDependency, ...] = ()
    configuration_schema: ComponentRef | None = None
    runtime_factory: ComponentRef | None = None
class AgentLifecycleTool(FrozenSpec):
    owner_only: bool = False
    name: str
    description: str
    input_schema_sha256: str
    capability: str
class AgentLifecycleProtocol(FrozenSpec):
    version: str
    tools: tuple[AgentLifecycleTool, ...]
@dataclass(frozen=True, slots=True)
class PermissionTemplate:
    inputs: tuple[tuple[str, str, int, int], ...]
    workspace: str
    tools: tuple[str, ...]
    resources: tuple[str, ...]
    model: str | None
    native_tools: NativeToolPolicy
    limits: LimitsSpec
    lifecycle: AgentLifecycleProtocol
@dataclass(frozen=True, slots=True)
class CompiledComponent:
    plugin_id: str
    spec: ComponentSpec
    implementation: Any
    resource_digest: str | None
@dataclass(frozen=True, slots=True)
class CompiledOperation:
    plugin_id: str
    spec: OperationSpec
    component_ids: tuple[str, ...]
    implementations: Mapping[str, Any]
    component_specs: Mapping[str, ComponentSpec]
    permission_template: PermissionTemplate | None
    digest: str
    approval_identity: ApprovalProviderIdentity | None
    approval_providers: tuple[ApprovalProviderIdentity, ...]
    worker_tools: tuple[Any, ...]
    output_contracts: Mapping[str, Mapping[str, Any]]
class ApprovalProviderIdentity(FrozenSpec):
    operation_id: str
    version: str
    operation_digest: str
    approval_contract_digest: str
class SchedulerPortView(FrozenSpec):
    name: str; description: str
    schema_id: str = Field(alias="schema")
    min_items: int
    max_items: int
    required_non_null_fields: tuple[str, ...] = ()
class SchedulerInputPortView(SchedulerPortView):
    usage: str
    exposure: str
    require_current: bool
    media_types: tuple[str, ...]
    max_item_bytes: int
class SchedulerReviewEdgeView(FrozenSpec):
    policy: Literal["optional"] = "optional"
    reviewer_operation: str; reviewer_input_port: str
    subject_outputs: tuple[str, ...]; accepted_verdicts: tuple[Literal["pass", "inconclusive"], ...]
class SchedulerOperationView(FrozenSpec):
    operation_id: str
    version: str
    executor_kind: str
    catalog_scope: str
    purpose: str
    applies_when: str
    not_for: str
    inputs: tuple[SchedulerInputPortView, ...]
    outputs: tuple[SchedulerPortView, ...]
    qualification_policy: str | None = None
    input_validation: dict[str, Any] | None = None
    complete_transform_family: CompleteTransformFamilySpec | None = None
    consequence: str
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int
    max_files: int
    network_mode: str
    max_network_requests: int
    native_shell: str
    native_view_image: bool; review_edge: SchedulerReviewEdgeView | None = None
    requires_independent_review: bool
    requires_human_approval: bool
    allows_policy_authorization: bool
@dataclass(frozen=True, slots=True)
class CompiledDigestEnvelope:
    abi_version: str
    plugin_id: str
    plugin_version: str
    operation: OperationSpec
    components: tuple[tuple[str, str, ComponentSpec, str | None], ...]
    permission_template: PermissionTemplate | None
    reviewer_digest: str | None
    approval_providers: tuple[ApprovalProviderIdentity, ...] = ()
def scheduler_operation_view(spec: OperationSpec) -> SchedulerOperationView:
    if spec.limits is None:
        raise ValueError("an uncompiled operation has no limits")
    input_port = lambda item: SchedulerInputPortView(
        name=item.name, description=item.description, schema=item.schema_id,
        min_items=item.min_items, max_items=item.max_items,
        required_non_null_fields=item.required_non_null_fields,
        usage=item.usage, exposure=item.exposure, require_current=item.require_current,
        media_types=item.media_types, max_item_bytes=item.max_item_bytes,
    )
    output_port = lambda item: SchedulerPortView(
        name=item.name, description=item.description, schema=item.schema_id,
        min_items=item.min_items, max_items=item.max_items,
    )
    review = spec.review; review_edge = None
    if review is not None and review.reviewer_operation is not None:
        assert review.reviewer_input_port is not None
        review_edge = SchedulerReviewEdgeView(reviewer_operation=review.reviewer_operation, reviewer_input_port=review.reviewer_input_port, subject_outputs=review.subject_outputs, accepted_verdicts=review.accepted_verdicts)
    return SchedulerOperationView(
        operation_id=spec.operation_id, version=spec.version,
        executor_kind=spec.executor.kind,
        catalog_scope=spec.catalog_scope,
        purpose=spec.description.purpose, applies_when=spec.description.applies_when,
        not_for=spec.description.not_for,
        inputs=tuple(input_port(item) for item in spec.inputs if item.agent_visible and item.derivation is None),
        outputs=tuple(output_port(item) for item in spec.outputs if item.agent_visible),
        qualification_policy=("This action consumes an approved scientific foundation. Control checks the exact bound subject against its declared approval provider; readable historical material does not inherit qualification."
            if spec.input_admission and spec.input_admission.approval_kind else None),
        input_validation=input_validation_projection(spec),
        complete_transform_family=spec.complete_transform_family,
        consequence=spec.consequence,
        timeout_seconds=spec.limits.timeout_seconds,
        max_input_bytes=spec.limits.max_input_bytes,
        max_output_bytes=spec.limits.max_output_bytes, max_files=spec.limits.max_files,
        network_mode=spec.limits.network.mode,
        max_network_requests=spec.limits.network.max_requests,
        native_shell=spec.executor.native_tools.shell,
        native_view_image=spec.executor.native_tools.view_image,
        review_edge=review_edge,
        requires_independent_review=False,
        requires_human_approval=bool(spec.review and spec.review.approval and not spec.review.approval.allow_policy_authorization),
        allows_policy_authorization=bool(spec.review and spec.review.approval and spec.review.approval.allow_policy_authorization),
    )
def json_projection(value: Any) -> Any:
    if isinstance(value, BaseModel):
        # Preserve every pre-existing null field; omit only the new default.
        # Walk model objects so nested PluginDefinition operations retain this rule.
        return {
            (field.alias or name): json_projection(getattr(value, name))
            for name, field in type(value).model_fields.items()
            if not (isinstance(value, OperationSpec) and name == "input_validation"
                    and getattr(value, name) is None)
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_projection(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_projection(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_projection(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")
def canonical_json(value: Any) -> str:
    return json.dumps(json_projection(value), ensure_ascii=False,
                      separators=(",", ":"), sort_keys=True, allow_nan=False)
def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def freeze_json(value: Any) -> Any:
    """Freeze generated JSON recursively; per-Run projections are detached copies."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item) for item in value)
    return value
