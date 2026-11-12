"""H declares methods; optional authored notes do not become required tests."""
import copy
import json

import pytest

from scidiscovery.research_loop import accepted_budget, breadth, contracts as c, controller, handoff_state, search
from test_direct_input_bindings import stopped_seals
from test_history_handoff import finish


@pytest.fixture
def scene(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Explore methods for the original scientific task.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], max_tokens=10000,
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


def methods():
    return dict(commitments=[dict(label='Transport-limited explanation'),
        dict(label='Interface-state explanation'), dict(label='Joint representation')],
        focus=dict(index=None, action='screen'))


def test_choice_accepts_method_labels_and_exact_revision_without_test_notes():
    plan = methods()
    plan['commitments'][1]['existing_ref'] = 'h001.m2'
    original = copy.deepcopy(plan)
    choice = search._choice(dict(role='H', step=3), dict(search_plan=plan))
    assert choice['commitments'] == [dict(label=row['label'], minimal_test=row['label'],
        existing_ref=row.get('existing_ref')) for row in plan['commitments']]
    assert choice['focus'] == plan['focus']
    assert plan == original


def test_method_only_h_seals_and_binds_exact_numbered_w_catalog_and_reducer(scene):
    root, raw = scene
    plan = methods()
    original = copy.deepcopy(plan)
    job, _, record = finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    seal = c.read(record / 'result-identity.json')
    payload = c.read_scientific_output(record / 'science-output.json')
    assert seal['control_outcome']['search_status'] == 'applied'
    assert seal['control_outcome']['diagnostics'] == []
    assert payload['search_plan'] == plan == original
    assert all('minimal_test' not in row for row in payload['search_plan']['commitments'])
    assert (record / 'science-output.json').read_bytes() == raw[1]
    assert not (record / 'continuations').exists()

    worker = c.build_job(root, 2, 'W', [1])
    expected = [f'h001.m{index}' for index in range(1, 4)]
    assert worker['search']['focus'] == dict(mechanism_ref=expected[0], action='screen')
    assert search.launch_reason(root, worker) is None
    assert [row['mechanism_ref'] for row in worker['search']['coverage_obligation']] == expected
    rows = worker['search']['breadth']['mechanisms']
    assert [row['mechanism_ref'] for row in rows] == expected
    assert [row['ordinal'] for row in rows] == [1, 2, 3]
    assert [row['minimal_test'] for row in rows] == [row['label'] for row in plan['commitments']]
    assert worker['search']['admission_advice']['available_mechanisms'] == [
        dict(number=str(index), mechanism_ref=ref) for index, ref in enumerate(expected, 1)]
    table = accepted_budget.read(root)['search']['table']['mechanisms']
    scope = dict(step=2, job_ref=c.record_ref(root / 'jobs/002.json'))
    for row in rows:
        ref = row['mechanism_ref']
        assert row['source_step'] == table[ref]['source_step'] == 1
        assert row['source_role'] == table[ref]['source_role'] == 'H'
        assert row['source_ref'] == table[ref]['source_ref']
        assert row['origin_ref'] == table[ref]['origin_ref']
        assert row['origin_ref']['mechanism_ref'] == ref
        assert row['origin_ref']['source_step'] == 1
        assert row['origin_ref']['source_role'] == 'H'
        selected = breadth.resolve_choice(worker['search']['breadth'], row['ordinal'], scope)
        assert all(selected[key] == row[key] for key in (
            'mechanism_ref', 'ordinal', 'origin_ref', 'source_goal_id', 'source_step', 'source_ref', 'source_role', 'minimal_test'))

    reduced = dict(mechanisms={}, authorized=set(), extra_grant=None, order=dict(remaining=[]))
    search.apply_source(root, job, payload, seal, reduced)
    assert list(reduced['mechanisms']) == expected
    assert [reduced['mechanisms'][ref]['minimal_test'] for ref in expected] == [row['label'] for row in plan['commitments']]
    assert reduced['plan']['commitments'] == [dict(label=row['label'], minimal_test=row['label'],
        existing_ref=None) for row in plan['commitments']]
    assert (record / 'science-output.json').read_bytes() == raw[1]


@pytest.mark.parametrize('note', ['  Authored method detail.\nRetain its limits.  ',
    dict(method='A possible direction', unknown=[None, dict(detail='Optional science')]),
    [dict(direction='A different representation')]])
def test_optional_authored_notes_keep_exact_raw_shape_and_display(scene, note):
    root, raw = scene
    plan = methods()
    plan['commitments'][1]['minimal_test'] = note
    _, _, record = finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    worker = c.build_job(root, 2, 'W', [1])
    rows = worker['search']['breadth']['mechanisms']
    displayed = rows[1]['minimal_test']
    assert (displayed if isinstance(note, str) else json.loads(displayed)) == note
    assert rows[0]['minimal_test'] == plan['commitments'][0]['label']
    assert c.read_scientific_output(record / 'science-output.json')['search_plan'] == plan
    assert (record / 'science-output.json').read_bytes() == raw[1]


