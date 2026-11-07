"""Worker declarations and consultation handoffs reuse accepted scientific facts."""
import copy

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import accepted_search, breadth, correction, decision, role_share, search
from scidiscovery.research_loop.scientific_intent import resolve_intent
from scidiscovery.research_loop.protocol import CURRENT


ORIGIN = dict(goal_id='goal')
P, Q = 'h001.m1', 'w002.m1'


def job(step, role, parents=(), *, focus=None, purpose=None):
    return dict(step=step,role=role,handoff_protocol=CURRENT,purpose=purpose,
        direct_refs=[dict(path=f'inputs/parents/{parent:03d}/conclusion.json') for parent in parents],
        search=dict(focus=focus,allocation=None,breadth=None))


def plan(label, *, existing=None):
    return dict(commitments=[dict(label=label,minimal_test='An observation that distinguishes the mechanism',existing_ref=existing)],
        focus=dict(index=1,action='improve'))


def append(summary, facts, task, payload, *, focus=None, usage=None):
    seal=dict(control_outcome=dict(search_status='applied',selection_status='applied',decision_status='applied',
        resolved_focus=focus,resolved_allocation=None,diagnostics=[]))
    published=accepted_search.prepare_node_from_facts('/instance',task,payload,seal,
        summary=summary,lookup=facts.get,observation=None,policy={},origin=ORIGIN,usage_lookup=usage)
    facts.update(copy.deepcopy(published['rows']))
    return published['summary']


def declared():
    facts={}
    summary=append(accepted_search.empty(),facts,job(1,'H'),dict(search_plan=plan('first')),
        focus=dict(mechanism_ref=P,action='improve'))
    return summary,facts


def test_worker_plan_appends_real_producer_and_revision_keeps_original_ordinal():
    summary,facts=declared()
    old=copy.deepcopy(summary['table']['mechanisms'][P])
    summary=append(summary,facts,job(2,'W',[1]),dict(stage_intent='handoff',search_plan=plan('second')),
        focus=dict(mechanism_ref=Q,action='improve'))
    mechanism=summary['table']['mechanisms'][Q]
    assert mechanism['source_step']==2 and mechanism['source_role']=='W'
    assert mechanism['origin_ref']==dict(goal_id='goal',source_step=2,source_role='W',mechanism_ref=Q)
    assert summary['table']['mechanisms'][P]==old
    summary=append(summary,facts,job(3,'W',[2]),dict(stage_intent='handoff',search_plan=plan('revised',existing=P)),
        focus=dict(mechanism_ref=P,action='improve'))
    revised=summary['table']['mechanisms'][P]
    assert revised['ordinal']==old['ordinal'] and revised['origin_ref']==old['origin_ref']
    assert revised['source_step']==3 and revised['source_role']=='W'
    assert summary['table']['ordinals']==[P,Q]


def test_worker_plan_interpreter_uses_worker_identity_without_h_approval():
    state=dict(mechanisms={Q:dict(status='pending')},authorized={Q},focus=None,
        order=dict(remaining=[dict(mechanism_ref=Q,planned_step=2)],seen_w=set(),allocation=None))
    result=resolve_intent(state,job(2,'W'),dict(stage_intent='handoff',search_plan=plan('second')))
    assert result['focus']==dict(mechanism_ref=Q,action='improve') and result['diagnostics']==[]


def test_whole_pending_chain_is_assessed_once_and_cost_includes_earlier_w():
    summary,facts=declared()
    summary=append(summary,facts,job(2,'W',[1],focus=dict(mechanism_ref=P,action='improve')),
        dict(stage_intent='handoff'),focus=dict(mechanism_ref=P,action='improve'))
    # A second registered branch can be authored by the first worker.
    summary=append(summary,facts,job(3,'W',[2],focus=dict(mechanism_ref=P,action='improve')),
        dict(stage_intent='handoff',search_plan=plan('alternative')),focus=dict(mechanism_ref='w003.m1',action='improve'))
    summary=append(summary,facts,job(4,'W',[3],focus=dict(mechanism_ref='w003.m1',action='improve')),
        dict(stage_intent='decision'),focus=dict(mechanism_ref='w003.m1',action='improve'))
    pending=summary['portfolio']['pending_w']
    assert pending['steps']==[2,3,4] and pending['all_branches']==[P,'w003.m1']
    assert summary['portfolio']['completed_cycle_count']==0
    meter=lambda step:dict(complete=True,reported_tokens=step*10)
    summary=append(summary,facts,job(6,'A',[4]),dict(next_action='continue_w',scientific_result='Independent judgment'),
        focus=dict(mechanism_ref=P,action='improve'),usage=meter)
    cycle=facts[accepted_search.key('cycle',2)]
    assert cycle['w_steps']==[2,3,4] and cycle['reported_tokens']==150
    assert summary['portfolio']['completed_cycle_count']==1 and summary['portfolio']['pending_w'] is None
    assert all(facts[accepted_search.key('node',step)]['assessed'] for step in (2,3,4))
    summary=append(summary,facts,job(7,'A',[4]),dict(next_action='finish'),usage=meter)
    assert summary['portfolio']['completed_cycle_count']==1


