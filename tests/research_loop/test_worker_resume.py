"""Same-session native continuation through real detached local processes; no models."""
import json
import sys
import time
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, continuation
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from scidiscovery.research_loop.cli import status


FAKE = r'''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
role=os.environ['THIN_HWA_ROLE']
thread='11111111-2222-4333-8444-555555555555'
with (work/'work/launches').open('a') as f:f.write('resume\n' if 'resume' in args else 'exec\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
if 'resume' not in args and role!='A':
 p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
 (work/'work/checkpoint').write_text('original checkpoint')
 sys.exit(7)
if 'resume' in args:assert thread in args and (work/'work/checkpoint').read_text()=='original checkpoint'
assert pathlib.Path(os.environ['CODEX_HOME'])==home
payload={'summary':'Continued scientific finding','scientific_result':'Original checkpoint was retained.','limitations':['Fake transport only'],'implementation_selection':None}
if role=='W':
 payload.update(mechanism_findings=None,stage_intent='review')
 task=json.loads((work/'science-task.json').read_text());assigned=task.get('lineage',{}).get('mechanisms',{})
 nominations=task.get('candidate_nomination_mechanisms',[])
 if nominations:
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='This transport fixture produces no scientifically suitable candidate and preserves the retained pool for A.') for row in nominations if row['status']!='waived'])
 if assigned.get('task_action')=='screen' or 'Screen the assigned H combination' in task.get('question',''):
  payload['mechanism_findings']=[dict(mechanism=row['mechanism_ref'],basis='blocked',finding='The checkpoint transport fixture has no scientific measurement for this assigned mechanism.') for row in nominations if row['status']=='pending']
if role=='H':payload.update(search_plan=None,next_action='wait',route_reason='Bounded contribution complete')
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='finish',route_reason='Independent fake conclusion')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':5,'output_tokens':2}}),flush=True)
if (work/'work/fail-again').exists():sys.exit(7)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


REPAIR_FAKE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
role=os.environ['THIN_HWA_ROLE'];thread='11111111-2222-4333-8444-555555555555'
resume='resume' in args
with (work/'work/launches').open('a') as f:f.write('resume\n' if resume else 'exec\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
if not resume:
 p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
 (work/'work/completed-experiment').write_text('one completed execution')
else:
 assert thread in args and pathlib.Path(os.environ['CODEX_HOME'])==home
 assert (work/'work/completed-experiment').read_text()=='one completed execution'
 assert 'Repair only your final response' in prompt and 'do not rerun or resubmit' in prompt
 (work/'work/repair-prompt').write_text(prompt)
kind=(work/'work/output-kind').read_text()
if kind!='unknown':print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
payload={'summary':'Retained finding','scientific_result':'Completed experiment retained.','limitations':[],'implementation_selection':None}
if role=='W':payload.update(mechanism_findings=None,stage_intent='review')
if role=='H':payload.update(search_plan=None,next_action='wait',route_reason='Bounded output repaired')
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='finish',route_reason='Bounded output repaired')
destination=pathlib.Path(args[args.index('--output-last-message')+1])
if resume and (not (work/'work/repeat-bad-output').exists() or
 (work/'work/valid-after-second').exists() and (work/'work/launches').read_text().count('resume\n')>=2):
 destination.write_text(json.dumps(payload));sys.exit(0)
if kind=='missing':sys.exit(0)
if kind=='unsafe':destination.symlink_to(work/'work/completed-experiment');sys.exit(0)
if kind=='badjson':destination.write_text('{invalid JSON');sys.exit(0)
if kind=='missingfield':del payload['summary']
if kind=='wrongtype':payload['limitations']='not an array'
if kind=='empty':payload['scientific_result']='  '
destination.write_text(json.dumps(payload))
'''


# H app-server final-response repair and explicit incomplete-final controls.
# The legacy producers above retain their exact transport limitations.
QUALIFIED_H_OUTPUT_REPAIR_NATIVE = r'''import json,os,pathlib,shlex,subprocess,sys,tomllib
args=sys.argv[1:];work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);config={}
assert os.environ['THIN_HWA_ROLE']=='H'
kind=(work/'work/output-kind').read_text();assert kind in {'valid','badjson','missingfield','wrongtype','empty','missing-final','unknown'}
def merge(target,update):
 for key,value in update.items():
  if isinstance(value,dict) and isinstance(target.get(key),dict):merge(target[key],value)
  else:target[key]=value
for index,arg in enumerate(args[:-1]):
 if arg=='-c':merge(config,tomllib.loads(args[index+1]))
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
def receive():return json.loads(sys.stdin.readline())
request=receive();assert request['method']=='initialize';send(dict(id=request['id'],result={}))
assert receive()['method']=='initialized';request=receive()
if request['method']=='hooks/list':
 assert request['params']==dict(cwds=[str(work)])
 hooks=[]
 for name,key,event in [('PostToolUse','post_tool_use','postToolUse'),('PostModelRequest','post_model_request','postModelRequest'),('PostCompact','post_compact','postCompact')]:
  if name not in config.get('hooks',{}):continue
  handler=config['hooks'][name][0]['hooks'][0]
  hooks.append(dict(key='/<session-flags>/config.toml:'+key+':0:0',eventName=event,
   enabled=True,isManaged=False,trustStatus='untrusted',currentHash='sha256:'+'0'*64,
   source='sessionFlags',sourcePath='/<session-flags>/config.toml',handlerType='command',
   command=handler['command'],timeoutSec=handler['timeout'],additionalContextLimit=handler.get('additionalContextLimit')))
 send(dict(id=request['id'],result=dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])))
 raise SystemExit(0)
assert request['method'] in {'thread/start','thread/resume'}
start=request['params'];resumed=request['method']=='thread/resume';thread='11111111-2222-4333-8444-555555555555'
assert start['cwd']==str(work) and start['approvalPolicy']=='never'
assert '--dangerously-bypass-hook-trust' in args and start['config']==dict(bypass_hook_trust=True)
assert start['sandbox']=='workspace-write' and config['features']['multi_agent'] is False
assert not resumed or start['threadId']==thread and start['excludeTurns'] is True
send(dict(id=request['id'],result=dict(thread=dict(id=thread,turns=[]),model=start['model'],cwd=str(work),
 approvalPolicy='never',sandbox=dict(type='workspaceWrite',writableRoots=[],networkAccess=False),
 reasoningEffort=config['model_reasoning_effort'],serviceTier=start.get('serviceTier'))))
request=receive();assert request['method']=='turn/start';turn=request['params']
assert turn['threadId']==thread and turn['model']==start['model'] and turn['approvalPolicy']=='never'
assert turn.get('effort')==config['model_reasoning_effort'] and turn.get('serviceTier')==start.get('serviceTier')
assert len(turn['input'])==1 and turn['input'][0]['type']=='text'
prompt=turn['input'][0]['text'];turn_id='repair-turn' if resumed else 'original-turn'
send(dict(id=request['id'],result=dict(turn=dict(id=turn_id,items=[],status='inProgress'))))
home=pathlib.Path(os.environ['CODEX_HOME']);assert home==pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
transcript=home/'sessions/opaque.jsonl'
with (work/'work/launches').open('a') as stream:stream.write('resume\n' if resumed else 'exec\n')
repair_number=(work/'work/launches').read_text().count('resume\n')
bad=kind!='valid' and (not resumed or (work/'work/repeat-bad-output').exists() and
 not ((work/'work/valid-after').exists() and repair_number>=int((work/'work/valid-after').read_text())))
if resumed:
 assert (work/'work/completed-experiment').read_text()=='one completed execution'
 assert 'Fix diagnosed JSON/layout only' in prompt and 'full JSON' in prompt and 'No work/rerun/resubmit' in prompt
 assert '"code":"'+('invalid_json' if kind in {'badjson','empty'} else 'invalid_fields')+'"' in prompt
 (work/'work/repair-prompt').write_text(prompt)
else:
 transcript.parent.mkdir(parents=True,exist_ok=True);transcript.write_bytes(b'opaque native original\n')
 (work/'work/completed-experiment').write_text('one completed execution')
handler=config['hooks']['PostModelRequest'][0]['hooks'][0]
event=dict(session_id=thread,cwd=str(work),transcript_path=str(transcript),hook_event_name='PostModelRequest',
 status='completed',token_usage=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))
delivery=subprocess.run(shlex.split(handler['command']),input=json.dumps(event),text=True,capture_output=True,timeout=handler['timeout'])
assert delivery.returncode==0,delivery.stderr
payload=dict(summary='Retained finding',scientific_result='Completed experiment retained.',
 limitations=['Mechanical transport only; no scientific qualification.'],implementation_selection=None,
 search_plan=None,next_action='wait',route_reason='Bounded output repaired')
if bad and kind=='missingfield':del payload['summary']
if bad and kind=='wrongtype':payload['limitations']='not an array'
raw=('{invalid JSON' if kind=='badjson' else '' if kind=='empty' else json.dumps(payload)) if bad else json.dumps(payload)
(work/('work/repaired-final.json' if resumed else 'work/original-final.json')).write_text(raw)
if kind!='missing-final':
 send(dict(method='item/completed',params=dict(threadId=thread,turnId=turn_id,
  item=dict(type='agentMessage',text=raw,phase='final_answer'))))
if kind=='unknown':raise SystemExit(0)
send(dict(method='turn/completed',params=dict(threadId=thread,turn=dict(id=turn_id,status='completed'))))
'''


