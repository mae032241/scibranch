"""Explicit portable checks for small breadth-table snapshots and original facts.

No provider, solver, process launch or test-history enumeration is required.
"""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_search as accepted
from scidiscovery.research_loop import accepted_budget,accepted_prefix,breadth,candidate_nominations,contracts,history


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


def test_reauthor_exact_old_ref_preserves_calls_and_origin(facts):
    frozen=apply(facts, 1, 'H', plan('Original'))['summary']
    binding = dict(mechanism_ref='h001.m1', source_h=1, source_h_ref=dict(path='original'), job_ref=dict(path='job2'), grant=None)
    observation = dict(coverage=dict(status='complete'), candidates=[], managed_calls=[dict(call_id='call1', status='known_failed', mechanism_binding=binding)])
    apply(facts, 2, 'W', dict(_observation=observation), search=dict(focus=dict(mechanism_ref='h001.m1', action='screen')))
    assert facts['summary']['owed_count'] == 0
    apply(facts, 3, 'H', plan('Authored revision', 'h001.m1'))
    item = facts['summary']['table']['mechanisms']['h001.m1']
    assert item['original_h'] == 1 and item['source_h'] == 3
    assert item['accepted_calls'] == 1 and facts['summary']['owed_count'] == 0
    pointer=item['last_terminal']
    assert pointer['source_step']==2 and pointer['call_index']==0 and pointer['call_id']=='call1'
    assert pointer['observation_ref']==dict(path='host-history/observations/002.json',size_bytes=1)
    assert pointer['mechanism_binding']==binding
    assert pointer['mechanism_binding']['source_h']==1 and item['source_h']==3
    assert facts['summary']['mechanism_count'] == 1
    assert frozen['table']['mechanisms']['h001.m1']['source_h']==1
    assert frozen['table']['mechanisms']['h001.m1']['accepted_calls']==0
    assert not any(name.startswith(('search/mechanism/','search/ordinal/')) for name in facts['rows'])


def test_unknown_reconciliation_does_not_refund_consumed_opportunity(facts):
    apply(facts, 1, 'H', plan('Original'))
    binding = dict(mechanism_ref='h001.m1', source_h=1, source_h_ref=dict(path='original'), job_ref=dict(path='job2'), grant=None)
    observation = dict(coverage=dict(status='partial'), candidates=[], managed_calls=[dict(call_id='call1', status='unknown', mechanism_binding=binding)])
    apply(facts, 2, 'W', dict(_observation=observation), search=dict(focus=dict(mechanism_ref='h001.m1', action='screen')))
    before = facts['summary']['table']['mechanisms']['h001.m1']['accepted_calls']
    delta = accepted.prepare_recovery(summary=facts['summary'], lookup=facts['rows'].get, step=2, call_id='call1',
        resolved=dict(call_id='call1', status='known_failed', mechanism_binding=binding),
        recovery_ref=dict(path='execution-recovery/001.json',origin_identity=dict(goal_id='fixture')))
    assert delta['summary']['opportunity_unknown_count'] == 0
    assert delta['summary']['owed_count'] == 0
    assert accepted.key('mechanism', 'h001.m1') not in delta['rows']
    assert facts['summary']['table']['mechanisms']['h001.m1']['accepted_calls'] == before == 1
    pointer=delta['summary']['table']['mechanisms']['h001.m1']['last_terminal']
    assert pointer['call_id']=='call1' and pointer['recovery_ref']['path']=='execution-recovery/001.json'
    assert pointer['mechanism_binding']==binding
    with pytest.raises(ValueError, match='original scientific call binding'):
        accepted.prepare_recovery(summary=facts['summary'], lookup=facts['rows'].get, step=2, call_id='call1',
            resolved=dict(call_id='call1', status='known_failed', mechanism_binding=dict(binding, source_h=9)),
            recovery_ref=dict(path='execution-recovery/002.json'))


