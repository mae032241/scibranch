"""Exact review through existing native nodes; scripted transport is not Codex qualification."""
import sys
import json
import os
from pathlib import Path

import jsonschema
import pytest

from scidiscovery.operations.review_rules import independence
from scidiscovery.research_loop import budget, contracts as c, controller, formal_review as review, semantic
from test_history_handoff import finish


def choice_inventory(root,job,payload):
    """Accept this test response's exact selection once, outside consumers."""
    work=c.workspace(root,job['step'])
    diagnostics=[]
    selected=c._accept_selected_inputs(work,payload,job,diagnostics)
    return c.CurrentInventory(root,job,c.host_record(work),0,[],{},
        selected_inputs=selected,selection_diagnostics=diagnostics)


def formal_native_diagnostic(root,result):
    """Only the expected formal node's exact receipts and bounded stderr tails."""
    record=root/'native-evidence/004'
    paths=(record/'native-outcome.json',record/'native.stderr',record/'runner.stderr')
    retained={}
    for path in paths:
        if not path.is_file():
            retained[str(path.relative_to(root))]=dict(status='missing')
            continue
        with path.open('rb') as stream:
            stream.seek(0,2)
            size=stream.tell()
            stream.seek(max(0,size-8192))
            retained[str(path.relative_to(root))]=dict(size_bytes=size,tail=stream.read(8192).decode('utf-8',errors='replace'))
    diagnostic=dict(result=result,formal_step_004=retained)
    log_root=Path(os.environ.get('SCID_TEST_LOG_DIR',str(root.parent)))
    log_root.mkdir(parents=True,exist_ok=True)
    log_path=log_root/('formal-native-'+root.parent.name+'.log')
    log_path.write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n')
    return dict(diagnostic_log=str(log_path),status=result['status'],
        completed_steps=result['completed_steps'],reason=result.get('reason'))