@pytest.mark.parametrize('note', [None, False, 17, '', '   ', {}, []])
def test_malformed_present_note_still_names_its_exact_authored_field(note):
    plan = methods()
    plan['commitments'][1]['minimal_test'] = note
    with pytest.raises(search.UnresolvedSearch) as caught:
        search._choice(dict(role='H', step=1), dict(search_plan=plan))
    assert caught.value.subject == 'search_plan.commitments[1].minimal_test'
    assert caught.value.expected == 'a nonblank string or nonempty JSON object/array'


@pytest.mark.parametrize('row', [{}, dict(label=None), dict(label=False), dict(label=17),
    dict(label=''), dict(label='   ')])
def test_invalid_method_label_identifies_label_without_requesting_a_test(row):
    plan = methods()
    plan['commitments'][1] = row
    with pytest.raises(search.UnresolvedSearch) as caught:
        search._choice(dict(role='H', step=1), dict(search_plan=plan))
    assert caught.value.subject == 'search_plan.commitments[1].label'
    assert 'minimal_test' not in str(caught.value)


@pytest.mark.parametrize('focus', [dict(index=0, action='screen'), dict(index=4, action='screen'),
    dict(index=True, action='screen'), dict(index=None, action='improve'), dict(index=1, action='unknown')])
def test_method_only_plans_preserve_exact_focus_gates(tmp_path, focus):
    plan = methods()
    plan['focus'] = focus
    state = dict(mechanisms={}, current_h=None, scope=None, plan=None, authorized=set(), focus=None,
        order=dict(remaining=[], seen_w=set(), allocation=None, diagnostics=[], discriminating_work=[]))
    result = search._interpret_from_facts(tmp_path, dict(role='H', step=1),
        dict(search_plan=plan, next_action='experiment', route_reason='Fixture method selection'),
        policy=dict(search_policy=dict(search.DEFAULT_POLICY)), state=state, selected_inputs=[])
    assert result['diagnostics']
    assert result['focus'] is None


def test_method_only_revision_cannot_invent_an_existing_mechanism(tmp_path):
    plan = dict(commitments=[dict(label='A method', existing_ref='h001.m99')],
        focus=dict(index=1, action='screen'))
    state = dict(mechanisms={}, current_h=None, scope=None, plan=None, authorized=set(), focus=None,
        order=dict(remaining=[], seen_w=set(), allocation=None, diagnostics=[], discriminating_work=[]))
    result = search._interpret_from_facts(tmp_path, dict(role='H', step=3),
        dict(search_plan=plan, next_action='experiment', route_reason='Fixture revision'),
        policy=dict(search_policy=dict(search.DEFAULT_POLICY)), state=state, selected_inputs=[])
    assert result['diagnostics'] == [dict(code='h_mechanism_unresolved', subject='h001.m99')]
    assert result['focus'] is None


def test_w_requested_h_revision_retains_ordinal_and_original_sources(scene):
    root, raw = scene
    finish(root, 1, 'H', [], action='experiment', search_choice=methods())
    requester, _, _ = finish(root, 2, 'W', [1], stage_intent='revise_h')
    route = controller._route(root, 2, 'W', handoff_state.read_sealed(root, 2), requester, 1, None)
    consultant = c.build_job(root, 3, route.role, list(route.parents), instruction=route.instruction,
        evidence_return=route.evidence_return)
    plan = dict(commitments=[dict(label='Revised interface direction', existing_ref='h001.m2')],
        focus=dict(index=1, action='screen'))
    _, _, record = finish(root, 3, 'H', list(route.parents), action='wait', search_choice=plan)
    returned = controller._route(root, 3, 'H', handoff_state.read_sealed(root, 3), consultant,
        route.current_h, route.current_w)
    worker = c.build_job(root, 4, returned.role, list(returned.parents), instruction=returned.instruction,
        evidence_return=returned.evidence_return)
    row = next(row for row in worker['search']['breadth']['mechanisms'] if row['mechanism_ref'] == 'h001.m2')
    assert row['ordinal'] == 2 and row['source_step'] == 3 and row['source_role'] == 'H'
    assert row['source_ref'] == c.record_ref(record / 'result-identity.json')
    assert row['origin_ref']['source_step'] == 1 and row['origin_ref']['source_role'] == 'H'
    assert row['minimal_test'] == 'Revised interface direction'
    table = accepted_budget.read(root)['search']['table']['mechanisms']
    assert len(table) == 3 and table['h001.m2']['original_step'] == 1
    assert table['h001.m1']['source_step'] == table['h001.m3']['source_step'] == 1
    assert c.read_scientific_output(record / 'science-output.json')['search_plan'] == plan
    assert (root / 'native-evidence/001/science-output.json').read_bytes() == raw[1]
