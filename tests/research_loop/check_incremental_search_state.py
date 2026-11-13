"""Explicit portable checks for small breadth-table snapshots and original facts.

No provider, solver, process launch or test-history enumeration is required.
"""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_search as accepted
from scidiscovery.research_loop import contracts,history


@pytest.fixture
def facts(monkeypatch):
    monkeypatch.setattr(contracts, 'settings', lambda root, job=None: dict(search_policy=dict(initial_calls=1)))
    monkeypatch.setattr(history, 'read_observation', lambda root, job, seal: seal.get('test_observation'))
    monkeypatch.setattr(accepted,'_origin',lambda root,step:dict(goal_id='fixture',settings_ref=dict(path='/fixture/settings.json')))
    monkeypatch.setattr(accepted,'_seal_ref',lambda root,step:dict(path=f'/fixture/{step}-marker.json'))
    return dict(summary=accepted.empty(), rows={}, reads=[])


def apply(facts, step, role, payload, **job_fields):
    job = dict(step=step, role=role, direct_refs=[])
    job.update(job_fields)
    seal = dict(control_outcome=dict(search_status='applied', selection_status='applied', decision_status='applied',
        resolved_focus=job_fields.get('test_focus'), resolved_allocation=None))
    observation = payload.pop('_observation', None)
    if observation is not None:seal.update(observations=dict(path=f'host-history/observations/{step:03d}.json',size_bytes=1),test_observation=observation)
    def lookup(key):
        facts['reads'].append(key)
        return facts['rows'].get(key)
    delta = accepted.prepare_node(None, job, payload, seal, summary=facts['summary'], lookup=lookup,
        observation=observation)
    facts['rows'].update(delta['rows']);facts['summary'] = delta['summary']
    return delta


def plan(label, existing=None):
    return dict(search_plan=dict(commitments=[dict(label=label, minimal_test='Exact authored test', existing_ref=existing)]))


def test_current_seal_ref_does_not_stat_unpublished_marker(tmp_path,monkeypatch):
    def unexpected(*args,**kwargs):pytest.fail('Prospective seal reference touched an unpublished marker')
    with monkeypatch.context() as patch:
        patch.setattr(Path,'stat',unexpected)
        patch.setattr(Path,'resolve',unexpected)
        reference=accepted._seal_ref(tmp_path,7)
    assert reference==dict(path=str(tmp_path/'native-evidence/007/result-identity.json'))


def test_e_append_never_reads_older_entities(facts):
    apply(facts,1,'H',plan('One retained mechanism'))
    for size in (2, 8, 16, 32):
        before = copy.deepcopy(facts['summary'])
        facts['reads'].clear()
        delta = apply(facts, size, 'E', dict(scientific_result='One current evidence fact'))
        assert facts['reads'] == [accepted.key('node', size)]
        assert set(delta['rows']) == {accepted.key('node', size)}
        assert delta['summary']['node_count'] == before['node_count'] + 1
        assert delta['summary']['mechanism_count'] == before['mechanism_count']
        assert delta['summary']['table']==before['table']
        assert delta['summary']['table'] is not before['table']


def test_duplicate_original_requires_writer_reconciliation(facts):
    apply(facts, 1, 'E', {})
    with pytest.raises(ValueError, match='already accepted'):
        apply(facts, 1, 'E', {})


def test_unknown_current_w_reconciliation_keeps_consumption_and_exact_binding(facts):
    declaration=dict(version=1,mechanism_ref='w001.m1',source_step=1,source_role='W',
        source_ref=dict(path='/fixture/research/000001.json'),revision=1,
        question='Which retained observation changes this comparison?')
    binding=dict(mechanism_ref='w001.m1',source_step=1,source_role='W',
        source_ref=declaration['source_ref'],research_binding=declaration,grant=None)
    observation=dict(coverage=dict(status='partial'),candidates=[],managed_calls=[
        dict(call_id='original-call',status='unknown',mechanism_binding=binding,
            scope=dict(job_ref=dict(path='/fixture/jobs/001.json')))])
    apply(facts,1,'W',dict(_observation=observation),research_strategy_version=3)
    original=facts['summary']
    assert original['table']['mechanisms']['w001.m1']['accepted_calls']==1
    assert original['opportunity_unknown_count']==1
    resolved=dict(call_id='original-call',status='known_failed',mechanism_binding=binding)
    delta=accepted.prepare_recovery(summary=original,lookup=facts['rows'].get,
        step=1,call_id='original-call',resolved=resolved,
        recovery_ref=dict(path='execution-recovery/001.json'))
    assert delta['summary']['opportunity_unknown_count']==0
    assert delta['summary']['table']['mechanisms']['w001.m1']['accepted_calls']==1
    assert original['opportunity_unknown_count']==1
    assert delta['summary']['table']['mechanisms']['w001.m1']['last_terminal']['mechanism_binding']==binding
    with pytest.raises(ValueError,match='original scientific call binding'):
        accepted.prepare_recovery(summary=original,lookup=facts['rows'].get,
            step=1,call_id='original-call',resolved=dict(resolved,mechanism_binding=dict(binding,source_step=9)),
            recovery_ref=dict(path='execution-recovery/002.json'))