# This independent local process supplies protocol-shaped native/session events.
# It never calls a model, backend or solver. Its identities are synthetic.
LAUNCHER = r'''import hashlib,json,os,pathlib,shlex,subprocess,sys,tomllib,uuid
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);step=int(work.name)
args=sys.argv[1:]
assert args[-2:]==['app-server','--stdio'],args
assert args[args.index('-C')+1]==str(work)
config={}
def merge(target,update):
 for key,value in update.items():
  if isinstance(value,dict) and isinstance(target.get(key),dict):merge(target[key],value)
  else:target[key]=value
for index,arg in enumerate(args[:-1]):
 if arg=='-c':merge(config,tomllib.loads(args[index+1]))
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
def receive():
 line=sys.stdin.readline()
 assert line,'Missing app-server request'
 return json.loads(line)
request=receive();assert request['method']=='initialize'
send(dict(id=request['id'],result={}))
assert receive()['method']=='initialized'
request=receive()
if request['method']=='hooks/list':
 assert request['params']==dict(cwds=[str(work)])
 hooks=[]
 for name,key,event in (('PostToolUse','post_tool_use','postToolUse'),
                       ('PostModelRequest','post_model_request','postModelRequest'),
                       ('PostCompact','post_compact','postCompact')):
  declared=config.get('hooks',{}).get(name,[])
  if not declared:continue
  assert len(declared)==1 and len(declared[0]['hooks'])==1
  handler=declared[0]['hooks'][0]
  hooks.append(dict(key='/<session-flags>/config.toml:'+key+':0:0',
   currentHash='sha256:'+hashlib.sha256(json.dumps(handler,sort_keys=True).encode()).hexdigest(),
   displayOrder=0,enabled=True,source='sessionFlags',sourcePath='/<session-flags>/config.toml',
   isManaged=False,trustStatus='untrusted',eventName=event,matcher=None,handlerType=handler['type'],
   command=handler['command'],**{'async':handler['async']},timeoutSec=handler['timeout'],
   additionalContextLimit=handler.get('additionalContextLimit')))
 send(dict(id=request['id'],result=dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])))
 raise SystemExit(0)
assert request['method'] in ('thread/start','thread/resume')
start=request['params']
assert start['cwd']==str(work) and start['approvalPolicy']=='never'
assert '--dangerously-bypass-hook-trust' in args and start['config']==dict(bypass_hook_trust=True)
assert set(config['hooks'])=={'PostToolUse','PostModelRequest','PostCompact'}
anchor='REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE'
thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(2 if step==4 and os.environ.get('REVIEW_REUSE_AUTHOR') else step)))
if request['method']=='thread/resume':
 assert start['threadId']==thread and start['excludeTurns'] is True
else:assert start['allowProviderModelFallback'] is False and start['experimentalRawEvents'] is False
mode=start['sandbox'];sandbox=config.get('sandbox_workspace_write',{})
assert mode==config['sandbox_mode']
policy=dict(type={'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[mode])
if mode!='danger-full-access':policy['networkAccess']=sandbox.get('network_access',False) if mode=='workspace-write' else False
if mode=='workspace-write':
 policy.update(writableRoots=sandbox.get('writable_roots',[]),
  excludeSlashTmp=sandbox.get('exclude_slash_tmp',False),excludeTmpdirEnvVar=sandbox.get('exclude_tmpdir_env_var',False))
send(dict(id=request['id'],result=dict(thread=dict(id=thread,turns=[]),cwd=str(work),model=start['model'],
 approvalPolicy='never',sandbox=policy,reasoningEffort=config.get('model_reasoning_effort'),
 serviceTier=start.get('serviceTier'))))
request=receive();assert request['method']=='turn/start'
turn=request['params'];assert turn['threadId']==thread and turn['model']==start['model']
assert turn['approvalPolicy']=='never' and turn.get('effort')==config.get('model_reasoning_effort')
assert turn.get('serviceTier')==start.get('serviceTier')
assert len(turn['input'])==1 and turn['input'][0]['type']=='text'
prompt=turn['input'][0]['text']
task=json.loads((work/'science-task.json').read_text())
if 'review' in task:
 # The neutral developer instruction crosses the real thread/start control,
 # once, separately from both the user prompt and public science-task packet.
 instruction=start['developerInstructions']
 assert instruction==config['developer_instructions']
 assert isinstance(instruction,str) and instruction
 assert 'instruction' not in task['review']
 assert instruction not in prompt and instruction not in json.dumps(task)
 actual_input=prompt+chr(10)+instruction
 assert actual_input.count(instruction)==1
 for concept in ('exact requested claims','original objective','authorized scope','locatable sources','experiment conditions',
                 'measurements','inferences','assumptions','target metrics','proxies','sensitivity',
                 'conflicting evidence','uncertainty','missing inputs','favorable verdict'):
  assert concept in instruction,concept
 assert 'scientific_judgment' not in task and 'candidate_recommendation' not in task['final_response']
 assert 'history' not in task and 'parents' not in task
 assert anchor not in json.dumps(task) and anchor not in actual_input
 assert not (work/'inputs/history').exists()
 assert not (work/'inputs/parents/002/conclusion.json').exists()
 assert not (work/'inputs/parents/003/conclusion.json').exists()
 declared_inputs=[task['review']['subject'],*task['global_inputs']]
 assert all(anchor.encode() not in (work/path).read_bytes() for path in declared_inputs)
 assert (work/task['review']['subject']).read_text()=='a scientific implementation'
 assert all((work/path).is_file() for path in task['global_inputs'])
 value=dict(verdict='pass',summary='Exact fixture review',scientific_result='No fixture defect observed.',limitations=['Synthetic native transport only.'])
else:
 value=dict(summary='Fixture science',scientific_result='A bounded finding.',limitations=['No real science.'],implementation_selection=None)
 role=task['role']
 if role=='H':value.update(next_action='experiment',route_reason='Implement the question',search_plan=None)
 if role=='W':
  (work/'artifacts/model.txt').write_text('a scientific implementation')
  value.update(stage_intent='decision',mechanism_findings=None)
 if role=='A':
  mode=os.environ.get('REVIEW_UNAVAILABLE_ROUTE','finish')
  wants_review=step==3 or bool(os.environ.get('REVIEW_MISSING_AUTHOR')) and mode=='repeat'
  value.update(next_action='extract' if step==4 and mode=='extract' else 'formal_review' if wants_review else 'finish',route_reason='Assess the selected implementation '+str(step),candidate_recommendation=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
  if value['next_action']=='extract':
   value['decision_request']=dict(decision='mechanism',
    question='Which public observations support or contradict the exact subject scientific claim?',
    change_criterion='Locatable independent contradictory observations would change whether the scientific claim can be retained.')
  if wants_review:value['implementation_selection']=['W002-I001']
  if step==3:value.update(summary=anchor,scientific_result=anchor,route_reason=anchor)
  if step==5 and not os.environ.get('REVIEW_MISSING_AUTHOR'):assert task['formal_review_feedback']['status']==('not_qualified' if os.environ.get('REVIEW_REUSE_AUTHOR') else 'passed')
home=pathlib.Path(os.environ['CODEX_HOME']);sessions=home/'sessions';sessions.mkdir(parents=True,exist_ok=True)
transcript=sessions/(thread+'.jsonl')
# Appending preserves an overlapping thread's original entity. Headers alone
# confer no proof: the real declared helper must deliver the bound metadata.
missing=step==2 and bool(os.environ.get('REVIEW_MISSING_AUTHOR'))
if not missing:
 with transcript.open('a') as stream:
  stream.write(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
event=dict(cwd=str(work),session_id=thread,hook_event_name='PostModelRequest',status='completed',
 token_usage=dict(input_tokens=10,cached_input_tokens=0,output_tokens=5))
if not missing:event['transcript_path']=str(transcript)
def hook(name,payload):
 handler=config['hooks'][name][0]['hooks'][0]
 result=subprocess.run(shlex.split(handler['command']),input=json.dumps(payload).encode(),
  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=handler['timeout'],check=True)
 return result.stdout
send(dict(id=request['id'],result=dict(turn=dict(id='turn-one',items=[],status='inProgress'))))
assert hook('PostModelRequest',event)==b''
notice=hook('PostToolUse',dict(cwd=str(work),session_id=thread,hook_event_name='PostToolUse'))
if notice:
 output=json.loads(notice)
 assert set(output)=={'hookSpecificOutput'}
 assert output['hookSpecificOutput']['hookEventName']=='PostToolUse'
 assert set(output['hookSpecificOutput'])=={'hookEventName','additionalContext'}
# Capacity is not reported by this fixture. Context fullness remains unknown;
# billing comes only from the actual PostModelRequest helper delivery above.
send(dict(method='thread/tokenUsage/updated',params=dict(threadId=thread,turnId='turn-one',
 tokenUsage=dict(last=dict(totalTokens=15)))))
send(dict(method='item/completed',params=dict(threadId=thread,turnId='turn-one',
 item=dict(type='agentMessage',text=json.dumps(value),phase='final_answer'))))
send(dict(method='turn/completed',params=dict(threadId=thread,
 turn=dict(id='turn-one',items=[],status='completed'))))
'''