def test_late_old_recovery_keeps_newer_terminal_binding(facts):
    apply(facts,1,'H',plan('Original'))
    old=dict(mechanism_ref='h001.m1',source_h=1,source_h_ref=dict(path='/fixture/1-marker.json'),job_ref=dict(path='job2'),grant=None)
    apply(facts,2,'W',dict(_observation=dict(coverage=dict(status='partial'),candidates=[],
        managed_calls=[dict(call_id='old',status='unknown',mechanism_binding=old)])),
        search=dict(focus=dict(mechanism_ref='h001.m1',action='screen')))
    apply(facts,3,'H',plan('Current revision','h001.m1'))
    current=dict(old,source_h=3,source_h_ref=dict(path='/fixture/3-marker.json'),job_ref=dict(path='job4'))
    apply(facts,4,'W',dict(_observation=dict(coverage=dict(status='complete'),candidates=[],
        managed_calls=[dict(call_id='new',status='known_success',mechanism_binding=current)])),
        search=dict(focus=dict(mechanism_ref='h001.m1',action='screen')))
    delta=accepted.prepare_recovery(summary=facts['summary'],lookup=facts['rows'].get,step=2,call_id='old',
        resolved=dict(call_id='old',status='known_failed',mechanism_binding=old),recovery_ref=dict(path='execution-recovery/001.json'))
    item=delta['summary']['table']['mechanisms']['h001.m1']
    assert item['last_terminal']['source_step']==4
    assert item['last_terminal']['mechanism_binding']==current
    assert item['accepted_calls']==2 and delta['summary']['opportunity_unknown_count']==0


def test_remaining_list_reopens_without_mechanism_files_or_linked_queue(facts):
    for step in (1, 2, 3):apply(facts, step, 'H', plan(str(step)))
    facts['reads'].clear()
    apply(facts, 4, 'A', dict(search_review=dict(dispositions=[dict(mechanism_ref='h002.m1', status='waived', reason='Authored negative')]), next_action='finish'))
    assert set(facts['reads']) == {accepted.key('node', 4)}
    assert [row['mechanism_ref'] for row in facts['summary']['table']['remaining']]==['h001.m1','h003.m1']
    apply(facts, 5, 'H', plan('Reopen exact old ref', 'h002.m1'))
    assert [row['mechanism_ref'] for row in facts['summary']['table']['remaining']]==['h001.m1','h003.m1','h002.m1']
    assert facts['summary']['table']['ordinals']==['h001.m1','h002.m1','h003.m1']
    assert not any('next_remaining' in item or 'previous_remaining' in item for item in facts['summary']['table']['mechanisms'].values())
    assert facts['summary']['remaining_count'] == 3


def test_duplicate_original_requires_writer_reconciliation(facts):
    apply(facts, 1, 'E', {})
    with pytest.raises(ValueError, match='already accepted'):
        apply(facts, 1, 'E', {})


def test_frozen_table_carries_pending_w_into_child_without_parent_rows(facts):
    apply(facts,1,'H',plan('Original'))
    binding=dict(mechanism_ref='h001.m1',source_h=1,source_h_ref=dict(path='/fixture/1-marker.json'),job_ref=dict(path='job2'),grant=None)
    observation=dict(coverage=dict(status='complete'),candidates=[],managed_calls=[dict(call_id='call1',status='known_success',mechanism_binding=binding)])
    apply(facts,2,'W',dict(stage_intent='review',_observation=observation),
        search=dict(focus=dict(mechanism_ref='h001.m1',action='screen'),allocation=dict(mechanism_ref='h001.m1',planned_step=1)))
    frozen=copy.deepcopy(facts['summary'])
    child=dict(summary=copy.deepcopy(frozen),rows={},reads=[])
    apply(child,3,'A',dict(next_action='continue_w',scientific_result='Assess the frozen exact W',
        search_review=dict(dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='Retained observation')]),
        portfolio_review=dict(main_line='h001.m1',reason='Explicit comparison',discriminating_work=None)),
        direct_refs=[dict(path='inputs/parents/002/conclusion.json')])
    assert child['summary']['portfolio']['completed_cycle_count']==1
    assert child['summary']['portfolio']['pending_w'] is None
    assert child['summary']['last_w']==2
    assert child['summary']['table']['mechanisms']['h001.m1']['accepted_calls']==1
    assert child['summary']['table']['mechanisms']['h001.m1']['last_participated_cycle']==1
    assert frozen['portfolio']['pending_w']['step']==2
    assert frozen['portfolio']['completed_cycle_count']==0


