"""Structured H descriptions retain their originals through the first W handoff.

The small r8 declaration fixture exercises control acceptance and projections;
no model, external experiment or scientific qualification runs here.
"""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, search
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_direct_input_bindings import stopped_seals
from test_history_handoff import finish


FIXTURE = Path(__file__).parent / 'fixtures/r8_h_structured_minimal_test.json'


@pytest.fixture
def declaration():
    return json.loads(FIXTURE.read_text())['search_plan']


@pytest.fixture
def scene(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Test the exact declared mechanisms within fixture limitations.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=10000,
        max_seconds=300, max_steps=8, model='fixture', reasoning_effort='high',
        search_policy=dict(search.DEFAULT_POLICY), experiment_mode='external')
    stopped_seals(monkeypatch)
    seal = c.seal_result
    raw = {}
    def seal_original(root, step):
        path = c.host_record(c.workspace(root, step)) / 'science-output.json'
        raw[step] = path.read_bytes()
        result = seal(root, step)
        assert path.read_bytes() == raw[step]
        return result
    monkeypatch.setattr(c, 'seal_result', seal_original)
    return root, raw


def test_r8_first_h_seals_structured_descriptions_and_screens_all_three(scene, declaration):
    root, raw = scene
    before = copy.deepcopy(declaration)
    assert len(declaration['commitments']) == 3
    assert all(isinstance(row['minimal_test'], dict) for row in declaration['commitments'])
    assert declaration['focus'] == dict(index=None, action='screen')
    job, _, record = finish(root, 1, 'H', [], action='experiment', search_choice=declaration)
    seal = c.read(record / 'result-identity.json')
    payload = c.read_scientific_output(record / 'science-output.json')
    assert seal['control_outcome']['search_status'] == 'applied'
    assert seal['control_outcome']['diagnostics'] == []
    assert payload['search_plan'] == before == declaration
    assert (record / 'science-output.json').read_bytes() == raw[1]
    assert not (record / 'continuations').exists()

    worker = c.build_job(root, 2, 'W', [1])
    expected = [f'h001.m{index}' for index in range(1, 4)]
    assert worker['search']['focus'] == dict(mechanism_ref=expected[0], action='screen')
    assert [row['mechanism_ref'] for row in worker['search']['coverage_obligation']] == expected
    rows = worker['search']['breadth']['mechanisms']
    assert [row['mechanism_ref'] for row in rows] == expected
    assert [json.loads(row['minimal_test']) for row in rows] == [row['minimal_test'] for row in before['commitments']]
    accepted = accepted_budget.read(root)['search']['table']['mechanisms']
    assert [accepted[ref]['minimal_test'] for ref in expected] == [row['minimal_test'] for row in rows]

    # Historical reduction consumes the same sealed original, without changing
    # the raw structured scientific descriptions or widening breadth row types.
    reduced = dict(mechanisms={}, authorized=set(), extra_grant=None, order=dict(remaining=[]))
    search.apply_source(root, job, payload, seal, reduced)
    assert [reduced['mechanisms'][ref]['minimal_test'] for ref in expected] == [row['minimal_test'] for row in rows]
    assert (record / 'science-output.json').read_bytes() == raw[1]


def test_string_description_projection_preserves_authored_text(scene):
    root, raw = scene
    text = '  Compare the declared conditions.\nRetain uncertainty.  '
    plan = dict(commitments=[dict(label='String fixture', minimal_test=text)], focus=dict(index=None, action='screen'))
    _, _, record = finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    worker = c.build_job(root, 2, 'W', [1])
    assert worker['search']['breadth']['mechanisms'][0]['minimal_test'] == text
    assert c.read_scientific_output(record / 'science-output.json')['search_plan'] == plan
    assert (record / 'science-output.json').read_bytes() == raw[1]


@pytest.mark.parametrize('description', [[dict(method='Bounded comparison')], dict(unknown=None)])
def test_structured_description_has_no_nested_science_schema(description):
    payload = dict(search_plan=dict(commitments=[dict(label='Fixture', minimal_test=description)],
        focus=dict(index=None, action='screen')))
    original = copy.deepcopy(payload)
    choice = search._choice(dict(role='H', step=1), payload)
    assert json.loads(choice['commitments'][0]['minimal_test']) == description
    assert payload == original