def prepare(tmp_path, launcher, *, enabled=True, steps=6, delivery_policy=None, decision_contract_version=None):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Review one exact authored scientific object when useful.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,launcher,max_tokens=2000,max_seconds=60,max_steps=steps,
        model='no-model',reasoning_effort='high',search_policy=None,experiment_mode='external',
        formal_review_policy=dict(version=1) if enabled else None,delivery_policy=delivery_policy,
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=5) for role in 'HWAE'} if delivery_policy is not None else None,
        # These fixtures intentionally exercise the retained H -> W -> A route,
        # not the separately covered v3 W-first/protected-Final-A strategy.
        decision_contract_version=decision_contract_version,research_strategy_version=None)
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
    assert result['status']=='finish' and result['completed_steps']==5,formal_native_diagnostic(root,result)
    job=c.read_job(root/'jobs/004.json');record=c.host_record(c.workspace(root,4))
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
    assert own['attempts'][0]['proof_kind']=='native_app_server_hooks_v1'
    outcome=c.read(record/'native-outcome.json')
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert outcome['budget_notification']==dict(status='available',mechanism='codex_post_tool_use')
    assert outcome['native_thread_event']['rpc_method']=='thread/start'
    assert outcome['native_thread_event']['requested_profile']==outcome['native_thread_event']['effective_profile']
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
    result=controller.run(root)
    assert result['status']=='finish',formal_native_diagnostic(root,result)
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
    author=c.read(c.host_record(c.workspace(root,2))/'native-outcome.json')
    assert author['native_identity']['session'] is None
    assert author['budget_notification']['status']=='available'  # Usage delivery is not author proof.
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
    assert result['status']=='finish' and result['last_step']==6,formal_native_diagnostic(root,result)
    assert result['goal_completion']=='not_asserted'
    assert [c.validate(c.workspace(root,step))['role'] for step in range(1,7)]==['H','W','A','A','E','A']
    assert [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in (4,5,6)]==['A','E','A']
    requester=c.validate(c.workspace(root,4))
    assert requester['next_action']=='extract'
    assert requester['decision_request']==dict(decision='mechanism',
        question='Which public observations support or contradict the exact subject scientific claim?',
        change_criterion='Locatable independent contradictory observations would change whether the scientific claim can be retained.')
    assert c.read(root/'jobs/005.json')['evidence_return']==dict(role='A',step=4)
    returned=c.read(root/'jobs/006.json')
    assert 'inputs/parents/004/conclusion.json' in returned['instruction']
    assert 'inputs/parents/005/conclusion.json' in returned['instruction']
    assert {'inputs/parents/004/conclusion.json','inputs/parents/005/conclusion.json'} <= {
        ref['path'] for ref in returned['direct_refs']}
    state=budget.closeout_state(root)
    assert state['begin'] is None and state['actions']==[]
    assert budget.final_a_barrier(root) is None
    assert not (root/'jobs/007.json').exists()
    job=c.read_job(root/'jobs/006.json')
    assert job['formal_review_choices']['unavailable']==c.read(root/'jobs/004.json')['formal_review_choices']['unavailable']
    assert 'author_correction' not in job
    science=c.validate(c.workspace(root,6))
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    request=dict(science,next_action='formal_review',implementation_selection=['W002-I001'])
    inventory=choice_inventory(root,job,request)
    with pytest.raises(OutputRepairRequired,match='exact subject and identity'):
        review.validate_choice(root,job,request,inventory=inventory)
    limits=budget.snapshot(root)
    assert limits['max_tokens']==2000 and limits['max_seconds']==60
    assert limits['consumed_seconds']>0 and limits['seconds_remaining']<60
    assert not list((root/'native-evidence').glob('*/formal-review.json'))
    originals={path:path.read_bytes() for step in range(1,7) for path in
        (root/'jobs'/f'{step:03d}.json',c.host_record(c.workspace(root,step))/'result-identity.json')}
    repeated=controller.run(root)
    assert repeated['status']=='finish' and repeated['last_step']==6
    assert repeated['goal_completion']=='not_asserted'
    assert all(path.read_bytes()==data for path,data in originals.items())
    assert budget.closeout_state(root)['begin'] is None and budget.closeout_state(root)['actions']==[]
    assert budget.final_a_barrier(root) is None and not (root/'jobs/007.json').exists()


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
    with pytest.raises(OutputRepairRequired,match='exact subject and identity') as rejected:
        finish(root,4,'A',route8[1],action='formal_review',selection=['W002-I001'])
    record=c.host_record(c.workspace(root,4))
    assert not (record/'result-identity.json').exists()
    retained={path:path.read_bytes() for path in (record/'native-intent.json',
        record/'science-output.json',record/'native-outcome.json')}
    inventory=rejected.value.inventory
    repair=continuation.output_request(root,4,record,inventory=inventory)
    assert repair['code']=='formal_review_unavailable'
    for selection in (None,[],['W002-I001','W002-I002'],['W999-I001']):
        request=dict(next_action='formal_review',implementation_selection=selection)
        inventory=choice_inventory(root,child,request)
        with pytest.raises(OutputRepairRequired):
            review.validate_choice(root,child,request,inventory=inventory)
    # Isolate key comparison only: newly verified identity is a changed fact,
    # not qualification. No fake identity is persisted into a native receipt.
    with monkeypatch.context() as changed_identity:
        changed_identity.setattr(review,'authors',lambda *args:dict(complete=True,participants=[['fixture','new-identity']]))
        request=dict(next_action='formal_review',implementation_selection=['W002-I001'])
        inventory=choice_inventory(root,child,request)
        review.validate_choice(root,child,request,inventory=inventory)
    # Same frozen job and same output fields: a different exact material may
    # request review. It still has no verified author and grants no qualification.
    request=dict(next_action='formal_review',implementation_selection=['W002-I002'])
    inventory=choice_inventory(root,child,request)
    review.validate_choice(root,child,request,inventory=inventory)
    changed=review._request_value(root,inventory.selected_inputs,child)
    assert changed['reason'] is not None  # A new subject still has no qualified author.
    assert changed['subject']['material']!=review.unavailable(root,3)['failure_key']['subject']['material']
    assert review.unavailable(root,3)['failure_key']!=review._failure_key(changed)
    assert all(path.read_bytes()==contents for path,contents in retained.items())
    assert not (record/'result-identity.json').exists()
    assert not (record/'formal-review-unavailable.json').exists()
    assert child['formal_review_choices']['unavailable']==c.read(root/'jobs/004.json')['formal_review_choices']['unavailable']
    assert len(child['formal_review_choices']['unavailable'])==1 # A hypothetical new subject cannot change the frozen job.
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
    with pytest.raises(OutputRepairRequired,match='Correct next_action'):
        finish(root,3,'A',[1,2],action='formal_review',selection=['W002-I001'])
    record=c.host_record(c.workspace(root,3))
    rejected={path:path.read_bytes() for path in (root/'jobs/003.json',record/'native-intent.json',
        record/'science-output.json',record/'native-outcome.json')}
    assert not (record/'result-identity.json').exists()
    # A separate valid task proves ordinary science remains available without
    # relaunching the closed invalid native original through this thin fixture.
    ordinary=tmp_path/'ordinary';ordinary.mkdir()
    valid=prepare(ordinary,['unsupported-launcher'])
    finish(valid,1,'H',[],action='experiment')
    finish(valid,2,'W',[1],files={'model.txt':'scientific implementation'})
    finish(valid,3,'A',[1,2],action='finish',selection=['W002-I001'])
    assert c.validate(c.workspace(valid,3))['next_action']=='finish'
    assert all(path.read_bytes()==value for path,value in rejected.items())
    assert review.unavailable(root,3) is None
    assert review.unavailable(valid,3) is None


