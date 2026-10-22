from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import runpy
from pathlib import Path

from scidiscovery.operations.input_validation import ValidationSources
from scidiscovery.plugin_runtime.calculations import CalculationResult
import pytest

from curve_score.analysis_tool import AnalysisScoreInput, evaluate_analysis_request, replay_calculation
from curve_score.science_operations import Components, validate_analysis_report
from curve_score.plugin import PLUGIN
from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.artifact_agent.schema.layered_diagnosis import CalculationRecord, LayeredDiagnosisReport
from scidiscovery.artifact_agent.schema.refs import ArtifactRef
from scidiscovery.builtin_plugin import CORE_PLUGIN
from scidiscovery.general_science_plugin import PLUGIN as GENERAL
from scidiscovery.operations.catalog import compile_catalog
from scidiscovery.operations.invoke import InvocationArtifact, OperationInvocationError, preflight_operation
from scidiscovery.operation_contract import SemanticRuleViolation

_FIXTURES = runpy.run_path(str(Path(__file__).with_name('test_m2_curve_analysis_boundary.py')))


def score_inputs():
    return {'curve_bundle': _FIXTURES['_bundle']().canonical_json()}, {'sources': [{'input_alias': 'curve_bundle', 'format': 'bundle'}], 'comparison_spec': _FIXTURES['_contract']().comparison_spec.model_dump(mode='json')}


def limited_report():
    report = _FIXTURES['_diagnosis']().model_dump(mode='json')
    report['evidence'][0].update(source_key='experiment_results', locator='experiment_results:failure', title='Bound result failure')
    report['source_references'] = [dict(source_key='experiment_results', input_alias='experiment_results')]
    for gate in report['gates'].values():
        if isinstance(gate, dict) and gate.get('evidence_keys'):
            gate['evidence_keys'] = ['experiment_results']
    report['summary'] = 'Execution ended before the requested observable was saved.'
    report['gates']['numerical_validity']['summary'] = 'The execution failed; quantitative validity cannot be established.'
    report['remaining_contradiction'] = 'The planned comparison remains unperformed because its output is missing.'
    report['next_action'] = 'Resolve the execution failure and preserve the required observable before comparison.'
    return report


def test_generic_score_replays_exact_values_without_contract():
    sources, request = score_inputs()
    record = evaluate_analysis_request(record_key='score', request=request, sources=sources)
    assert record.status == 'computed'
    replay_calculation(record, sources)
    raw = record.model_dump(mode='json')
    raw['result']['comparisons'][0]['metrics'][0]['value'] += 1
    with pytest.raises(SemanticRuleViolation, match='replay'):
        replay_calculation(CalculationResult.model_validate_json(canonical_json(raw)), sources)
    with pytest.raises(SemanticRuleViolation, match='source bytes'):
        replay_calculation(record, {'curve_bundle': sources['curve_bundle'] + b' '})


def test_curve_plugin_keeps_historical_public_bytes_and_complete_private_result(tmp_path):
    from types import SimpleNamespace
    from curve_score.analysis_tool import complete_curve_calculation
    from scidiscovery.operations.analysis_artifacts import complete_calculation
    from scidiscovery.operations.calculation_proof import controlled_calculation
    sources, request = score_inputs()
    record = evaluate_analysis_request(record_key='score', request=request, sources=sources)
    assert record.status == 'computed'
    saved = {}
    def accept_evidence(**kwargs):
        saved.update(kwargs)
        return {'alias': 'saved_calculation'}
    context = SimpleNamespace(workspace=tmp_path, accept_evidence=accept_evidence,
        finish_attempt=lambda **kwargs: None, input_names_for_port=lambda port: ())
    context.complete_calculation = lambda record, **kwargs: complete_calculation(context, record, **kwargs)
    reply = complete_curve_calculation(context, record)
    expected = record.model_dump(mode='json', exclude={'input_digests', 'diagnostics'})
    for key in ('curve_bundle_sha256', 'comparison_spec_sha256', 'validation_plan_sha256'):
        expected['result'].pop(key, None)
    assert saved['raw'] == canonical_json(expected)
    restored = controlled_calculation(saved['raw'], {'metadata': saved['metadata']})
    assert restored.result == record.result
    assert reply['calculation_ref'] == 'saved_calculation'
    assert reply['summary']['comparison_count'] == len(record.result['comparisons'])
    assert reply['summary']['interpretation_boundary'] == 'deterministic_metrics_only_no_physical_interpretation'
    assert 'result' not in reply


@pytest.mark.parametrize('change', ['metric', 'samples', 'work'])
def test_invalid_comparisons_are_rejected_before_calculation(change):
    sources, request = score_inputs()
    comparison = request['comparison_spec']['comparisons'][0]
    if change == 'metric':
        comparison['operators'][0]['kind'] = 'raw_row_group_weighted_rms'
    elif change == 'samples':
        comparison['evaluation_points'] = 4097
    else:
        comparison['evaluation_points'] = 4096
        comparison['operators'] = [{**comparison['operators'][0], 'operator_key': f'op_{i}'} for i in range(17)]
    with pytest.raises(ValueError):
        AnalysisScoreInput.model_validate_json(canonical_json(dict(record_key='invalid', request=request)))
    record = evaluate_analysis_request(record_key='invalid', request=request, sources=sources)
    assert record.status == 'unavailable' and record.reason_code == 'invalid_arguments'
    assert record.result is None and not record.input_digests


