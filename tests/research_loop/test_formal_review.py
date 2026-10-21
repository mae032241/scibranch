"""Exact review through existing native nodes; scripted transport is not Codex qualification."""
import sys

import jsonschema
import pytest

from scidiscovery.operations.review_rules import independence
from scidiscovery.research_loop import budget, contracts as c, controller, formal_review as review, semantic
from test_history_handoff import finish


# This independent local process supplies protocol-shaped native/session events.
# It never calls a model, backend or solver. Its identities are synthetic.
LAUNCHER = '''import json,os,pathlib,sys,uuid
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);step=int(work.name)
task=json.loads((work/'science-task.json').read_text());prompt=sys.stdin.read()
anchor='REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE'
thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(2 if step==4 and os.environ.get('REVIEW_REUSE_AUTHOR') else step)))
home=pathlib.Path(os.environ['CODEX_HOME']);sessions=home/'sessions';sessions.mkdir(parents=True,exist_ok=True)
if not (step==2 and os.environ.get('REVIEW_MISSING_AUTHOR')):
 (sessions/(thread+'.jsonl')).write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
if 'review' in task:
 instruction=task['review']['instruction']
 assert instruction in prompt
 for concept in ('exact requested claims','original objective','authorized scope','locatable sources','experiment conditions',
                 'measurements','inferences','assumptions','target metrics','proxies','sensitivity',
                 'conflicting evidence','uncertainty','missing inputs','favorable verdict'):
  assert concept in instruction,concept
 assert 'scientific_judgment' not in task and 'candidate_recommendation' not in task['final_response']
 assert 'history' not in task and 'parents' not in task
 assert anchor not in json.dumps(task) and anchor not in prompt
 assert not (work/'inputs/history').exists()
 assert not list((work/'inputs/parents').glob('*/conclusion.json'))
 assert all(anchor.encode() not in path.read_bytes() for path in (work/'inputs').rglob('*') if path.is_file())
 assert (work/task['review']['subject']).read_text()=='a scientific implementation'
 assert all((work/path).is_file() for path in task['global_inputs'])
 value=dict(verdict='pass',summary='Exact fixture review',scientific_result='No fixture defect observed.',limitations=['Synthetic native transport only.'])
else:
 value=dict(summary='Fixture science',scientific_result='A bounded finding.',limitations=['No real science.'],implementation_selection=None)
 role=task['role']
 if role=='H':value.update(next_action='experiment',route_reason='Implement the question',search_plan=None)
 if role=='W':
  (work/'artifacts/model.txt').write_text('a scientific implementation')
  value.update(stage_intent='review',mechanism_findings=None)
 if role=='A':
  mode=os.environ.get('REVIEW_UNAVAILABLE_ROUTE','finish')
  request=step==3 or bool(os.environ.get('REVIEW_MISSING_AUTHOR')) and mode=='repeat'
  value.update(next_action='extract' if step==4 and mode=='extract' else 'formal_review' if request else 'finish',route_reason='Assess the selected implementation '+str(step),candidate_recommendation=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
  if request:value['implementation_selection']=['W002-I001']
  if step==3:value.update(summary=anchor,scientific_result=anchor,route_reason=anchor)
  if step==5 and not os.environ.get('REVIEW_MISSING_AUTHOR'):assert task['formal_review_feedback']['status']==('not_qualified' if os.environ.get('REVIEW_REUSE_AUTHOR') else 'passed')
args=sys.argv[1:];pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(value))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':5}}),flush=True)
'''


def prepare(tmp_path, launcher, *, enabled=True, steps=6, delivery_policy=None, decision_contract_version=None):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Review one exact authored scientific object when useful.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,launcher,max_tokens=2000,max_seconds=60,max_steps=steps,
        model='no-model',reasoning_effort='high',search_policy=None,experiment_mode='external',
        formal_review_policy=dict(version=1) if enabled else None,delivery_policy=delivery_policy,
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=5) for role in 'HWAE'} if delivery_policy is not None else None,
        decision_contract_version=decision_contract_version)
    return root