def test_ordinary_a_and_pass_text_do_not_gain_formal_qualification(tmp_path):
    root=prepare(tmp_path,['fixture'],enabled=False)
    finish(root,1,'H',[],action='analyze')
    _,_,record=finish(root,2,'A',[1],text='pass: independent and ready to execute')
    assert 'formal_review' not in c.read(record/'result-identity.json')
    assert 'formal_review' not in c.result_schema('A')['properties']['next_action']['enum']
    assert set(review.schema()['properties'])=={'verdict','summary','scientific_result','limitations'}
    ordinary=c.build_job(root,3,'A',[2],instruction='Ordinary scientific continuation')
    assert ordinary['direct_refs'][0]['path']=='inputs/parents/002/conclusion.json'
    c._validate_inputs(root,c.workspace(root,3),ordinary)
    original=c.read(record/'result-identity.json')['raw_ref']
    assert ordinary['direct_refs'][0]['size_bytes']==original['size_bytes']
    assert Path(original['path'])==record/'science-output.json'
    assert c.read(Path(original['path']))['scientific_result']=='pass: independent and ready to execute'
    assert not (c.workspace(root,3)/ordinary['direct_refs'][0]['path']).exists()


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
    # Exact review material production supplies a sparse known seal/job view.
    # Qualification completes only its missing control locators and cannot
    # reopen the already loaded scientific producer originals or its admission.
    from scidiscovery.research_loop import accepted_budget,accepted_prefix
    known={}
    binding=review.input_binding(root,job['formal_review'],sources=known)
    head=accepted_budget.read(root);base=c.settings(root)
    original_read=c.read
    original_ref=q._ref
    seal_probes=[]
    producer_originals={root/'jobs/002.json',root/'native-evidence/002/result-identity.json'}
    rereads=[]
    def counted_read(path):
        if Path(path) in producer_originals:rereads.append(path)
        return original_read(path)
    def counted_ref(instance,path,**options):
        if Path(path)==root/'native-evidence/002/result-identity.json':seal_probes.append(Path(path))
        return original_ref(instance,path,**options)
    with monkeypatch.context() as observed:
        observed.setattr(c,'read',counted_read)
        observed.setattr(q,'_ref',counted_ref)
        observed.setattr(accepted_prefix,'reconcile',lambda *args,**kwargs:pytest.fail('Qualification must not renew producer admission'))
        facts=q.input_facts(root,job,base=base,head=head,sources=known,review_binding=binding)
    assert rereads==[] and seal_probes==[root/'native-evidence/002/result-identity.json']
    assert 'seal_path' in facts['producers'][2] and 'raw_path' in facts['producers'][2]
    assert [edge['step'] for edge in facts['producer_edges']]==[2]
    with monkeypatch.context() as missing:
        missing.setattr(review,'input_binding',lambda *args,**kwargs:pytest.fail('Missing explicit binding must not lazily reload review inputs'))
        with pytest.raises(q.IntegrityError,match='explicit review binding'):
            q.input_facts(root,job,base=base,head=head,sources={})
    work=c.workspace(root,4);c._validate_inputs(root,work,job)
    assert job['direct_refs']==[] and len(job['direct_materials'])==1
    assert set(job['history'])=={'prefix','breadth_snapshot','inputs','view'}
    assert job['history']==binding['history']
    assert job['history']['inputs']==[] and job['history']['view']=={}
    view=c.read(work/'science-task.json')
    assert not {'history','parents','lineage','research'} & set(view)
    assert view['review']['subject']==job['direct_materials'][0]['path']
    assert view['global_inputs']==[row['path'] for row in job['global_materials']]
    assert not (work/'inputs/history').exists() and not (work/'inputs/parents/003').exists()
    assert all(anchor.encode() not in Path(row['source']).read_bytes()
        for row in [*job['global_materials'],*job['direct_materials']])
    assert anchor in c.read(record/'science-output.json')['scientific_result']
    source=q._read(root,job['source_provenance'])
    assert [edge['step'] for edge in source['edges'] if edge['kind']=='producer']==[2]
    assert not any(edge['kind']=='unknown' for edge in source['edges'])
    changed=dict(job,selected_inputs=[dict(job['selected_inputs'][0],origin_step=1)])
    retained={path:path.read_bytes() for path in (
        root/'jobs/004.json',work/'science-task.json',root/job['source_provenance']['path'])}
    with monkeypatch.context() as rejected:
        rejected.setattr(c,'load_validated_input_facts',lambda *args,**kwargs:
            pytest.fail('Canonical mismatch must reject before consuming polluted scientific inputs'))
        with pytest.raises(ValueError,match='Scientific inputs differ from their accepted canonical job'):
            c._validate_inputs(root,work,changed)
    assert all(path.read_bytes()==contents for path,contents in retained.items())


