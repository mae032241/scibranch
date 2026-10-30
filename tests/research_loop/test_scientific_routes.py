"""Real current jobs and seals, no model or domain submission."""
import copy
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import contracts as c,controller,correction,decision,execution,experiments,native,search,semantic
from execution_fixtures import execution_registry
from test_breadth_science import findings,run


def scene(tmp_path,*,portfolio=False):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Discriminate competing mechanisms while retaining unfinished work.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-call-a-model'],max_tokens=1000,max_seconds=60,max_steps=24,
        model='no-model-fixture',reasoning_effort='high',search_policy=search.DEFAULT_POLICY,
        portfolio_policy=dict(version=2,review_after_cycles=100) if portfolio else None)
    return root


def plan(focus=None):
    return dict(scope='Competing target mechanisms',commitments=[dict(label=label,minimal_test='A bounded independent discrimination',existing_ref=None) for label in ('P','Q')],focus=focus or dict(index=None,action='screen'))


def review(ref):
    return dict(dispositions=[dict(mechanism_ref=ref,status='screened',reason='Assess the exact W finding with its retained observation and limitations')],
        next_focus=dict(mechanism_ref=None,action='screen'))


def observe(root,step,*labels):
    record=execution.record(root,step)
    job=c.read(root/'jobs'/f'{step:03d}.json')
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),search_slice=search.accept(root,job)))
    path=c.workspace(root,step)/'work/experiment-config.json'
    experiments.configure(root,step,'local_execution:command',dict(run_argv=[sys.executable]),path)
    config=c.read(path)
    original=copy.deepcopy(config)
    assert set(config)=={'worker_view_version','step','attempt'}
    for label in labels:
        assert run(root,config,label,client_path=path)['status']=='known_success'
    assert config==original and c.read(path)==original


def seal(root,step,role,parents,*,action='wait',choice=None,question='What target observation would change this choice?',evidence_return=None,stage_intent='review',mechanism_findings=None):
    job=c.build_job(root,step,role,parents,instruction='Bounded original scientific question',evidence_return=evidence_return)
    payload=dict(summary='Bounded scientific finding',scientific_result='The original limitations remain explicit.',limitations=['No scientific efficacy was tested.'],implementation_selection=None)
    if role in {'H','A'}:
        payload.update(next_action=action,route_reason='Prioritize a decision-changing target discrimination')
        payload['search_plan' if role=='H' else 'search_review']=choice
    if role=='W':
        payload.update(stage_intent=stage_intent,mechanism_findings=mechanism_findings)
        task=c.read(c.workspace(root,step)/'science-task.json')
        if task.get('candidate_nomination_mechanisms'):
            payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
                reason='This no-model observation fixture declares no scientifically suitable candidate.')
                for row in task['candidate_nomination_mechanisms'] if row['status']!='waived'])
    if role=='A':payload.update(candidate_recommendation=None,progress_assessment=None,portfolio_review=None,
        decision_request=dict(decision='mechanism',question=question,change_criterion='A conflicting qualified target observation') if action in decision.ACTIONS else None)
    record=execution.record(root,step)
    if not (record/'native-intent.json').exists():
        c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'science-output.json',payload)
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,step)
    return job,payload,c.read(record/'result-identity.json')


def prefix(root):
    seal(root,1,'H',[],action='experiment',choice=plan())
    first=c.build_job(root,2,'W',[1],instruction='Bounded original scientific question')
    assert first['search']['focus']['mechanism_ref']=='h001.m1'
    observe(root,2,'h001.m1')
    _,_,receipt=seal(root,2,'W',[1],mechanism_findings=findings('h001.m1',blocked=('h001.m2',)))
    assert receipt['control_outcome']['worker_delivery']['status']=='partial'