@pytest.mark.parametrize('mappings', [[{'format': 'bundle'}], [{'input_alias': 'absent', 'format': 'bundle'}], []])
def test_missing_or_malformed_sources_return_bounded_unavailable(mappings):
    sources, request = score_inputs()
    request['sources'] = mappings
    record = evaluate_analysis_request(record_key='missing', request=request, sources=sources)
    assert record.status == 'unavailable'
    if record.reason_code != 'invalid_arguments':
        replay_calculation(record, sources)
    assert len(record.canonical_json()) < 32768


def test_request_byte_admission_precedes_calculation():
    with pytest.raises(ValueError, match='12288 canonical JSON bytes'):
        AnalysisScoreInput(record_key='oversize', request={'unbounded': 'x' * 13000})


def test_csv_columns_preserve_explicit_identity_and_missing_column_reason():
    bundle = _FIXTURES['_bundle']()
    sources, request = {}, score_inputs()[1]
    request['sources'] = []
    for item in bundle.series:
        alias = item.series_key
        sources[alias] = ('depth,value,extra\n' + ''.join(f'{point.x},{point.y},kept\n' for point in item.points)).encode()
        request['sources'].append({'input_alias': alias, 'format': 'csv', 'series_key': item.series_key, 'case_key': item.case_key, 'role': item.role, 'x_axis': item.x_axis.model_dump(mode='json'), 'y_axis': item.y_axis.model_dump(mode='json'), 'x_column': 'depth', 'y_column': 'value'})
    record = evaluate_analysis_request(record_key='csv', request=request, sources=sources)
    assert record.status == 'computed'
    assert all(record.input_digests[key] == hashlib.sha256(raw).hexdigest() for key, raw in sources.items())
    replay_calculation(record, sources)
    request['sources'][0]['y_column'] = 'absent'
    missing = evaluate_analysis_request(record_key='missing', request=request, sources=sources)
    assert missing.status == 'unavailable'
    replay_calculation(missing, sources)


def test_error_has_no_numeric_result_and_is_not_required_to_recur():
    sources, request = score_inputs()
    good = evaluate_analysis_request(record_key='error', request=request, sources=sources)
    record = CalculationResult.model_validate_json(canonical_json({**good.model_dump(mode='json'), 'status': 'error', 'result': None, 'reason_code': 'timeout'}))
    replay_calculation(record, sources)
    with pytest.raises(ValueError, match='no numeric result'):
        CalculationResult.model_validate_json(canonical_json({**record.model_dump(mode='json'), 'result': {'value': 1}}))


def test_no_score_limited_analysis_uses_actual_bound_results():
    Components.diagnosis_context.implementation(limited_report(), ValidationSources({'experiment_plan': _FIXTURES['_plan']().canonical_json(), 'experiment_results': b'Execution failed before observable output.'}, {}), {})


def test_optional_objective_assessment_must_match_exact_plan():
    plan = _FIXTURES['_compiler_plan']()
    report = limited_report()
    report['study_kind'] = 'scientific'
    sources = ValidationSources({'experiment_plan': plan.canonical_json(),
        'experiment_results': b'Execution failed before observable output.'}, {})
    Components.diagnosis_context.implementation(report, sources, {})
    report['objective_assessment'] = {'objective_key': 'wrong_objective',
        'status': 'not_evaluable', 'summary': 'Cannot assess a different objective.'}
    with pytest.raises(SemanticRuleViolation, match='objective differs from plan') as caught:
        Components.diagnosis_context.implementation(report, sources, {})
    assert caught.value.details[0]['path'] == '$.objective_assessment'
    report['objective_assessment'] = {
        'objective_key': plan.objective_key,
        'status': 'not_evaluable',
        'summary': 'The failed bounded execution did not produce the planned observable.',
    }
    Components.diagnosis_context.implementation(
        report,
        ValidationSources({'experiment_plan': plan.canonical_json(),
         'experiment_results': b'Execution failed before observable output.'}, {}),
        {},
    )


@pytest.mark.parametrize('record_kind', ['unsupported', 'failed', 'unrelated'])
def test_record_status_does_not_mechanically_decide_scientific_verdict(record_kind):
    sources, request = score_inputs()
    if record_kind == 'unsupported':
        request['comparison_spec']['comparisons'][0]['operators'][0]['kind'] = 'unsupported_statistic'
    if record_kind == 'unrelated':
        request['comparison_spec']['comparisons'][0]['operators'][0]['validation_check_key'] = 'unrelated_check'
    record = evaluate_analysis_request(record_key='score', request=request, sources=sources)
    report = _FIXTURES['_passing_diagnosis']().model_dump(mode='json')
    report['evidence'][0]['locator'] = 'score'
    validate_analysis_report(LayeredDiagnosisReport.model_validate_json(canonical_json(report)), _FIXTURES['_plan'](), calculations=[record])