def test_review_subject_from_requester_keeps_artifact_but_not_its_conclusion(tmp_path):
    from scidiscovery.research_loop import accepted_budget, qualification as q
    root=prepare(tmp_path,['codex'])
    finish(root,1,'H',[],action='analyze')
    anchor='REQUESTER_SELF_ASSESSMENT_MUST_NOT_BE_DEFAULT_CONTEXT'
    _,_,record=finish(root,2,'A',[1],text=anchor,files={'subject.txt':'the exact subject','unrelated.txt':anchor})
    seal=q._ref(root,record/'result-identity.json')
    materials=c._public_materials(c.read(record/'result-identity.json')['materials'])
    material=next(item for item in materials if item['path'].endswith('/subject.txt'))
    head=accepted_budget.read(root)
    # A host-bound exact request fixture, not an independence/production qualification.
    reference=q._save(root,record/'formal-review-request.json',dict(contract=review.contract(root),question=review.QUESTION,
        requester_rationale=anchor,requester=dict(step=2,seal=seal),
        prefix=head['prefix'],breadth_snapshot=head['search_ref'],
        subject=dict(producer_step=2,producer_seal=seal,material=material),
        authors=dict(complete=False,participants=[]),unresolved_selection=None,reason=None))
    back=dict(kind='formal_review',request=reference)
    job=c.build_job(root,3,'A',[2],instruction=review.QUESTION,evidence_return=back)
    work=c.workspace(root,3);c._validate_inputs(root,work,job)
    assert job['direct_materials']==[material] and job['direct_refs']==[]
    assert Path(material['source']).read_text()=='the exact subject'
    assert not (work/material['path']).exists()
    assert not (work/'inputs/parents/002/conclusion.json').exists()
    assert not (work/'inputs/parents/002/artifacts/unrelated.txt').exists()
    assert not (work/'inputs/history').exists()
    assert controller._check_job(root,c.settings(root),3,'A',[2],review.QUESTION,back)==job