def test_a_consultation_preserves_pending_direction_and_recommendation():
    summary,facts=declared()
    summary=append(summary,facts,job(2,'W',[1],focus=dict(mechanism_ref=P,action='improve')),
        dict(stage_intent='review'),focus=dict(mechanism_ref=P,action='improve'))
    before=copy.deepcopy(summary)
    summary=append(summary,facts,job(3,'A',[2],purpose='worker_consultation'),dict(next_action='continue_w',
        scientific_result='Advice only',candidate_recommendation='unrelated',
        search_review=dict(dispositions=[dict(mechanism_ref=P,status='waived',reason='advice')],next_focus=dict(mechanism_ref=P,action='screen')),
        portfolio_review=dict(main_line=P,reason='advice',discriminating_work=None)),focus=None)
    for field in ('focus','allocation','last_science','latest_a_step','a_recommended','extra_grant','portfolio'):
        assert summary[field]==before[field]
    assert summary['table']['mechanisms']==before['table']['mechanisms']


def test_response_uses_exact_parent_not_adjacent_step():
    requester=job(3,'A')
    response=job(8,'W',[3,7])
    assert decision._completed(requester,dict(next_action='continue_w'),{8:(response,None,None,None)})
    assert not decision._completed(requester,dict(next_action='continue_w'),{4:(job(4,'W',[2]),None,None,None)})
    assert not decision._completed(requester,dict(next_action='revise_h'),{8:(job(8,'H',[3],purpose='worker_consultation'),None,None,None)})


def test_worker_consultation_remains_optional_under_actual_role_budget():
    assert not role_share._required(None,2,job(2,'W'),dict(stage_intent='review'),'A',None)
    assert role_share._required(None,2,job(2,'W'),dict(stage_intent='decision'),'A',None)


def test_correction_does_not_redirect_consultation(monkeypatch):
    monkeypatch.setattr(correction,'project',lambda *args:pytest.fail('Consultation must not consume correction state'))
    route=('W',[2,3],'Return advice',None,1,2)
    assert correction.effective_route(None,3,{},job(3,'A',[2],purpose='worker_consultation'),route)==(*route,None)


def test_initial_opportunity_reminder_does_not_require_a_grant():
    summary,facts=declared()
    summary=append(summary,facts,job(2,'W',[1]),dict(stage_intent='handoff',search_plan=plan('second')),
        focus=dict(mechanism_ref=Q,action='screen'))
    summary['table']['mechanisms'][P]['accepted_calls']=20
    state=dict(summary=summary,mechanisms=summary['table']['mechanisms'],combination_refs=[P,Q],
        extra_grant=None,opportunity_unknown=False,opportunity_counts={P:20,Q:0},
        _catalog=dict(prefix=dict(version=1,cutoff=2),max_ordinal=2))
    frozen=search._breadth(None,state,{},dict(mechanism_ref=P,action='screen'))
    assert frozen['version']==3 and frozen['grant'] is None
    decisions,available=breadth._availability(frozen,{},[],head=dict(search=summary),focus=dict(mechanism_ref=P,action='screen'))
    assert decisions[P]['reason'] is None and {row['mechanism_ref'] for row in available}=={P,Q}
    summary['opportunity_unknown_count']=1
    decisions,_=breadth._availability(frozen,{},[],head=dict(search=summary),focus=None)
    assert {row['reason'] for row in decisions.values()}=={'original_execution_unknown'}


