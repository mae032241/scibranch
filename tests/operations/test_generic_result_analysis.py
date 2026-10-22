"""Mechanical scientific source identities in the generic analysis contract."""
import json
from types import SimpleNamespace

import pytest

from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.general_science_result_analysis import OPERATION, validate_context
from scidiscovery.operation_contract import SemanticRuleViolation
from scidiscovery.operations.input_validation import ValidationSources
from scidiscovery.operations.workspace import WorkspaceFinalizationRequest, WorkspaceProtocolError
from scidiscovery.plugin_runtime.results import finalize_general_result, result_draft_schema
from curve_score.analysis_workspace import analysis_draft_schema
from tests.operations.test_hypothesis_objective_boundary import _proposal


def sources(*, original_text=False):
    proposal = _proposal(hypothesis_keys=('mechanism',))
    if original_text:
        proposal.pop('research_objective_key')
    return ValidationSources({'hypothesis_portfolio': canonical_json(proposal),
        'experiment_results': b'{"summary":"Observed result"}',
        'scientific_materials': b'Other exact reference.'}, {})


def payload(**fields):
    return {'summary': 'Bounded interpretation.', 'overall_verdict': 'inconclusive',
        'claim_allowed': False, **fields}


def evidence(key, locator):
    return {'source_key': key, 'source_type': 'frozen_input',
        'title': 'Exact observation.', 'locator': locator}


@pytest.mark.parametrize('fields', [
    {'source_references': [{'source_key': 'experiment_results', 'input_alias': 'scientific_materials'}]},
    {'source_references': [{'source_key': 'observed', 'input_alias': 'experiment_results'}],
        'evidence': [evidence('observed', 'scientific_materials:table')]},
    {'source_references': [{'source_key': 'observed', 'input_alias': 'missing'}]},
    {'evidence': [evidence('fabricated', '/x')]},
])
def test_analysis_rejects_conflicting_or_unbound_sources(fields):
    with pytest.raises(SemanticRuleViolation):
        validate_context(payload(**fields), sources(), {})


def test_analysis_accepts_local_source_names_and_repeated_citations():
    reference = {'source_key': 'observed', 'input_alias': 'experiment_results'}
    validate_context(payload(source_references=[reference, reference],
        evidence=[evidence('observed', 'experiment_results:table')]*2), sources(), {})


@pytest.mark.parametrize('fields', [
    {'experiment_key': 'invented'}, {'plan_key': 'invented'},
    {'source_references': [{'source_key': 'observed', 'input_alias': 'experiment_results',
        'experiment_key': 'invented', 'case_key': 'invented'}]},
])
def test_generic_analysis_cannot_invent_plan_or_case_identities(fields):
    with pytest.raises(SemanticRuleViolation, match='no bound experiment plan'):
        validate_context(payload(**fields), sources(), {})


def test_original_text_analysis_cannot_invent_an_objective_key():
    with pytest.raises(SemanticRuleViolation, match='exact hypothesis objective'):
        validate_context(payload(objective_assessment={'objective_key': 'invented',
            'status': 'inconclusive', 'summary': 'No structured objective.'}), sources(original_text=True), {})


def test_analysis_cannot_assess_a_different_hypothesis():
    with pytest.raises(SemanticRuleViolation, match='exact experiment'):
        validate_context(payload(hypothesis_assessments=[{'hypothesis_key': 'other',
            'outcome': 'not_tested', 'evidence_keys': ['experiment_results'],
            'rationale': 'No test.'}]), sources(), {})


def test_optional_worker_progress_reuses_bound_evidence():
    validate_context(payload(progress_assessment=dict(outcome='no_progress', reason='The decision did not change.',
        evidence_keys=['experiment_results'])), sources(), {})
    with pytest.raises(SemanticRuleViolation):
        validate_context(payload(progress_assessment=dict(outcome='advance', reason='Unbound claim.',
            evidence_keys=['fabricated'])), sources(), {})


def test_progress_increment_is_separate_from_hypothesis_direction_but_checks_are_bound():
    assessed = dict(hypothesis_key='mechanism', outcome='contradicts', evidence_keys=['experiment_results'],
        rationale='A bounded negative observation.')
    validate_context(payload(hypothesis_assessments=[assessed], progress_assessment=dict(
        outcome='no_progress', reason='The same refutation was already known.', evidence_keys=['experiment_results'])), sources(), {})
    with pytest.raises(SemanticRuleViolation, match='exact hypothesis'):
        validate_context(payload(hypothesis_assessments=[dict(assessed, predictions_checked=['invented'])]), sources(), {})


def test_analysis_context_retains_bound_subject_evidence_alias():
    available = dict(sources())
    available['subject'] = available['experiment_results']
    contextual = ValidationSources({name: raw for name, raw in available.items()
        if name in OPERATION.outputs[0].context_sources}, {})
    validate_context(payload(progress_assessment=dict(outcome='no_progress', reason='Bound observation.',
        evidence_keys=['subject'])), contextual, {})
    with pytest.raises(SemanticRuleViolation):
        validate_context(payload(progress_assessment=dict(outcome='advance', reason='Unbound observation.',
            evidence_keys=['unbound_subject'])), contextual, {})