def scripted(tmp_path, monkeypatch, *, reuse=False, missing=False, unavailable_route='finish', delivery_policy=None):
    launcher=tmp_path/'codex'
    launcher.write_text('#!'+sys.executable+'\n'+LAUNCHER)
    launcher.chmod(0o700)
    home=tmp_path/'actual-native-home';home.mkdir()
    monkeypatch.setenv('CODEX_HOME',str(home))
    if reuse:monkeypatch.setenv('REVIEW_REUSE_AUTHOR','1')
    else:monkeypatch.delenv('REVIEW_REUSE_AUTHOR',raising=False)
    if missing:monkeypatch.setenv('REVIEW_MISSING_AUTHOR','1')
    else:monkeypatch.delenv('REVIEW_MISSING_AUTHOR',raising=False)
    monkeypatch.setenv('REVIEW_UNAVAILABLE_ROUTE',unavailable_route)
    return prepare(tmp_path,[str(launcher)],steps=10,delivery_policy=delivery_policy),home


def test_independence_needs_every_verified_participant():
    assert independence({('engine','author')},{('engine','reviewer')})=='independent'
    assert independence({('engine','author')},{('engine','reviewer'),('engine','author')})=='overlap'
    assert independence(set(),{('engine','reviewer')},authors_complete=False)=='unknown'


def test_formal_review_scientific_prose_has_no_minimum_length_and_keeps_verdict_contract():
    schema = review.schema()
    value = dict(verdict='inconclusive',summary='',scientific_result='',limitations=[])
    jsonschema.validate(value,schema)
    for field in ('summary','scientific_result'):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**value,field:None},schema)
        missing = {key:item for key,item in value.items() if key != field}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(missing,schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**value,'verdict':'looks good'},schema)


@pytest.mark.process_e2e
def test_real_native_route_freezes_exact_review_and_uses_actual_home(tmp_path,monkeypatch):
    root,home=scripted(tmp_path,monkeypatch)
    result=controller.run(root)
    assert result['status']=='finish' and result['completed_steps']==5
    job=c.read(root/'jobs/004.json');record=c.host_record(c.workspace(root,4))
    assert job['role']=='A' and job['purpose']=='formal_review'
    request=c.read(root/job['formal_review']['path'])
    assert request['requester_rationale']=='REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE'
    assert request['question']==job['instruction']==review.QUESTION
    assert job['direct_refs']==[] and len(job['direct_materials'])==1
    assert job['selected_inputs'][0]['origin_step']==2
    assert set(c.read(record/'science-output.json'))=={'verdict','summary','scientific_result','limitations'}
    seal=c.read(record/'result-identity.json');proof=c.read(root/seal['formal_review']['path'])
    assert proof['status']=='passed' and proof['independence']=='independent'
    assert proof['subject']['producer_step']==2
    own=c.read(root/seal['native_authorship']['path'])
    assert own['complete'] and own['attempts'][0]['session']['home']==str(home)
    assert home!=root/'native-homes/004'
    assert review.require_review(root,proof['subject'],seal['formal_review'])==seal['formal_review']
    wrong=dict(proof['subject'],material={**proof['subject']['material'],'source':str(root/'same-bytes-other-origin')})
    with pytest.raises(ValueError,match='Exact subject'):
        review.require_review(root,wrong,seal['formal_review'])
    before={path:path.read_bytes() for path in (root/'jobs').glob('*.json')}
    assert controller.run(root)['status']=='finish'
    assert all(path.read_bytes()==data for path,data in before.items())


@pytest.mark.process_e2e
def test_native_author_session_cannot_review_itself_even_with_pass(tmp_path,monkeypatch):
    root,_=scripted(tmp_path,monkeypatch,reuse=True)
    assert controller.run(root)['status']=='finish'
    seal=c.read(c.host_record(c.workspace(root,4))/'result-identity.json')
    proof=c.read(root/seal['formal_review']['path'])
    assert proof['verdict']=='pass' and proof['independence']=='overlap' and proof['status']=='not_qualified'
    with pytest.raises(ValueError,match='independent sealed pass'):
        review.require_review(root,proof['subject'],seal['formal_review'])


@pytest.mark.process_e2e
def test_unknown_identity_preserves_science_and_allows_ordinary_finish(tmp_path,monkeypatch):
    root,_=scripted(tmp_path,monkeypatch,missing=True)
    result=controller.run(root)
    assert result['status']=='finish' and result['completed_steps']==4,result
    assert c.validate(c.workspace(root,3))['next_action']=='formal_review'
    assert review.unavailable(root,3)['failure']['reason']=='subject_authorship_unsupported'
    view=c.read(c.workspace(root,4)/'science-task.json')
    assert view['formal_review_feedback']['status']=='unavailable'
    assert view['formal_review_capability']['choices']['unavailable']==[
        dict(subject='W002-I001',reason='subject_authorship_unsupported')]
    assert 'control_request' not in view
    assert 'author_correction' not in c.read(root/'jobs/004.json')
    assert not list((root/'native-evidence').glob('*/formal-review.json'))


