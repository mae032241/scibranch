"""Numerical boundaries retained independently of Operation/Artifact hosts."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from curve_algorithms.common import canonical_json
from curve_algorithms.schema import CurveBundle, CurveComparisonSpec, evaluate_curve_consistency, _raw_value_at
from curve_algorithms.analysis import compute_curve_error, render_curve_error
from curve_algorithms.sources import CSVSource, csv_series
from curve_algorithms.curve_normalizer import SProcessLogSourceSpec, normalize_sprocess_log
from curve_algorithms.plx_normalizer import SProcessPLXSourceSpec, normalize_sprocess_plx

FIXTURES = Path(__file__).parents[1] / 'fixtures'


def inputs():
    frozen = json.loads((FIXTURES / 'curve_calculation_pre_support.json').read_bytes())
    return json.loads(frozen['sources']['curve_bundle']), deepcopy(frozen['record']['request']['comparison_spec'])


def evaluate(bundle, spec):
    return evaluate_curve_consistency(CurveBundle.model_validate_json(canonical_json(bundle)), CurveComparisonSpec.model_validate_json(canonical_json(spec)))


def test_original_numeric_fixture_and_residual_renderer_remain_reproducible():
    raw, spec = inputs()
    report = evaluate(raw, spec)
    frozen = json.loads((FIXTURES / 'curve_calculation_pre_support.json').read_bytes())
    # Historical vectors predate empty crossing-support metadata. Compare the
    # numerical observation and explicit threshold result, not host-era objects.
    expected = frozen['record']['result']['comparisons'][0]
    actual = report.comparisons[0]
    assert actual.status == expected['status']
    for field in ('operator_key', 'kind', 'status', 'value', 'unit', 'reason_code'):
        assert getattr(actual.metrics[0], field) == expected['metrics'][0][field]
    for field in ('status', 'observed_value', 'reason_code'):
        assert getattr(actual.checks[0], field) == expected['checks'][0][field]
    assert actual.checks[0].threshold.model_dump(mode='json') == expected['checks'][0]['threshold']
    assert report.aggregate_status == 'fail'
    bundle = CurveBundle.model_validate_json(canonical_json(raw))
    parsed = CurveComparisonSpec.model_validate_json(canonical_json(spec))
    numerics = compute_curve_error(bundle, parsed, report)
    artifacts = render_curve_error(numerics)
    assert artifacts.plots and all(raw.startswith(b'\x89PNG') for _, raw in artifacts.plots)
    assert artifacts.report.analyses[0].global_score.reported == report.comparisons[0].metrics[0].value
    assert render_curve_error(numerics) == artifacts


@pytest.mark.parametrize('change', ['gap','exclusion','missing','unit'])
def test_unsupported_comparison_never_manufactures_a_metric(change):
    bundle, spec = inputs()
    if change == 'gap': bundle['series'][0]['valid_intervals'] = [{'start':0.,'stop':.4},{'start':.6,'stop':1.}]
    elif change == 'exclusion': bundle['series'][0]['exclusions'] = [{'start':.2,'stop':.4}]
    elif change == 'missing': bundle['series'].pop()
    elif change == 'unit': bundle['series'][0]['x_axis']['unit'] = 'nm'
    result = evaluate(bundle,spec).comparisons[0]
    assert result.status == 'unavailable'
    assert result.metrics[0].value is None and result.metrics[0].reason_code


def test_zero_log_floor_and_duplicate_observation_rules_are_explicit():
    bundle, spec = inputs()
    for series in bundle['series']:
        series['points'] = [{'x':0.,'y':0.},{'x':.5,'y':0.},{'x':1.,'y':1.}]
    assert evaluate(bundle, spec).comparisons[0].metrics[0].value is None
    spec['comparisons'][0]['floor_mask'] = {'reference_at_or_below':.1,'candidate_at_or_below':.1,'unit':'cm^-3','rationale':'Explicit detection floor'}
    spec['comparisons'][0]['interpolation'] = 'linear_y'
    result = evaluate(bundle,spec).comparisons[0].metrics[0]
    assert result.status == 'available' and result.value == 0.
    bundle['series'][0]['points'].insert(1, {'x':0.,'y':0.})
    parsed = CurveBundle.model_validate_json(canonical_json(bundle))
    with pytest.raises(ValueError, match='duplicate_x_masked'):
        _raw_value_at(parsed.series[0], 0., 'linear_y')


def test_nonfinite_values_fail_before_computation():
    bundle, spec = inputs(); bundle['series'][0]['points'][0]['y'] = float('nan')
    with pytest.raises(ValueError): canonical_json(bundle)
    raw, _ = inputs(); raw['series'][0]['points'][0]['y'] = 'NaN'
    with pytest.raises(ValidationError): CurveBundle.model_validate_json(json.dumps(raw))


def source_spec():
    return {'series_key':'line','case_key':'case','role':'candidate',
        'x_axis':{'name':'depth','unit':'um'},'y_axis':{'name':'density','unit':'cm^-3'}}


def test_csv_plx_and_declared_log_have_equal_observations_and_raw_digests():
    spec=source_spec()
    csv=CSVSource.model_validate_json(canonical_json({'format':'csv','input_alias':'original','x_column':'depth','y_column':'density',**spec}))
    points=csv_series(csv,b'depth,density\n0,1\n1,2\n').points
    plx, audit=normalize_sprocess_plx(b'"density"\n0 1\n1 2\n',SProcessPLXSourceSpec.model_validate_json(canonical_json({'series':spec})))
    assert plx.series[0].points == points and audit.input_point_count==2
    log=b'SCID_CURVE_V1|POINT|case|line|0|0|1\nSCID_CURVE_V1|POINT|case|line|1|1|2\nSCID_CURVE_V1|END|case|line|2\n'
    normalized, audit=normalize_sprocess_log(log,SProcessLogSourceSpec.model_validate_json(canonical_json({'expected_series':[spec]})))
    assert normalized.series[0].points == points and audit.matched_record_lines==3
    with pytest.raises(ValueError): csv_series(csv,b'depth,depth,density\n0,0,1\n1,1,2\n')
    with pytest.raises(ValueError): normalize_sprocess_plx(b'"density"\n0 nan\n1 2\n',SProcessPLXSourceSpec.model_validate_json(canonical_json({'series':spec})))


def test_repeated_x_residual_skips_the_seam_but_point_metric_refuses_it():
    bundle, spec = inputs()
    bundle['series'][0]['points'].insert(1, deepcopy(bundle['series'][0]['points'][1]))
    result = evaluate(bundle, spec).comparisons[0]
    # Independent oracle from the fixture's two log-linear branches: the
    # residual is 4*x below x=.5 and 4*(1-x) above it. The repeated x=.5
    # sample is ineligible, while the other 256 grid observations remain valid.
    residuals = [4 * min(i / 256, 1 - i / 256) for i in range(257) if i != 128]
    assert result.metrics[0].value == pytest.approx(math.sqrt(sum(r*r for r in residuals) / 256))
    assert result.status == 'fail'
    spec['comparisons'][0]['operators'] = [{'operator_key':'at_seam', 'kind':'point_difference', 'x':.5}]
    point = evaluate(bundle, spec).comparisons[0].metrics[0]
    assert point.status == 'unavailable' and point.value is None
    assert point.reason_code == 'duplicate_x_masked'
    parsed = CurveBundle.model_validate_json(canonical_json(bundle))
    assert [p.x for p in parsed.series[0].points].count(.5) == 2


@pytest.mark.parametrize('support', ['none','covering_interval','gap','exclusion','outside_exclusion','unavailable'])
@pytest.mark.parametrize('operator', ['residual_rms','point_difference','crossing_shift','width_shift'])
def test_fixed_support_vectors_preserve_numeric_values_and_missing_reasons(support, operator):
    bundle, spec = inputs()
    reference = bundle['series'][0]
    comparison = spec['comparisons'][0]
    if support == 'covering_interval': reference['valid_intervals'] = [{'start':0.,'stop':1.}]
    elif support == 'gap': reference['valid_intervals'] = [{'start':0.,'stop':.4},{'start':.6,'stop':1.}]
    elif support == 'exclusion': reference['exclusions'] = [{'start':.2,'stop':.4}]
    elif support == 'outside_exclusion':
        reference['exclusions'] = [{'start':.8,'stop':1.}]
        comparison['domain']['stop'] = .5
    elif support == 'unavailable': reference['availability'].update(status='unavailable', reason_code='source_missing')
    op = {'operator_key':'probe','kind':operator,'value_space':'linear'}
    if operator == 'point_difference': op['x'] = .25
    if operator in {'crossing_shift','width_shift'}: op['level'] = 5e10
    if operator == 'width_shift': op['second_level'] = 8e10
    comparison['operators'] = [op]
    vectors = json.loads((FIXTURES / 'curve_support_v1.json').read_bytes())
    expected = vectors['cases'][operator + '/' + support]
    metric = evaluate(bundle, spec).comparisons[0].metrics[0]
    if expected['result'] is None:
        assert metric.status == 'unavailable' and metric.value is None
        assert metric.reason_code == expected['reason_code']
    else:
        original = expected['result']['comparisons'][0]['metrics'][0]
        assert metric.status == original['status'] and metric.unit == original['unit']
        assert metric.value == pytest.approx(original['value'], rel=1e-14)
        assert metric.reason_code == original['reason_code']
