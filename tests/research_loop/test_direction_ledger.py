"""Explicit scientific batches and frozen A coverage; no execution or model runs."""
import copy
import json

import pytest

from scidiscovery.research_loop import accepted_search, lineage, supervision


def round_fact(index, mechanism='h001.m1'):
    return dict(round_id=f'w{index:03d}.r1', source_step=index, pointer='/exploration_rounds/0',
        mechanism=mechanism, question='Does the discriminator separate the mechanisms?',
        finding='The declared observable differs within its stated limitations.')


def ledger(count, mechanism='h001.m1'):
    state=supervision.empty()
    for index in range(1,count+1):
        state=supervision.add_round(state,round_fact(index,mechanism))
    return state


def test_explicit_rounds_count_across_w_and_rearm_after_exact_a_cutoff():
    state=ledger(4)
    frozen=supervision.request(state,budget_fraction=.31,automatic=True)
    assert frozen['cutoff']==dict(total_rounds=4,source_step=4,round_id='w004.r1')
    assert set(frozen['due_reasons'])=={'consecutive_rounds','budget_checkpoint:0.3'}
    state=supervision.add_round(state,round_fact(5))
    state=supervision.cover(state,frozen,source_step=6)
    assert state['total_rounds']==5 and state['covered_rounds']==4
    assert state['consecutive_rounds']==1 and state['automatic_reviews']==1
    assert not supervision.due(state,budget_fraction=.35)['due']
    for index in (7,8,9):
        state=supervision.add_round(state,round_fact(index))
    assert supervision.due(state,budget_fraction=.42)['automatic_allowed']


def test_direction_changes_use_canonical_ref_and_preserve_pending_due():
    state=ledger(4)
    state=supervision.add_round(state,round_fact(5,'w005.m1'))
    assert state['direction']=='w005.m1' and state['consecutive_rounds']==1
    assert supervision.due(state)['reasons']==['consecutive_rounds']
    assert state['total_rounds']==5


def test_budget_unknown_cooldown_and_recovery_do_not_clear_due_or_review_count():
    state=supervision.observe(ledger(1),.31)
    frozen=supervision.request(state,budget_fraction=.31,automatic=True)
    state=supervision.cover(state,frozen,source_step=2)
    state=supervision.observe(state,.66)
    snapshot=copy.deepcopy(state)
    assert supervision.due(state,budget_fraction=None)['reasons']==['budget_checkpoint:0.65']
    assert supervision.due(state,budget_fraction=.32)['automatic_allowed']
    assert state==snapshot
    frozen=supervision.request(state,budget_fraction=.66,automatic=True)
    state=supervision.cover(state,frozen,source_step=3)
    for index in range(4,8):
        state=supervision.add_round(state,round_fact(index))
    status=supervision.due(json.loads(json.dumps(state)),budget_fraction=.90)
    assert status['due'] and not status['automatic_allowed']
    assert status['automatic_reviews']==2
    assert supervision.due(supervision.empty(),budget_fraction=float('nan'))['budget_fraction'] is None
    assert supervision.due(supervision.empty(),budget_fraction=None)['budget_fraction'] is None


def test_late_a_cannot_clear_newer_count_trigger():
    state=ledger(1)
    frozen=supervision.request(state,budget_fraction=.1)
    for index in range(2,5):
        state=supervision.add_round(state,round_fact(index))
    state=supervision.add_round(state,round_fact(5,'h001.m2'))
    state=supervision.cover(state,frozen,source_step=6)
    assert supervision.due(state)['reasons']==['consecutive_rounds']


def test_policy_rejects_nonfinite_boolean_and_changed_frozen_policy():
    for patch in (dict(consecutive_rounds=True),dict(min_budget_fraction=float('inf')),
                  dict(budget_checkpoints=[.65,.3]),dict(max_automatic_reviews=-1)):
        with pytest.raises(ValueError):
            supervision.policy(dict(supervision.DEFAULT_POLICY,**patch))
    with pytest.raises(ValueError):
        supervision.due(ledger(1),policy=dict(supervision.DEFAULT_POLICY,consecutive_rounds=3))