@pytest.mark.process_e2e
def test_reworded_unavailable_request_needs_original_output_repair_not_another_a(tmp_path,monkeypatch):
    root,_=scripted(tmp_path,monkeypatch,missing=True,unavailable_route='repeat')
    result=controller.run(root)
    # This launcher has no authorized resume policy. The invalid output remains
    # repairable work, rather than a sealed failure consuming new A turns.
    assert result['status']=='output_repair_required' and result['completed_steps']==3,result
    assert c.read(c.host_record(c.workspace(root,3))/'science-output.json')['route_reason']!=c.read(
        c.host_record(c.workspace(root,4))/'science-output.json')['route_reason']
    assert not (c.host_record(c.workspace(root,4))/'result-identity.json').exists()
    assert not (root/'jobs/005.json').exists()
    assert review.unavailable(root,4) is None
    assert budget.closeout_state(root)['begin'] is None
    assert budget.snapshot(root)['max_tokens']==2000 and budget.snapshot(root)['max_seconds']==60
    assert result['usage']['observed_tokens']==60 and budget.used_steps(root)==4
    originals={path:path.read_bytes() for path in (root/'native-evidence').glob('*/formal-review-unavailable.json')}
    assert controller.run(root)['status']=='output_repair_required'
    assert all(path.read_bytes()==data for path,data in originals.items())
    assert not (root/'jobs/005.json').exists()


@pytest.mark.process_e2e
def test_unavailable_review_does_not_force_delivery_closeout_across_extraction(tmp_path,monkeypatch):
    policy=dict(closeout_a=dict(max_observed_tokens=30,max_seconds=1),inflight_tokens=0,
        cleanup_seconds=0,submit_seconds=0,confirm_seconds=0,adapter=None)
    root,_=scripted(tmp_path,monkeypatch,missing=True,unavailable_route='extract',delivery_policy=policy)
    result=controller.run(root)
    assert result['status']=='final_a_completed',result
    assert result['goal_completion']=='not_asserted' and result['submission_result']['status']=='unsubmitted'
    assert [c.validate(c.workspace(root,step))['role'] for step in range(1,7)]==['H','W','A','A','E','A']
    assert [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in (4,5,6)]==['A','E','A']
    requester=c.validate(c.workspace(root,4))
    assert requester['next_action']=='extract'
    assert c.read(root/'jobs/005.json')['evidence_return']==dict(role='A',step=4)
    assert c.read(root/'jobs/006.json')['instruction']==requester['route_reason']
    state=budget.closeout_state(root)
    assert state['begin']['stop_reason']=='scientific_finish' and state['begin']['source_cutoff']==6
    assert state['begin']['mode']=='final' and state['begin']['first_step']==7
    final=c.read(root/'jobs/007.json')
    assert final['role']=='A' and final['final_only']
    assert final['final_report']==dict(source_cutoff=6,stopped_original=None)
    assert budget.final_a_barrier(root)['step']==7
    assert state['actions']==[] and not (root/'jobs/008.json').exists()
    job=c.read(root/'jobs/006.json')
    assert job['formal_review_choices']['unavailable']==c.read(root/'jobs/004.json')['formal_review_choices']['unavailable']
    assert 'author_correction' not in job
    science=c.validate(c.workspace(root,6))
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    with pytest.raises(OutputRepairRequired,match='exact subject and identity'):
        review.validate_choice(root,job,dict(science,next_action='formal_review',implementation_selection=['W002-I001']))
    limits=budget.snapshot(root)
    assert limits['max_tokens']==2000 and limits['max_seconds']==60
    assert limits['consumed_seconds']>0 and limits['seconds_remaining']<60
    assert not list((root/'native-evidence').glob('*/formal-review.json'))
    originals={path:path.read_bytes() for step in range(1,8) for path in
        (root/'jobs'/f'{step:03d}.json',c.host_record(c.workspace(root,step))/'result-identity.json')}
    repeated=controller.run(root)
    assert repeated['status']=='final_a_completed' and repeated['last_step']==7
    assert repeated['source_cutoff']==6 and repeated['submission_result']['status']=='unsubmitted'
    assert all(path.read_bytes()==data for path,data in originals.items())
    assert budget.closeout_state(root)['actions']==[] and not (root/'jobs/008.json').exists()


