"""Pure input admission on bounded, explicitly bound bytes; never a submit hook."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from scidiscovery.artifact_agent.schema.refs import ArtifactRef
from .spec import CompiledOperation
from ..operation_contract import DiagnosticError, contract_diagnostic


class OperationEngineeringError(DiagnosticError):
    """The control component failed; changing scientific inputs is not a repair."""
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code, details=(contract_diagnostic(reason_code,
            phase="tool_execution", affected_action="invoke",
            message="An Operation control component failed; input admission could not be completed."),))


class OperationInvocationError(ValueError):
    def __init__(self, reason_code: str, *, port: str | None = None, field: str | None = None, message: str = "The declared requirement was not satisfied.") -> None:
        self.reason_code = reason_code
        self.port = port
        self.field = field
        from ..operation_contract import contract_diagnostic, diagnostic_path
        path = "$.inputs" + (f".{port}" if port else "")
        if field:
            parts = field.strip("/").replace("/", ".").split(".")
            path = diagnostic_path((int(p) if p.isdigit() else p for p in parts), parts, root=path)
        self.details = (contract_diagnostic(reason_code, phase="input_admission",
            affected_action="invoke", path=path,
            repairable=True, message=message),)
        super().__init__(f"{reason_code}: {port or 'operation'}{(' ' + field) if field else ''}")

@dataclass(frozen=True, slots=True)
class InputBindingDescriptor:
    source_name: str
    port_name: str
    artifact_ref: ArtifactRef
    media_type: str
    size_bytes: int
    sha256: str
    output_name: str | None = None
    parent_refs: tuple[ArtifactRef, ...] = ()
    labels: tuple[tuple[str, str], ...] = ()
    producer_run_id: str | None = None


class ValidationSources(dict[str, bytes]):
    """Legacy byte mapping with an immutable projection of exact Run bindings."""

    __slots__ = ("__binding_descriptors", "__validation_deadline", "__tool_snapshot", "__reference_calculation_resolver", "__sealed_material_resolver")

    def __init__(
        self,
        sources: Mapping[str, bytes],
        binding_descriptors: Mapping[str, InputBindingDescriptor],
        validation_deadline: float | None = None,
        tool_snapshot: bytes | None = None,
        reference_calculation_resolver: Callable | None = None,
        sealed_material_resolver: Callable | None = None,
    ) -> None:
        super().__init__(sources)
        self.__binding_descriptors = MappingProxyType(dict(binding_descriptors))
        self.__validation_deadline = validation_deadline
        self.__tool_snapshot = tool_snapshot
        self.__reference_calculation_resolver = reference_calculation_resolver
        self.__sealed_material_resolver = sealed_material_resolver

    def sealed_material_record(self, alias, *, read_content=False):
        if self.__sealed_material_resolver is None:
            return None
        return (self.__sealed_material_resolver(alias, read_content=True) if read_content
                else self.__sealed_material_resolver(alias))

    def verified_calculations(self, report):
        """Read-only calculation view; core retains receipt and origin verification."""
        from ..artifact_agent.service.analysis_artifacts import verified_calculations
        return verified_calculations(report, self)

    def reference_calculation_sources(self, alias):
        return self.__reference_calculation_resolver(alias) if self.__reference_calculation_resolver else None

    @property
    def tool_snapshot(self) -> bytes | None:
        return self.__tool_snapshot

    @property
    def binding_descriptors(self) -> Mapping[str, InputBindingDescriptor]:
        return self.__binding_descriptors


    @property
    def validation_deadline(self) -> float | None:
        """Monotonic deadline for optional bounded domain replay; never persisted."""
        return self.__validation_deadline

    @property
    def prior_source_bindings(self) -> Mapping[str, str]:
        projection = prior_analysis_sources(self)
        return projection["source_bindings"] if projection else MappingProxyType({})


def read_validation_sources(
    compiled: CompiledOperation,
    inputs: tuple[Any, ...],
    read_artifact: Callable[[ArtifactRef], bytes] | None,
) -> ValidationSources:
    """Budget metadata before any read, then verify exact byte identity."""
    if sum(item.artifact.size_bytes for item in inputs if item.exposure != "file_reference") > compiled.spec.limits.max_input_bytes:
        raise OperationInvocationError("input_total_too_large")
    if read_artifact is None:
        raise OperationInvocationError("input_content_reader_missing")
    contents = {}
    descriptors = {}
    for item in inputs:
        artifact = item.artifact
        try:
            raw = b"" if item.exposure == "file_reference" else read_artifact(artifact.ref)
        except Exception as error:
            raise OperationInvocationError("input_content_unavailable", port=item.port_name) from error
        if item.exposure != "file_reference" and (not isinstance(raw, bytes) or len(raw) != artifact.size_bytes
                or hashlib.sha256(raw).hexdigest() != artifact.ref.sha256):
            raise OperationInvocationError("input_content_integrity_failure", port=item.port_name)
        contents[item.source_name] = raw
        descriptors[item.source_name] = InputBindingDescriptor(
            source_name=item.source_name, port_name=item.port_name,
            artifact_ref=artifact.ref, media_type=artifact.media_type,
            size_bytes=artifact.size_bytes, sha256=artifact.ref.sha256,
            output_name=dict(artifact.labels).get("logical_name"),
            parent_refs=artifact.parent_refs, labels=artifact.labels,
            producer_run_id=artifact.producer_run_id,
        )
    return ValidationSources(contents, descriptors)


def validate_operation_inputs(compiled: CompiledOperation, sources: ValidationSources) -> None:
    """Execute only the declared input checker; no output or storage authority."""
    validation = compiled.spec.input_validation
    if validation is None:
        return
    reference = validation.validator
    key = f"{reference.plugin_id or compiled.plugin_id}:{reference.component_id}"
    try:
        result = compiled.implementations[key](sources)
        if result is not None:
            raise OperationEngineeringError("input_checker_result_invalid")
    except (OperationInvocationError, OperationEngineeringError):
        raise
    except Exception as error:
        raise OperationEngineeringError("input_checker_failed") from error


class BoundSourceError(ValueError):
    """Parsing an already accepted frozen source exposed an admission defect."""


def prior_analysis_sources(sources: Mapping[str, bytes]) -> Mapping[str, Any] | None:
    """Resolve one explicit prior proof; never put historical aliases in inputs.

    Both admission and output consumers use these immutable identity facts.
    Eligibility/current-head decisions remain exclusively with admission.
    """
    descriptors = getattr(sources, "binding_descriptors", {})
    def port(name):
        return next((d for d in descriptors.values() if d.port_name == name), None)
    prior, explicit = port("prior_analysis"), port("prior_analysis_manifest")
    if prior is None:
        if explicit is not None:
            raise OperationInvocationError("prior_analysis_missing", port="prior_analysis",
                message="Bind the prior analysis primary together with its recovery manifest.")
        return None
    proof = explicit or port("recovery_manifest")
    if proof is None:
        raise OperationInvocationError("prior_manifest_missing", port="prior_analysis_manifest",
            message="Bind the prior analysis primary's direct recovery manifest.")
    pair_failures = []
    if proof.artifact_ref not in prior.parent_refs:
        pair_failures.append("direct_parent")
    if not prior.producer_run_id:
        pair_failures.append("prior_producer")
    if proof.producer_run_id != prior.producer_run_id:
        pair_failures.append("same_producer")
    if dict(proof.labels).get("operation_output_port") != "recovery_manifest_output":
        pair_failures.append("recovery_output_port")
    if pair_failures:
        raise OperationInvocationError("prior_manifest_pair_mismatch", port=proof.port_name,
            message=("Bind the direct recovery manifest produced with the prior analysis primary; "
                     "mismatched facts: " + ", ".join(pair_failures) + "."))
    from ..artifact_agent.schema.tool_evidence import ToolEvidenceManifest
    try:
        manifest = ToolEvidenceManifest.model_validate_json(sources[proof.source_name])
    except (ValueError, KeyError) as error:
        raise OperationInvocationError("prior_manifest_invalid", port=proof.port_name) from error
    identities = {old: binding.artifact_ref for old, binding in manifest.bindings.items()}
    if "bindings" not in manifest.model_fields_set:
        # Legacy manifests prove only recovered files, never unrecorded inputs.
        for record in manifest.records:
            old = record.get("alias")
            if not isinstance(old, str) or not old:
                continue
            try:
                identity = ArtifactRef.model_validate(record.get("artifact_ref"))
            except ValueError:
                continue
            if old in identities and identities[old] != identity:
                raise OperationInvocationError("prior_manifest_binding_mismatch", port=proof.port_name,
                    message="The recovery manifest maps one source alias to conflicting exact artifacts.")
            identities[old] = identity
    mapped = {}
    for old, identity in identities.items():
        if identity not in proof.parent_refs:
            raise OperationInvocationError("prior_manifest_binding_mismatch", port=proof.port_name,
                message="The recovery manifest names an exact source that is not its direct parent.")
        matches = [d.source_name for d in descriptors.values() if d.artifact_ref == identity]
        if len(matches) > 1:
            raise OperationInvocationError("input_artifact_duplicate")
        if matches:
            mapped[old] = matches[0]
    return MappingProxyType({"analysis_alias":prior.source_name,
        "manifest_alias":proof.source_name, "source_bindings":MappingProxyType(mapped),
        "source_ports":MappingProxyType({old: binding.port_name for old, binding in manifest.bindings.items()})})


def parse_bound_json(
    model: Any, raw: bytes, *, strict: bool = True, admission_port: str | None = None
) -> Any:
    from pydantic import ValidationError
    try:
        return model.model_validate_json(raw, strict=strict)
    except (ValidationError, UnicodeError) as error:
        if admission_port is None:
            raise BoundSourceError("bound_source_schema_invalid") from error
        failure = OperationInvocationError("input_content_incompatible", port=admission_port, field="/")
        if isinstance(error, ValidationError):
            from ..operation_contract import diagnostic_path, validation_diagnostics
            root = diagnostic_path((admission_port,), (admission_port,), root="$.inputs")
            details = validation_diagnostics(error, schema=model.model_json_schema(mode="validation"),
                                             phase="input_admission", action="invoke")
            failure.details = tuple(contract_diagnostic("input_content_incompatible",
                phase="input_admission", affected_action="invoke", repairable=True,
                path=(root + detail["path"].removeprefix("$"))[:512],
                message=detail["message"], error_type=detail.get("type")) for detail in details)
        raise failure from error