def test_high_precision_checkpoint_is_covered_by_its_exact_frozen_review():
    point=.123456789012345
    chosen=dict(supervision.DEFAULT_POLICY,budget_checkpoints=[point])
    state=supervision.observe(supervision.empty(chosen),point)
    assert supervision.due(state)['reasons']==['budget_checkpoint:'+repr(point)]
    frozen=supervision.request(state)
    assert frozen['budget_fraction']==point
    state=supervision.cover(state,frozen,source_step=1)
    assert state['covered_checkpoints']==[point]
    assert not supervision.due(state)['due']


def test_unknown_review_baseline_preserves_due_without_automatic_spacing_grant():
    state=ledger(4)
    assert supervision.due(state)['automatic_allowed']
    first=supervision.request(state,automatic=True)
    assert first['budget_fraction'] is None
    state=supervision.cover(state,first,source_step=5)
    for index in range(6,10):
        state=supervision.add_round(state,round_fact(index))
    status=supervision.due(state)
    assert status['due'] and status['cooldown'] and not status['automatic_allowed']
    assert supervision.due(state,budget_fraction=.8)['cooldown']
    # An ordinary/manual review with a known observation establishes a real
    # baseline; a later proved increment can then satisfy the spacing policy.
    state=supervision.cover(state,supervision.request(state,budget_fraction=.8),source_step=10)
    for index in range(11,15):
        state=supervision.add_round(state,round_fact(index))
    assert supervision.due(state,budget_fraction=.91)['automatic_allowed']


def test_explicit_zero_spacing_allows_count_only_reviews_after_unknown_baseline():
    state=supervision.empty(dict(supervision.DEFAULT_POLICY,min_budget_fraction=0))
    for index in range(1,5):
        state=supervision.add_round(state,round_fact(index))
    state=supervision.cover(state,supervision.request(state,automatic=True),source_step=5)
    for index in range(6,10):
        state=supervision.add_round(state,round_fact(index))
    assert supervision.due(state)['automatic_allowed']


@pytest.mark.parametrize('current',[None,.32])
def test_request_freezes_retained_proved_fraction_when_current_observation_is_unknown_or_lower(current):
    state=supervision.observe(supervision.empty(),.66)
    status=supervision.due(state,budget_fraction=current)
    assert status['automatic_allowed'] and status['budget_fraction']==.66
    assert status['current_budget_fraction']==current
    frozen=supervision.request(state,budget_fraction=current,automatic=True)
    assert frozen['budget_fraction']==.66
    state=supervision.cover(state,frozen,source_step=1)
    assert set(state['covered_checkpoints'])=={.3,.65}
    assert not supervision.due(state,budget_fraction=current)['due']


def test_review_cooldown_uses_produced_completion_consumption_and_merges_own_checkpoints():
    state=supervision.observe(ledger(4),.31)
    frozen=supervision.request(state,budget_fraction=.31,automatic=True)
    state=supervision.cover(state,frozen,source_step=5,completion_fraction=.66)
    assert state['last_review_fraction']==.66
    assert set(state['covered_checkpoints'])=={.3,.65}
    assert not supervision.due(state)['due']
    for index in range(6,10):
        state=supervision.add_round(state,round_fact(index))
    status=supervision.due(state,budget_fraction=.70)
    assert status['due'] and status['cooldown'] and not status['automatic_allowed']
    assert supervision.due(state,budget_fraction=.77)['automatic_allowed']


def test_review_completion_does_not_cover_checkpoint_for_newer_rounds():
    state=supervision.observe(ledger(4),.31)
    frozen=supervision.request(state,budget_fraction=.31,automatic=True)
    state=supervision.add_round(state,round_fact(5))
    state=supervision.cover(state,frozen,source_step=6,completion_fraction=.66)
    assert state['covered_rounds']==4 and state['total_rounds']==5
    assert state['covered_checkpoints']==[.3]
    assert 'budget_checkpoint:0.65' in supervision.due(state)['reasons']
    assert state['last_review_fraction']==.66