def test_unavailable_review_preserves_existing_nonreview_author_correction(tmp_path):
    from test_decision_contract import REQUEST, author, next_worker
    root=prepare(tmp_path,['codex'],steps=8,decision_contract_version=1)
    finish(root,1,'H',[],action='experiment')
    _,_,record=finish(root,2,'W',[1],files={'model.txt':'a scientific implementation'})
    author(root,3,[1,2],REQUEST)
    next_worker(root,text=c.read(record/'science-output.json')['scientific_result'])
    # A completed bounded request with no new assessed originals is a genuine
    # nonreview correction; a missing request is same-output repair instead.
    job,science=author(root,5,[1,2,3],dict(REQUEST))
    route=semantic.effective_route(root,5,science,job,(*controller._route(root,5,'A',science,job,1,4),None))
    prior=route[7]
    assert prior['reason']=='a_decision_request_repeated' and prior['root']['step']==5
    c.build_job(root,6,'A',route[1],instruction=route[2],author_correction=prior)
    job,_,_=finish(root,6,'A',route[1],action='formal_review',selection=['W002-I001'])
    science=c.validate(c.workspace(root,6))
    route=semantic.effective_route(root,6,science,job,(*controller._route(root,6,'A',science,job,1,4),None))
    assert route[7]['root']==prior['root'] and route[7]['reason']==prior['reason']
    assert route[7]['source']['step']==6 and route[3]['kind']=='formal_review_unavailable'
    assert semantic.accepted(root,route[7],7)==1
    child=c.build_job(root,7,'A',route[1],instruction=route[2],evidence_return=route[3],author_correction=route[7])
    assert child['author_correction']==route[7] and not child.get('formal_review')


