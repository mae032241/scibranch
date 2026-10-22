"""Author-visible contracts and one submission across scientific/control ownership."""
import json

import pytest
from pydantic import ValidationError
from scidiscovery.artifact_agent.schema.scientific_foundation import EvidenceItem
from scidiscovery.operations.catalog import compile_catalog
from tests.operations.test_agent_contract_alignment import CORE_PLUGIN, GENERAL_PLUGIN, CURVE_PLUGIN
from curve_figure_evidence.plugin import PLUGIN as FIGURE_PLUGIN

from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.plugin_runtime.results import materialize_general_result
from tests.operations.test_general_transform_operations import _intake


def test_no_plan_analysis_has_no_placeholder_requirement():
    from scidiscovery.artifact_agent.schema.layered_diagnosis import LayeredDiagnosisReport
    result = LayeredDiagnosisReport.model_validate_json(canonical_json(dict(summary='Only digitized evidence was assessed.',
        overall_verdict='inconclusive', claim_allowed=False, limitations=['No experiment plan.'])))
    assert result.plan_key is result.experiment_key is None


def test_large_intake_summary_and_gaps_are_projected_without_truncation():
    from scidiscovery.artifact_agent.schema.role_result import parse_role_result
    draft = dict(payload=_intake().model_dump(mode='json'), handoff={'verdict': 'inconclusive'})
    foundation = draft['payload']['scientific_foundation']
    foundation['summary'] = 'S' * 8192
    foundation['missing_inputs'] = [f'Gap {index}' for index in range(128)]
    materialize_general_result(draft, 'scidiscovery.scientific-intake.v1')
    result = parse_role_result(draft)
    assert result.handoff.summary == foundation['summary']
    assert list(result.handoff.missing_inputs) == foundation['missing_inputs']


def test_missing_authored_summary_diagnostic_points_to_science_not_generated_handoff():
    from scidiscovery.operations.workspace import WorkspaceProtocolError
    draft = dict(payload=_intake().model_dump(mode='json'), handoff={'verdict': 'inconclusive'})
    draft['payload']['scientific_foundation'].pop('summary')
    with pytest.raises(WorkspaceProtocolError) as caught:
        materialize_general_result(draft, 'scidiscovery.scientific-intake.v1')
    assert caught.value.details[0]['path'] == '$.payload.scientific_foundation.summary'


def test_public_diagnostic_sanitizer_discloses_omissions_without_input_echo():
    from scidiscovery.operation_contract import sanitize_diagnostic_details
    raw = [dict(path='$.summary', message='PRIVATE_VALUE', type='missing') for _ in range(40)]
    visible = sanitize_diagnostic_details(raw, schema={'properties': {'summary': {}}})
    assert len(visible) == 16
    assert visible[-1]['type'] == 'diagnostics_omitted'
    assert '15 of 40' in visible[-1]['message'] and 'omitted 25' in visible[-1]['message']
    assert 'PRIVATE_VALUE' not in json.dumps(visible)


def _compiled(name='science.evidence.extract.figure.v3'):
    return compile_catalog((CORE_PLUGIN, GENERAL_PLUGIN, CURVE_PLUGIN, FIGURE_PLUGIN)).operation(name)


def test_figure_contract_and_validator_report_independent_scientific_defects():
    from scidiscovery.operation_contract import validation_diagnostics
    item = dict(item_key='p', item_type='parameter', epistemic_status='inference',
        statement='The inferred parameter is 3.', value=3, scope='Only this panel.')
    with pytest.raises(ValidationError) as caught:
        EvidenceItem.model_validate_json(canonical_json(item))
    details = validation_diagnostics(caught.value, schema=EvidenceItem.model_json_schema())
    assert {d['path'] for d in details} == {'$.unit', '$.rationale'}
    # Unknown parameters remain parameters, without invented values or units.
    unknown = dict(item, statement='The source does not report this parameter.',
        value=None, rationale='No estimate can be justified from this image.')
    assert EvidenceItem.model_validate_json(canonical_json(unknown)).value is None