def test_explicit_unknown_completion_baseline_does_not_reuse_task_start_as_known_completion():
    state=supervision.observe(ledger(4),.31)
    frozen=supervision.request(state,budget_fraction=.31,automatic=True)
    state=supervision.cover(state,frozen,source_step=5,completion_fraction=None)
    assert state['max_budget_fraction']==.31 and state['last_review_fraction'] is None
    for index in range(6,10):
        state=supervision.add_round(state,round_fact(index))
    assert supervision.due(state,budget_fraction=.9)['cooldown']


def test_actual_e_publication_retains_owner_produced_budget_trigger_without_a_request(tmp_path,monkeypatch):
    """The existing publisher samples its accepted head once, before pure folding."""
    from types import SimpleNamespace
    from scidiscovery.research_loop import accepted_budget, accepted_prefix, budget, contracts as c, originals
    root=tmp_path/'instance'
    immutable_base=dict(goal_id='goal',max_steps=10)
    settings_ref=dict(path=str(root/'settings.json'))
    origin=dict(goal_id='goal',settings_ref=settings_ref)
    selected=dict(supervision_policy=supervision.policy(),
        supervision_budget_basis=dict(max_tokens=1000,max_seconds=100))
    summary=accepted_search.empty();summary['node_count']=1
    prefix=dict(branch=origin,count=1,cutoff=1,tip=None)
    produced=[]
    reported=[660,100]
    current_head=None
    def usage(path,*,head):
        assert head is current_head
        produced.append(('usage',head))
        return dict(accounted_tokens=reported.pop(0),observed_tokens=0,unknown_step_count=1)
    def snapshot(path,base,events):
        assert base is immutable_base and events.state is current_head
        produced.append(('clock',events.state))
        return dict(consumed_seconds=1)
    monkeypatch.setattr(accepted_budget,'usage',usage)
    monkeypatch.setattr(budget,'_snapshot',snapshot)
    monkeypatch.setattr(accepted_budget,'search_row',lambda *args,**kwargs:None)
    monkeypatch.setattr(accepted_prefix,'navigation',lambda *args:None)
    monkeypatch.setattr(accepted_prefix,'for_job',lambda job:prefix)
    monkeypatch.setattr(originals,'identity_key',lambda identity:'branch')
    monkeypatch.setattr(accepted_budget,'read',lambda *args,**kwargs:pytest.fail('Publisher must reuse its produced accepted head'))
    for step in (2,3):
        job=dict(step=step,role='E',handoff_protocol=33,
            evidence_return=dict(step=1,role='A'),direct_refs=[],search=None)
        c.write(root/'jobs'/f'{step:03d}.json',job)
        current_head=dict(prefix=prefix,settings_ref=settings_ref,search=summary,
            current_job=dict(step=step,role='E',status='terminal',job_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
        inventory=SimpleNamespace(base=immutable_base,policy=selected,head=current_head,delivery=None)
        seal=dict(observations=None,materials=[],selected_inputs=[],
            result_ref=dict(path='private/seal'),raw_ref=dict(path='private/science'),
            control_outcome=dict(search_status='applied',selection_status='applied',decision_status='applied'))
        payload=dict(summary='Exact bounded evidence response',scientific_result='The required source remains missing.',limitations=['Missing original'])
        (root/'native-evidence'/f'{step:03d}').mkdir(parents=True)
        published=accepted_prefix.prepare_node(root,job,payload,seal,inventory=inventory)
        summary=published['search_delta']['summary']
        assert 'supervision_request' not in job
        assert summary['supervision']['max_budget_fraction']==.66
        assert set(supervision.due(summary)['reasons'])=={'budget_checkpoint:0.3','budget_checkpoint:0.65'}
        prefix=published['prefix']
    assert [kind for kind,_ in produced]==['usage','clock','usage','clock']


def test_actual_a_publication_keeps_unknown_completion_separate_from_proved_trigger_lower_bound(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import accepted_budget, accepted_prefix, budget, contracts as c, originals
    root=tmp_path/'instance'
    immutable_base=dict(goal_id='goal',max_steps=10)
    settings_ref=dict(path=str(root/'settings.json'))
    origin=dict(goal_id='goal',settings_ref=settings_ref)
    selected=dict(supervision_policy=supervision.policy(),
        supervision_budget_basis=dict(max_tokens=1000,max_seconds=100))
    summary=accepted_search.empty()
    prefix=dict(branch=origin,count=0,cutoff=0,tip=None)
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
    for step,role in ((1,'A'),(2,'E')):
        job=dict(step=step,role=role,handoff_protocol=33,direct_refs=[],search=None)
        payload=dict(summary='A bounded scientific judgment',scientific_result='Retain the scientific direction with the stated gaps.',limitations=[])
        if role=='A':
            job['supervision_request']=supervision.request(summary,budget_fraction=.31,automatic=True)
            payload.update(next_action='continue_w',supervision_result='continue_existing')
        else:
            job['evidence_return']=dict(step=1,role='A')
        c.write(root/'jobs'/f'{step:03d}.json',job)
        current_head=dict(prefix=prefix,settings_ref=settings_ref,search=summary,
            current_job=dict(step=step,role=role,status='terminal',job_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
        inventory=SimpleNamespace(base=immutable_base,policy=selected,head=current_head,delivery=None)
        seal=dict(observations=None,materials=[],selected_inputs=[],
            result_ref=dict(path='private/seal'),raw_ref=dict(path='private/science'),
            control_outcome=dict(search_status='applied',selection_status='applied',decision_status='applied'))
        (root/'native-evidence'/f'{step:03d}').mkdir(parents=True)
        published=accepted_prefix.prepare_node(root,job,payload,seal,inventory=inventory)
        summary=published['search_delta']['summary'];prefix=published['prefix']
        assert summary['supervision']['last_review_fraction'] is None
        assert summary['supervision']['automatic_reviews']==1
    # The extra .12 is a later original-bill reconciliation, not evidence of
    # consumption after A completion. New rounds remain due without a spacing
    # grant until a meaningful review establishes a complete new baseline.
    assert summary['supervision']['max_budget_fraction']==.43
    state=summary['supervision']
    for index in range(3,7):
        state=supervision.add_round(state,round_fact(index))
    status=supervision.due(state,budget_fraction=.43)
    assert status['due'] and status['cooldown'] and not status['automatic_allowed']
    assert completions==[None,.43]
    assert samples==['usage','clock','usage','clock']


def reduced(payload, calls, *, previous=None, step=2, role='W', request=None, purpose=None):
    """Exercise the pure accepted reducer against already produced host values."""
    summary=accepted_search.empty() if previous is None else copy.deepcopy(previous['summary'])
    summary['node_count']+=1
    bindings={'h001.m1':dict(source_step=1,source_role='H',source_ref={'path':'private/producer-seal'})}
    delta=accepted_search._Delta(summary,lambda name:(previous or {}).get('rows',{}).get(name))
    job=dict(step=step,role=role,handoff_protocol=33,purpose=purpose)
    if request is not None:
        job['supervision_request']=request
    accepted_search._supervision(delta,job,payload,
        dict(observations={'path':'private/observation'},raw_ref={'path':'private/scientific-original'}),
        dict(managed_calls=calls),origin=dict(goal_id='private-origin'),bindings=bindings,
        valid=purpose not in {'worker_consultation','formal_review'},policy={})
    rows=dict((previous or {}).get('rows',{}));rows.update(delta.rows)
    return dict(summary=delta.summary,rows=rows)


def call(identity, status='known_success', mechanism='h001.m1', original=None):
    return dict(call_id=identity,status=status,accepted=dict(path=original or f'private/{identity}/accepted.json',size_bytes=19),
        mechanism_binding=dict(mechanism_ref=mechanism,source_step=1,source_role='H',source_ref={'path':'private/producer-seal'}))


def declaration(*ids, mechanism='h001.m1'):
    return dict(mechanism=mechanism,question='A single scientific batch?',call_ids=list(ids),finding='Declared scientific finding.')


def test_new_original_batch_not_raw_call_count_and_duplicate_round_is_diagnostic():
    result=reduced(dict(exploration_rounds=[declaration('a','b'),declaration('a')]),[call('a'),call('b','known_failed')])
    state=result['summary']['supervision']
    assert state['total_rounds']==1
    fact=result['rows'][accepted_search.key('round','w002.r1')]
    assert fact['call_ids']==['a','b'] and fact['evidence'][1]['status']=='known_failed'
    assert state['diagnostics'][0]['reason']=='original_already_counted'
    assert fact['pointer']=='/exploration_rounds/0'
    following=reduced(dict(exploration_rounds=[declaration('c')]),[call('c')],previous=result,step=3)
    assert following['summary']['supervision']['consecutive_rounds']==2
    assert supervision.project(following['summary'])['direction']=='h001.m1'


@pytest.mark.parametrize('declarations,calls,reason',[
    ([declaration('unknown')],[call('unknown','unknown')],'original_outcome_unknown'),
    ([declaration('a')],[call('a',mechanism='h999.m1')],'exact_direction_binding_mismatch'),
    ([declaration('absent')],[call('a')],'ambiguous_or_missing_current_w_call'),
    ([declaration('a')],[call('a'),call('a')],'ambiguous_or_missing_current_w_call'),
    ([dict(declaration('a'),finding=' ')],[call('a')],'invalid_scientific_round_declaration'),
])
def test_unknown_unbound_ambiguous_and_no_finding_never_count(declarations,calls,reason):
    result=reduced(dict(exploration_rounds=declarations),calls)
    assert result['summary']['supervision']['total_rounds']==0
    assert result['summary']['supervision']['diagnostics'][0]['reason']==reason


def test_omitted_rounds_and_reused_original_never_invent_round():
    result=reduced({},[call('a')])
    assert result['summary']['supervision']['total_rounds']==0
    first=reduced(dict(exploration_rounds=[declaration('a')]),[call('a')])
    second=reduced(dict(exploration_rounds=[declaration('other')]),[call('other',original='private/a/accepted.json')],previous=first,step=3)
    assert second['summary']['supervision']['total_rounds']==1
    assert second['summary']['supervision']['diagnostics'][0]['reason']=='original_already_counted'


def test_only_meaningful_ordinary_a_covers_its_frozen_original():
    first=reduced(dict(exploration_rounds=[declaration('a')]),[call('a')])
    request=supervision.request(first['summary'],budget_fraction=.31,automatic=True)
    payload=dict(supervision_result='continue_existing',scientific_result='Evidence supports retaining the direction.',next_action='continue_w')
    for purpose in ('worker_consultation','formal_review'):
        observed=reduced(payload,[],previous=first,step=4,role='A',request=request,purpose=purpose)
        assert observed['summary']['supervision']['covered_rounds']==0
    invalid=reduced(dict(payload,scientific_result=' '),[],previous=first,step=4,role='A',request=request)
    assert invalid['summary']['supervision']['covered_rounds']==0
    newer=reduced(dict(exploration_rounds=[declaration('b')]),[call('b')],previous=first,step=3)
    covered=reduced(payload,[],previous=newer,step=4,role='A',request=request)
    assert covered['summary']['supervision']['covered_rounds']==1
    assert covered['summary']['supervision']['total_rounds']==2
    assert covered['summary']['supervision']['consecutive_rounds']==1
    forged=copy.deepcopy(request);forged['cutoff']['source_step']=999
    rejected=reduced(payload,[],previous=newer,step=4,role='A',request=forged)
    assert rejected['summary']['supervision']['covered_rounds']==0


def test_projection_is_bounded_and_contains_no_private_original_bindings():
    result=reduced(dict(exploration_rounds=[declaration('a')]),[call('a')])
    value=supervision.project(result['summary'])
    assert 'private' not in json.dumps(value)
    assert value['recent_rounds'][0]['pointer']=='/exploration_rounds/0'
    value=supervision.project(ledger(100))
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