def qualified_h_repair_instance(tmp_path, monkeypatch, kind, *, enabled=True, attempts=2):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Repair one retained H final response without repeating work.')
    home=tmp_path/'native-home';home.mkdir();monkeypatch.setenv('CODEX_HOME',str(home))
    launcher=tmp_path/'codex';launcher.write_text('#!'+sys.executable+'\n'+QUALIFIED_H_OUTPUT_REPAIR_NATIVE);launcher.chmod(0o700)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(launcher)],max_tokens=1000,max_seconds=120,max_steps=6,
        model='no-model',reasoning_effort='high',search_policy=None,experiment_mode='external',node_budget_mode='soft',
        native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False),
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=20) for role in 'HWAE'},
        resume_policy=dict(max_attempts=attempts,protocol='codex_exec_resume',usage_scope='turn',
            output_repair=enabled,**(dict(output_only=True,native_home=str(home)) if enabled else {})))
    c.build_job(root,1,'H',[])
    (c.workspace(root,1)/'work/output-kind').write_text(kind)
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','qualified_h_output_repair')
    return root


def retain_qualified_h_bad_original(root,monkeypatch):
    publish=c.seal_result_from_inventory
    class RetainedBadFinal(RuntimeError):pass
    def retain(root,step,*,inventory):
        try:return publish(root,step,inventory=inventory)
        except OutputRepairRequired as error:
            assert inventory.record==execution.origin(root,step)
            raise RetainedBadFinal('Original diagnosis retained before repair') from error
    with monkeypatch.context() as patch:
        patch.setattr(c,'seal_result_from_inventory',retain)
        with pytest.raises(RetainedBadFinal) as failure:native.execute_native(root,1)
    assert failure.value.__cause__.request()['code']=='invalid_json'
    first=wait_runner(root);outcome=c.read(first/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['terminal_observed'] is True and outcome['stop_reason'] is None
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'
    assert outcome['raw_ref']==c.file_ref(first/'science-output.json')
    assert (first/'science-output.json').read_bytes()==(c.workspace(root,1)/'work/original-final.json').read_bytes()
    assert native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1
    return first


def instance(tmp_path, *, cap=100, attempts=2, max_tokens=1000, steps=6, search_policy=None, delivery_policy=None,
             fake=FAKE, output_repair=False, with_policy=True):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Continue a bounded scientific task using its checkpoint.')
    launcher=tmp_path/'native.py';launcher.write_text(fake)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=max_tokens,
        max_seconds=120,max_steps=steps,model='no-model',reasoning_effort='high', node_budget_mode='soft',search_policy=search_policy,delivery_policy=delivery_policy,
        node_budget_policy={role:dict(max_observed_tokens=cap,max_seconds=20) for role in 'HWAE'},
        resume_policy=dict(max_attempts=attempts,protocol='codex_exec_resume',usage_scope='turn',
                           **(dict(output_repair=True) if output_repair else {})) if with_policy else None)
    return root


def wait_runner(root, step=1):
    # Terminal outcome precedes runner exit by a bounded transport gap.
    from scidiscovery.research_loop import runtime
    end=time.monotonic()+3
    record=execution.record(root,step)
    while runtime.process_matches(c.read(record/'runner-process.json'),execution.identity(root,step)):
        assert time.monotonic()<end
        time.sleep(.02)
    return record


def retain_metering_failed_original(tmp_path,monkeypatch):
    from test_native_cli import metering_instance
    from scidiscovery.research_loop import runtime
    tokens=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    root=metering_instance(tmp_path,monkeypatch,'resume',baseline=tokens,delta=tokens)
    assert controller.run(root)['status']=='technical_pause'
    first=wait_runner(root);outcome=c.read(first/'native-outcome.json')
    assert outcome['stop_reason']=='native_failure_event' and outcome['cleanup']=='observed_group_cleaned'
    assert outcome['returncode']==1 and outcome['native_started'] is True and outcome['terminal_observed'] is False
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert c.read(first/'usage.json')==tokens and native.usage(root)['observed_tokens']==14
    assert (c.workspace(root,1)/'work/checkpoint').read_text()=='original checkpoint'
    # The detached watchdog observes the outcome on its own polling turn,
    # after the runner has stopped. Settle that exact original owner before
    # asking recovery to prove every retained writer is stopped.
    watchdog=c.read(first/'watchdog-process.json');binding=c.record_ref(root/'jobs/001.json')
    assert watchdog['task_ref']==binding
    until=time.monotonic()+3
    while runtime.process_matches(watchdog,binding):
        assert time.monotonic()<until,dict(phase='original_watchdog_cleanup',record=str(first),watchdog=watchdog)
        time.sleep(.02)
    return root,first,outcome


def fail(root, step=1):
    assert controller.run(root)['status']=='native_stopped'
    return wait_runner(root, step)


def repair_instance(tmp_path, role='H', kind='badjson', *, enabled=True, attempts=2, cap=100, max_tokens=1000):
    root=instance(tmp_path, fake=REPAIR_FAKE, output_repair=enabled, attempts=attempts, cap=cap,max_tokens=max_tokens)
    c.build_job(root,1,role,[])
    (c.workspace(root,1)/'work').mkdir(exist_ok=True)
    (c.workspace(root,1)/'work/output-kind').write_text(kind)
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','fake_output_repair')
    return root


@pytest.mark.parametrize('kind', ['badjson','missingfield'])
@pytest.mark.process_e2e
def test_completed_output_repair_is_same_role_node_session_without_experiment_replay(tmp_path,monkeypatch,kind):
    assert_qualified_h_output_repair(tmp_path,monkeypatch,kind)