def test_inconclusive_observation_can_submit_without_scoring():
    report = _FIXTURES['_passing_diagnosis']().model_dump(mode='json')
    report['evidence'][0].update(source_key='experiment_results', locator='experiment_results:run_log')
    report['source_references'] = [dict(source_key='experiment_results', input_alias='experiment_results')]
    for gate in report['gates'].values():
        if isinstance(gate, dict) and gate.get('evidence_keys'):
            gate['evidence_keys'] = ['experiment_results']
    report['gates']['observation']['status'] = 'inconclusive'
    report['gates']['observation']['summary'] = 'The requested observable is absent.'
    report['overall_verdict'] = 'inconclusive'
    report['summary'] = 'Run prerequisites hold but the observation cannot be compared.'
    Components.diagnosis_context.implementation(report, ValidationSources({'experiment_plan': _FIXTURES['_plan']().canonical_json(), 'experiment_results': b'Run completed; observable missing.'}, {}), {})


@pytest.mark.parametrize('location', ['request', 'source'])
def test_unknown_weighting_option_is_not_silently_scored_as_rms(location):
    sources, request = score_inputs()
    if location == 'request':
        request['weighting'] = 'original_row_groups'
    else:
        request['sources'][0]['weight_column'] = 'group_weight'
    record = evaluate_analysis_request(record_key='weighted', request=request, sources=sources)
    assert record.status == 'unavailable' and record.reason_code == 'invalid_arguments'
    assert not record.input_digests
    with pytest.raises(ValueError):
        AnalysisScoreInput.model_validate_json(canonical_json(dict(record_key='weighted', request=request)))


@pytest.mark.parametrize('comparisons', [[None], [42], ['invalid'], None])
def test_persisted_malformed_comparisons_remain_readable_as_failed_requests(comparisons):
    sources, request = score_inputs()
    request['comparison_spec']['comparisons'] = comparisons
    record = evaluate_analysis_request(record_key='malformed', request=request, sources=sources)
    assert record.status == 'unavailable' and record.result is None
    assert record.reason_code == 'invalid_arguments' and not record.input_digests
    assert canonical_json(record.request) == canonical_json(request)
    assert all(item.phase == 'tool_arguments' for item in record.diagnostics)


@pytest.mark.parametrize('documented', [False, True])
def test_changed_threshold_record_remains_visible_for_scientific_review(documented):
    sources, request = score_inputs()
    request['comparison_spec']['comparisons'][0]['operators'][0]['threshold']['value'] = 100.0
    record = evaluate_analysis_request(record_key='score', request=request, sources=sources)
    report = _FIXTURES['_passing_diagnosis']().model_dump(mode='json')
    report['evidence'][0]['locator'] = 'score'
    if documented:
        report['method_changes'] = ['Exploratory relaxed threshold, not the original acceptance criterion.']
    validate_analysis_report(LayeredDiagnosisReport.model_validate_json(canonical_json(report)), _FIXTURES['_plan'](), calculations=[record])
    assert record.request['comparison_spec']['comparisons'][0]['operators'][0]['threshold']['value'] == 100.0


def test_score_tool_contract_describes_comparison_and_rejects_invalid_record_key():
    schema = AnalysisScoreInput.model_json_schema()
    assert schema['properties']['request'] == {'$ref': '#/$defs/AnalysisScoreRequest'}
    sources = schema['$defs']['AnalysisScoreRequest']['properties']['sources']['items']
    assert sources['discriminator']['propertyName'] == 'format'
    assert set(sources['discriminator']['mapping']) == {'bundle', 'csv'}
    comparison = schema['$defs']['AnalysisScoreRequest']['properties']['comparison_spec']['$ref'].split('/')[-1]
    assert comparison in schema['$defs'] and 'CurveAxis' in schema['$defs']
    assert 'sprocess_plx' not in json.dumps(schema)
    with pytest.raises(ValueError):
        AnalysisScoreInput(record_key='bad key', request={})


def test_matching_plan_check_can_still_pass_with_computed_evidence():
    import json
    sources, request = score_inputs()
    bundle = json.loads(sources['curve_bundle'])
    by_key = {item['series_key']: item for item in bundle['series']}
    comparison = request['comparison_spec']['comparisons'][0]
    by_key[comparison['candidate_series']]['points'] = by_key[comparison['reference_series']]['points']
    sources['curve_bundle'] = canonical_json(bundle)
    record = evaluate_analysis_request(record_key='score', request=request, sources=sources)
    report = _FIXTURES['_passing_diagnosis']().model_dump(mode='json')
    report['evidence'][0]['locator'] = 'score'
    validate_analysis_report(LayeredDiagnosisReport.model_validate_json(canonical_json(report)), _FIXTURES['_plan'](), calculations=[record])