def test_frozen_table_carries_active_work_and_exact_response_without_parent_rows(facts):
    apply(facts,1,'H',plan('Original'))
    apply(facts,2,'A',dict(next_action='extract',scientific_result='An exact missing question',
        portfolio_review=dict(main_line='h001.m1',reason='Resolve the target input',
            discriminating_work=dict(mechanism_refs=['h001.m1'],question='Which exact target input changes this choice?',
                change_criterion='A reliable target contradiction changes the choice',status='unconsumed narrative extension',next_action='extract'))))
    child=dict(summary=copy.deepcopy(facts['summary']),rows={},reads=[])
    apply(child,3,'E',dict(scientific_result='The exact requested evidence'),
        direct_refs=[dict(path='inputs/parents/002/conclusion.json')],evidence_return=dict(step=2,role='A'))
    work=child['summary']['current_facts'][accepted.key('work','A002-D001')]
    assert work['change_criterion']=='A reliable target contradiction changes the choice'
    assert work['status']=='executed'
    assert work['executions']==[dict(source_a=2,response=dict(source_step=3,role='E',pointer='/scientific_result'))]
    apply(child,4,'A',dict(next_action='finish',scientific_result='The bounded question is answered',
        portfolio_review=dict(main_line='h001.m1',reason='Retain the limitations',discriminating_work=None,
            work_updates=[dict(work_ref='A002-D001',action='withdraw',reason='The current question is answered')])) )
    assert accepted.key('work','A002-D001') not in child['summary']['current_facts']


def test_candidate_nominations_keep_omitted_mechanism_advice_and_latest_extras(facts):
    declaration=plan('P')
    declaration['search_plan']['commitments'].append(dict(label='Q',minimal_test='Another comparison',existing_ref=None))
    apply(facts,1,'H',declaration)
    scope=[dict(mechanism_ref=ref) for ref in ('h001.m1','h001.m2')]
    first=dict(representatives=[dict(mechanism_ref='h001.m1',candidate='first',reason='Original scientific representative')],
        extras=[dict(candidate='old-extra',reason='Original extra')],
        gaps=[dict(mechanism_ref='h001.m2',reason='Target input missing'),dict(mechanism_ref='unbound-advice',reason='Optional unknown reference')])
    apply(facts,2,'W',dict(candidate_nominations=first),candidate_nomination_mechanisms=scope)
    second=dict(representatives=[dict(mechanism_ref='h001.m2',candidate='second',reason='New independent representative'),
        dict(mechanism_ref='h001.m1',candidate=dict(call_id='unresolved',artifact='model'),reason='Unresolved optional slot')],
        extras=[dict(candidate='new-extra',reason='Latest extra')],gaps=[])
    apply(facts,3,'W',dict(candidate_nominations=second),candidate_nomination_mechanisms=scope)
    projected=candidate_nominations.table_project(facts['summary'])
    reference=candidate_nominations.project([dict(step=2,role='W',candidate_nominations=first),
        dict(step=3,role='W',candidate_nominations=second)],dict(mechanisms=facts['summary']['table']['mechanisms']))
    assert projected==reference
    assert projected['mechanism_sources']=={'h001.m1':2,'h001.m2':3,'unbound-advice':2}
    assert 'unbound-advice' not in facts['summary']['table']['mechanisms']