def test_binding_keeps_actual_producer_and_rejects_changed_role():
    summary,facts=declared()
    summary=append(summary,facts,job(2,'W',[1]),dict(stage_intent='handoff',search_plan=plan('second')),
        focus=dict(mechanism_ref=Q,action='improve'))
    state=dict(summary=summary,mechanisms=summary['table']['mechanisms'],combination_refs=[Q],
        extra_grant=None,opportunity_unknown=False,opportunity_counts={Q:0},
        _catalog=dict(prefix=dict(version=1,cutoff=2),max_ordinal=2))
    frozen=search._breadth(None,state,{},dict(mechanism_ref=Q,action='improve'))
    row=frozen['mechanisms'][0];scope=dict(job_ref=dict(path='/instance/jobs/003.json'))
    binding={key:row[key] for key in ('mechanism_ref','source_step','source_ref','source_role')}
    binding.update(job_ref=scope['job_ref'],grant=None,catalog_binding={key:row[key] for key in ('ordinal','origin_ref','source_goal_id')})
    assert breadth.check_binding(frozen,binding,scope)==binding
    with pytest.raises(ValueError,match='changed its frozen model'):
        breadth.check_binding(frozen,dict(binding,source_role='H'),scope)


@pytest.mark.parametrize('role,purpose', [('W',None),('H','worker_consultation'),('A','worker_consultation')])
def test_refused_new_worker_retains_exact_reason_instead_of_routing_h(tmp_path,monkeypatch,role,purpose):
    from scidiscovery.research_loop import contracts as c, engineering, semantic
    monkeypatch.setattr(c,'read',lambda path:dict(control_outcome=dict(selection_status='applied')))
    monkeypatch.setattr(c,'settings',lambda *args:dict(search_policy={}))
    monkeypatch.setattr(engineering,'request',lambda *args:None)
    monkeypatch.setattr(decision,'reason',lambda *args:None)
    monkeypatch.setattr(search,'bind',lambda *args,**kwargs:dict())
    monkeypatch.setattr(search,'launch_reason',lambda *args:'search_repair_limit')
    route=('W',[2],'Continue science',None,1,2,None)
    with pytest.raises(semantic.ScientificAdmissionRefused) as refused:
        semantic.effective_route(tmp_path,3,dict(stage_intent='continue'),job(3,role,[2],purpose=purpose),route)
    assert refused.value.reason=='search_repair_limit' and refused.value.next_role=='W'


def observation(call, binding):
    return dict(mechanism_binding=copy.deepcopy(binding),accepted=dict(
        path=f'native-evidence/002/experiments/calls/{call}/accepted.json',
        snapshot=f'archive/{call}.json',retention='archived',size_bytes=10))


def test_screen_sources_reuse_same_observation_but_keep_distinct_contributions(monkeypatch):
    binding=dict(mechanism_ref=P,source_step=1,source_role='H',source_ref=dict(path='/instance/native-evidence/001/result-identity.json'))
    retained=observation('original',binding)
    previous=dict(step=2,role='W',origin_identity=ORIGIN,screen_observations=[retained])
    reused=dict(step=3,role='W',origin_identity=ORIGIN,screen_observations=[copy.deepcopy(retained)])
    state=dict(mechanisms={P:binding},assessment_workers=[previous,reused])
    monkeypatch.setattr(search.history,'direct_sources',lambda *args:pytest.fail('Accepted screen consumers must not reopen W originals'))
    assert search._mechanism_sources(None,job(4,'A',[3]),P,state,screen=True)==[previous]
    reused['screen_observations'].append(observation('different',binding))
    assert search._mechanism_sources(None,job(4,'A',[3]),P,state,screen=True)==[previous,reused]
    # A new declaration cannot qualify itself from another mechanism version.
    state['mechanisms'][P]=dict(binding,source_step=3,source_role='W')
    assert search._mechanism_sources(None,job(4,'A',[3]),P,state,screen=True)==[]
    del state['assessment_workers']
    with pytest.raises(ValueError,match='produced assessment Worker facts'):
        search._mechanism_sources(None,job(4,'A',[3]),P,state,screen=True)


def test_partial_delivery_and_blocked_findings_do_not_create_screen_observations():
    binding=dict(mechanism_ref=P,source_step=1,source_role='H',source_ref=dict(path='/instance/native-evidence/001/result-identity.json'))
    worker=job(2,'W',[1],focus=dict(mechanism_ref=P,action='screen'))
    worker['search']['breadth']=dict(mechanisms=[binding])
    retained=observation('original',binding)
    evidence=dict(mechanism_ref=P,accepted=retained['accepted'])
    blocked=dict(stage_intent='handoff',mechanism_findings=[dict(mechanism=P,basis='blocked',finding='The discriminator remains unavailable')])
    assert accepted_search._screen_observations(worker,blocked,None,dict(evidence=[evidence]))==[]
    assert accepted_search._screen_observations(worker,dict(stage_intent='handoff'),None,dict(evidence=[]))==[]
    qualified=accepted_search._screen_observations(worker,dict(stage_intent='handoff'),None,dict(evidence=[evidence]))
    assert qualified==[retained]