def test_hypothesis_identity_is_generated_from_exact_frozen_foundation(tmp_path):
    from types import SimpleNamespace
    from scidiscovery.plugin_runtime.results import finalize_general_result
    from scidiscovery.operations.workspace import WorkspaceFinalizationRequest
    from tests.operations.test_hypothesis_objective_boundary import _foundation, _proposal
    payload = _proposal()
    payload.pop('research_objective_key')
    payload.pop('schema_version')
    draft = dict(payload=payload, handoff={'verdict': 'inconclusive', 'summary': 'No supported mechanism yet.'})
    (tmp_path / 'output').mkdir()
    (tmp_path / 'output/result.json').write_bytes(canonical_json(draft))
    raw = _foundation('exact_objective')
    request = WorkspaceFinalizationRequest('fixture', tmp_path, {}, 65536,
        output_schema_id='scidiscovery.hypothesis-proposal.v2', input_contents={'original': raw},
        binding_descriptors={'original': SimpleNamespace(port_name='scientific_foundation')})
    result = json.loads(finalize_general_result(request))
    assert result['payload']['research_objective_key'] == 'exact_objective'
    assert result['payload']['stage_objective'] == payload['stage_objective']
    assert request.input_contents['original'] == raw


def test_parameter_doi_and_objective_draft_copies_finalize_and_check(tmp_path):
    from types import SimpleNamespace
    from scidiscovery.operations.workspace import WorkspaceFinalizationRequest
    from tcad_artifact.parameter_operations import _finalize_parameter_result, check_parameters, ParameterCheckInput
    from tests.operations.test_m2_parameter_package import _package
    payload = _package().model_dump(mode='json')
    payload['scientific_intake']['problem_frame'].pop('objective')
    payload['source_catalog']['sources'][0]['doi'] = '10.1234/EXACT'
    payload['source_catalog']['sources'][0].pop('work_key')
    payload.pop('coverage', None)
    draft = dict(payload=payload, handoff={'verdict': 'inconclusive'})
    def versions(value):
        if isinstance(value, dict):
            value.pop('schema_version', None)
            for child in value.values(): versions(child)
        elif isinstance(value, list):
            for child in value: versions(child)
    versions(draft)
    (tmp_path / 'output').mkdir()
    (tmp_path / 'output/result.json').write_bytes(canonical_json(draft))
    checked = check_parameters(ParameterCheckInput(package_path='output/result.json'), SimpleNamespace(workspace=tmp_path))
    result = json.loads(_finalize_parameter_result(WorkspaceFinalizationRequest('fixture', tmp_path, {}, 1024*1024,
        output_schema_id='scidiscovery.parameter-evidence-package.v1')))
    assert result['payload']['source_catalog']['sources'][0]['work_key'] == 'doi:10.1234/exact'
    assert result['payload']['coverage'] == checked['coverage']
    assert result['handoff']['summary'] == payload['scientific_intake']['scientific_foundation']['summary']


def test_parameter_plugin_owns_doi_and_coverage_authoring_contract():
    from scidiscovery.artifact_agent.schema.role_result import RoleHandoff
    from scidiscovery.plugin_runtime.results import result_draft_schema
    from tcad_artifact.parameter_operations import ParameterEvidencePackage, parameter_draft_schema
    schema_id = 'scidiscovery.parameter-evidence-package.v1'
    resource = ParameterEvidencePackage.model_json_schema()
    resource['$id'] = 'urn:test:parameter-package'
    envelope = {'$defs': {'RoleHandoff': RoleHandoff.model_json_schema()},
        'properties': {'payload': resource, 'handoff': {'$ref': '#/$defs/RoleHandoff'}},
        'required': ['payload', 'handoff']}
    assert result_draft_schema(envelope, schema_id) == envelope
    draft = parameter_draft_schema(envelope, schema_id)
    payload = draft['properties']['payload']
    assert 'coverage' not in payload['properties']
    with_doi, without_doi = payload['$defs']['EvidenceSourceCatalogEntry']['oneOf']
    assert 'work_key' not in with_doi['properties']
    assert 'doi' in with_doi['required']
    assert 'work_key' in without_doi['required']
    assert without_doi['properties']['doi'] == {'type': 'null'}
    assert 'coverage' in envelope['properties']['payload']['properties']


@pytest.mark.parametrize('schema_id,operation,payload', [
    ('scidiscovery.experiment-report.v1', 'science.experiment.v1', dict(summary='Unavailable observation.', outcome='inconclusive', remaining_question='Which source is needed?')),
    ('scidiscovery.experiment-review.v1', 'science.object.review.v1', dict(summary='Missing support.', verdict='inconclusive')),
])
def test_complete_task_draft_finalizer_and_scientific_repair_location(tmp_path, schema_id, operation, payload):
    from scidiscovery.general_science_experiment_task import finalize
    from scidiscovery.operations.workspace import WorkspaceFinalizationRequest, WorkspaceProtocolError
    draft = {'payload': payload}
    (tmp_path / 'output').mkdir()
    path = tmp_path / 'output/result.json'
    path.write_bytes(canonical_json(draft))
    request = WorkspaceFinalizationRequest('fixture', tmp_path, {}, 65536, output_schema_id=schema_id)
    result = json.loads(finalize(request))
    assert result['handoff']['summary'] == payload['summary']
    assert result['handoff']['verdict'] == 'inconclusive'
    payload.pop('summary')
    path.write_bytes(canonical_json(draft))
    with pytest.raises(WorkspaceProtocolError) as error:
        finalize(request)
    assert error.value.details[0]['path'] == '$.payload.summary'