def test_fork_snapshot_keeps_nomination_preview_without_private_origin_fields(facts):
    apply(facts,1,'H',plan('Original'))
    apply(facts,2,'W',dict(candidate_nominations=dict(
        representatives=[dict(mechanism_ref='h001.m1',candidate='original-id',reason='Scientific selection')],
        extras=[dict(candidate='extra-id',reason='Scientific extra')],gaps=[])),
        candidate_nomination_mechanisms=[dict(mechanism_ref='h001.m1')])
    frozen=copy.deepcopy(facts['summary'])
    child=dict(summary=copy.deepcopy(frozen),rows={},reads=[])
    apply(child,3,'W',dict(candidate_nominations='Unconsumable optional narrative'),
        candidate_nomination_mechanisms=[dict(mechanism_ref='h001.m1')])
    assert candidate_nominations.table_project(child['summary'])==candidate_nominations.table_project(frozen)
    public=candidate_nominations.source_table_projection(child['summary'],lambda identity:{'original-id':'C001','extra-id':'C002'}.get(identity,identity))
    assert public['representatives'][0]['candidate_id']=='C001'
    assert public['extras'][0]['candidate_id']=='C002'
    assert public['representatives'][0]['original']=='inputs/history/nodes/fixture/002/science.json'
    assert '/fixture/settings.json' not in str(public) and 'origin_identity' not in str(public)


def test_older_judgment_correction_reduces_only_accepted_suffix_in_original_order(facts):
    declaration=plan('P')
    declaration['search_plan']['commitments'].append(dict(label='Q',minimal_test='Another comparison',existing_ref=None))
    apply(facts,1,'H',declaration)
    def judgment(step,main,root=None):
        correction={} if root is None else dict(author_correction=dict(root=dict(step=root)))
        apply(facts,step,'A',dict(next_action='continue_w',scientific_result='Retained scientific judgment',
            portfolio_review=dict(main_line=main,reason='Compare original observations',discriminating_work=None)),**correction)
    for step in (2,3,4):judgment(step,'h001.m1')
    assert facts['summary']['portfolio']['mainline_streak']==3
    judgment(5,'h001.m2',root=2)
    portfolio=facts['summary']['portfolio']
    assert portfolio['main_line']=='h001.m1' and portfolio['mainline_streak']==2
    assert portfolio['mainline_source']==dict(source_step=4,pointer='/portfolio_review/main_line')
    judgment(6,'h001.m2',root=4)
    assert facts['summary']['portfolio']['mainline_streak']==1
    judgment(7,'h001.m2',root=3)
    portfolio=facts['summary']['portfolio']
    assert portfolio['mainline_streak']==3 and portfolio['main_line']=='h001.m2'
    assert portfolio['mainline_source']==dict(source_step=6,pointer='/portfolio_review/main_line')


def test_portfolio_counts_unserved_from_current_small_table(facts, monkeypatch):
    monkeypatch.setattr(contracts,'settings',lambda root,job=None:dict(search_policy=dict(initial_calls=1),portfolio_policy=dict(review_after_cycles=1)))
    declaration=plan('P')
    declaration['search_plan']['commitments'] += [dict(label=label,minimal_test='Exact authored test',existing_ref=None) for label in ('Q','R','S')]
    apply(facts,1,'H',declaration)
    refs=('h001.m1','h001.m2')
    calls=[dict(call_id=ref,status='known_success',mechanism_binding=dict(mechanism_ref=ref,source_h=1,
        source_h_ref=dict(path='original'),job_ref=dict(path='job2'),grant=None)) for ref in refs]
    apply(facts,2,'W',dict(stage_intent='review',_observation=dict(coverage=dict(status='complete'),candidates=[],managed_calls=calls)),
        search=dict(focus=dict(mechanism_ref=refs[0],action='screen'),breadth=True))
    facts['reads'].clear()
    apply(facts,3,'A',dict(next_action='continue_w',scientific_result='Authored comparison',
        search_review=dict(dispositions=[dict(mechanism_ref=ref,status='screened',reason='Retained original observation') for ref in refs]),
        portfolio_review=dict(main_line=refs[0],discriminating_work=None,reason='Explicit comparison')),
        direct_refs=[dict(path='inputs/parents/002/conclusion.json')])
    assert facts['summary']['portfolio']['completed_cycle_count']==1
    assert facts['summary']['portfolio']['overdue_count']==2
    assert not any(name.startswith(('search/mechanism/','search/ordinal/','search/due/')) for name in facts['reads'])
    assert facts['summary']['portfolio']['token_advice']['excluded_sample_count']==1