@pytest.mark.process_e2e
def test_formal_a_can_screen_the_earlier_worker_in_exact_handoff_chain(tmp_path,monkeypatch,execution_registry):
    from scidiscovery.research_loop import contracts as c, execution, native
    from test_breadth_science import prepare, h, worker, run, findings, a
    root=prepare(tmp_path,qualified_local=True,portfolio=True)
    h(root,labels=('P','Q'))
    p,q='h001.m1','h001.m2'

    def seal_worker(step,parents,mechanism,stage,*,declaration=None):
        task,config=worker(root,tmp_path,step,parents,qualified_local=True)
        result=run(root,config,mechanism,qualified_local=True)
        assert result['status']=='known_success'
        record=execution.record(root,step)
        assigned=[row['mechanism_ref'] for row in task['search']['coverage_obligation']]
        payload=dict(summary='Retained mechanical discriminator',scientific_result='A owns the scientific interpretation.',
            limitations=['A synthetic command does not establish scientific effectiveness'],implementation_selection=None,
            stage_intent=stage,mechanism_findings=findings(mechanism,blocked=[ref for ref in assigned if ref!=mechanism]))
        if declaration is not None:payload['search_plan']=declaration
        c.write(record/'science-output.json',payload)
        meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
        c.write(record/'usage.json',meter)
        intent=c.read(record/'native-intent.json')
        c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(root),
            raw_ref=c.file_ref(record/'science-output.json'),search_slice=search.report(root,task,intent,meter)))
        c.seal_result(root,step)
        return task,record

    # W2 supplies P's observation, then chooses Q for the next frozen W.
    _,first=seal_worker(2,[1],p,'handoff',declaration=dict(commitments=[dict(
        label='Q',existing_ref=q,minimal_test='Improve the independent Q discriminator')],focus=dict(index=1,action='improve')))
    successor,last=seal_worker(3,[2],q,'decision')
    assert successor['search']['focus']==dict(mechanism_ref=q,action='improve')
    before={path:path.read_bytes() for record in (first,last) for path in (record/'science-output.json',record/'result-identity.json')}
    monkeypatch.setattr(search,'_screen_refs',lambda *args,**kwargs:pytest.fail('A must reuse accepted observation facts'))
    a(root,4,[3],focus=q,mode='improve',dispositions=[dict(mechanism_ref=p,status='screened',
        reason='The earlier W supplies the exact discriminator and its limitations')])
    accepted=accepted_search.state(root,5)
    assert accepted['mechanisms'][p]['status']=='screened'
    assert accepted['portfolio']['completed_cycle_count']==1 and accepted['portfolio']['pending_w'] is None
    assert all(path.read_bytes()==value for path,value in before.items())


def test_local_and_inherited_screen_proof_share_exact_original_origin(monkeypatch):
    binding=dict(mechanism_ref=P,source_step=1,source_role='H',source_ref=dict(path='/instance/native-evidence/001/result-identity.json'))
    retained=observation('original',binding)
    local=dict(step=2,role='W',origin_identity=ORIGIN,screen_observations=[retained])
    explicit=copy.deepcopy(retained)
    explicit['accepted']['origin_identity']=copy.deepcopy(ORIGIN)
    inherited=dict(step=3,role='W',origin_identity=dict(goal_id='fork'),screen_observations=[explicit])
    originals=copy.deepcopy([local,inherited])
    state=dict(mechanisms={P:binding},assessment_workers=[local,inherited])
    monkeypatch.setattr(search.history,'direct_sources',lambda *args:pytest.fail('Screen proof normalization must not load originals'))
    assert search._mechanism_sources(None,job(4,'A',[3]),P,state,screen=True)==[local]
    assert [local,inherited]==originals
    # Matching call names/paths in a different original branch stay distinct.
    explicit['accepted']['origin_identity']=dict(goal_id='another-original')
    assert search._mechanism_sources(None,job(4,'A',[3]),P,state,screen=True)==[local,inherited]
    assert local==originals[0]
