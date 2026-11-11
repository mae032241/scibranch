"""Only an authored mechanism revision reopens direction; A advice grants none."""
import copy

import pytest

from scidiscovery.research_loop import search


def retained_state(status='screened'):
    return dict(current_h=1, scope='Original scientific scope', plan=None,
        mechanisms={'w001.m1': dict(status=status, original_step=1, original_role='W',
            source_step=1, source_role='W', label='Original mechanism', minimal_test='Original discriminator')},
        authorized={'w001.m1'}, focus=None, extra_grant=None,
        opportunity_counts={'w001.m1': 2}, opportunity_grants={},
        portfolio=dict(invested={'w001.m1': 1}),
        order=dict(remaining=[], seen_w={(2, '/original/seal.json')}, allocation=None,
            diagnostics=[], discriminating_work=[]))


@pytest.mark.parametrize('status', ['pending', 'screened', 'waived'])
def test_ordinary_a_advice_cannot_reopen_waive_or_allocate_research(status):
    state = retained_state()
    original = copy.deepcopy(state)
    payload = dict(next_action='continue_w', search_review=dict(
        dispositions=[dict(mechanism_ref='w001.m1', status=status, reason='Advice only')],
        next_focus=dict(mechanism_ref='w001.m1', action='screen')),
        portfolio_review=dict(main_line='w001.m1', discriminating_work=dict(question='Advice only')))
    interpreted = search._interpret_from_facts(None, dict(role='A'), payload,
        policy=dict(search_policy=search.DEFAULT_POLICY), state=state, selected_inputs=[])
    assert interpreted == dict(state=original, focus=None, allocation=None, diagnostics=[])
    search.apply_source(None, dict(role='A'), payload, dict(control_outcome={}), state)
    assert state == original


@pytest.mark.parametrize('role', ['H', 'W'])
def test_same_identity_revision_reopens_once_without_refunding_prior_work(role):
    state = retained_state('waived')
    before = copy.deepcopy(state)
    job = dict(step=4, role=role)
    payload = dict(search_plan=dict(scope='Original scientific scope', commitments=[dict(
        existing_ref='w001.m1', label='Revised mechanism', minimal_test='New discriminator')], focus=None))
    seal = dict(control_outcome=dict(search_status='applied', selection_status='applied', decision_status='applied',
        resolved_focus=None, resolved_allocation=None))
    search.apply_source(None, job, payload, seal, state)
    search.apply_source(None, job, payload, seal, state)
    item = state['mechanisms']['w001.m1']
    assert item['status'] == 'pending' and item['original_step'] == 1 and item['original_role'] == 'W'
    assert item['source_step'] == 4 and item['source_role'] == role
    assert state['order']['remaining'] == [dict(mechanism_ref='w001.m1', planned_step=4)]
    assert state['opportunity_counts'] == before['opportunity_counts']
    assert state['opportunity_grants'] == before['opportunity_grants']
    assert state['portfolio'] == before['portfolio']
    assert state['order']['seen_w'] == before['order']['seen_w']
    assert state['extra_grant'] is None


def test_accepted_call_counter_survives_same_ref_revision_and_a_advice(tmp_path):
    from scidiscovery.research_loop import accepted_search

    summary = accepted_search.empty(); facts = {}
    origin = dict(goal_id='fixture')
    observation = dict(coverage=dict(status='complete'), managed_calls=[], candidates=[], contexts=[], current_candidate=None)
    control = dict(search_status='applied', selection_status='applied', decision_status='applied',
        resolved_focus=dict(mechanism_ref='h001.m1', action='improve'), resolved_allocation=None)
    def apply(job, payload, observed=observation):
        nonlocal summary
        result = accepted_search.prepare_node_from_facts(tmp_path, dict(research_strategy_version=3, **job), payload,
            dict(control_outcome=control, observations=dict(path=f'observation-{job["step"]}.json')),
            summary=summary, lookup=facts.get, observation=observed,
            policy=dict(search_policy=search.DEFAULT_POLICY), origin=origin)
        summary = result['summary']; facts.update(result['rows'])
    apply(dict(step=1, role='H', direct_refs=[]), dict(search_plan=dict(commitments=[dict(
        label='Original mechanism', minimal_test='Original discriminator')])) )
    binding = dict(mechanism_ref='h001.m1', source_step=1, source_role='H',
        source_ref=dict(path=str(tmp_path / 'native-evidence/001/result-identity.json')), grant=None)
    call = dict(call_id='original-failed-call', status='known_failed', mechanism_binding=binding,
        accepted=dict(path='calls/original-failed-call/accepted.json'), scope=dict(job_ref=dict(path='/fixture/jobs/002.json')))
    apply(dict(step=2, role='W', direct_refs=[], search=dict(focus=control['resolved_focus'], breadth=None, allocation=None)),
        dict(stage_intent='decision'), dict(observation, managed_calls=[call]))
    assert summary['table']['mechanisms']['h001.m1']['accepted_calls'] == 1
    apply(dict(step=3, role='H', direct_refs=[]), dict(search_plan=dict(commitments=[dict(
        existing_ref='h001.m1', label='Revised mechanism', minimal_test='New discriminator')])))
    apply(dict(step=4, role='A', direct_refs=[], advisory_scope_version=1), dict(next_action='continue_w',
        search_review=dict(dispositions=[dict(mechanism_ref='h001.m1', status='waived')],
            next_focus=dict(mechanism_ref='h001.m1', action='screen'))))
    item = summary['table']['mechanisms']['h001.m1']
    assert item['accepted_calls'] == 1 and item['status'] == 'pending'
    assert item['original_step'] == 1 and item['source_step'] == 3
    assert summary['extra_grant'] is None
    assert facts[accepted_search.key('call', '2:original-failed-call')]['mechanism_binding'] == binding