def test_default_after_final_a_resolves_q_and_decision_key_uses_same_receipt(tmp_path):
    root=scene(tmp_path,portfolio=True);prefix(root)
    job,payload,receipt=seal(root,3,'A',[1,2],action='continue_w',choice=review('h001.m1'))
    control=receipt['control_outcome'];focus=control['resolved_focus']
    assert not control['diagnostics']
    assert focus==dict(mechanism_ref='h001.m2',action='screen')
    assert decision._key(job,payload,focus)[4]=='h001.m2'
    original=c.validate(c.workspace(root,3))
    route=controller._route(root,3,'A',original,job,1,2)
    route=correction.effective_route(root,3,original,job,route)
    route=semantic.effective_route(root,3,original,job,route)
    assert route[0]=='W' and route[-1] is None
    next_=c.build_job(root,4,route[0],route[1],instruction=route[2])
    assert next_['search']['focus']==focus
    assert [item['mechanism_ref'] for item in next_['search']['unfinished_plan']]==['h001.m2']
    state=search.project(root,4)
    assert state['portfolio']['invested']['h001.m1']==1
    assert not state['portfolio']['pending_w']


def test_explicit_redirect_and_deepening_do_not_require_priority_prefix(tmp_path):
    root=scene(tmp_path)
    seal(root,1,'H',[],action='experiment',choice=plan(dict(index=2,action='screen')))
    redirected=c.build_job(root,2,'W',[1],instruction='Bounded original scientific question')
    assert redirected['search']['focus']['mechanism_ref']=='h001.m2'
    assert [item['mechanism_ref'] for item in redirected['search']['unfinished_plan']]==['h001.m1','h001.m2']
    observe(root,2,'h001.m2')
    seal(root,2,'W',[1],mechanism_findings=findings('h001.m2',blocked=('h001.m1',)))
    choice=review('h001.m2');choice['next_focus']=dict(mechanism_ref='h001.m2',action='improve')
    job,_,receipt=seal(root,3,'A',[1,2],action='continue_w',choice=choice)
    assert not receipt['control_outcome']['diagnostics']
    original=c.validate(c.workspace(root,3))
    route=controller._route(root,3,'A',original,job,1,2)
    route=correction.effective_route(root,3,original,job,route)
    route=semantic.effective_route(root,3,original,job,route)
    assert route[0]=='W' and route[-1] is None
    deepened=c.build_job(root,4,route[0],route[1],instruction=route[2])
    assert deepened['search']['focus']==dict(mechanism_ref='h001.m2',action='improve')
    assert [item['mechanism_ref'] for item in deepened['search']['unfinished_plan']]==['h001.m1']
    assert not search.launch_reason(root,deepened)


def test_e_round_trip_preserves_unfinished_plan_until_eligible_final_a(tmp_path):
    root=scene(tmp_path,portfolio=True);prefix(root)
    seal(root,3,'A',[1,2],action='extract',choice=None)
    before=search.project(root,4)
    seal(root,4,'E',[1,2,3],evidence_return=dict(step=3,role='A'))
    after=search.project(root,5)
    assert after['order']['remaining']==before['order']['remaining']
    assert after['portfolio']['seen_w']==before['portfolio']['seen_w']==set()
    _,_,receipt=seal(root,5,'A',[1,2,3,4],action='continue_w',choice=review('h001.m1'))
    assert not receipt['control_outcome']['diagnostics']
    state=search.project(root,6)
    assert [item['mechanism_ref'] for item in state['order']['remaining']]==['h001.m2']
    assert state['portfolio']['invested']['h001.m1']==1


def test_a_to_h_empty_revision_preserves_original_unfinished_q(tmp_path):
    root=scene(tmp_path);prefix(root)
    choice=review('h001.m1');choice['next_focus']=None
    seal(root,3,'A',[1,2],action='revise_h',choice=choice)
    _,_,receipt=seal(root,4,'H',[1,2,3],action='experiment',choice=dict(scope='Retain the original unfinished comparison',
        commitments=[],focus=dict(index=None,action='screen')))
    assert not receipt['control_outcome']['diagnostics']
    next_=c.build_job(root,5,'W',[4],instruction='Bounded original scientific question')
    assert next_['search']['focus']['mechanism_ref']=='h001.m2'
    assert len(search.project(root,5)['mechanisms'])==2
    assert [item['mechanism_ref'] for item in next_['search']['unfinished_plan']]==['h001.m2']