def test_omitted_number_uses_frozen_point_catalog_and_preserves_initial_fairness(facts,monkeypatch,tmp_path):
    declaration=plan('P')
    declaration['search_plan']['commitments'].append(dict(label='Q',minimal_test='Other original test',existing_ref=None))
    apply(facts,1,'H',declaration)
    apply(facts,2,'H',plan('Current P','h001.m1'))
    prefix=dict(version=1,branch=dict(goal_id='fixture'),cutoff=2,count=2)
    head=dict(prefix=prefix,search=facts['summary'])
    monkeypatch.setattr(accepted_budget,'read',lambda root:head)
    monkeypatch.setattr(accepted_budget,'search_row',lambda root,key,head=None:facts['rows'].get(key))
    def member(root,frozen,step,origin=None):
        assert frozen==prefix and origin['goal_id']=='fixture'
        return dict(origin_identity=origin,seal_ref=dict(path=f'/fixture/{step}-marker.json'))
    monkeypatch.setattr(accepted_prefix,'member',member)
    p=facts['summary']['table']['mechanisms']['h001.m1']
    value=dict(version=2,initial_calls=1,mechanisms=[dict(mechanism_ref='h001.m1',label=p['label'],
        minimal_test=p['minimal_test'],status=p['status'],source_h=2,source_h_ref=dict(path='/fixture/2-marker.json'),
        ordinal=p['ordinal'],origin_ref=p['origin_ref'],source_goal_id='fixture')],
        baseline=[dict(mechanism_ref='h001.m1',accepted_count=0)],grant=None,prior_unknown=True,
        outside_initial_owed=1,catalog=dict(prefix=prefix,max_ordinal=2))
    path=tmp_path/'instance/jobs/003.json'
    path.parent.mkdir(parents=True)
    contracts.write(path,dict(step=3,role='W',history=dict(prefix=prefix),
        search=dict(focus=dict(mechanism_ref='h001.m1',action='screen'),breadth=value)))
    scope=dict(step=3,role='W',job_ref=contracts.record_ref(path))
    head['search']['opportunity_unknown_count']=1
    with pytest.raises(breadth.OpportunityRequired,match='original unknown execution'):
        breadth.admit(value,'2',scope,[])
    head['search']['opportunity_unknown_count']=0
    binding=breadth.admit(value,'2',scope,[])
    assert binding['mechanism_ref']=='h001.m2'
    assert binding['catalog_binding']['ordinal']==2
    assert binding['catalog_binding']['origin_ref']['goal_id']=='fixture'
    assert breadth.check_binding(value,binding,scope)==binding
    with pytest.raises(breadth.OpportunityRequired,match='Other H mechanisms'):
        breadth.admit(value,'2',scope,[dict(mechanism_binding=binding)])
    # The old non-screen scope permits only its focus and has no other scoped
    # initial opportunity. A global catalog must not widen or tighten that rule.
    contracts.write(path,dict(step=3,role='W',history=dict(prefix=prefix),
        search=dict(focus=dict(mechanism_ref='h001.m1',action='improve'),breadth=value)))
    with pytest.raises(breadth.OpportunityRequired,match='frozen focused mechanism'):
        breadth.admit(value,'2',scope,[])
    focused=breadth.admit(value,'1',scope,[])
    assert breadth.admit(value,'1',scope,[dict(mechanism_binding=focused)])['mechanism_ref']=='h001.m1'
    monkeypatch.setattr(accepted_budget,'read',lambda root:pytest.fail('Accepted binding reopened historical authority'))
    assert breadth.check_binding(value,binding,scope)==binding
    foreign=tmp_path/'foreign/jobs/003.json'
    foreign.parent.mkdir(parents=True)
    contracts.write(foreign,{})
    with pytest.raises(ValueError,match='escaped frozen combination'):
        breadth.check_binding(value,binding,dict(scope,job_ref=contracts.record_ref(foreign)))
    foreign_catalog=dict(value['catalog'],prefix=dict(prefix,branch=dict(goal_id='other-fork')))
    with pytest.raises(ValueError,match='exact frozen accepted prefix'):
        accepted.catalog_item(tmp_path,foreign_catalog,'2',head=head)