def test_finalizer_and_output_validation_report_real_bounded_diagnostic_counts(tmp_path):
    from scidiscovery.artifact_agent.schema.research_cycle import ScientificIntake
    from scidiscovery.operations.workspace import WorkspaceProtocolError
    from scidiscovery.plugin_runtime.results import validate_finalizer_payload
    from scidiscovery.operation_contract import sanitize_diagnostic_details
    payload = _intake().model_dump(mode='json')
    payload['scientific_foundation']['items'] = [dict(item_key=f'p{i}', item_type='parameter',
        epistemic_status='inference', statement='PRIVATE_SCIENTIFIC_TEXT', value=i,
        scope='This panel.') for i in range(20)]
    with pytest.raises(WorkspaceProtocolError) as finalizer:
        validate_finalizer_payload(ScientificIntake, payload)
    for diagnostics in (finalizer.value.details,):
        visible = sanitize_diagnostic_details(diagnostics, schema=ScientificIntake.model_json_schema(),
            rules=tuple(d["rule_id"] for d in diagnostics if "rule_id" in d))
        assert len(visible) == 16
        summary = visible[-1]
        assert summary['type'] == 'dependent_checks_deferred'
        assert '15 of 40' in summary['message'] and 'omitted 25' in summary['message']
        assert 'not evaluated' in summary['message']
        assert 'PRIVATE_SCIENTIFIC_TEXT' not in json.dumps(visible)
        assert all(d['path'].startswith('$.payload') for d in visible[:-1])


def test_compiled_digest_tracks_actual_finalizer_projection(monkeypatch):
    from dataclasses import replace
    import scidiscovery.general_science_components as components
    original = components.RESULT_FINALIZER
    baseline = _compiled('science.evidence.extract.v1')
    hook = original.implementation
    monkeypatch.setattr(components, 'RESULT_FINALIZER', replace(original,
        implementation=replace(hook, projection_version=hook.projection_version + '.changed')))
    changed = _compiled('science.evidence.extract.v1')
    assert changed.digest != baseline.digest
    monkeypatch.setattr(components, 'RESULT_FINALIZER', replace(original, implementation=hook.implementation))
    plain = _compiled('science.evidence.extract.v1')
    assert plain.digest not in {baseline.digest, changed.digest}
    monkeypatch.setattr(components, 'RESULT_FINALIZER', original)
    assert _compiled('science.evidence.extract.v1').digest == baseline.digest


def test_intake_author_writes_summary_and_objective_once():
    draft = dict(payload=_intake().model_dump(mode='json'), handoff={'verdict': 'inconclusive'})
    draft['payload']['problem_frame'].pop('objective')
    def versions(value):
        if isinstance(value, dict):
            value.pop('schema_version', None)
            for child in value.values():
                versions(child)
        elif isinstance(value, list):
            for child in value:
                versions(child)
    versions(draft)
    materialize_general_result(draft, 'scidiscovery.scientific-intake.v1')
    assert draft['handoff']['summary'] == draft['payload']['scientific_foundation']['summary']
    assert draft['handoff']['verdict'] == 'inconclusive'
    from scidiscovery.artifact_agent.schema.role_result import parse_role_result
    from scidiscovery.artifact_agent.schema.research_cycle import ScientificIntake
    parse_role_result(draft)
    ScientificIntake.model_validate_json(canonical_json(draft['payload']))


def test_actual_figure_assignment_tool_contract_excludes_source_control_fields():
    from scidiscovery.operations.tooling import operation_role_instructions, operation_worker_tools
    compiled = _compiled()
    tools = {tool.name: tool for tool in operation_worker_tools(compiled)}
    for tool_name in ('worker_curve_figure_preview', 'worker_curve_figure_save'):
        source = tools[tool_name].schema()['inputSchema']['$defs']['ScientificFigureSource']
        assert set(source['properties']) == {'page', 'document_image_index'}
    assert 'mechanical metadata fields may be omitted' not in operation_role_instructions(compiled)
