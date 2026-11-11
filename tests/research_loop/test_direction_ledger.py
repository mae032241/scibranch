"""Current reflection boundaries, retained provenance and rejected legacy policies."""
import copy
import json

import pytest

from scidiscovery.research_loop import accepted_search, lineage, supervision
from current_strategy_fixtures import budget_facts


def round_fact(index, mechanism='h001.m1'):
    return dict(round_id=f'w{index:03d}.r1', source_step=index, pointer='/exploration_rounds/0',
        mechanism=mechanism, question='Does the discriminator separate the mechanisms?',
        finding='The declared observable differs within its stated limitations.')


def ledger(count, mechanism='h001.m1'):
    state=supervision.empty()
    for index in range(1,count+1):
        state=supervision.add_round(state,round_fact(index,mechanism))
    return state


@pytest.mark.parametrize('changed',[
    dict(version=3), dict(consecutive_rounds=4), dict(min_budget_fraction=0),
    dict(consecutive_calls=True), dict(min_budget_fraction=float('inf')),
    dict(budget_checkpoints=[.3,.65]), dict(max_automatic_reviews=-1),
])
def test_retired_round_and_checkpoint_policies_are_rejected(changed):
    with pytest.raises(ValueError):
        supervision.policy(dict(supervision.DEFAULT_POLICY,**changed))


def test_rounds_remain_navigation_and_only_frozen_coverage_can_advance():
    state=ledger(4)
    # Authored batches are navigation; they do not invent fresh A inputs or
    # convert a budget fraction into an automatic-review admission grant.
    assert not supervision.due(state,budget_fraction=.9)['automatic_allowed']
    frozen=supervision.request(state,budget_fraction=.31)
    assert frozen['cutoff']==dict(total_rounds=4,source_step=4,round_id='w004.r1')
    state=supervision.add_round(state,round_fact(5))
    state=supervision.cover(state,frozen,source_step=6)
    assert state['total_rounds']==5 and state['covered_rounds']==4
    assert state['consecutive_rounds']==1
    assert state['automatic_reviews']==0
    assert state['last_covered_cutoff']==dict(frozen['cutoff'],assessment_step=6)
    invalid=copy.deepcopy(frozen);invalid['cutoff']['total_rounds']=6
    with pytest.raises(ValueError,match='cutoff moved outside accepted rounds'):
        supervision.cover(state,invalid,source_step=7)