def assert_qualified_h_output_repair(tmp_path,monkeypatch,kind):
    import hashlib
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,kind)
    first=execution.origin(root,1)
    inputs={p:p.read_bytes() for p in (root/'jobs/001.json',first/'science-result.schema.json',c.workspace(root,1)/'science-task.json')}
    assert native.execute_native(root,1)['role']=='H'
    first=execution.origin(root,1);current=execution.record(root,1)
    assert current==first/'continuations/001'
    assert c.read(first/'native-outcome.json')['returncode']==0
    assert (first/'science-output.json').read_bytes()!=execution.raw(root,1).read_bytes()
    intent=c.read(current/'native-intent.json')
    assert intent['output_repair']['raw_ref']==c.file_ref(first/'science-output.json')
    assert intent['session']['workspace']==str(c.workspace(root,1))
    assert intent['session']['home']==str(root.parent/'native-home')
    assert intent['session']['thread_id']=='11111111-2222-4333-8444-555555555555'
    assert native.usage(root)['observed_tokens']==28 and budget.used_steps(root)==2
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    before={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    assert intent['continuation_kind']=='repair' and intent['native_profile']==c.read(first/'native-intent.json')['native_profile']
    assert intent['output_repair']['code']==('invalid_json' if kind in {'badjson','empty'} else 'invalid_fields')
    if kind=='missingfield':assert 'summary' not in json.loads((first/'science-output.json').read_text())
    if kind=='wrongtype':assert json.loads((first/'science-output.json').read_text())['limitations']=='not an array'
    if kind=='empty':assert (first/'science-output.json').read_bytes()==b''
    for record,name in ((first,'original-final.json'),(current,'repaired-final.json')):
        outcome=c.read(record/'native-outcome.json')
        assert outcome['budget_notification']['status']=='available'
        assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
        assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'
        raw=(record/'science-output.json').read_bytes()
        assert raw==(c.workspace(root,1)/'work'/name).read_bytes()
        assert outcome['terminal_evidence']['final']==dict(sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
    head=accepted_budget.read(root)
    assert head['dispatch_count']==2 and head['continuation_count']==1
    repaired={p:p.read_bytes() for p in current.iterdir() if p.is_file()}
    assert native.wait_native(root,1)['role']=='H'
    assert all(p.read_bytes()==data for p,data in inputs.items())
    assert all(p.read_bytes()==data for p,data in repaired.items())
    assert len(execution.records(root,1))==2 and not (root/'jobs/002.json').exists()
    assert native.usage(root)['observed_tokens']==28 and budget.used_steps(root)==2
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    assert all(p.read_bytes()==data for p,data in before.items())
    assert (c.workspace(root,1)/'work/completed-experiment').read_text()=='one completed execution'


@pytest.mark.parametrize('kind', ['wrongtype','empty'])
@pytest.mark.process_e2e
def test_known_output_failure_keeps_execution_facts_and_repairs(tmp_path,monkeypatch,kind):
    assert_qualified_h_output_repair(tmp_path,monkeypatch,kind)


@pytest.mark.process_e2e
def test_old_resume_policy_does_not_automatically_repair_successful_bad_output(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson',enabled=False)
    settings=(root/'settings.json').read_bytes()
    assert c.settings(root)['resume_policy']['output_repair'] is False
    assert 'output_only' not in c.settings(root)['resume_policy'] and 'native_home' not in c.settings(root)['resume_policy']
    with pytest.raises(OutputRepairRequired) as failure:native.execute_native(root,1)
    assert failure.value.request()['code']=='invalid_json'
    first=wait_runner(root);outcome=c.read(first/'native-outcome.json')
    assert outcome['native_started'] is True and outcome['terminal_observed'] is True
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['budget_notification']['status']=='available'
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert outcome['native_identity']['session']['home']==str(execution.home(root,1))
    assert outcome['thread_id']=='11111111-2222-4333-8444-555555555555'
    assert c.read(first/'native-intent.json')['native_profile']==c.read(root/'jobs/001.json')['native_profile']
    assert (first/'science-output.json').read_bytes()==(c.workspace(root,1)/'work/original-final.json').read_bytes()
    original={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    inputs={p:p.read_bytes() for p in (root/'jobs/001.json',first/'science-result.schema.json',c.workspace(root,1)/'science-task.json')}
    session=c.read(first/'native-outcome.json').get('native_identity',{}).get('session')
    if session is not None:inputs[Path(session['path'])]=Path(session['path']).read_bytes()
    with pytest.raises(OutputRepairRequired) as failure:native.wait_native(root,1)
    assert failure.value.request()['code']=='invalid_json'
    with pytest.raises(OutputRepairRequired) as failure:controller.worker_resume(root,1)
    assert failure.value.request()['code']=='invalid_json'
    assert len(execution.records(root,1))==1 and not (first/'result-identity.json').exists()
    assert accepted_budget.read(root)['dispatch_count']==1 and budget.used_steps(root)==1
    assert native.usage(root)['observed_tokens']==14
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert all(p.read_bytes()==raw for p,raw in original.items())
    assert (root/'settings.json').read_bytes()==settings
    assert all(p.read_bytes()==raw for p,raw in inputs.items())
    assert not (root/'jobs/002.json').exists()


@pytest.mark.process_e2e
def test_custom_exec_without_resume_policy_retains_typed_output_error(tmp_path):
    # The supported custom exec transport has no standard-Codex default policy.
    # This policy boundary does not claim a qualified native session.
    root=instance(tmp_path,fake=REPAIR_FAKE,with_policy=False)
    c.build_job(root,1,'H',[])
    (c.workspace(root,1)/'work').mkdir(exist_ok=True)
    (c.workspace(root,1)/'work/output-kind').write_text('badjson')
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','fake_default_output')
    assert 'resume_policy' not in c.settings(root)
    settings=(root/'settings.json').read_bytes()
    with pytest.raises(OutputRepairRequired) as failure:native.execute_native(root,1)
    assert failure.value.request()['code']=='invalid_json'
    first=wait_runner(root)
    assert c.read(first/'native-outcome.json')['raw_ref']==c.file_ref(first/'science-output.json')
    original={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    inputs={p:p.read_bytes() for p in (root/'jobs/001.json',first/'science-result.schema.json',c.workspace(root,1)/'science-task.json')}
    session=c.read(first/'native-outcome.json').get('native_identity',{}).get('session')
    if session is not None:inputs[Path(session['path'])]=Path(session['path']).read_bytes()
    with pytest.raises(OutputRepairRequired) as failure:native.wait_native(root,1)
    assert failure.value.request()['code']=='invalid_json'
    assert len(execution.records(root,1))==1 and not (first/'result-identity.json').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1
    assert all(p.read_bytes()==raw for p,raw in original.items())
    assert (root/'settings.json').read_bytes()==settings
    assert all(p.read_bytes()==raw for p,raw in inputs.items())
    assert not (root/'jobs/002.json').exists()


@pytest.mark.process_e2e
def test_output_repair_shares_original_attempt_limit_and_retains_all_bad_outputs(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson',attempts=2)
    (c.workspace(root,1)/'work/repeat-bad-output').touch()
    with pytest.raises(continuation.ResumeUnavailable,match='^worker_resume_limit$'):native.execute_native(root,1)
    wait_runner(root);paths=execution.records(root,1);first=paths[0]
    assert len(paths)==3 and native.usage(root)['observed_tokens']==42 and budget.used_steps(root)==3
    for number,record in enumerate(paths):
        outcome=c.read(record/'native-outcome.json');intent=c.read(record/'native-intent.json')
        assert outcome['returncode']==0 and outcome['terminal_observed'] is True and outcome['stop_reason'] is None
        assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
        assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'
        assert outcome['raw_ref']==c.file_ref(record/'science-output.json')
        assert (record/'science-output.json').read_text()=='{invalid JSON'
        assert outcome['budget_slice']['observed_tokens']==14*(number+1)
        assert outcome['budget_slice']['dispatches']==number+1
        if number:
            assert intent['continuation_kind']=='repair' and intent['attempt']==number
            assert intent['budget_slice']['previous_tokens']==14*number
            assert intent['output_repair']['raw_ref']==c.file_ref(paths[number-1]/'science-output.json')
            assert intent['output_repair']['source_attempt']==number-1
            assert intent['native_profile']==c.read(paths[0]/'native-intent.json')['native_profile']
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json',
        Path(c.read(first/'native-outcome.json')['native_identity']['session']['path']))}
    retained={p:p.read_bytes() for record in paths for p in record.iterdir() if p.is_file()}
    for _ in range(2):
        with pytest.raises(continuation.ResumeUnavailable,match='^worker_resume_limit$'):native.wait_native(root,1)
        head=accepted_budget.read(root)
        assert head['dispatch_count']==3 and head['continuation_count']==2
        assert len(execution.records(root,1))==3 and not (paths[0]/'continuations/003').exists()
        assert native.usage(root)['observed_tokens']==42 and budget.used_steps(root)==3
        assert all(p.read_bytes()==raw for p,raw in inputs.items())
        assert all(p.read_bytes()==raw for p,raw in retained.items())
        assert not (paths[0]/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\nresume\n'


@pytest.mark.process_e2e
def test_output_repair_does_not_replenish_insufficient_shared_budget(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson')
    first=retain_qualified_h_bad_original(root,monkeypatch)
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json',
        Path(c.read(first/'native-outcome.json')['native_identity']['session']['path']))}
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    amendment=budget.amend(root,max_tokens=native.usage(root)['accounted_tokens'],
        authorization_note='Explicit fixture reduction to actual consumed usage after native completion')
    assert amendment['after']['max_tokens']==14 and amendment['consumption']=='retained'
    for _ in range(2):
        with pytest.raises(budget.NodeBudgetUnavailable) as failure:native.wait_native(root,1)
        assert failure.value.reason=='worker_resume_budget_unavailable'
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert len(execution.records(root,1))==1 and not (first/'continuations/001').exists()
        assert native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1
        assert budget.snapshot(root)['max_tokens']==14
        assert all(p.read_bytes()==raw for p,raw in inputs.items())
        assert all(p.read_bytes()==raw for p,raw in retained.items())
        assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('attempts', [1,2])
@pytest.mark.process_e2e
def test_terminal_repair_rechecks_initial_bad_output_before_seal(tmp_path,monkeypatch,attempts):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson',attempts=attempts)
    first=execution.origin(root,1)
    if attempts==2:
        (c.workspace(root,1)/'work/repeat-bad-output').touch()
        (c.workspace(root,1)/'work/valid-after').write_text('2')
    publish=c.seal_result_from_inventory
    class DeferSeal(RuntimeError):pass
    def terminal_before_seal(root,step,*,inventory):
        if inventory.attempt==attempts:
            assert inventory.payload['summary']=='Retained finding' and inventory.output_error is None
            assert inventory.native_outcome['returncode']==0 and inventory.native_outcome['terminal_observed'] is True
            raise DeferSeal('Known repaired turn retained before publication')
        return publish(root,step,inventory=inventory)
    with monkeypatch.context() as patch:
        patch.setattr(c,'seal_result_from_inventory',terminal_before_seal)
        with pytest.raises(DeferSeal):native.execute_native(root,1)
    wait_runner(root);paths=execution.records(root,1)
    assert len(paths)==attempts+1 and native.usage(root)['observed_tokens']==14*(attempts+1)
    assert budget.used_steps(root)==attempts+1
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json',
        Path(c.read(first/'native-outcome.json')['native_identity']['session']['path']))}
    retained={p:p.read_bytes() for record in paths for p in record.iterdir() if p.is_file()}
    accept_terminal=accepted_budget.accept_terminal_from_base;collect_sources=c._repair_source_facts
    observations=[]
    def accept_bill(root,step,record,**kwargs):
        head=accept_terminal(root,step,record,**kwargs)
        row=head.row('attempt/'+record.relative_to(root).as_posix().replace('/',':'))
        assert record==paths[-1] and row['status']=='closed' and row['meter']==kwargs['meter']
        observations.append('bill')
        return head
    def collect_after_bill(intents,receipts):
        assert observations==['bill']
        observations.append('sources')
        return collect_sources(intents,receipts)
    # This observes composition on an already accepted bill, not its first ledger write.
    with monkeypatch.context() as patch:
        patch.setattr(accepted_budget,'accept_terminal_from_base',accept_bill)
        patch.setattr(c,'_repair_source_facts',collect_after_bill)
        # Each paid malformed source remains bound, including the intermediate turn.
        for record in paths[:-1]:
            raw=record/'science-output.json';original=raw.read_bytes()
            assert original==b'{invalid JSON'
            raw.write_text('{changed retained bad output')
            observations.clear()
            with pytest.raises(ValueError,match='Accepted repair raw original changed'):native.wait_native(root,1)
            assert observations==['bill','sources']
            assert raw.read_text()=='{changed retained bad output'
            raw.write_bytes(original)
            assert all(p.read_bytes()==raw for p,raw in inputs.items())
            assert all(p.read_bytes()==data for p,data in retained.items())
            head=accepted_budget.read(root)
            assert head['dispatch_count']==attempts+1 and head['continuation_count']==attempts
            assert len(execution.records(root,1))==attempts+1 and budget.used_steps(root)==attempts+1
            assert native.usage(root)['observed_tokens']==14*(attempts+1)
            assert not (first/'result-identity.json').exists() and not (first/f'continuations/{attempts+1:03d}').exists()
        observations.clear()
        assert native.wait_native(root,1)['next_action']=='wait'
        assert observations==['bill','sources'] and (first/'result-identity.json').is_file()
    def forbid_sources(*args,**kwargs):raise AssertionError('Sealed wait reread historical repair sources')
    with monkeypatch.context() as patch:
        patch.setattr(c,'_repair_source_facts',forbid_sources)
        for _ in range(2):
            assert native.wait_native(root,1)['next_action']=='wait'
            head=accepted_budget.read(root)
            assert head['dispatch_count']==attempts+1 and head['continuation_count']==attempts
            assert len(execution.records(root,1))==attempts+1 and budget.used_steps(root)==attempts+1
            assert native.usage(root)['observed_tokens']==14*(attempts+1)
            assert all(p.read_bytes()==raw for p,raw in inputs.items())
            assert all(p.read_bytes()==data for p,data in retained.items())
            assert not (first/f'continuations/{attempts+1:03d}').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'+'resume\n'*attempts
    assert not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('kind,code,phase', [
    pytest.param('missing-final','final_unavailable','turn_completion',id='missing-final'),
    pytest.param('unknown','native_terminal_unreported','event_stream',id='unknown')])
@pytest.mark.process_e2e
def test_incomplete_native_final_retains_unknown_original_without_repair(tmp_path,monkeypatch,kind,code,phase):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,kind)
    settings=(root/'settings.json').read_bytes()
    with pytest.raises(ValueError,match='Terminal scientific output is unverified or changed'):
        native.execute_native(root,1)
    first=wait_runner(root);outcome=c.read(first/'native-outcome.json')
    assert outcome['native_started'] is True and outcome['returncode']==1
    # Transport diagnostics retain the unknown call; they are not turn.failed.
    assert outcome['terminal_observed'] is False and outcome['stop_reason'] is None
    assert outcome['native_failure'] is None
    assert outcome['cleanup']=='observed_group_cleaned' and outcome['budget_notification']['status']=='available'
    assert outcome['technical_diagnostic']==dict(phase=phase,code=code,original_call_state='unknown',thread_bound=True,turn_bound=True,next_action='reconcile_original_without_replay')
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert outcome['metering']['coverage']=='unknown' and outcome['metering']['reason']=='terminal_usage_unreported'
    assert not (first/'science-output.json').exists() and not (first/'result.pending.json').exists()
    assert 'output_status' not in outcome and outcome.get('raw_ref') is None
    assert (outcome['terminal_evidence']['final'] is None)==(kind=='missing-final')
    assert native.usage(root)['unknown_steps']==[1] and native.usage(root)['observed_tokens']==14
    original={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    inputs={p:p.read_bytes() for p in (root/'jobs/001.json',first/'science-result.schema.json',c.workspace(root,1)/'science-task.json',Path(outcome['native_identity']['session']['path']))}
    with pytest.raises(ValueError,match='Terminal scientific output is unverified or changed'):
        native.wait_native(root,1)
    assert len(execution.records(root,1))==1 and not (first/'result-identity.json').exists()
    assert accepted_budget.read(root)['dispatch_count']==1 and budget.used_steps(root)==1
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert all(p.read_bytes()==raw for p,raw in original.items())
    assert (root/'settings.json').read_bytes()==settings
    assert all(p.read_bytes()==raw for p,raw in inputs.items())
    assert not (root/'jobs/002.json').exists()


@pytest.mark.process_e2e
def test_redirected_native_final_original_never_starts_repair(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson')
    first=execution.origin(root,1)
    # Scheduling only: dispatch remains real; defer collection until this exact
    # native writer has finished. Acceptance, output publication and guards run.
    with monkeypatch.context() as patch:
        patch.setattr(native,'wait_native',lambda *args,**kwargs:None)
        native.execute_native(root,1)
    end=time.monotonic()+10
    while not (first/'native-outcome.json').exists():
        assert time.monotonic()<end
        time.sleep(.02)
    wait_runner(root)
    outcome=c.read(first/'native-outcome.json')
    assert outcome['native_started'] is True and outcome['terminal_observed'] is True
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    raw=first/'science-output.json';retained=first/'retained-original-final.json'
    original=raw.read_bytes()
    assert original==(c.workspace(root,1)/'work/original-final.json').read_bytes()
    assert outcome['raw_ref']==c.file_ref(raw)
    raw.rename(retained);raw.symlink_to(retained)
    originals={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    inputs={p:p.read_bytes() for p in (root/'jobs/001.json',first/'science-result.schema.json',c.workspace(root,1)/'science-task.json',Path(outcome['native_identity']['session']['path']))}
    settings=(root/'settings.json').read_bytes()
    for _ in range(2):
        with pytest.raises(ValueError,match='^Expected an original regular file$'):native.wait_native(root,1)
        assert raw.is_symlink() and raw.readlink()==retained and retained.read_bytes()==original
        assert all(p.read_bytes()==data for p,data in originals.items())
        assert len(execution.records(root,1))==1 and not (first/'result-identity.json').exists()
        assert accepted_budget.read(root)['dispatch_count']==1 and budget.used_steps(root)==1
        assert native.usage(root)['observed_tokens']==14
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
        assert (root/'settings.json').read_bytes()==settings
        assert all(p.read_bytes()==data for p,data in inputs.items())
        assert not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('mutation', ['raw','task_binding','schema_missing','profile'])
@pytest.mark.process_e2e
def test_output_repair_does_not_soften_original_integrity_failure(tmp_path,monkeypatch,mutation):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson')
    first=retain_qualified_h_bad_original(root,monkeypatch)
    if mutation=='raw':(first/'science-output.json').write_text('{different broken output')
    elif mutation=='task_binding':
        task_path=c.workspace(root,1)/'science-task.json'
        task=c.read(task_path);task['global']['objective']='inputs/global/changed-objective.md';c.write(task_path,task)
    elif mutation=='profile':
        intent=c.read(first/'native-intent.json');intent['native_profile']['model']='changed';c.write(first/'native-intent.json',intent)
    else:(first/'science-result.schema.json').unlink()
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json')}
    for _ in range(2):
        with pytest.raises(ValueError):native.wait_native(root,1)
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert not (first/'continuations/001').exists() and not (first/'result-identity.json').exists()
        assert budget.used_steps(root)==1
        assert {key:head['usage'][key] for key in ('input_tokens','cached_input_tokens','output_tokens')}==dict(
            input_tokens=10,cached_input_tokens=3,output_tokens=4)
        assert all(p.read_bytes()==raw for p,raw in retained.items()) and all(p.read_bytes()==raw for p,raw in inputs.items())
        if mutation=='schema_missing':assert not (first/'science-result.schema.json').exists()
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n' and not (root/'jobs/002.json').exists()


@pytest.mark.process_e2e
def test_host_write_failure_retries_original_seal_without_model_reexecution(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'valid')
    original=c.write;faults=[]
    def fail_result(path,value):
        if path==execution.origin(root,1)/'result.json':
            faults.append(path)
            raise OSError('Host disk fixture')
        return original(path,value)
    with monkeypatch.context() as patch:
        patch.setattr(c,'write',fail_result)
        with pytest.raises(OSError,match='^Host disk fixture$'):native.execute_native(root,1)
    first=wait_runner(root);outcome=c.read(first/'native-outcome.json')
    assert faults==[first/'result.json'] and not (first/'result-identity.json').exists()
    assert outcome['returncode']==0 and outcome['terminal_observed'] is True and outcome['stop_reason'] is None
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'
    assert outcome['raw_ref']==c.file_ref(first/'science-output.json')
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json')}
    for _ in range(2):
        assert native.wait_native(root,1)['next_action']=='wait'
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert len(execution.records(root,1))==1 and native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1
        assert all(p.read_bytes()==raw for p,raw in retained.items()) and all(p.read_bytes()==raw for p,raw in inputs.items())
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
        assert not (first/'continuations/001').exists() and not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('mutation', ['root_intent', 'process_receipt', 'session_path', 'schema_missing'])
@pytest.mark.process_e2e
def test_original_binding_tamper_never_creates_continuation(tmp_path, monkeypatch, mutation):
    from scidiscovery.research_loop import accepted_budget
    tokens=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    root,first,outcome=retain_metering_failed_original(tmp_path,monkeypatch)
    work=c.workspace(root,1);session_file=Path(outcome['native_identity']['session']['path'])
    session_bytes=session_file.read_bytes()
    assert (work/'work/checkpoint').read_text()=='original checkpoint'
    if mutation=='root_intent':
        value=c.read(first/'native-intent.json');value['task_ref']=dict(path='/wrong/original.json')
        c.write(first/'native-intent.json',value)
    elif mutation=='process_receipt':
        value=c.read(first/'native-process.json');value['task_ref']=dict(path='/different/accepted-intent.json')
        c.write(first/'native-process.json',value)
    elif mutation=='session_path':
        # Native transcript bytes are opaque and mutable; redirecting the
        # retained original path is the rejected binding change.
        retained=session_file.with_name('retained-opaque.jsonl');retained.write_bytes(session_file.read_bytes())
        session_file.unlink();session_file.symlink_to(retained)
    else:
        (first/'science-result.schema.json').unlink()
    expected=dict(root_intent='Original accepted native intent identity changed',
        process_receipt='unexpected_recovery_original_process_identity_invalid',
        session_path='Original native session binding changed',schema_missing='Expected an original regular file')[mutation]
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    for _ in range(2):
        with pytest.raises(ValueError,match='^'+expected+'$'):
            continuation.start(root,1,after_interrupt=True,expected_attempt=0,
                native_intent_ref=c.record_ref(first/'native-intent.json'))
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert {key:head['usage'][key] for key in tokens}==tokens
        assert c.read(first/'usage.json')==tokens and budget.used_steps(root)==1
        assert accepted_budget.native_attempt(root,1,first,head=head) is not None
        assert not (first/'continuations/001/native-intent.json').exists()
        if mutation=='root_intent':
            with pytest.raises(ValueError,match='^Original accepted native intent identity changed$'):
                execution.records(root,1)
        else:assert len(execution.records(root,1))==1
        assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert all(p.read_bytes()==raw for p,raw in retained.items())
        assert (work/'work/launches').read_text()=='exec\n'
    if mutation=='schema_missing':assert not (first/'science-result.schema.json').exists()
    if mutation=='session_path':assert session_file.is_symlink() and session_file.read_bytes()==session_bytes


@pytest.mark.process_e2e
def test_shared_amendment_does_not_change_original_soft_target(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root,first,original_outcome=retain_metering_failed_original(tmp_path,monkeypatch)
    before={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    original_limits=dict(original_outcome['budget_slice']['limits'])
    assert original_limits==dict(max_observed_tokens=5000000,max_seconds=30)
    # Only the mechanical next-turn input changes; retained task/session and
    # original node target stay fixed while the explicit shared ceiling rises.
    tokens=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    delta=dict(input_tokens=5000000,cached_input_tokens=3,output_tokens=4)
    c.write(tmp_path/'metering.json',dict(mode='resume',baseline=tokens,delta=delta))
    budget.amend(root,max_tokens=10000000,authorization_note='Explicit shared ceiling only')
    result=continuation.start(root,1,after_interrupt=True,expected_attempt=0,
        native_intent_ref=c.record_ref(first/'native-intent.json'))
    current=wait_runner(root);outcome=c.read(current/'native-outcome.json')
    intent=c.read(current/'native-intent.json')
    assert result['role']=='H' and c.validate(c.workspace(root,1))==result
    assert intent['continuation_kind']=='reentry' and intent['budget_slice']['previous_tokens']==14
    assert intent['budget_slice']['shared_limits']['max_tokens']==10000000
    assert outcome['stop_reason'] is None and outcome['returncode']==0 and outcome['terminal_observed'] is True
    assert outcome['budget_slice']['limits']==original_limits
    assert outcome['budget_slice']['observed_tokens']==5000018>original_limits['max_observed_tokens']
    assert c.read(current/'usage.json')==delta and c.read(first/'usage.json')==tokens
    assert native.usage(root)['observed_tokens']==5000018
    head=accepted_budget.read(root)
    assert head['dispatch_count']==2 and head['continuation_count']==0 and budget.used_steps(root)==1
    assert {key:head['usage'][key] for key in tokens}==dict(input_tokens=5000010,cached_input_tokens=6,output_tokens=8)
    assert c.read(first/'result-identity.json')['execution_ref']==execution.reference(root,1)
    assert execution.raw(root,1)==current/'science-output.json'
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert native.wait_native(root,1)==result and len(execution.records(root,1))==2
    assert not (first/'continuations/002').exists() and not (root/'jobs/002.json').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.process_e2e
def test_resume_budget_denial_restores_inactive_clock_and_keeps_attempt_unaccepted(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root,first,outcome=retain_metering_failed_original(tmp_path,monkeypatch)
    originals={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    original_ref=c.record_ref(first/'native-intent.json')
    budget.amend(root,max_tokens=14,authorization_note='Explicit shared ceiling at the paid original lower bound')
    accept=budget.accept_continuation;refused=[]
    def observed_accept(*args,**kwargs):
        try:return accept(*args,**kwargs)
        except budget.NodeBudgetUnavailable as error:
            refused.append(error.reason)
            raise
    with monkeypatch.context() as patch:
        patch.setattr(budget,'accept_continuation',observed_accept)
        for _ in range(2):
            prior_seconds=budget.snapshot(root)['advance_seconds']
            result=controller.worker_resume(root,1,after_interrupt=True,expected_attempt=0,native_intent_ref=original_ref)
            assert result['status']=='worker_resume_unavailable' and result['reason']=='worker_resume_budget_unavailable'
            snapshot=budget.snapshot(root)
            assert snapshot['clock_phase']=='paused' and snapshot['advance_seconds']>=prior_seconds
            head=accepted_budget.read(root)
            assert head['dispatch_count']==1 and head['continuation_count']==0 and budget.used_steps(root)==1
            assert {key:head['usage'][key] for key in ('input_tokens','cached_input_tokens','output_tokens')}==dict(
                input_tokens=10,cached_input_tokens=3,output_tokens=4)
            assert native.usage(root)['observed_tokens']==14 and len(execution.records(root,1))==1
            assert not (first/'continuations/001/native-intent.json').exists()
            assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
            assert all(p.read_bytes()==raw for p,raw in originals.items())
            assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert refused==['worker_resume_budget_unavailable']*2
    marker=c.read(first/'interruption.json')
    assert marker['intent_ref']==original_ref and marker['budget_slice']['observed_tokens']==14
    budget.amend(root,max_tokens=200,authorization_note='Explicit capacity restoration for the same original')
    result=continuation.start(root,1,after_interrupt=True,expected_attempt=0,native_intent_ref=original_ref)
    current=wait_runner(root);intent=c.read(current/'native-intent.json')
    assert result['role']=='H' and intent['continuation_kind']=='reentry'
    assert intent['interruption']==c.record_ref(first/'interruption.json') and intent['budget_slice']['previous_tokens']==14
    assert native.usage(root)['observed_tokens']==28 and budget.used_steps(root)==1
    head=accepted_budget.read(root)
    assert head['dispatch_count']==2 and head['continuation_count']==0
    assert {key:head['usage'][key] for key in ('input_tokens','cached_input_tokens','output_tokens')}==dict(
        input_tokens=20,cached_input_tokens=6,output_tokens=8)
    assert native.wait_native(root,1)==result and len(execution.records(root,1))==2
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert not (first/'continuations/002').exists() and not (root/'jobs/002.json').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.process_e2e
def test_operator_pause_does_not_accept_a_new_continuation(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson')
    first=retain_qualified_h_bad_original(root,monkeypatch)
    c.write(root/'pause.request',dict(reason='user pause'))
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    for _ in range(2):
        with pytest.raises(continuation.ResumeUnavailable,match='^worker_resume_paused_or_closeout$'):
            native.wait_native(root,1)
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert {key:head['usage'][key] for key in ('input_tokens','cached_input_tokens','output_tokens')}==dict(
            input_tokens=10,cached_input_tokens=3,output_tokens=4)
        assert native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1
        assert not (first/'continuations/001/native-intent.json').exists() and len(execution.records(root,1))==1
        assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert (root/'pause.request').exists() and all(p.read_bytes()==raw for p,raw in retained.items())
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.process_e2e
def test_unaccepted_intent_is_not_replayed_or_given_another_attempt(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root,first,outcome=retain_metering_failed_original(tmp_path,monkeypatch)
    original={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    record=first/'continuations/001'
    orphan=dict(c.read(first/'native-intent.json'),attempt=1,continuation_kind='reentry',
        root_intent_ref=c.record_ref(first/'native-intent.json'),previous=execution.proof(first))
    c.write(record/'native-intent.json',orphan)
    assert accepted_budget.native_attempt(root,1,record) is None
    retained=(record/'native-intent.json').read_bytes()
    for _ in range(2):
        with pytest.raises(ValueError,match='^Original native intent has not been accepted$'):
            continuation.start(root,1,after_interrupt=True,expected_attempt=0,
                native_intent_ref=c.record_ref(first/'native-intent.json'))
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert {key:head['usage'][key] for key in ('input_tokens','cached_input_tokens','output_tokens')}==dict(
            input_tokens=10,cached_input_tokens=3,output_tokens=4)
        assert native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1
        assert accepted_budget.native_attempt(root,1,record,head=head) is None
        assert not (record/'runner-process.json').exists() and not (record/'native-outcome.json').exists()
        assert not (first/'continuations/002').exists() and not (first/'result-identity.json').exists()
        assert not (root/'jobs/002.json').exists() and (record/'native-intent.json').read_bytes()==retained
        assert all(p.read_bytes()==raw for p,raw in original.items())
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.process_e2e
def test_continuation_cannot_exceed_consumed_shared_step_budget(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson')
    first=retain_qualified_h_bad_original(root,monkeypatch)
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json',
        Path(c.read(first/'native-outcome.json')['native_identity']['session']['path']))}
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    amendment=budget.amend(root,max_steps=budget.used_steps(root),
        authorization_note='Explicit fixture reduction to the original consumed step after native completion',
        authorization_statement='Set the shared step ceiling to one; retain the completed original step.')
    assert amendment['after_max_steps']==1 and amendment['consumption']=='retained'
    for _ in range(2):
        with pytest.raises(budget.NodeBudgetUnavailable) as failure:native.wait_native(root,1)
        assert failure.value.reason=='worker_resume_budget_unavailable'
        head=accepted_budget.read(root)
        assert head['dispatch_count']==1 and head['continuation_count']==0
        assert len(execution.records(root,1))==1 and not (first/'continuations/001').exists()
        assert native.usage(root)['observed_tokens']==14 and budget.used_steps(root)==1 and budget.effective_max_steps(root)==1
        assert all(p.read_bytes()==raw for p,raw in inputs.items())
        assert all(p.read_bytes()==raw for p,raw in retained.items())
        assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('mutation,value',[
    ('previous_tokens',0),('previous_tokens',-1),('previous_tokens',True),('previous_tokens',float('nan')),
    ('required_report',None)])
def test_required_original_budget_facts_cannot_refund_or_hide_paid_usage(tmp_path,mutation,value):
    # Explicit owner receipts: no native process, accepted ledger or scientific qualification.
    root=tmp_path/'instance';job=dict(step=1,role='H');task_ref=dict(path=str(root/'jobs/001.json'))
    limits=dict(max_observed_tokens=100,max_seconds=20)
    receipts=[]
    for number,(meter,previous,total,seconds) in enumerate([
        (dict(input_tokens=10,cached_input_tokens=3,output_tokens=4),0,14,2.),
        (dict(input_tokens=2,cached_input_tokens=1,output_tokens=1),14,17,3.)]):
        intent=dict(task_ref=task_ref,budget_slice=dict(role='H',limits=limits,previous_tokens=previous,
            previous_seconds=0. if number==0 else 2.))
        report=dict(role='H',limits=limits,usage_observed=True,dispatches=number+1,
            observed_tokens=total,advance_seconds=seconds,**meter)
        receipts.append((root/str(number),intent,dict(budget_slice=report),meter,False))
    def consume():
        return budget._attempt_consumption_from_facts(root,job,receipts,policy={},limits=limits,task_ref=task_ref)
    assert consume()==(17,3.)
    paid=dict(receipts[0][3]);current=dict(receipts[1][3])
    if mutation=='previous_tokens':receipts[1][1]['budget_slice']['previous_tokens']=value
    else:del receipts[0][2]['budget_slice']['input_tokens']
    with pytest.raises(ValueError,match='^Original node usage report or identity changed$'):consume()
    assert receipts[0][3]==paid and receipts[1][3]==current
    assert not root.exists()




@pytest.mark.parametrize('tamper', ['intent', 'report'])
@pytest.mark.process_e2e
def test_success_before_seal_does_not_rebind_drifted_attempt(tmp_path,monkeypatch,tamper):
    from scidiscovery.research_loop import accepted_budget
    root=qualified_h_repair_instance(tmp_path,monkeypatch,'badjson')
    publish=c.seal_result_from_inventory
    class DeferSeal(RuntimeError):pass
    def retain_success(root,step,*,inventory):
        if inventory.attempt==1:
            assert inventory.payload['summary']=='Retained finding' and inventory.output_error is None
            assert inventory.native_outcome['returncode']==0 and inventory.native_outcome['terminal_observed'] is True
            raise DeferSeal('Successful repair retained before publication')
        return publish(root,step,inventory=inventory)
    with monkeypatch.context() as patch:
        patch.setattr(c,'seal_result_from_inventory',retain_success)
        with pytest.raises(DeferSeal):native.execute_native(root,1)
    current=wait_runner(root);first=execution.origin(root,1)
    assert native.usage(root)['observed_tokens']==28 and budget.used_steps(root)==2
    if tamper=='intent':
        value=c.read(current/'native-intent.json');value['budget_slice']['previous_tokens']-=1
        c.write(current/'native-intent.json',value)
    else:
        value=c.read(current/'native-outcome.json');value['budget_slice']['observed_tokens']-=1
        c.write(current/'native-outcome.json',value)
    inputs={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json',
        Path(c.read(first/'native-outcome.json')['native_identity']['session']['path']))}
    retained={p:p.read_bytes() for record in (first,current) for p in record.iterdir() if p.is_file()}
    for _ in range(2):
        with pytest.raises(ValueError):native.wait_native(root,1)
        head=accepted_budget.read(root)
        assert head['dispatch_count']==2 and head['continuation_count']==1
        assert {key:head['usage'][key] for key in ('input_tokens','cached_input_tokens','output_tokens')}==dict(
            input_tokens=20,cached_input_tokens=6,output_tokens=8)
        assert budget.used_steps(root)==2
        assert all(p.read_bytes()==raw for p,raw in inputs.items())
        assert all(p.read_bytes()==raw for p,raw in retained.items())
        assert not (first/'result-identity.json').exists() and not (first/'continuations/002').exists()
        assert not (root/'jobs/002.json').exists()
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.parametrize('tamper',['task','schema_missing'])
@pytest.mark.process_e2e
def test_accepted_continuation_rechecks_task_and_schema_before_actual_popen(tmp_path,monkeypatch,tamper):
    from scidiscovery.research_loop import accepted_budget, runtime
    tokens=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    root,first,outcome=retain_metering_failed_original(tmp_path,monkeypatch)
    work=c.workspace(root,1);assert (work/'work/checkpoint').read_text()=='original checkpoint'
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    start=runtime.start;admitted=[]
    def drift(root,step,attempt=0):
        current=first/'continuations/001';intent=c.read(current/'native-intent.json')
        assert attempt==1 and intent['continuation_kind']=='reentry' and intent['budget_slice']['previous_tokens']==14
        assert accepted_budget.read(root)['dispatch_count']==2
        if tamper=='schema_missing':
            (first/'science-result.schema.json').unlink()
        else:
            value=c.read(work/'science-task.json');value['global']['objective']='inputs/global/different-objective.md'
            c.write(work/'science-task.json',value)
        admitted.append(attempt)
        return start(root,step,attempt)
    monkeypatch.setattr(runtime,'start',drift)
    for check in range(2):
        if tamper=='task':
            error=RuntimeError;reason='Original native execution is unknown; preserved without replay'
        else:
            # The first call collects the caught pre-Popen refusal. A repeated
            # exact recovery source checks the still-missing schema before
            # reusing its accepted successor; neither path submits again.
            error=ValueError;reason=('Terminal scientific output is unverified or changed' if check==0
                else 'Expected an original regular file')
        with pytest.raises(error,match='^'+reason+'$'):
            continuation.start(root,1,after_interrupt=True,expected_attempt=0,
                native_intent_ref=c.record_ref(first/'native-intent.json'))
        current=wait_runner(root)
        if tamper=='task':
            assert 'ValueError: Frozen scientific task changed its input binding' in (current/'runner.stderr').read_text()
            assert not (current/'native-outcome.json').exists()
        else:
            assert 'ValueError: Expected an original regular file' in (current/'runner.stderr').read_text()
            refusal=c.read(current/'native-outcome.json')
            assert refusal['limitation']=='Expected an original regular file'
            assert refusal['native_started'] is False and refusal['returncode'] is None
            assert refusal['stop_reason']=='native_error_unknown' and refusal['terminal_observed'] is False
            assert refusal['cleanup'] is None and not (first/'science-result.schema.json').exists()
        head=accepted_budget.read(root)
        assert head['dispatch_count']==2 and head['continuation_count']==0
        assert {key:head['usage'][key] for key in tokens}==tokens and budget.used_steps(root)==1
        assert c.read(first/'usage.json')==tokens
        assert all(p.read_bytes()==raw for p,raw in retained.items() if tamper!='schema_missing' or p.name!='science-result.schema.json')
        assert not (current/'native-process.json').exists()
        assert len(execution.records(root,1))==2 and not (first/'continuations/002').exists()
        assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert (work/'work/launches').read_text()=='exec\n'
    assert admitted==[1]



def retained_session_original(tmp_path):
    """An exact native hook binding; its retained transcript stays opaque."""
    from scidiscovery.research_loop.native_usage import stream_binding
    root=instance(tmp_path);c.build_job(root,1,'H',[])
    first=execution.origin(root,1)
    thread='11111111-2222-4333-8444-555555555555'
    home=root/'native-homes/001';directory=home/'sessions';directory.mkdir(parents=True)
    original=directory/'rollout.jsonl'
    original.write_bytes(b'opaque retained native session transcript without a JSON header\n')
    info=stream_binding(original)
    binding=dict(source='native_metadata_hooks',thread_id=thread,home=str(home),
        workspace=str(c.workspace(root,1)),path=str(original),identity=info['identity'],offset=info['size_bytes'])
    c.write(first/'native-outcome.json',dict(thread_id=thread,
        native_thread_event=dict(type='thread.started',thread_id=thread),
        native_identity=dict(backend='codex',home=str(home),path=str(original),session=binding)))
    return root,first,home,original


def test_native_session_reference_retains_exact_hook_binding_and_opaque_original(tmp_path,monkeypatch):
    root,first,home,session_file=retained_session_original(tmp_path)
    original=session_file.read_bytes()
    expected=c.native_outcome(first)['native_identity']['session']
    original_open=Path.open
    def no_transcript_read(path,*args,**kwargs):
        if path==session_file:pytest.fail('Resume must not read the native transcript')
        return original_open(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',no_transcript_read)
        reference=continuation.session(root,1,first,home=home,session_path=session_file)
    assert reference==expected and reference['path']==str(session_file)
    assert session_file.read_bytes()==original
    assert not (c.workspace(root,1)/'work/launches').exists()
    assert not (first/'continuations').exists()


def test_native_session_hook_wrong_workspace_does_not_accept_resume(tmp_path):
    root,first,home,session_file=retained_session_original(tmp_path)
    original=session_file.read_bytes()
    value=c.native_outcome(first);value['native_identity']['session']['workspace']='/incorrect-workspace'
    c.write(first/'native-outcome.json',value)
    with pytest.raises(ValueError,match='Original native session binding changed'):
        continuation.session(root,1,first,home=home,session_path=session_file)
    assert session_file.read_bytes()==original
    assert not (first/'continuations/001/native-intent.json').exists()
    assert not (c.workspace(root,1)/'work/launches').exists()


@pytest.mark.process_e2e
def test_retained_session_path_redirect_cannot_launch_accepted_resume(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget, runtime
    tokens=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    root,first,outcome=retain_metering_failed_original(tmp_path,monkeypatch)
    work=c.workspace(root,1);assert (work/'work/checkpoint').read_text()=='original checkpoint'
    session_file=Path(outcome['native_identity']['session']['path']);original=session_file.read_bytes()
    retained={p:p.read_bytes() for p in first.iterdir() if p.is_file()}
    start=runtime.start;admitted=[]
    def redirect(root,step,attempt=0):
        current=first/'continuations/001';intent=c.read(current/'native-intent.json')
        assert attempt==1 and intent['continuation_kind']=='reentry' and intent['session']==outcome['native_identity']['session']
        assert intent['budget_slice']['previous_tokens']==14 and accepted_budget.read(root)['dispatch_count']==2
        target=session_file.with_name('retained-opaque.jsonl');target.write_bytes(original)
        session_file.unlink();session_file.symlink_to(target);admitted.append(attempt)
        return start(root,step,attempt)
    monkeypatch.setattr(runtime,'start',redirect)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='^Original native execution is unknown; preserved without replay$'):
            continuation.start(root,1,after_interrupt=True,expected_attempt=0,
                native_intent_ref=c.record_ref(first/'native-intent.json'))
        current=wait_runner(root)
        assert 'ValueError: Original native session binding changed' in (current/'runner.stderr').read_text()
        head=accepted_budget.read(root)
        assert head['dispatch_count']==2 and head['continuation_count']==0
        assert {key:head['usage'][key] for key in tokens}==tokens and budget.used_steps(root)==1
        assert c.read(first/'usage.json')==tokens and all(p.read_bytes()==raw for p,raw in retained.items())
        assert session_file.is_symlink() and session_file.read_bytes()==original
        assert not (current/'native-process.json').exists() and not (current/'native-outcome.json').exists()
        assert len(execution.records(root,1))==2 and not (first/'continuations/002').exists()
        assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
        assert (work/'work/launches').read_text()=='exec\n'
    assert admitted==[1]


@pytest.mark.parametrize('schema_bytes', [None,
    b'{"type":"object","properties":{"retained_field":{"type":"string"}}}\n',
    b'{"type":"object","properties":{"implementation_selection":{"maxItems":16}}}\n'])
def test_resume_preserves_the_original_opaque_transport_schema(tmp_path,schema_bytes):
    root=instance(tmp_path);job=c.build_job(root,1,'H',[])
    path=execution.origin(root,1)/'science-result.schema.json'
    if schema_bytes is not None:
        # Construct this fixture's historical original before accepting work.
        path.unlink()
        path.write_bytes(schema_bytes)
        path.chmod(0o400)
    originals={p:p.read_bytes() for p in (path,root/'jobs/001.json',c.workspace(root,1)/'science-task.json')}
    assert continuation.validate_task(root,1)==c.job_record(job)
    assert all(p.read_bytes()==data for p,data in originals.items())
    assert not (execution.origin(root,1)/'native-intent.json').exists()


@pytest.mark.parametrize('shape',['missing','symlink'])
def test_resume_rejects_missing_or_redirected_original_transport_schema(tmp_path,shape):
    root=instance(tmp_path);c.build_job(root,1,'H',[])
    path=execution.origin(root,1)/'science-result.schema.json'
    original=path.read_bytes();path.unlink()
    if shape=='symlink':
        target=tmp_path/'other.schema.json';target.write_bytes(original);path.symlink_to(target)
    with pytest.raises((ValueError,FileNotFoundError)):
        continuation.validate_task(root,1)
    assert not (execution.origin(root,1)/'native-intent.json').exists()


def test_resume_launcher_allows_source_upgrade_and_rejects_path_redirect(tmp_path):
    root=instance(tmp_path);launcher=tmp_path/'native.py'
    settings=(root/'settings.json').read_bytes()
    assert c.settings(root)['native_launcher_argv'][-1]==str(launcher)
    limits=continuation.validate_launcher(root)
    launcher.write_bytes(launcher.read_bytes()+b'\n# upgraded launcher\n')
    assert continuation.validate_launcher(root)==limits
    redirected=tmp_path/'redirected-native.py';redirected.write_bytes(launcher.read_bytes())
    launcher.unlink();launcher.symlink_to(redirected)
    with pytest.raises(ValueError,match='^Frozen resume launcher changed$'):
        continuation.validate_launcher(root)
    assert (root/'settings.json').read_bytes()==settings
    assert not (root/'jobs/001.json').exists() and not (root/'native-evidence/001/native-intent.json').exists()