def compact_identity_fixture():
    """Synthetic authentic shape, never a replacement session_meta header."""
    thread='11111111-2222-4333-8444-555555555555'
    session=dict(source='native_metadata_hooks', thread_id=thread, home='/native',
        workspace='/workspace', path='/native/sessions/original.jsonl', identity=[1,2], offset=100)
    event=dict(type='thread.started', thread_id=thread)
    outcome=dict(thread_id=thread, native_identity=dict(backend='codex',
        participant_scope='codex_exec_single_agent_v1',session=session), native_thread_event=event,
        budget_notification=dict(status='available'))
    row=dict(attempt=0, proof_kind='native_metadata_hooks_v1',intent={'path':'intent'},
        outcome={'path':'outcome'}, session=dict(session,thread_event=event))
    metadata=dict(version=1,kind='native_metadata_hooks_v1',intent=row['intent'],outcome=row['outcome'],session=row['session'])
    return row,metadata,outcome,dict(task_ref={'path':'job'}),dict(task_ref={'path':'job'})


def test_compact_native_authorship_proof_keeps_exact_session_and_attempt():
    row,metadata,outcome,intent,process=compact_identity_fixture()
    assert review._metadata_participant(row,metadata,outcome,intent,process,'/workspace',{'path':'intent'}) == (
        'codex_exec','11111111-2222-4333-8444-555555555555')
    assert 'header' not in row and 'header' not in metadata


@pytest.mark.parametrize('mutation',['borrowed_thread','borrowed_workspace','borrowed_process','unqualified_hook','replaced_receipt'])
def test_compact_native_authorship_rejects_borrowed_or_unqualified_facts(mutation):
    import copy
    row,metadata,outcome,intent,process=copy.deepcopy(compact_identity_fixture())
    if mutation=='borrowed_thread':outcome['native_thread_event']['thread_id']='another'
    elif mutation=='borrowed_workspace':outcome['native_identity']['session']['workspace']='/another'
    elif mutation=='borrowed_process':process['task_ref']={'path':'another-job'}
    elif mutation=='unqualified_hook':outcome['budget_notification']['status']='unavailable'
    else:metadata['outcome']={'path':'another-outcome'}
    with pytest.raises(ValueError,match='compact native identity'):
        review._metadata_participant(row,metadata,outcome,intent,process,'/workspace',{'path':'intent'})