def test_actual_a_publication_keeps_unknown_completion_separate_from_proved_trigger_lower_bound(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import accepted_budget, accepted_prefix, budget, contracts as c, originals
    root=tmp_path/'instance'
    immutable_base=dict(goal_id='goal',max_steps=10)
    settings_ref=dict(path=str(root/'settings.json'))
    origin=dict(goal_id='goal',settings_ref=settings_ref)
    selected=dict(supervision_policy=supervision.policy(),
        supervision_budget_basis=dict(max_tokens=1000,max_seconds=100))
    summary=accepted_search.empty();summary.update(node_count=1,last_w=1)
    prefix=dict(branch=origin,count=1,cutoff=1,tip=None)
    current_head=None
    observations=[dict(accounted_tokens=310,observed_tokens=310,unknown_step_count=1,
                       blocking_unknown_count=1,unknown_steps=[1],blocking_unknown_steps=[1]),
                  dict(accounted_tokens=430,observed_tokens=430,unknown_step_count=0,
                       blocking_unknown_count=0,unknown_steps=[],blocking_unknown_steps=[])]
    samples=[];completions=[]
    def usage(path,*,head):
        assert head is current_head
        samples.append('usage')
        return observations.pop(0)
    def snapshot(path,base,events):
        assert base is immutable_base and events.state is current_head
        samples.append('clock')
        return dict(consumed_seconds=1)
    prepare=accepted_search.prepare_node_from_facts
    def consume(*args,**kwargs):
        completions.append(kwargs['completion_fraction'])
        return prepare(*args,**kwargs)
    monkeypatch.setattr(accepted_budget,'usage',usage)
    monkeypatch.setattr(budget,'_snapshot',snapshot)
    monkeypatch.setattr(accepted_search,'prepare_node_from_facts',consume)
    monkeypatch.setattr(accepted_budget,'search_row',lambda *args,**kwargs:None)
    monkeypatch.setattr(accepted_prefix,'navigation',lambda *args:None)
    monkeypatch.setattr(accepted_prefix,'for_job',lambda job:prefix)
    monkeypatch.setattr(originals,'identity_key',lambda identity:'branch')
    monkeypatch.setattr(accepted_budget,'read',lambda *args,**kwargs:pytest.fail('Publisher must reuse its produced accepted head'))
    class Head(dict):
        @property
        def current_job(self):return self['current_job']
    for step,role in ((2,'A'),(3,'E')):
        job=dict(step=step,role=role,handoff_protocol=33,direct_refs=[],search=None,
            research_strategy_version=3,advisory_scope_version=1)
        payload=dict(summary='A bounded scientific judgment',scientific_result='Retain the scientific direction with the stated gaps.',limitations=[])
        if role=='A':
            job['supervision_request']=supervision.request(summary,budget_fraction=.31,
                budget_facts=dict(tokens=310,seconds=1,limits=dict(tokens=1000,seconds=100),
                    complete=dict(tokens=False,seconds=True)))
            payload.update(next_action='continue_w',decision_request=None)
        else:
            job['evidence_return']=dict(step=2,role='A')
        c.write(root/'jobs'/f'{step:03d}.json',job)
        current_head=Head(prefix=prefix,settings_ref=settings_ref,search=summary,
            current_job=dict(step=step,role=role,status='terminal',prefix=prefix,
                reported_tokens=310 if role=='A' else 430,unknown_count=int(role=='A'),complete=role!='A',
                job_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
        inventory=SimpleNamespace(base=immutable_base,policy=selected,head=current_head,delivery=None,
            review_binding=None,native_intent=None,research_note_records=[],
            research_note_diagnostics=[],execution_fact_view=None)
        seal=dict(observations=None,materials=[],selected_inputs=[],
            result_ref=dict(path='private/seal'),raw_ref=dict(path='private/science'),
            control_outcome=dict(search_status='applied',selection_status='applied',decision_status='applied'))
        (root/'native-evidence'/f'{step:03d}').mkdir(parents=True)
        published=accepted_prefix.prepare_node(root,job,payload,seal,inventory=inventory)
        summary=published['search_delta']['summary'];prefix=published['prefix']
        assert summary['supervision']['last_review_fraction'] is None
        assert summary['supervision']['automatic_reviews']==0
    # The extra .12 is a later original-bill reconciliation, not evidence of
    # consumption after A completion. Even a fresh completed original cannot
    # grant spacing until a review establishes a complete new baseline.
    assert summary['supervision']['max_budget_fraction']==.43
    state=supervision.add_investment(summary['supervision'],dict(
        original_call_id='new-completed-original',mechanism_ref='w001.question',completed_receipt=True))
    status=supervision.due(state,budget_fraction=.43,budget_facts=budget_facts(.43))
    assert status['cooldown'] and not status['automatic_allowed']
    assert status['suppression_reason']=='shared_consumption_unknown_reconcile_original_usage'
    assert completions==[None,.43]
    assert samples==['usage','clock','usage','clock']


def call(identity, status='known_success', mechanism='h001.m1', original=None):
    return dict(call_id=identity,status=status,accepted=dict(path=original or f'private/{identity}/accepted.json',size_bytes=19),
        mechanism_binding=dict(mechanism_ref=mechanism,source_step=1,source_role='H',source_ref={'path':'private/producer-seal'}))


def declaration(*ids, mechanism='h001.m1'):
    return dict(mechanism=mechanism,question='A single scientific batch?',call_ids=list(ids),finding='Declared scientific finding.')


def test_current_worker_cannot_reactivate_legacy_round_declarations(monkeypatch):
    from scidiscovery.research_loop import contracts
    monkeypatch.setattr(contracts,'read',lambda *args,**kwargs:pytest.fail('Pure consumer reread originals'))
    summary=accepted_search.empty();summary['node_count']=1
    delta=accepted_search._Delta(summary,lambda name:pytest.fail('Legacy declaration queried an index'))
    accepted_search._supervision(delta,dict(step=2,role='W',handoff_protocol=33,
        research_strategy_version=3,advisory_scope_version=1),
        dict(exploration_rounds=[declaration('a')]),{},dict(managed_calls=[call('a')]),
        origin=dict(goal_id='private-origin'),bindings={},valid=True,policy={})
    assert delta.summary['supervision']['total_rounds']==0 and delta.rows=={}
    assert delta.summary['supervision']['investment']['total_calls']==0


def test_projection_is_bounded_and_contains_no_private_original_bindings():
    state=supervision.add_investment(ledger(100),dict(original_call_id='private/accepted-original',
        mechanism_ref='h001.m1',completed_receipt=True))
    value=supervision.project(state)
    assert 'private' not in json.dumps(value)
    assert value['mechanism_investments']=={'h001.m1':1}
    assert value['recent_rounds'][0]['pointer']=='/exploration_rounds/0'
    assert len(value['recent_rounds'])==2 and value['rounds_omitted_count']==98
    assert len(json.dumps(value).encode())<4096


def test_research_navigation_retains_exact_round_author_original_across_a():
    state=ledger(1)
    payload=dict(summary='Authored science',limitations=[],scientific_result='Finding',next_action='wait')
    first=lineage.research_navigation({},dict(step=1,role='W'),payload,node_path='nodes/branch/001',supervision_state=state)
    row=first['direction_supervision']['recent_rounds'][0]
    assert row['original']=='inputs/history/nodes/branch/001/scientific-original.json'
    following=lineage.research_navigation(first,dict(step=2,role='A'),payload,node_path='nodes/branch/002',supervision_state=state)
    assert following['direction_supervision']['recent_rounds'][0]['original']==row['original']
    assert following['latest_w']==first['latest_w']