def test_review_choice_rejects_known_failure_but_new_subject_can_use_same_output(tmp_path,monkeypatch):
    from scidiscovery.research_loop import continuation
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    root=prepare(tmp_path,['codex'])
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'first exact subject','other.txt':'second exact subject'})
    job,_,_=finish(root,3,'A',[1,2],action='formal_review',selection=['W002-I001'])
    route=review.request_route(root,job,c.validate(c.workspace(root,3)),[1,2,3])
    route8=semantic.effective_route(root,3,c.validate(c.workspace(root,3)),job,(*route,1,2,None))
    assert route8[7] is None
    child=c.build_job(root,4,'A',route8[1],instruction=route8[2],evidence_return=route8[3])
    with pytest.raises(OutputRepairRequired,match='exact subject and identity'):
        finish(root,4,'A',route8[1],action='formal_review',selection=['W002-I001'])
    record=c.host_record(c.workspace(root,4))
    assert not (record/'result-identity.json').exists()
    repair=continuation.output_request(root,4,record)
    assert repair['code']=='formal_review_unavailable'
    for selection in (None,[],['W002-I001','W002-I002'],['W999-I001']):
        with pytest.raises(OutputRepairRequired):
            review.validate_choice(root,child,dict(next_action='formal_review',implementation_selection=selection))
    # Isolate key comparison only: newly verified identity is a changed fact,
    # not qualification. No fake identity is persisted into a native receipt.
    with monkeypatch.context() as changed_identity:
        changed_identity.setattr(review,'authors',lambda *args:dict(complete=True,participants=[['fixture','new-identity']]))
        review.validate_choice(root,child,dict(next_action='formal_review',implementation_selection=['W002-I001']))
    # Same frozen job and same output fields: a different exact material may
    # request review. It still has no verified author and grants no qualification.
    child,_,_=finish(root,4,'A',route8[1],action='formal_review',selection=['W002-I002'])
    changed=review.request_route(root,child,c.validate(c.workspace(root,4)),[1,2,4])
    assert changed[3]['kind']=='formal_review_unavailable'
    assert review.unavailable(root,3)['failure_key']!=review.unavailable(root,4)['failure_key']
    assert child['formal_review_choices']['unavailable']==c.read(root/'jobs/004.json')['formal_review_choices']['unavailable']
    assert len(child['formal_review_choices']['unavailable'])==1 # Future failure never changes old job.
    reference=child['formal_review_choices']['unavailable'][0]
    original=(root/reference['path']).read_bytes()
    (root/reference['path']).chmod(0o600) # Deliberate test-only mutation of a frozen original.
    (root/reference['path']).write_bytes(original+b' ')
    with pytest.raises(ValueError):
        c._validate_inputs(root,c.workspace(root,4),child)


def test_unsupported_launcher_removes_review_action_without_blocking_science(tmp_path):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    root=prepare(tmp_path,['unsupported-launcher'])
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'scientific implementation'})
    job=c.build_job(root,3,'A',[1,2])
    schema=c.result_schema('A',**c.response_options(job))
    assert 'formal_review' not in schema['properties']['next_action']['enum']
    with pytest.raises(OutputRepairRequired,match='Correct required fields'):
        finish(root,3,'A',[1,2],action='formal_review',selection=['W002-I001'])
    finish(root,3,'A',[1,2],action='finish',selection=['W002-I001'])
    assert c.validate(c.workspace(root,3))['next_action']=='finish'
    assert review.unavailable(root,3) is None


def test_ordinary_a_and_pass_text_do_not_gain_formal_qualification(tmp_path):
    root=prepare(tmp_path,['fixture'],enabled=False)
    finish(root,1,'H',[],action='analyze')
    _,_,record=finish(root,2,'A',[1],text='pass: independent and ready to execute')
    assert 'formal_review' not in c.read(record/'result-identity.json')
    assert 'formal_review' not in c.result_schema('A')['properties']['next_action']['enum']
    assert set(review.schema()['properties'])=={'verdict','summary','scientific_result','limitations'}
    ordinary=c.build_job(root,3,'A',[2],instruction='Ordinary scientific continuation')
    assert ordinary['direct_refs'][0]['path']=='inputs/parents/002/conclusion.json'
    assert 'pass: independent and ready to execute' in (c.workspace(root,3)/ordinary['direct_refs'][0]['path']).read_text()