@pytest.mark.parametrize('original_text', [False, True])
def test_analysis_finalizer_binds_objective_without_replacing_scientific_assessment(tmp_path, original_text):
    bound = sources(original_text=original_text)
    assessment = dict(objective_key='stale_authored_key', status='inconclusive',
        summary='Target representativeness remains unknown.', evidence_keys=['experiment_results'])
    value = dict(payload=payload(objective_assessment=assessment))
    path = tmp_path / 'output' / 'result.json'
    path.parent.mkdir()
    original = canonical_json(value)
    path.write_bytes(original)
    request = WorkspaceFinalizationRequest(operation_id=OPERATION.operation_id, workspace=tmp_path,
        input_paths={}, output_limit_bytes=65536, output_schema_id='scidiscovery.layered-diagnosis.v1',
        input_contents={'portfolio_alias': bound['hypothesis_portfolio']},
        binding_descriptors={'portfolio_alias': SimpleNamespace(port_name='hypothesis_portfolio')})
    sealed = json.loads(finalize_general_result(request))
    expected_key = None if original_text else 'global_objective'
    assert sealed['payload']['objective_assessment'] == {**assessment, 'objective_key': expected_key}
    assert path.read_bytes() == original
    validate_context(sealed['payload'], bound, sealed['handoff'])


@pytest.mark.parametrize('aliases', [(), ('one', 'two')])
def test_analysis_objective_projection_requires_one_exact_portfolio(tmp_path, aliases):
    path = tmp_path / 'output' / 'result.json'
    path.parent.mkdir()
    original = canonical_json(dict(payload=payload(objective_assessment=dict(
        status='inconclusive', summary='The global contribution remains unknown.'))))
    path.write_bytes(original)
    request = WorkspaceFinalizationRequest(operation_id=OPERATION.operation_id, workspace=tmp_path,
        input_paths={}, output_limit_bytes=65536, output_schema_id='scidiscovery.layered-diagnosis.v1',
        input_contents={alias: sources()['hypothesis_portfolio'] for alias in aliases},
        binding_descriptors={alias: SimpleNamespace(port_name='hypothesis_portfolio') for alias in aliases})
    with pytest.raises(WorkspaceProtocolError, match='hypothesis portfolio binding'):
        finalize_general_result(request)
    assert path.read_bytes() == original


def test_text_objective_assessment_remains_available_without_a_structured_key():
    validate_context(payload(objective_assessment=dict(status='inconclusive',
        summary='Original target achievement is unknown.', evidence_keys=['experiment_results'])),
        sources(original_text=True), {})
    with pytest.raises(ValueError, match='requires evidence_keys'):
        validate_context(payload(objective_assessment=dict(status='pass', summary='Unsupported success.')),
            sources(original_text=True), {})


@pytest.mark.parametrize('key', [None, 'other_objective'])
def test_structured_objective_assessment_still_requires_exact_key(key):
    with pytest.raises(SemanticRuleViolation, match='exact hypothesis objective') as caught:
        validate_context(payload(objective_assessment=dict(objective_key=key,
            status='inconclusive', summary='Unknown target achievement.')), sources(), {})
    assert caught.value.details[0]['path'] == '$.objective_assessment.objective_key'


@pytest.mark.parametrize('project, ports, authored_key', [
    (result_draft_schema, {'portfolio': 'hypothesis_portfolio'}, False),
    (result_draft_schema, {'plan': 'experiment_plan'}, True),
    (result_draft_schema, {'package': 'curve_analysis_package'}, False),
    (analysis_draft_schema, {'package': 'curve_analysis_package'}, True),
])
def test_analysis_owner_projects_objective_identity_from_authoring_schema(project, ports, authored_key):
    from scidiscovery.artifact_agent.schema.layered_diagnosis import LayeredDiagnosisReport
    from scidiscovery.artifact_agent.schema.role_result import RoleHandoff
    resource = LayeredDiagnosisReport.model_json_schema()
    resource['$id'] = 'urn:test:layered-diagnosis'
    envelope = {'$defs': {'RoleHandoff': RoleHandoff.model_json_schema()},
        'properties': {'payload': resource, 'handoff': {'$ref': '#/$defs/RoleHandoff'}},
        'required': ['payload', 'handoff']}
    draft = project(envelope, 'scidiscovery.layered-diagnosis.v1', ports)
    assessment = draft['properties']['payload']['$defs']['ObjectiveDiagnosisAssessment']
    assert ('objective_key' in assessment['properties']) is authored_key
    assert {'status', 'summary'} <= set(assessment['required'])
    assert 'evidence_keys' in assessment['properties']
    assert 'objective_key' in envelope['properties']['payload']['$defs']['ObjectiveDiagnosisAssessment']['properties']


def test_curve_finalizer_keeps_package_plan_identities_without_a_generic_portfolio(tmp_path):
    from curve_score.analysis_workspace import finalize_analysis_result
    value = dict(payload=payload(plan_key='exact_plan', experiment_key='exact_experiment',
        objective_assessment=dict(objective_key='exact_objective', status='inconclusive',
            summary='No full objective verdict.')))
    path = tmp_path / 'output' / 'result.json'
    path.parent.mkdir()
    original = canonical_json(value)
    path.write_bytes(original)
    request = WorkspaceFinalizationRequest(operation_id='fixture', workspace=tmp_path,
        input_paths={}, output_limit_bytes=65536, output_schema_id='scidiscovery.layered-diagnosis.v1',
        binding_descriptors={'package': SimpleNamespace(port_name='curve_analysis_package')})
    result = json.loads(finalize_analysis_result(request))
    assert result['payload'] == value['payload']
    assert result['handoff']['verdict'] == 'inconclusive'
    assert path.read_bytes() == original
