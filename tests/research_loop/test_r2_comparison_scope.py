"""Synthetic current-comparison admission; no model, runtime or trial inputs."""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, breadth, search


def accepted_comparison(*, compared=('h001.m4', 'h001.m3')):
    summary = accepted_search.empty()
    prefix = dict(version=1, cutoff=3)
    for number in range(1, 6):
        ref = f'h001.m{number}'
        identity = dict(goal_id='synthetic-r2', step=1)
        summary['table']['mechanisms'][ref] = dict(
            label=ref, minimal_test='Compare matched target conditions.', status='screened',
            source_step=1, source_role='H', source_ref=dict(path='/fixture/native-evidence/001/result-identity.json'),
            source_origin_identity=identity, ordinal=number, accepted_calls=1,
            origin_ref=dict(goal_id='synthetic-r2', source_step=1, source_role='H', mechanism_ref=ref))
        summary['table']['ordinals'].append(ref)
    summary.update(mechanism_count=5, current_h=1, last_science=dict(step=3, role='A'),
        focus=dict(mechanism_ref='h001.m4', action='improve'),
        pending_work_response=dict(source_a=3))
    facts = summary['current_facts']
    facts[accepted_search.key('node', 3)] = dict(next_action='continue_w', executed_work=['A003-D001'])
    facts[accepted_search.key('work', 'A003-D001')] = dict(
        work_ref='A003-D001', status='executed', next_action='continue_w',
        mechanism_refs=list(compared), executions=[dict(source_a=3, response=None)])
    # Retained older work must not silently enlarge this task's comparison.
    facts[accepted_search.key('work', 'A001-D001')] = dict(
        work_ref='A001-D001', status='executed', next_action='continue_w',
        mechanism_refs=['h001.m2'], executions=[dict(source_a=1, response=None)])
    facts[accepted_search.key('work', 'A002-D001')] = dict(
        work_ref='A002-D001', status='withdrawn', next_action='continue_w',
        mechanism_refs=['h001.m1'], executions=[dict(source_a=2, response=None)])
    head = dict(prefix=prefix, search=summary)
    # The production point-state producer has no author-addressed work refs
    # when constructing a fresh W. Its retained accepted facts carry scope.
    state = accepted_search.state(None, 4, head=head)
    assert state['order']['discriminating_work'] == []
    search._current_combination(None, state)
    value = search._breadth(None, state, search.DEFAULT_POLICY, state['focus'])
    return state, value, head


def admission(monkeypatch, value, state, head):
    monkeypatch.setattr(breadth, '_catalog_root', lambda frozen, scope: ('/fixture', state['focus']))
    monkeypatch.setattr(accepted_budget, 'read', lambda root: head)
    return dict(step=4, job_ref=dict(path='/fixture/jobs/004.json'))


def test_current_two_mechanism_comparison_preserves_primary_and_each_number_binding(monkeypatch):
    state, value, head = accepted_comparison()
    assert state['focus'] == dict(mechanism_ref='h001.m4', action='improve')
    assert [row['mechanism_ref'] for row in value['mechanisms']] == ['h001.m4', 'h001.m3']
    scope = admission(monkeypatch, value, state, head)
    accepted = []
    for number in ('4', '3'):
        binding = breadth.admit(value, number, scope, accepted)
        assert binding['mechanism_ref'] == f'h001.m{number}'
        assert binding['catalog_binding']['ordinal'] == int(number)
        assert binding['job_ref'] == scope['job_ref']
        assert breadth.check_binding(value, binding, scope) == binding
        accepted.append(dict(mechanism_binding=binding))
    for number in ('1', '2', '5'):
        with pytest.raises(breadth.OpportunityRequired) as error:
            breadth.admit(value, number, scope, accepted)
        assert error.value.reason == 'focused_mechanism_required'
        assert [row['number'] for row in error.value.available_mechanisms] == ['3', '4']
        with pytest.raises(breadth.OpportunityRequired):
            breadth.resolve_choice(value, number, scope, head=head)
    assert breadth.resolve_choice(value, '3', scope, head=head)['mechanism_ref'] == 'h001.m3'


def test_single_focus_scope_and_missing_number_behavior_remain(monkeypatch):
    state, value, head = accepted_comparison(compared=('h001.m4',))
    assert [row['mechanism_ref'] for row in value['mechanisms']] == ['h001.m4']
    scope = admission(monkeypatch, value, state, head)
    assert breadth.admit(value, '4', scope, [])['mechanism_ref'] == 'h001.m4'
    with pytest.raises(breadth.OpportunityRequired) as error:
        breadth.admit(value, None, scope, [])
    assert error.value.reason == 'mechanism_choice_required'
    with pytest.raises(breadth.OpportunityRequired):
        breadth.admit(value, '3', scope, [])


def test_comparison_does_not_reopen_a_scientific_waiver(monkeypatch):
    state, value, head = accepted_comparison()
    head['search']['table']['mechanisms']['h001.m3']['status'] = 'waived'
    scope = admission(monkeypatch, value, state, head)
    with pytest.raises(breadth.OpportunityRequired) as error:
        breadth.admit(value, '3', scope, [])
    assert error.value.reason == 'mechanism_waived'
    assert breadth.admit(value, '4', scope, [])['mechanism_ref'] == 'h001.m4'


@pytest.mark.parametrize('change', ['responded', 'older_author', 'deferred', 'withdrawn', 'other_route', 'no_pending'])
def test_old_or_inactive_comparison_never_expands_focused_scope(change):
    state, _, _ = accepted_comparison()
    state = copy.deepcopy(state)
    summary = state['summary']
    work = summary['current_facts'][accepted_search.key('work', 'A003-D001')]
    if change == 'responded':
        work['executions'][0]['response'] = dict(source_step=4, role='W', pointer='/scientific_result')
    elif change == 'older_author':
        work['executions'][0]['source_a'] = 1
    elif change in {'deferred', 'withdrawn'}:
        work['status'] = change
    elif change == 'other_route':
        work['next_action'] = 'revise_h'
    else:
        summary['pending_work_response'] = None
    search._current_combination(None, state)
    value = search._breadth(None, state, search.DEFAULT_POLICY, state['focus'])
    assert [row['mechanism_ref'] for row in value['mechanisms']] == ['h001.m4']


def test_missing_current_accepted_work_fails_without_record_read_fallback():
    state, _, _ = accepted_comparison()
    del state['summary']['current_facts'][accepted_search.key('work', 'A003-D001')]
    with pytest.raises(ValueError, match='comparison work is missing'):
        search._current_combination(None, state)