def test_mechanism_repair_completes_original_opportunity_once_not_an_extra_cycle(tmp_path):
    root=scene(tmp_path,portfolio=True);prefix(root)
    seal(root,3,'A',[1,2],action='continue_w',choice=dict(dispositions=[],next_focus=dict(mechanism_ref='h001.m1',action='repair')))
    state=search.project(root,4)
    assert not state['portfolio']['seen_w']
    assert [item['mechanism_ref'] for item in state['order']['remaining']]==['h001.m1','h001.m2']
    repair=c.build_job(root,4,'W',[1,2,3],instruction='Bounded original scientific question')
    assert repair['search']['repair_root']==2
    observe(root,4,'h001.m1')
    seal(root,4,'W',[1,2,3])
    _,_,receipt=seal(root,5,'A',[1,4],action='continue_w',choice=review('h001.m1'),question='After the repair, which mechanism should be screened next?')
    assert not receipt['control_outcome']['diagnostics']
    state=search.project(root,6)
    assert state['portfolio']['invested']['h001.m1']==1
    assert len(state['portfolio']['seen_w'])==1
    assert [item['mechanism_ref'] for item in state['order']['remaining']]==['h001.m2']
    assert c.validate(c.workspace(root,3))['search_review']['next_focus']['action']=='repair'
    assert c.validate(c.workspace(root,5))['search_review']['next_focus']==dict(mechanism_ref=None,action='screen')


def test_empty_plan_seals_original_science_and_yields_bounded_author_correction(tmp_path):
    root=scene(tmp_path)
    job,payload,receipt=seal(root,1,'H',[],action='experiment',choice=dict(scope='No unfinished mechanism is available',
        commitments=[],focus=dict(index=None,action='screen')))
    assert receipt['control_outcome']['diagnostics'][0]['code']=='science_plan_empty'
    route=('W',[1],payload['route_reason'],None,1,None,None)
    corrected=semantic.effective_route(root,1,c.validate(c.workspace(root,1)),job,route)
    assert corrected[0]=='H' and corrected[-1]['reason']=='science_plan_empty'
    assert semantic.accepted(root,corrected[-1],2)==0
    assert semantic.MAX_AUTHOR_CORRECTIONS==2
    assert (execution.record(root,1)/'science-output.json').exists()


def test_internal_parser_failure_remains_host_error_without_author_retry(tmp_path,monkeypatch):
    root=scene(tmp_path);job=c.build_job(root,1,'H',[])
    before=list((root/'jobs').iterdir())
    def failed(*args,**kwargs):raise ValueError('Host interpretation failure')
    monkeypatch.setattr(search,'interpret',failed)
    with pytest.raises(ValueError,match='Host interpretation failure'):
        semantic.outcome(root,job,dict(search_plan=None,next_action='experiment'))
    assert list((root/'jobs').iterdir())==before
    assert not (execution.record(root,1)/'native-intent.json').exists()


def test_unconfigured_continue_preserves_raw_and_records_control_reason_without_author_retry(tmp_path):
    root=scene(tmp_path)
    seal(root,1,'H',[],action='experiment',choice=plan())
    c.build_job(root,2,'W',[1],instruction='Bounded original scientific question')
    observe(root,2,'h001.m1','h001.m2')
    job,payload,receipt=seal(root,2,'W',[1],stage_intent='continue',mechanism_findings=findings('h001.m1','h001.m2'))
    work=c.workspace(root,2);view=c.read(work/'science-task.json')
    assert view['stage_continuation']['status']=='unavailable'
    assert view['stage_continuation']['context'] is None
    raw=execution.record(root,2)/'science-output.json';before=raw.read_bytes()
    assert receipt['control_outcome']['stage_end_reason']=='continuation_unavailable'
    assert receipt['control_outcome']['worker_delivery']['status']=='complete'
    assert not receipt['control_outcome']['diagnostics']
    result=c.validate(work)
    route=controller._route(root,2,'W',result,job,1,None)
    route=correction.effective_route(root,2,result,job,route)
    route=semantic.effective_route(root,2,result,job,route)
    assert route[0]=='A' and route[-1] is None and 'unavailable' in route[2]
    assert raw.read_bytes()==before and c.read(raw)['stage_intent']=='continue'
    next_job=c.build_job(root,3,route[0],route[1],instruction=route[2],
        evidence_return=route[3],correction_request=route[6],author_correction=route[7])
    assert next_job['role']=='A'
    assert 'unavailable' in c.read(c.workspace(root,3)/'science-task.json')['question']