def test_review_default_packet_keeps_exact_source_without_requester_arguments(tmp_path,monkeypatch):
    from scidiscovery.research_loop import qualification as q
    root=prepare(tmp_path,['codex'])
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'exact scientific claim','other.txt':'unselected material'})
    anchor='REQUESTER_FAVORS_PASS_AND_DISMISSES_ALL_LIMITATIONS'
    requester,_,record=finish(root,3,'A',[1,2],action='formal_review',selection=['W002-I001'],text=anchor,summary=anchor)
    monkeypatch.setattr(review,'authors',lambda *args:dict(complete=True,participants=[['fixture','author']]))
    route=review.request_route(root,requester,c.validate(c.workspace(root,3)),[1,2,3])
    assert route[1]==[2] and route[2]==review.QUESTION
    job=c.build_job(root,4,'A',route[1],instruction=route[2],evidence_return=route[3])
    work=c.workspace(root,4);c._validate_inputs(root,work,job)
    assert job['direct_refs']==[] and len(job['direct_materials'])==1
    assert job['history']['files']==[] and job['history']['view']=={}
    assert not (work/'inputs/history').exists() and not (work/'inputs/parents/003').exists()
    assert all(anchor.encode() not in path.read_bytes() for path in (work/'inputs').rglob('*') if path.is_file())
    assert anchor in c.read(record/'science-output.json')['scientific_result']
    source=q._read(root,job['source_provenance'])
    assert [edge['step'] for edge in source['edges'] if edge['kind']=='producer']==[2]
    assert not any(edge['kind']=='unknown' for edge in source['edges'])
    changed=dict(job,selected_inputs=[dict(job['selected_inputs'][0],origin_step=1)])
    with pytest.raises(ValueError,match='neutral input boundary'):
        c._validate_inputs(root,work,changed)


def test_review_subject_from_requester_keeps_artifact_but_not_its_conclusion(tmp_path):
    from scidiscovery.research_loop import qualification as q
    root=prepare(tmp_path,['codex'])
    finish(root,1,'H',[],action='analyze')
    anchor='REQUESTER_SELF_ASSESSMENT_MUST_NOT_BE_DEFAULT_CONTEXT'
    _,_,record=finish(root,2,'A',[1],text=anchor,files={'subject.txt':'the exact subject','unrelated.txt':anchor})
    seal=q._ref(root,record/'result-identity.json')
    material=next(item for item in c.read(record/'result-identity.json')['materials'] if item['path'].endswith('/subject.txt'))
    # A host-bound exact request fixture, not an independence/production qualification.
    reference=q._save(root,record/'formal-review-request.json',dict(contract=review.contract(root),question=review.QUESTION,
        requester_rationale=anchor,requester=dict(step=2,seal=seal),
        subject=dict(producer_step=2,producer_seal=seal,material=material),
        authors=dict(complete=False,participants=[]),unresolved_selection=None,reason=None))
    back=dict(kind='formal_review',request=reference)
    job=c.build_job(root,3,'A',[2],instruction=review.QUESTION,evidence_return=back)
    work=c.workspace(root,3);c._validate_inputs(root,work,job)
    assert job['direct_materials']==[material] and job['direct_refs']==[]
    assert (work/material['path']).read_text()=='the exact subject'
    assert not (work/'inputs/parents/002/conclusion.json').exists()
    assert not (work/'inputs/parents/002/artifacts/unrelated.txt').exists()
    assert not (work/'inputs/history').exists()
    assert controller._check_job(root,c.settings(root),3,'A',[2],review.QUESTION,back)==job


def test_unavailable_review_preserves_existing_nonreview_author_correction(tmp_path):
    root=prepare(tmp_path,['codex'],decision_contract_version=1)
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'a scientific implementation'})
    job,_,_=finish(root,3,'A',[1,2],action='continue_w',selection=['W002-I001'])
    science=c.validate(c.workspace(root,3))
    route=semantic.effective_route(root,3,science,job,(*controller._route(root,3,'A',science,job,1,2),None))
    prior=route[7]
    assert prior['reason']=='a_decision_request_missing' and prior['root']['step']==3
    c.build_job(root,4,'A',route[1],instruction=route[2],author_correction=prior)
    job,_,_=finish(root,4,'A',route[1],action='formal_review',selection=['W002-I001'])
    science=c.validate(c.workspace(root,4))
    route=semantic.effective_route(root,4,science,job,(*controller._route(root,4,'A',science,job,1,2),None))
    assert route[7]['root']==prior['root'] and route[7]['reason']==prior['reason']
    assert route[7]['source']['step']==4 and route[3]['kind']=='formal_review_unavailable'
    assert semantic.accepted(root,route[7],5)==1
    child=c.build_job(root,5,'A',route[1],instruction=route[2],evidence_return=route[3],author_correction=route[7])
    assert child['author_correction']==route[7] and not child.get('formal_review')
