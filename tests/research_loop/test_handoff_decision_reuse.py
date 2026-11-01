"""One new A comparison; saved routes consume its accepted decision."""
from copy import deepcopy

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_prefix
from scidiscovery.research_loop import budget, contracts as c, controller, decision, history, native, semantic
from test_decision_contract import REQUEST, author, next_worker, prepare


def test_sealed_decision_and_route_do_not_repeat_assessment(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    original, original_payload = author(root, 3, [1, 2], REQUEST)
    next_worker(root, text='An accepted response; the scientific question remains open.')
    observed = []
    assess = decision.assess
    def counted(*args, **kwargs):
        observed.append(True)
        return assess(*args, **kwargs)
    monkeypatch.setattr(decision, 'assess', counted)
    job, payload = author(root, 5, [1, 2, 3], REQUEST)
    assert observed == [True]
    def forbidden(*args, **kwargs):
        raise AssertionError('Saved A decision was assessed again')
    monkeypatch.setattr(decision, 'assess', forbidden)
    assert decision.reason(root, original, original_payload) is None
    changed = dict(payload, next_action='revise_h', route_reason='Changed caller payload')
    assert decision.reason(root, job, changed) == 'a_decision_request_repeated'
    route = ('W', [1, 2, 5], '', None, 1, 2, None)
    routed = semantic.effective_route(root, 5, payload, job, route)
    assert routed[0] == 'A'
    assert routed[-1]['reason'] == 'a_decision_request_repeated'


def _comparison(tmp_path, monkeypatch):
    origin = dict(goal_id='parent', settings_ref=dict(path='/original/settings.json'))
    prefix = dict(version=accepted_prefix.VERSION, count=8, cutoff=8, visibility_root=dict(path=accepted_prefix.PUBLIC+'/branch'))
    job = dict(step=9, role='A', history=dict(prefix=prefix))
    payload = dict(next_action='continue_w', decision_request=deepcopy(REQUEST),
        candidate_recommendation='candidate', route_reason='Compare the same original')
    roles = {1:'H', 2:'W', 3:'A', 4:'W', 5:'A', 6:'W', 7:'A', 8:'E'}
    metadata = {step:dict(role=role, next_action='extract' if step==7 else 'continue_w',
        origin_identity=origin) for step,role in roles.items()}
    rows = {}
    for step in (3, 5, 7):
        original = dict(step=step, role='A', direct_refs=[])
        prior = dict(deepcopy(payload), next_action='extract' if step==7 else 'continue_w')
        control = dict(decision_status='applied', selection_status='applied', search_status='applied',
            resolved_focus=dict(mechanism_ref='mechanism'))
        rows[step] = (original, prior, dict(control_outcome=control), dict(path=f'/original/{step}/seal.json'))
    for step in (4, 6):
        following = dict(step=step, role='W', direct_refs=[dict(path=f'inputs/parents/{step-1:03d}/conclusion.json')])
        rows[step] = (following, {}, {}, dict(path=f'/original/{step}/seal.json'))
    for step in roles:
        c.write(tmp_path/accepted_prefix.MEMBERS/'branch'/'ordinals'/f'{step:09d}.json',
            dict(step=step, origin_identity=origin))
    monkeypatch.setattr(c, 'settings', lambda root, job: dict(decision_contract_version=1))
    monkeypatch.setattr(accepted_budget, 'read', lambda root: {})
    monkeypatch.setattr(accepted_budget, 'search_row', lambda root, key, **kwargs: metadata[int(key.split('/')[-1])])
    def member(root, prefix, step, **kwargs):
        assert kwargs.get('origin', origin) == origin
        return dict(seal_ref=rows[step][3])
    monkeypatch.setattr(accepted_prefix, 'member', member)
    opened = []
    def source(root, step):
        opened.append(step)
        return rows[step]
    monkeypatch.setattr(history, '_source', source)
    bases = {3:{'old', 'new', 'extra'}, 5:{'old'}, 9:{'old', 'new'}}
    monkeypatch.setattr(decision, '_basis', lambda root, original, **kwargs: bases[original['step']])
    return job, payload, metadata, rows, opened


def test_older_completed_superset_still_rejects_after_latest_smaller_basis(tmp_path, monkeypatch):
    job, payload, _, _, opened = _comparison(tmp_path, monkeypatch)
    assert decision.assess(tmp_path, job, payload, focus=dict(mechanism_ref='mechanism')) == [
        dict(code='a_decision_request_repeated', subject='continue_w')]
    assert opened == [3, 4]


@pytest.mark.parametrize('change', ['question', 'candidate', 'focus', 'action', 'incomplete'])
def test_different_decision_keys_and_incomplete_responses_are_not_reused(tmp_path, monkeypatch, change):
    job, payload, _, rows, opened = _comparison(tmp_path, monkeypatch)
    focus = dict(mechanism_ref='mechanism')
    if change == 'question':payload['decision_request']['question'] += ' Another question.'
    elif change == 'candidate':payload['candidate_recommendation'] = 'other'
    elif change == 'focus':focus['mechanism_ref'] = 'other'
    elif change == 'action':payload['next_action'] = 'revise_h'
    else:rows[4][0]['direct_refs'] = []
    assert decision.assess(tmp_path, job, payload, focus=focus) == []
    assert not ({1, 2, 7, 8} & set(opened))


def test_light_metadata_cannot_replace_fork_member_origin(tmp_path, monkeypatch):
    job, payload, metadata, _, opened = _comparison(tmp_path, monkeypatch)
    metadata[3]['origin_identity'] = dict(goal_id='child', settings_ref=dict(path='/child/settings.json'))
    with pytest.raises(ValueError, match='prefix identity changed'):
        decision.assess(tmp_path, job, payload, focus=dict(mechanism_ref='mechanism'))
    assert opened == []


def test_closeout_phase_does_not_count_earlier_final_nodes_twice(tmp_path, monkeypatch):
    root = tmp_path/'instance'
    for step in (1, 2, 4):
        c.write(root/'jobs'/f'{step:03d}.json', dict(step=step, role='A', purpose='closeout', final_only=step<4))
        c.write(root/'native-evidence'/f'{step:03d}'/'result-identity.json', {})
        c.write(root/'native-evidence'/f'{step:03d}'/'native-outcome.json', {})
    state = dict(begin=dict(mode='model', first_step=4, source_cutoff=3, seq=1),
        actions=[], unresolved_operations=[])
    monkeypatch.setattr(budget, 'closeout_state', lambda root: state)
    monkeypatch.setattr(budget, 'status_snapshot', lambda root: {})
    monkeypatch.setattr(budget, 'used_steps', lambda root: 4)
    monkeypatch.setattr(native, 'usage', lambda root: {})
    monkeypatch.setattr(accepted_budget, 'read', lambda root: dict(max_local_step=4))
    monkeypatch.setattr(controller.configuration_updates, 'current', lambda root: {})
    monkeypatch.setattr(controller, '_record', lambda root, step: root/'native-evidence'/f'{step:03d}')
    monkeypatch.setattr(controller.execution, 'record', lambda root, step: root/'native-evidence'/f'{step:03d}')
    monkeypatch.setattr(controller.execution, 'raw', lambda root, step: root/f'science-{step}.json')
    slots, science = [], []
    def accepted(root, step):
        slots.append(step)
        return dict(actual_submission=None, a_recommended=None, latest_a_step=step)
    def sealed(root, step):
        science.append(step)
        return dict(role='A', next_action='finish', limitations=[])
    monkeypatch.setattr(controller, '_closeout_accepted_slots', accepted)
    monkeypatch.setattr(controller.handoff_state, 'read_sealed', sealed)
    result = controller._closeout_result(root, 'delivery_unknown', read_only=True)
    assert result['completed_scientific_nodes'] == 4
    assert result['source_cutoff'] == 3 and result['last_step'] == 4
    assert slots == [3, 4] and science == [4]