@pytest.mark.parametrize('description', [None, False, 17, '', '   ', {}, []])
def test_invalid_minimal_description_names_its_exact_path_and_expected_type(description):
    payload = dict(search_plan=dict(commitments=[dict(label='Valid', minimal_test='Comparison'),
        dict(label='Invalid', minimal_test=description)], focus=dict(index=None, action='screen')))
    with pytest.raises(search.UnresolvedSearch) as caught:
        search._choice(dict(role='H', step=1), payload)
    assert caught.value.subject == 'search_plan.commitments[1].minimal_test'
    assert caught.value.expected == 'a nonblank string or nonempty JSON object/array'
    assert caught.value.subject in str(caught.value)


def test_invalid_minimal_description_repairs_only_the_identified_field(scene):
    root, _ = scene
    plan = dict(commitments=[dict(label='Fixture', minimal_test=None)], focus=dict(index=None, action='screen'))
    with pytest.raises(OutputRepairRequired) as caught:
        finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    message = str(caught.value)
    assert 'search_plan.commitments[0].minimal_test' in message
    assert 'nonblank string or nonempty JSON object/array' in message
    assert 'declared choice/action fields' not in message


def _empty_state():
    return dict(mechanisms={}, current_h=None, scope=None, plan=None, authorized=set(), focus=None,
        order=dict(remaining=[], seen_w=set(), allocation=None, diagnostics=[], discriminating_work=[]))


@pytest.mark.parametrize('focus', [dict(index=0, action='screen'), dict(index=4, action='screen'),
    dict(index=True, action='screen'), dict(index=None, action='improve'), dict(index=1, action='unknown')])
def test_structured_description_preserves_focus_type_and_action_gates(tmp_path, declaration, focus):
    declaration['focus'] = focus
    result = search._interpret_from_facts(tmp_path, dict(role='H', step=1),
        dict(search_plan=declaration, next_action='experiment', route_reason='Fixture question'),
        policy=dict(search_policy=dict(search.DEFAULT_POLICY)), state=_empty_state(), selected_inputs=[])
    assert result['diagnostics']
    assert result['focus'] is None


def test_empty_screen_still_has_no_mechanism_to_dispatch(tmp_path):
    result = search._interpret_from_facts(tmp_path, dict(role='H', step=1),
        dict(search_plan=dict(commitments=[], focus=dict(index=None, action='screen')), next_action='experiment'),
        policy=dict(search_policy=dict(search.DEFAULT_POLICY)), state=_empty_state(), selected_inputs=[])
    assert result['diagnostics'] == [dict(code='science_plan_empty', subject='unfinished scientific plan')]
    assert result['focus'] is None


def test_revised_structured_description_preserves_exact_existing_mechanism(scene, declaration):
    root, raw = scene
    finish(root, 1, 'H', [], action='analyze', search_choice=declaration)
    finish(root, 2, 'A', [1], action='revise_h')
    description = dict(method='Revised bounded comparison', caveat='Fixture is not scientific qualification')
    plan = dict(commitments=[dict(label='Revised fixture', minimal_test=description, existing_ref='h001.m2')],
        focus=dict(index=1, action='screen'))
    _, _, record = finish(root, 3, 'H', [2], action='experiment', search_choice=plan)
    worker = c.build_job(root, 4, 'W', [3])
    row, = worker['search']['breadth']['mechanisms']
    assert row['mechanism_ref'] == 'h001.m2' and row['source_h'] == 3
    assert json.loads(row['minimal_test']) == description
    mechanisms = accepted_budget.read(root)['search']['table']['mechanisms']
    assert len(mechanisms) == 3 and mechanisms['h001.m2']['original_h'] == 1
    assert mechanisms['h001.m1']['source_h'] == 1
    assert c.read_scientific_output(record / 'science-output.json')['search_plan'] == plan
    assert (root / 'native-evidence/001/science-output.json').read_bytes() == raw[1]
