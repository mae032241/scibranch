"""One complete parameter evidence task with optional deterministic checks."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from scidiscovery.artifact_agent.schema.common import SchemaModel, canonical_json
from scidiscovery.artifact_agent.schema.research_cycle import ScientificIntake
from scidiscovery.operations.input_validation import ValidationSources, parse_bound_json
from scidiscovery.operation_contract import SemanticRuleViolation, declared_violation, validate_evidence_source_aliases
from scidiscovery.operation_declaration import OPERATION_AGENT_PREAMBLE, scientific_agent_operation, semantic_contract
from scidiscovery.operations.spec import CallableComponent, ComponentRef, ComponentSpec, InputPortSpec, OutputPortSpec, SemanticRuleSpec
from scidiscovery.operations.tooling import WorkerToolDefinition
from .device_parameters import (DeviceParameterCoverageReport, DeviceParameterRequirementSet, DeviceParameterSet,
    EvidenceSourceCatalog, evaluate_device_parameter_coverage, project_parameter_uncertainty)

EXTRACT_OPERATION = "tcad.parameter.evidence.extract.v1"
PARAMETER_RESULT_COPIES = {
    '/payload/scientific_intake/problem_frame/objective': '/payload/scientific_intake/scientific_foundation/objective',
    '/payload/scientific_intake/scientific_foundation/objective_contract/statement': '/payload/scientific_intake/scientific_foundation/objective',
    '/handoff/summary': '/payload/scientific_intake/scientific_foundation/summary',
    '/handoff/missing_inputs': '/payload/scientific_intake/scientific_foundation/missing_inputs',
    '/handoff/assumptions': '/payload/scientific_intake/problem_frame/assumptions',
}


def parameter_draft_schema(envelope, schema_id, ports=None):
    from scidiscovery.plugin_runtime.results import result_draft_schema
    if schema_id != "scidiscovery.parameter-evidence-package.v1":
        return result_draft_schema(envelope, schema_id, ports)
    result = result_draft_schema(envelope, schema_id, ports, copies=PARAMETER_RESULT_COPIES)
    payload = result['properties']['payload']
    payload['properties'].pop('coverage', None)
    payload['required'] = [name for name in payload.get('required', ()) if name != 'coverage']
    definitions = payload['$defs']
    entry = definitions['EvidenceSourceCatalogEntry']
    with_doi, without_doi = deepcopy(entry), deepcopy(entry)
    with_doi['properties'].pop('work_key')
    with_doi['required'] = [name for name in with_doi['required'] if name != 'work_key'] + ['doi']
    with_doi['properties']['doi'] = {'type': 'string', 'minLength': 3, 'maxLength': 512}
    without_doi['properties']['doi'] = {'type': 'null'}
    without_doi['properties']['work_key']['description'] = 'Scientific identity of one underlying work without a DOI; group mirrors and pages from that same work.'
    definitions['EvidenceSourceCatalogEntry'] = {'oneOf': [with_doi, without_doi]}
    return result

def _schema(model: type[BaseModel], schema_id: str) -> str:
    value = model.model_json_schema(mode="validation")
    value["$id"] = schema_id
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

class ParameterEvidencePackage(SchemaModel):
    """One Agent-owned scientific judgment package before mechanical expansion."""

    scientific_intake: ScientificIntake
    parameter_requirements: DeviceParameterRequirementSet
    device_parameters: DeviceParameterSet
    source_catalog: EvidenceSourceCatalog
    coverage: DeviceParameterCoverageReport | None = None

    @model_validator(mode="after")
    def _family_is_closed(self) -> ParameterEvidencePackage:
        _validate_parameter_family(
            self.scientific_intake,
            self.parameter_requirements,
            self.device_parameters,
            self.source_catalog,
        )
        if self.coverage is not None and self.coverage != evaluate_device_parameter_coverage(
                self.parameter_requirements, self.device_parameters, self.source_catalog):
            raise ValueError("parameter coverage differs from the declared values and sources")
        return self

def _materialize_parameter_draft(payload):
    from scidiscovery.plugin_runtime.results import materialize_intake
    if isinstance(payload.get("scientific_intake"), dict):
        materialize_intake(payload["scientific_intake"])
    catalog = payload.get("source_catalog")
    if isinstance(catalog, dict) and isinstance(catalog.get("sources"), list):
        for source in catalog["sources"]:
            if isinstance(source, dict) and isinstance(source.get("doi"), str):
                source["work_key"] = "doi:" + source["doi"].lower().removeprefix("https://doi.org/")
    payload.pop("coverage", None)


def _finalize_parameter_result(request):
    from scidiscovery.plugin_runtime.results import (
        finalize_result, materialize_general_result, project_result_copies, validate_finalizer_payload,
    )
    def project(value):
        materialize_general_result(value, request.output_schema_id)
        if request.output_schema_id != "scidiscovery.parameter-evidence-package.v1":
            return
        payload = value["payload"]
        _materialize_parameter_draft(payload)
        package = validate_finalizer_payload(ParameterEvidencePackage, payload)
        payload["coverage"] = evaluate_device_parameter_coverage(package.parameter_requirements,
            package.device_parameters, package.source_catalog).model_dump(mode="json")
        project_result_copies(value, request.output_schema_id, copies=PARAMETER_RESULT_COPIES)
    return finalize_result(request, project)

def _strict(model: type[BaseModel]):
    def validate(raw: bytes) -> None:
        try:
            model.model_validate_json(raw, strict=True)
        except ValidationError as error:
            raise SemanticRuleViolation(str(error)) from error

    return validate

def _scientific_requirement_fields(
    value: DeviceParameterRequirementSet,
) -> dict[str, Any]:
    result = value.model_dump(mode="json")
    for item in result["parameters"]:
        item.pop("display_name", None)
    return result

def _validate_parameter_family(
    intake: ScientificIntake,
    requirements: DeviceParameterRequirementSet,
    selected: DeviceParameterSet,
    catalog: EvidenceSourceCatalog,
) -> None:
    """Validate scientific closure without storage or control-plane knowledge."""

    evaluate_device_parameter_coverage(requirements, selected, catalog)
    if not (
        intake.scientific_foundation.objective
        == requirements.objective
        == selected.objective
    ):
        raise ValueError("parameter family does not share one exact objective")
    catalog_by_key = {item.source_key: item for item in catalog.sources}
    observed_keys = {
        observation.source_key
        for claim in selected.claims
        for observation in claim.observations
    }
    if not observed_keys.issubset(catalog_by_key):
        raise ValueError("a parameter observation references an undeclared source")
    foundation_evidence = intake.scientific_foundation.evidence
    if any(
        item.source_type != catalog_by_key[item.source_key].source_type
        for item in foundation_evidence if item.source_key in catalog_by_key
    ):
        raise ValueError("parameter source type differs from the intake foundation")

def validate_extract_context(
    payload: dict[str, Any], sources: ValidationSources, handoff: dict[str, Any]
) -> None:
    del handoff
    package = ParameterEvidencePackage.model_validate_json(
        canonical_json(payload), strict=True
    )
    source_aliases = {name for name, descriptor in sources.binding_descriptors.items()
                      if descriptor.port_name == "source_material"}
    for index, source in enumerate(package.source_catalog.sources):
        if source.source_key not in source_aliases:
            raise declared_violation("Parameter source must name a bound source input.",
                path=f"$.source_catalog.sources[{index}].source_key")
    validate_evidence_source_aliases(payload, sources)
    raw = sources.get("required_parameter_checklist")
    if raw is None:
        return
    required = parse_bound_json(DeviceParameterRequirementSet, raw)
    if _scientific_requirement_fields(package.parameter_requirements) != (
        _scientific_requirement_fields(required)
    ):
        raise SemanticRuleViolation(
            "parameter package requirements differ from the supplied checklist"
        )

class ParameterCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_path: str = Field(description="Workspace-relative JSON package or result-envelope draft below scratch/ or output/.")

def check_parameters(request, context):
    from scidiscovery.plugin_runtime.workspace import read_control_workspace_file
    path = Path(request.package_path)
    if path.is_absolute() or not path.parts or path.parts[0] not in {"scratch", "output"}:
        raise ValueError("parameter package must be a task-local scratch/ or output/ draft")
    document = json.loads(read_control_workspace_file(context.workspace, path, max_bytes=7*1024*1024))
    payload = document.get("payload", document)
    _materialize_parameter_draft(payload)
    package = ParameterEvidencePackage.model_validate_json(canonical_json(payload))
    coverage = evaluate_device_parameter_coverage(package.parameter_requirements, package.device_parameters, package.source_catalog)
    uncertainty = project_parameter_uncertainty(package.parameter_requirements, package.device_parameters, coverage)
    return {"coverage": coverage.model_dump(mode="json"), "uncertainty": uncertainty.model_dump(mode="json"),
        "interpretation": "Deterministic parameter checks; no qualification or scientific approval."}

from scidiscovery.operations.workspace import WorkspaceFinalizer
from scidiscovery.plugin_runtime.results import RESULT_PROJECTION_VERSION
PARAMETER_RESULT_FINALIZER = CallableComponent("workspace_finalizer", WorkspaceFinalizer(
    _finalize_parameter_result, parameter_draft_schema, RESULT_PROJECTION_VERSION + ".tcad-parameters-1",
    predecessor=("tcad_artifact.parameter_operations:PARAMETER_RESULT_FINALIZER", "4")))
PARAMETER_PACKAGE_SCHEMA = _schema(ParameterEvidencePackage, "scidiscovery.parameter-evidence-package.v1")
PACKAGE_VALIDATOR = CallableComponent("validator", _strict(ParameterEvidencePackage))
EXTRACT_CONTEXT_VALIDATOR = CallableComponent("validator", validate_extract_context)
EXTRACT_AGENT = CallableComponent("agent", lambda: None)
EXTRACT_PROMPT = OPERATION_AGENT_PREAMBLE + (Path(__file__).with_name("roles") / "parameter_evidence_extractor.md").read_text()
from scidiscovery.general_science_resources import INTAKE_RULES

PARAMETER_SEMANTIC_CONTRACT = semantic_contract(
    *(SemanticRuleSpec(f"parameter.intake_{index}", rule) for index, rule in enumerate(INTAKE_RULES)),
    SemanticRuleSpec("parameter.reference_and_unit_closure", "Preserve consistent objectives, parameter keys, units, conditions and exact scientific source references. Missing coverage is a scientific limitation, not a task-completion gate."),
    SemanticRuleSpec("parameter.source_binding", "Parameter observations cite the exact supplied sources. Preserve any supplied scientific checklist."))
PARAMETER_CHECK_TOOL = WorkerToolDefinition(name="worker_parameter_check", input_model=ParameterCheckInput,
    description="Check a complete local parameter draft and return deterministic coverage and use limitations in this task. Does not select or average values, grant qualification, or require another Run.",
    capability="evidence.parameter_check", contextual_handler=check_parameters)


def _ref(name, plugin=None):
    return ComponentRef(name, plugin_id=plugin)


COMPONENT_SPECS = (
    ComponentSpec("parameter_result_finalizer", "workspace_finalizer", __name__+":PARAMETER_RESULT_FINALIZER"),
    ComponentSpec("parameter_workspace", "workspace", "scidiscovery.general_science_components:WORKSPACE", resources=(_ref("parameter_result_finalizer"),)),
    ComponentSpec("parameter_extract_agent", "agent", __name__+":EXTRACT_AGENT"),
    ComponentSpec("parameter_extract_prompt", "resource", __name__+":EXTRACT_PROMPT"),
    ComponentSpec("parameter_semantic_contract", "resource", __name__+":PARAMETER_SEMANTIC_CONTRACT"),
    ComponentSpec("parameter_package_schema", "resource", __name__+":PARAMETER_PACKAGE_SCHEMA"),
    ComponentSpec("parameter_package_validator", "validator", __name__+":PACKAGE_VALIDATOR", resources=(_ref("parameter_semantic_contract"),)),
    ComponentSpec("parameter_extract_context", "validator", __name__+":EXTRACT_CONTEXT_VALIDATOR", resources=(_ref("parameter_semantic_contract"),)),
    ComponentSpec("parameter_check_tool", "worker_tool", __name__+":PARAMETER_CHECK_TOOL"),
)


def _input(name, description, schema, resource, **kwargs):
    return InputPortSpec(name=name, description=description, schema=schema,
        codec=_ref("opaque_codec" if schema in {"opaque", "*"} else "json_codec", "general_science"),
        schema_resource=resource, exposure="on_demand", usage="evidence_inventory", **kwargs)


INPUTS = (
    _input("required_parameter_checklist", "Optional scientific parameter requirements.", "scidiscovery.device-parameter-requirements.v1", _ref("parameter_requirements_schema"), media_types=("application/json",), min_items=0),
    _input("source_material", "Exact original device descriptions, papers, tables or user evidence.", "opaque", _ref("opaque_schema", "general_science"),
        media_types=("application/json","application/pdf","image/png","image/jpeg","image/webp","text/plain","text/plain; charset=utf-8","text/csv"),
        min_items=1,max_items=32,max_item_bytes=32*1024*1024),
    _input("previous_evidence", "Optional complete prior evidence or scientific feedback to reconsider.", "*", _ref("wildcard_schema", "general_science"), media_types=("*/*",),min_items=0,max_items=4,max_item_bytes=8*1024*1024),
)
OPERATIONS = (scientific_agent_operation(EXTRACT_OPERATION,
    "Extract or revise complete source-bound device-parameter evidence; inspect coverage and uncertainty inside this task.",
    "A scientific objective and original parameter sources need a bounded evidence judgment.",
    "Executing experiments or granting qualification.",
    agent=_ref("parameter_extract_agent"),workspace=_ref("parameter_workspace"),prompt=_ref("parameter_extract_prompt"),
    tools=(_ref("pdf_extract_tool","general_science"),_ref("parameter_check_tool")),
    inputs=INPUTS, outputs=(OutputPortSpec(name="parameter_evidence_package", description="Complete parameter evidence and deterministic coverage, including unresolved limitations.",
        kind="parameter_evidence_package",schema="scidiscovery.parameter-evidence-package.v1",media_types=("application/json",),
        codec=_ref("json_codec","general_science"),schema_resource=_ref("parameter_package_schema"),max_item_bytes=7*1024*1024,
        validator=_ref("parameter_package_validator"),validator_rule_id="parameter.reference_and_unit_closure",semantic_contract=_ref("parameter_semantic_contract"),
        context_validator=_ref("parameter_extract_context"),context_rule_id="parameter.source_binding",context_sources=tuple(port.name for port in INPUTS),
        evidence_paths=("/scientific_intake/scientific_foundation/evidence",)),),
    decision_fields=("/scientific_intake/scientific_foundation/objective", "/scientific_intake/scientific_foundation/objective_contract/intent", "/scientific_intake/scientific_foundation/objective_contract/mandatory_targets", "/scientific_intake/scientific_foundation/objective_contract/closure_requirements", "/scientific_intake/scientific_foundation/summary", "/scientific_intake/scientific_foundation/missing_inputs", "/coverage/status"),
    timeout=1200,max_input_bytes=1058*1024*1024,max_output_bytes=7*1024*1024,max_files=1,native_view_image=True),)
