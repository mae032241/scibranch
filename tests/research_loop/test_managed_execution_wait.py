"""Real controller CLI, native session transport and registered async backend; no models."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from test_composed_execution_results import composed_registry
from test_history_handoff import finish
from scidiscovery.research_loop import accepted_budget, backends, budget, contracts as c, controller, execution, experiments as e, history, native_app_server

pytestmark = pytest.mark.process_e2e

# Synthetic native transport only: standard launches use the real app-server
# adapter and declared metadata helper, never header-only session qualification.
LAUNCHER = r'''import hashlib,json,os,pathlib,shlex,subprocess,sys,time,tomllib
args=sys.argv[1:];app_server='app-server' in args
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
home=pathlib.Path(os.environ['CODEX_HOME'] if app_server else os.environ['THIN_HWA_NATIVE_HOME']);role=os.environ['THIN_HWA_ROLE']
root=work.parents[2];thread='11111111-2222-4333-8444-'+('555555555555' if role=='W' else work.name.zfill(12))
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
def receive():
 line=sys.stdin.readline();assert line,'Missing app-server request'
 return json.loads(line)
config={}
def merge(target,update):
 for key,value in update.items():
  if isinstance(value,dict) and isinstance(target.get(key),dict):merge(target[key],value)
  else:target[key]=value
if app_server:
 assert args[-2:]==['app-server','--stdio'] and args[args.index('-C')+1]==str(work)
 for index,arg in enumerate(args[:-1]):
  if arg=='-c':merge(config,tomllib.loads(args[index+1]))
 message=receive();assert message['method']=='initialize'
 send(dict(id=message['id'],result={}))
 assert receive()['method']=='initialized'
 message=receive()
 if message['method']=='hooks/list':
  assert message['params']==dict(cwds=[str(work)])
  hooks=[]
  for name,key,event in (('PostToolUse','post_tool_use','postToolUse'),('PostModelRequest','post_model_request','postModelRequest'),('PostCompact','post_compact','postCompact')):
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
  send(dict(id=message['id'],result=dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])))
  raise SystemExit(0)
 assert message['method'] in ('thread/start','thread/resume')
 resume=message['method']=='thread/resume';start=message['params']
 assert start['cwd']==str(work) and start['approvalPolicy']=='never'
 assert '--dangerously-bypass-hook-trust' in args and start['config']==dict(bypass_hook_trust=True)
 assert set(config['hooks'])=={'PostToolUse','PostModelRequest','PostCompact'}
 if resume:assert start['threadId']==thread and start['excludeTurns'] is True
 else:assert start['allowProviderModelFallback'] is False and start['experimentalRawEvents'] is False
 mode=start['sandbox'];assert mode==config['sandbox_mode']
 sandbox=config.get('sandbox_workspace_write',{})
 policy=dict(type={'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[mode])
 if mode!='danger-full-access':policy['networkAccess']=sandbox.get('network_access',False) if mode=='workspace-write' else False
 if mode=='workspace-write':policy.update(writableRoots=sandbox.get('writable_roots',[]),excludeSlashTmp=sandbox.get('exclude_slash_tmp',False),excludeTmpdirEnvVar=sandbox.get('exclude_tmpdir_env_var',False))
 send(dict(id=message['id'],result=dict(thread=dict(id=thread,turns=[]),cwd=str(work),model=start['model'],
  approvalPolicy='never',sandbox=policy,reasoningEffort=config.get('model_reasoning_effort'),serviceTier=start.get('serviceTier'))))
 message=receive();assert message['method']=='turn/start'
 turn=message['params'];assert turn['threadId']==thread and turn['model']==start['model']
 assert turn['approvalPolicy']=='never' and turn.get('effort')==config.get('model_reasoning_effort')
 assert turn.get('serviceTier')==start.get('serviceTier')
 assert len(turn['input'])==1 and turn['input'][0]['type']=='text'
 prompt=turn['input'][0]['text']
 # A temporary RPC response can be lost after side effects. Keep that exact
 # turn/start unresolved; a failed terminal is a different transport fact.
 rpc_failure=resume and json.loads((root/'fixture-options.json').read_text()).get('temporary_wake_failure') and not (work/'work/temporary-wake-failure').exists()
 if not rpc_failure:send(dict(id=message['id'],result=dict(turn=dict(id='turn-one',items=[],status='inProgress'))))
else:
 prompt=sys.stdin.read();resume='resume' in args
from scidiscovery.research_loop import worker_execution_bridge as bridge
def request(arguments):
 reply=bridge.client(work/'work/experiment',arguments)
 return reply['code'],reply['result']
with (work/'work/launches').open('a') as f:f.write('resume\n' if resume else 'exec\n')
if not app_server:send(dict(type='thread.started',thread_id=thread))
p=home/'sessions'/f'rollout-{thread}.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
if not p.exists():p.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
payload=dict(summary='Synthetic original execution',scientific_result='Only fixture engineering facts.',limitations=['No model or solver.'],implementation_selection=None)
options=json.loads((root/'fixture-options.json').read_text())
worker_first=options.get('worker_first',False)
def batch_command(package,number):
 if not worker_first:return ['run','--payload',str(package),'--new-plan','--mechanism',str(number)]
 ref='w'+work.name+'.m'+str(number)
 research=work/'work'/('research-'+str(number)+'.json')
 if not research.exists():
  research.write_text(json.dumps(dict(declarations=[dict(action='declare',mechanism_ref=ref,
   question='Does this exact opaque input preserve its declared observable?',exploration=True)],
   mechanism_ref=ref,experiment_question=dict(question='Does this captured input retain the original fixture bytes?',
    change_criterion='Keep the original if its bytes agree; otherwise report the missing or changed observation.'))))
 return ['run','--payload',str(package),'--new-plan','--research',str(research)]
if role=='W':
 task=json.loads((work/'science-task.json').read_text())
 nominations=task.get('candidate_nomination_mechanisms',[])
 if nominations:
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='This transport fixture declares no scientifically suitable candidate; retain the missing scientific assessment.') for row in nominations if row['status']!='waived'])
 if not resume:
  (work/'work/initial-prompt').write_text(prompt)
  options=json.loads((root/'fixture-options.json').read_text())
  package=work/'code/package';package.parent.mkdir(exist_ok=True);package.write_bytes(b'opaque original job package')
  command=batch_command(package,1)
  if options.get('synchronous_first'):
   code,first=request(command);assert code==0,first
   command=batch_command(package,2)
  code,result=request(command)
  assert code==2,result
  assert result['status']==options.get('query_state','running'),result
  (work/'work/original-call').write_text(result['call_id'])
  (work/'work/executor-handoff.json').write_text(json.dumps(result['handoff']))
  if options.get('adaptive_batches'):
   (work/'work/managed-calls.json').write_text(json.dumps([dict(call_id=result['call_id'],payload=package.read_text())]))
  if options.get('follow_handoff') and result['handoff']['action']=='poll_original':
   handoff=result['handoff'];queries=[]
   while handoff['action']=='poll_original':
    assert 'max_queries' not in handoff
    # The host bridge retains the original finite deadline. Synthetic backend
    # events belong to the test host, never this readonly native namespace.
    time.sleep(handoff['poll_interval_seconds'])
    event=dict(call_id=result['call_id'],number=len(queries)+1)
    temporary=work/'work/fixture-query.pending';temporary.write_text(json.dumps(event))
    temporary.replace(work/'work/fixture-query.json')
    deadline=time.monotonic()+10
    while True:
     try:ack=json.loads((work/'work/fixture-query-ack.json').read_text())
     except (FileNotFoundError,json.JSONDecodeError):ack=None
     if ack==event:break
     assert time.monotonic()<deadline,'Host did not deliver the synthetic backend event'
     time.sleep(.02)
    code,query=request(['query','--call-id',result['call_id']])
    assert code==0,query
    handoff=query['handoff']
    assert handoff['call_id']==result['call_id']
    queries.append(handoff['call_id'])
    (work/'work/poll-queries.json').write_text(json.dumps(queries))
   if handoff['action']=='reconcile_or_stop':
    code,terminal=request(['reconcile','--call-id',result['call_id']])
    assert code in (0,2),terminal
    handoff=terminal['handoff']
    assert handoff['call_id']==result['call_id']
    (work/'work/reconciled-call').write_text(result['call_id'])
    if handoff['action']=='consume_terminal':
     code,collected=request(['collect','--call-id',result['call_id']])
     assert code==0,collected
     outputs=collected['outputs']
     for item in outputs:assert pathlib.Path(item['path']).read_bytes()==b'opaque original job package'
     (work/'work/consumed-output-count').write_text(str(len(outputs)))
     (work/'work/consumed-result').write_text(terminal['status'])
   (work/'work/final-handoff.json').write_text(json.dumps(handoff))
 else:
  assert (app_server or thread in args) and (work/'work/original-call').exists()
  repair='Fix diagnosed JSON/layout only' in prompt
  if not repair:assert 'Same task/scope/permissions/budget' in prompt
  (work/'work/wake-prompt').write_text(prompt)
  packet=json.loads((work/'work/execution-results.json').read_text())
  assert len(packet['executions'])==1
  row=packet['executions'][0]
  assert not {'native_originals','original'} & row.keys()
  result=json.loads((work/row['result']).read_text())
  assert result['status']==row['status']
  assert not {'proofs','scope','accepted','submission','sha256','config_record'} & result.keys()
  assert not (work/row['result']).parent.joinpath('originals').exists()
  options=json.loads((root/'fixture-options.json').read_text())
  calls=json.loads((work/'work/managed-calls.json').read_text()) if options.get('adaptive_batches') else None
  if calls:assert result['call_id']==calls[-1]['call_id']
  references=work/'work/consumed-output-refs.json'
  prior_refs=json.loads(references.read_text()) if references.exists() else []
  for prior in prior_refs:
   retained=work/prior['path'];assert retained.read_text()==prior['payload']
   try:retained.write_text('must stay readonly across wake/recovery')
   except OSError:pass
   else:raise AssertionError('A prior batch output became writable')
  for item in packet['executions'][0]['outputs']:
   retained=work/item['path'];assert retained.read_bytes()==(calls[-1]['payload'].encode() if calls else b'opaque original job package')
   for action in (lambda:retained.chmod(0o600),lambda:retained.write_bytes(b'must stay readonly')):
    try:action()
    except OSError:pass
    else:raise AssertionError('Managed wake reopened a shared output')
   if not any(prior['path']==item['path'] for prior in prior_refs):
    prior_refs.append(dict(path=item['path'],payload=retained.read_text()))
  references.write_text(json.dumps(prior_refs))
  failure_marker=work/'work/temporary-wake-failure'
  if options.get('temporary_wake_failure') and not failure_marker.exists():
   failure_marker.write_text('Original results were consumed before this temporary native failure.')
   if app_server:
    assert rpc_failure
    send(dict(id=message['id'],error=dict(code=-32000,message='temporaryFailure: capacity_exceeded',data=dict(httpStatus=503))))
   else:send(dict(type='turn.failed',error=dict(code='capacity_exceeded',status=503)))
   sys.exit(7)
  (work/'work/consumed-result').write_text(packet['executions'][0]['status'])
  if 'native_state' in packet['executions'][0]:(work/'work/consumed-native-state').write_text(packet['executions'][0]['native_state'])
  if calls and len(calls)<options['adaptive_batches']:
   package=work/'code/package'
   package.write_text('adaptive batch '+str(len(calls)+1)+' after verified '+calls[-1]['call_id'])
   mechanism='1' if options.get('same_focus') else '2'
   code,next_call=request(batch_command(package,int(mechanism)))
   assert code==2 and next_call['status']=='running',next_call
   assert next_call['call_id'] not in [call['call_id'] for call in calls]
   calls.append(dict(call_id=next_call['call_id'],payload=package.read_text()))
   (work/'work/managed-calls.json').write_text(json.dumps(calls))
   (work/'work/new-call-accepted').write_text(next_call['call_id'])
  if repair and options.get('repeat_output_repair'):
   code,refused=request(['run','--payload',str(work/'code/package'),'--new-plan','--mechanism','1'])
   assert code==2 and refused['status']=='unavailable' and 'call_id' not in refused,refused
   with (work/'work/repair-refusals.jsonl').open('a') as stream:stream.write(json.dumps(refused)+'\n')
 consumed=(work/'work/consumed-result').exists()
 # The explicit legacy helper asks for ordinary A waiting, not an A
 # consultation returning to another W. Preserve any requested initial fragment.
 payload.update(stage_intent='decision' if worker_first or resume else options.get('stage_intent','decision'),mechanism_findings=[dict(mechanism=('w'+work.name+'.m1') if worker_first else 'h001.m1',basis='observed' if consumed else 'blocked',finding='Read exact original terminal' if consumed else 'Original accepted execution has no consumed terminal')])
 if not worker_first:payload['mechanism_findings'].append(dict(mechanism='h001.m2',basis='blocked',finding='This fixture supplies no Q scientific conclusion'))
elif role=='A':
 if worker_first:
  task=json.loads((work/'science-task.json').read_text())
  catalog=json.loads((work/task['lineage']['mechanisms']['catalog']['path']).read_text())
  payload.update(next_action='finish',route_reason='Decline delivery: fixture transport is not scientific qualification.',candidate_recommendation=None,
   final_coverage=[dict(mechanism_ref=row['mechanism_ref'],status='unexplored',reason='No scientific assessment or qualified candidate is supplied.') for row in catalog['mechanisms']])
 else:
  payload.update(candidate_recommendation=None,progress_assessment=None,portfolio_review=None,search_review=dict(dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='Review original W result')],next_focus=dict(mechanism_ref='h001.m1',action='improve')),next_action='wait',route_reason='Bounded engineering test complete',decision_request=dict(decision='mechanism',question='What changes the mechanism?',change_criterion='A discriminating observation'))
if role=='W' and resume and options.get('repeat_output_repair') and len(calls)==options['adaptive_batches']:
 payload.pop('summary')
previous=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
for line in p.read_text().splitlines():
 event=json.loads(line)
 if event.get('payload',{}).get('type')=='token_count':previous=event['payload']['info']['total_token_usage']
total={key:previous[key]+value for key,value in dict(input_tokens=10,cached_input_tokens=2,output_tokens=2).items()}
with p.open('a') as stream:
 stream.write(json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(
  total_token_usage=total,last_token_usage=dict(total_tokens=100),model_context_window=1000))))+'\n')
text=json.dumps(payload)
if app_server:
 def hook(name,event):
  handler=config['hooks'][name][0]['hooks'][0]
  return subprocess.run(shlex.split(handler['command']),input=json.dumps(event).encode(),
   stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=handler['timeout'],check=True).stdout
 assert hook('PostModelRequest',dict(cwd=str(work),session_id=thread,hook_event_name='PostModelRequest',
  status='completed',transcript_path=str(p),token_usage=dict(input_tokens=10,cached_input_tokens=2,output_tokens=2)))==b''
 notice=hook('PostToolUse',dict(cwd=str(work),session_id=thread,hook_event_name='PostToolUse'))
 if notice:assert json.loads(notice)['hookSpecificOutput']['hookEventName']=='PostToolUse'
 send(dict(method='thread/tokenUsage/updated',params=dict(threadId=thread,turnId='turn-one',
  tokenUsage=dict(last=dict(totalTokens=100),modelContextWindow=1000))))
 send(dict(method='item/completed',params=dict(threadId=thread,turnId='turn-one',
  item=dict(type='agentMessage',text=text,phase='final_answer'))))
 send(dict(method='turn/completed',params=dict(threadId=thread,turn=dict(id='turn-one',items=[],status='completed'))))
else:
 # The final native message owns these exact bytes; a completion counter or
 # output file alone is deliberately insufficient for terminal acceptance.
 send(dict(type='item.completed',item=dict(type='agent_message',text=text)))
 pathlib.Path(args[args.index('--output-last-message')+1]).write_text(text)
 send(dict(type='turn.completed',usage=dict(input_tokens=10,cached_input_tokens=2,output_tokens=2)))
'''


def instance(tmp_path,options,*,fragment=False,resume=True,seconds=120,node_seconds=20):
    public=tmp_path/'public';public.mkdir();goal=public/'objective.md';goal.write_text('Read an async original and retain scientific limits.')
    launcher=tmp_path/('codex' if resume else 'launcher.py')
    launcher.write_text('#!'+sys.executable+'\n'+LAUNCHER);launcher.chmod(0o700)
    root=tmp_path/'instance'
    # These tests retain the explicit H catalogue -> W -> A waiting route.
    # Current W-first strategy coverage lives in the worker-first fixtures.
    # The host freezes the remote fixture and serves the ordinary Worker bridge.
    controller.prepare(root,goal,public,[str(launcher)] if resume else [sys.executable,str(launcher)],max_tokens=1000,max_seconds=seconds,max_steps=7,
        model='no-model',reasoning_effort='high',node_budget_mode='soft',research_strategy_version=None,
        execution_config=dict(executor='composed_execution:fixture',configuration=options),
        node_budget_policy={role:dict(max_observed_tokens=200,max_seconds=node_seconds) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn') if resume else None,
        worker_fragment_policy=dict(context='same_session') if fragment else None)
    c.write(root/'fixture-options.json',options)
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two mechanisms',commitments=[
        dict(label=label,minimal_test='Read original backend fact',existing_ref=None) for label in ('P','Q')],focus=dict(index=1,action='screen')))
    return root


def run_cli(root,*,on_wait=None):
    options=c.read(root/'fixture-options.json');stop=threading.Event();errors=[]
    def backend_events():
        previous=None;selected=None;next_wait=0
        try:
            while not stop.wait(.01):
                current=accepted_budget.read(root).current_job
                if current is None or current['role']!='W':continue
                step=current['step'];work=c.workspace(root,step)
                if selected!=step:selected=step;next_wait=0
                if on_wait is not None:
                    record=execution.origin(root,step)
                    if next_wait:record=record/'continuations'/f'{next_wait:03d}'
                    waiting=record/'execution-wait.json'
                    if waiting.exists():
                        value=c.read(waiting)
                        for item in value['calls']:
                            config=c.read(Path(item['config']['path']))
                            call=c.read(Path(item['accepted']['path']))['call_id']
                            if not (e.Records(config).directory(call)/'completed.json').exists():
                                on_wait(record,config,call)
                                e.load(config).complete(call)
                        next_wait+=1
                    elif (record/'native-outcome.json').exists():
                        outcome=c.read(record/'native-outcome.json')
                        if type(outcome.get('returncode')) is int and outcome['returncode']!=0:next_wait+=1
                if not options.get('follow_handoff'):continue
                path=work/'work/fixture-query.json'
                if not path.exists():continue
                event=c.read(path)
                if event==previous:continue
                acknowledgement=work/'work/fixture-query-ack.json'
                if acknowledgement.exists() and c.read(acknowledgement)==event:continue
                config=c.read(execution.origin(root,step)/'experiment-config.json')
                call=event['call_id'];number=event['number']
                assert e.Records(config).accepted(call)['call_id']==call
                if number==options.get('terminal_at_query'):e.load(config).complete(call)
                if number==options.get('unknown_at_query'):
                    path=e.Records(config).directory(call)/'backend-accepted.json'
                    backend=c.read(path);backend['state']='unknown';c.write(path,backend)
                temporary=work/'work/fixture-query-ack.pending';c.write(temporary,event)
                temporary.replace(work/'work/fixture-query-ack.json');previous=event
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=backend_events) if options.get('follow_handoff') or on_wait is not None else None
    if thread is not None:thread.start()
    try:
        return subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)],capture_output=True,text=True,timeout=35)
    finally:
        stop.set()
        if thread is not None:thread.join(timeout=3)
        assert not errors,errors
        assert thread is None or not thread.is_alive()


def standard_instance(tmp_path,monkeypatch,*,wake='default',adaptive_batches=2,same_focus=False,
        repeat_output_repair=False,temporary_wake_failure=False):
    """Use the real prepare CLI and its standard Codex defaulting path."""
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Read the same original managed result and retain its limitations.')
    home=tmp_path/'native-home';home.mkdir()
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=true\n')
    executable=tmp_path/'bin/codex';executable.parent.mkdir()
    executable.write_text('#!'+sys.executable+'\n'+LAUNCHER);executable.chmod(0o700)
    monkeypatch.setenv('CODEX_HOME',str(home))
    monkeypatch.setenv('PATH',str(executable.parent)+os.pathsep+os.environ['PATH'])
    root=tmp_path/'instance';nodes=tmp_path/'nodes.json'
    c.write(nodes,{role:dict(max_observed_tokens=200,max_seconds=20) for role in 'HWAE'})
    launcher=['codex'] if wake=='default' and not temporary_wake_failure else [str(executable)]
    options=dict(collect=True,terminal_status='known_success',adaptive_batches=adaptive_batches,worker_first=True,
        same_focus=same_focus,repeat_output_repair=repeat_output_repair,temporary_wake_failure=temporary_wake_failure)
    selection=tmp_path/'execution.json';c.write(selection,dict(executor='composed_execution:fixture',configuration=options))
    arguments=['prepare','--root',str(root),'--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json',json.dumps(launcher),'--execution-config',str(selection),'--max-tokens','1000',
        '--max-seconds','120','--max-steps','7','--model','no-model','--reasoning-effort','high',
        '--node-budget-policy',str(nodes)]
    if wake!='default' or temporary_wake_failure:
        policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
            output_repair=True,output_only=True,native_home=str(home))
        if wake!='missing':policy['execution_wake']=True if wake=='default' else wake
        if temporary_wake_failure:
            policy['automatic_recovery']=dict(enabled=True,max_continues=2,max_retries=1,
                initial_backoff_seconds=0,max_backoff_seconds=0,max_wait_seconds=0,jitter_fraction=0)
        path=tmp_path/'resume.json';c.write(path,policy)
        arguments+=['--worker-resume-policy',str(path)]
    prepared=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',*arguments],
        capture_output=True,text=True,timeout=15)
    assert prepared.returncode==0,(prepared.stdout,prepared.stderr)
    c.write(root/'fixture-options.json',options)
    assert c.settings(root)['research_strategy_version']==3
    assert not (root/'jobs/001.json').exists()  # The real run must select W first.
    return root,home


def test_default_cli_completion_wake_keeps_distinct_followup_execution_under_original_w(tmp_path,monkeypatch,composed_registry):
    root,home=standard_instance(tmp_path,monkeypatch)
    policy=c.settings(root)['resume_policy']
    assert policy['execution_wake'] is True and policy['output_only'] is True and policy['output_repair'] is True
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'settings-identity.json',home/'config.toml')}
    config,call=stop_at_waiting(root)
    step=config['scope']['step'];assert step==1
    assert c.read(root/'jobs/001.json')['role']=='W'
    assert c.read(root/'jobs/001.json')['direct_refs']==[]
    work=c.workspace(root,step)
    capability=c.read(work/'science-task.json')['managed_execution_wait']
    assert capability['wake_authorized'] is True and 'handoff.action and instruction' in capability['instruction']
    handoff=c.read(work/'work/executor-handoff.json')
    assert handoff['action']=='return_control' and handoff['call_id']==call
    assert str(home) not in json.dumps(capability)
    assert len(execution.records(root,step))==1 and len(e.Records(config).calls())==1
    accepted=e.Records(config).accepted(call)
    assert accepted['mechanism_binding']['mechanism_ref']=='w001.m1'
    assert accepted['mechanism_binding']['research_binding']['exploration'] is True
    assert accepted['mechanism_binding']['research_binding']['experiment_question']['change_criterion']
    e.load(config).complete(call)
    original_command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/experiment-config.json')]
    terminal_query=subprocess.run([*original_command,'query','--call-id',call],capture_output=True,text=True,timeout=10)
    assert terminal_query.returncode==0,(terminal_query.stdout,terminal_query.stderr)
    assert json.loads(terminal_query.stdout)['handoff']['action']=='reconcile_or_stop'
    terminal=subprocess.run([*original_command,'reconcile','--call-id',call],capture_output=True,text=True,timeout=10)
    assert terminal.returncode==0,(terminal.stdout,terminal.stderr)
    assert json.loads(terminal.stdout)['handoff']['action']=='consume_terminal'
    from scidiscovery.research_loop import managed_wait
    retained=managed_wait.handoff(config,dict(call_id=call,status='unknown'),'query')
    assert retained['action']=='consume_terminal' and retained['reason']=='original_terminal_verified'
    reply=run_cli(root,on_wait=lambda *args:None);result=json.loads(reply.stdout)
    if reply.returncode!=0 or result['status']!='finish':
        record=execution.record(root,step)
        diagnostic=dict(stdout=reply.stdout,stderr=reply.stderr,attempt=str(record),originals={})
        for path in [record/name for name in ('runner.stderr','native.stderr','native-outcome.json','runner-cleanup.json')]+[work/'work/launches']:
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(max(0,path.stat().st_size-12000))
                    diagnostic['originals'][str(path)]=stream.read(12000).decode('utf-8',errors='replace')
        pytest.fail(json.dumps(diagnostic,ensure_ascii=False,indent=2))
    assert (work/'work/launches').read_text()=='exec\nresume\nresume\n'
    assert (work/'work/consumed-result').read_text()=='known_success'
    assert (work/'work/new-call-accepted').read_text()!=call
    prompt=(work/'work/wake-prompt').read_text()
    assert 'continue original W work' in prompt and 'Read work/execution-results.json' in prompt
    assert 'Output-only:' not in prompt
    assert 'handoff.action' in prompt
    assert result['reason']=='scientific_delivery_declined'
    assert c.read(root/'jobs/002.json')['role']=='A' and not (root/'jobs/003.json').exists()
    chain=execution.records(root,step);assert len(chain)==3
    intent=c.read(chain[1]/'native-intent.json')
    assert intent['continuation_kind']=='execution_wake' and intent['session']['home']==str(home)
    assert all(execution.home(root,step,number)==home for number in range(3))
    assert intent['budget_slice']['previous_tokens']==12
    assert (chain[0]/'execution-wait.json').exists() and (chain[0]/'result-identity.json').exists()
    assert c.read(chain[1]/'native-outcome.json')['usage']['unknown_steps']==[]
    for record in chain:
        outcome=c.read(record/'native-outcome.json')
        raw=(record/'science-output.json').read_bytes()
        assert outcome['terminal_observed'] is True and outcome['returncode']==0 and outcome['stop_reason'] is None
        assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
        assert outcome['native_thread_event']['requested_profile']==outcome['native_thread_event']['effective_profile']
        assert outcome['terminal_evidence']['final']==dict(sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
        assert outcome['raw_ref']==c.file_ref(record/'science-output.json')
        assert c.read(record/'usage.json')==dict(input_tokens=10,cached_input_tokens=2,output_tokens=2)
        assert outcome['metering']['context_windows']['windows_started']==1
    calls=e.Records(config).calls();assert len(calls)==2
    bindings=[e.Records(config).accepted(path.name)['mechanism_binding'] for path in calls]
    assert [binding['mechanism_ref'] for binding in bindings]==['w001.m1','w001.m2']
    assert all(binding['source_step']==step and binding['source_role']=='W' for binding in bindings)
    assert result['usage']['observed_tokens']==result['usage']['accounted_tokens']==48
    assert result['usage']['unknown_steps']==[]
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert json.loads(run_cli(root).stdout)['status']=='finish'
    assert (work/'work/launches').read_text()=='exec\nresume\nresume\n'


def test_rpc_temporary_failure_response_is_unknown_not_proven_non_submission(tmp_path):
    adapter=backends.Codex(dict(native_launcher_argv=['codex'],native_options=dict(
        workspace='workspace-write',network=False,web='disabled',subagents=False)))
    declaration=native_app_server.invocation(adapter.launch(
        dict(model='no-model',reasoning_effort='high',service_tier='default'),tmp_path,
        tmp_path/'pending',tmp_path/'schema','Fixture instruction'))
    declaration['schema']=None
    events=[];sent=[]
    transport=native_app_server.Transport(declaration,emit=events.append,send=sent.append)
    transport.initialize();transport.receive(dict(id=1,result={}), 'Fixture prompt')
    transport.receive(dict(id=2,result=dict(thread=dict(id='original-thread',turns=[]),
        cwd=str(tmp_path),model='no-model',approvalPolicy='never',reasoningEffort='high',serviceTier=None,
        sandbox=dict(type='workspaceWrite',writableRoots=[],networkAccess=False))), 'Fixture prompt')
    wire=(json.dumps(dict(id=3,error=dict(code=-32000,message='temporaryFailure: capacity_exceeded',
        data=dict(httpStatus=503,private_body='must not be interpreted'))))+'\n').encode()
    with pytest.raises(ValueError,match='submission response') as stopped:
        for message in native_app_server.native_lines(io.BytesIO(wire)):
            transport.receive(message,'Fixture prompt')
    transport.diagnostic('turn_response',native_app_server.diagnostic_code(stopped.value))
    failure=adapter.event(events[-2])['failure']
    assert failure['category']=='unknown' and failure['not_accepted'] is False
    assert failure['rpc_code']==-32000 and failure['submission_stage']==3
    diagnostic=adapter.event(events[-1])['transport_diagnostic']
    assert diagnostic['original_call_state']=='unknown'
    assert diagnostic['next_action']=='reconcile_original_without_replay'
    assert diagnostic['thread_bound'] is True and diagnostic['turn_bound'] is False
    assert sum(item.get('method')=='turn/start' for item in sent)==1
    assert not transport.finished and not (tmp_path/'pending').exists()
    assert 'capacity_exceeded' not in json.dumps(events) and 'private_body' not in json.dumps(events)


def test_default_rpc_temporary_failure_after_result_read_never_replays_wake_or_managed_call(tmp_path,monkeypatch,composed_registry):
    from scidiscovery.research_loop import native
    root,home=standard_instance(tmp_path,monkeypatch,adaptive_batches=1,temporary_wake_failure=True)
    config,call=stop_at_waiting(root);step=config['scope']['step'];assert step==1
    work=c.workspace(root,step);first=execution.origin(root,step)
    saved={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/001.json',
        first/'native-intent.json',first/'science-output.json',first/'usage.json',home/'config.toml')}
    accepted=e.Records(config).accepted(call);before=native.usage(root);clock=budget.snapshot(root)
    e.load(config).complete(call)
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert result['status']=='technical_pause',(reply.stdout,reply.stderr)
    diagnostic=result['technical_diagnostic']
    assert diagnostic['phase']=='turn_response' and diagnostic['code']=='rpc_response_unavailable'
    assert diagnostic['original_call_state']=='unknown'
    assert diagnostic['next_action']=='reconcile_original_without_replay'
    chain=execution.records(root,step);assert len(chain)==2
    intent=c.read(chain[1]/'native-intent.json')
    assert intent['continuation_kind']=='execution_wake' and intent['session']['home']==str(home)
    assert intent['budget_slice']['previous_tokens']==12
    failed=c.read(chain[1]/'native-outcome.json')
    assert failed['native_failure']['category']=='unknown' and failed['native_failure']['not_accepted'] is False
    assert failed['terminal_observed'] is False
    assert all(diagnostic[key]==value for key,value in failed['technical_diagnostic'].items())
    assert diagnostic['budgets_preserved'] is True
    assert (work/'work/temporary-wake-failure').exists()
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    assert c.read(work/'work/consumed-output-refs.json')
    assert len(e.Records(config).calls())==1 and e.Records(config).accepted(call)==accepted
    assert c.read(e.Records(config).directory(call)/'execution-observation.json')['status']=='known_success'
    assert not (first/'result-identity.json').exists() and not (root/'jobs/002.json').exists()
    assert native.usage(root)['observed_tokens']>=before['observed_tokens']==12
    assert budget.snapshot(root)['consumed_seconds']>=clock['consumed_seconds']
    assert budget.effective_max_steps(root)==7 and budget.used_steps(root)==2
    assert failed['metering']['coverage']=='unknown'
    retained={path:path.read_bytes() for path in (chain[1]/'native-intent.json',chain[1]/'native-outcome.json')}
    repeated=json.loads(run_cli(root).stdout)
    assert repeated['status']=='technical_pause' and repeated['technical_diagnostic']==diagnostic
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    assert len(execution.records(root,step))==2 and len(e.Records(config).calls())==1
    assert all(path.read_bytes()==raw for path,raw in {**saved,**retained}.items())


@pytest.mark.parametrize('wake',['missing',False])
def test_output_only_without_explicit_wake_diagnoses_before_waiting_for_terminal(tmp_path,monkeypatch,composed_registry,wake):
    root,_=standard_instance(tmp_path,monkeypatch,wake=wake)
    original=(root/'settings.json').read_bytes()
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert reply.returncode==2 and result['status']=='output_repair_unavailable',result
    assert result['reason']=='worker_execution_wake_unauthorized'
    current=accepted_budget.read(root).current_job
    step=current['step'];assert (step,current['role'])==(1,'W')
    work=c.workspace(root,step)
    record=execution.origin(root,step);wait=c.read(record/'execution-wait.json')
    item=wait['calls'][0];config=c.read(Path(item['config']['path']))
    call=c.read(Path(item['accepted']['path']))['call_id']
    assert e.load(config).status_details(call)['state']=='running'
    assert c.read(work/'work/executor-handoff.json')['action']=='poll_original'
    assert c.read(work/'science-task.json')['managed_execution_wait']['wake_authorized'] is False
    assert not (record/'result-identity.json').exists() and (record/'checkpoint.json').exists()
    assert (work/'work/launches').read_text()=='exec\n'
    e.load(config).complete(call);e.record_observation(config,e.load(config),call)
    assert json.loads(run_cli(root).stdout)['reason']=='worker_execution_wake_unauthorized'
    assert (root/'settings.json').read_bytes()==original and len(execution.records(root,step))==1
    assert len(e.Records(config).calls())==1 and not (record/'result-identity.json').exists()


@pytest.mark.parametrize('outcome,fragment,multicall,query_state,terminal_state',[
    ('known_success',False,False,'running',None),('known_failed',False,False,'running',None),
    ('known_success',True,False,'running',None),('known_success',False,True,'running',None),
    ('known_failed',False,False,'queued','cancelled'),
])
def test_one_run_waits_without_llm_then_automatically_wakes_same_session_and_seals(tmp_path,composed_registry,outcome,fragment,multicall,query_state,terminal_state):
    options=dict(collect=outcome=='known_success',terminal_status=outcome,query_state=query_state,
        stage_intent='continue' if fragment else 'review',synchronous_first=multicall)
    if terminal_state:options['terminal_state']=terminal_state
    root=instance(tmp_path,options,fragment=fragment)
    observed={};errors=[]
    def backend_event():
        try:
            wait=root/'native-evidence/002/execution-wait.json';deadline=time.monotonic()+15
            while not wait.exists():
                assert time.monotonic()<deadline
                time.sleep(.03)
            value=c.read(wait);config=c.read(Path(value['calls'][0]['config']['path']))
            assert value['calls'][0]['observed_state']==query_state
            call=c.read(Path(value['calls'][0]['accepted']['path']))['call_id']
            assert not (root/'native-evidence/002/result-identity.json').exists()
            assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
            # More than one controller poll, with no new native turn/accepted call.
            time.sleep(1.2)
            observed.update(before=budget.snapshot(root),steps=budget.used_steps(root),calls=len(e.Records(config).calls()))
            assert observed['before']['clock_phase']=='waiting'
            assert observed['before']['waiting_seconds']>.1
            assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
            if terminal_state=='cancelled':
                path=e.Records(config).directory(call)/'backend-accepted.json';backend=c.read(path)
                backend['state']='cancelling';c.write(path,backend)
                time.sleep(1.2)
                assert not (root/'native-evidence/002/result-identity.json').exists()
                assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
            e.load(config).complete(call)
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=backend_event);thread.start()
    reply=run_cli(root);thread.join(timeout=3)
    log=Path(os.environ.get('SCID_TEST_LOG_DIR',str(tmp_path/'fixture-logs')));log.mkdir(parents=True,exist_ok=True)
    c.write(log/('managed-'+outcome+f'-{fragment}-{multicall}.json'),dict(stdout=reply.stdout,stderr=reply.stderr,
        originals={str(p.relative_to(root)):p.read_text()[-16000:] for p in (root/'native-evidence').rglob('*') if p.is_file() and p.suffix in {'.json','.jsonl','.stderr'}}))
    assert not thread.is_alive() and not errors,(errors,reply.stdout,reply.stderr,[(str(p),p.read_text()[-3000:]) for p in root.glob("native-evidence/002/*.stderr")])
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    result=json.loads(reply.stdout);assert result['status']=='wait',result
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\nresume\n'
    assert (c.workspace(root,2)/'work/consumed-result').read_text()==outcome
    chain=execution.records(root,2);assert len(chain)==2
    intent=c.read(chain[-1]/'native-intent.json')
    assert intent['continuation_kind']=='execution_wake' and intent['session']['thread_id']=='11111111-2222-4333-8444-555555555555'
    for proof in intent['execution_wake']['observations']:
        assert e.proof(proof['path'])==proof
        observation=c.read(Path(proof['path']))
        accepted=c.read(Path(observation['accepted']['path']))
        e.verify_observation(observation,lambda item:Path(item['path']),interface=accepted['interface'])
        assert any(c.read(Path(item['path'])).get('native_payload')=='retained-native-terminal-only'
            for item in observation['proofs'])
    seal=c.read(chain[0]/'result-identity.json');fact=c.read(root/seal['observations']['path'])
    assert fact['managed_calls'][-1]['status']==outcome and observed['calls']==(2 if multicall else 1)
    if terminal_state=='cancelled':
        assert (c.workspace(root,2)/'work/consumed-native-state').read_text()=='cancelled'
        assert fact['managed_calls'][-1]['execution']['native_state']=='cancelled'
        assert fact['candidates']==[]
    assert not (Path(intent['execution_wake']['observations'][0]['path']).parent/'terminal.json').exists()
    c.validate(c.workspace(root,2));c.validate(c.workspace(root,3))
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file() and p.name not in {'host-run.lock','control-ledger.lock'}}
    assert json.loads(run_cli(root).stdout)['status']=='wait'
    assert all(p.read_bytes()==value for p,value in before.items())


def stop_at_waiting(root):
    process=subprocess.Popen([sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    deadline=time.monotonic()+15;path=None
    try:
        while True:
            current=accepted_budget.read(root).current_job
            if current is not None and current['role']=='W':
                path=execution.origin(root,current['step'])/'execution-wait.json'
                if path.exists():break
            if process.poll() is not None:
                stdout,stderr=process.communicate()
                diagnostic=dict(returncode=process.returncode,stdout=stdout,stderr=stderr,originals={})
                # Exact owned attempt only. Preserve the real source diagnosis;
                # neither a failed native turn nor an output file means waiting.
                for name in ('native-outcome.json','native.stderr','runner.stderr'):
                    if path is None:continue
                    original=path.parent/name
                    if not original.is_file():continue
                    with original.open('rb') as stream:
                        stream.seek(0,2);stream.seek(max(0,stream.tell()-8192))
                        diagnostic['originals'][name]=stream.read(8192).decode('utf-8',errors='replace')
                pytest.fail(json.dumps(diagnostic,ensure_ascii=False,indent=2))
            assert time.monotonic()<deadline,'Original W did not enter managed execution waiting'
            time.sleep(.03)
        # A competing run cannot acquire the same control owner or launch anything.
        competing=run_cli(root);assert competing.returncode==1 and 'running controller' in competing.stdout
    finally:
        # It is the control owner, not the completed original native runner.
        if process.poll() is None:process.terminate()
        process.communicate(timeout=5)
    value=c.read(path);config=c.read(Path(value['calls'][0]['config']['path']))
    call=c.read(Path(value['calls'][0]['accepted']['path']))['call_id']
    return config,call


def test_wait_restart_pause_and_same_call_terminal_notification_wakes_once(tmp_path,composed_registry):
    root=instance(tmp_path,dict(collect=False))
    config,call=stop_at_waiting(root)
    assert budget.snapshot(root)['clock_phase']=='waiting'
    with pytest.raises(RuntimeError,match='no running or unknown original execution'):
        budget.amend(root,max_seconds=150,authorization_note='Fixture must not amend a live execution')
    (root/'pause.request').write_text('Operator pause before wake')
    e.load(config).complete(call)
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert result['status']=='waiting_execution' and result['reason']=='paused_before_execution_wake',result
    assert (e.Records(config).directory(call)/'execution-observation.json').exists()
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert not (execution.origin(root,2)/'result-identity.json').exists()
    (root/'pause.request').unlink()
    assert json.loads(run_cli(root).stdout)['status']=='wait'
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\nresume\n'
    assert len(e.Records(config).calls())==1 and len(execution.records(root,2))==2
    assert json.loads(run_cli(root).stdout)['status']=='wait'
    c.validate(c.workspace(root,2))


def test_uncertain_execution_is_not_running_or_replayed(tmp_path,composed_registry):
    root=instance(tmp_path,dict(collect=False,query_state='unknown'))
    reply=run_cli(root)
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    record=execution.origin(root,2);assert not (record/'execution-wait.json').exists()
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert c.read(c.workspace(root,2)/'work/executor-handoff.json')['action']=='reconcile_or_stop'
    config=c.read(record/'experiment-config.json')
    assert len(e.Records(config).calls())==1 and len(execution.records(root,2))==1
    seal=c.read(record/'result-identity.json');fact=c.read(root/seal['observations']['path'])
    assert fact['managed_calls'][0]['status']=='unknown'
    run_cli(root)
    assert len(e.Records(config).calls())==1


def test_wait_deadline_is_finite_without_native_wake_or_submission_replay(tmp_path,composed_registry):
    root=instance(tmp_path,dict(collect=False),seconds=4,node_seconds=1)
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert result['status']=='waiting_execution' and result['reason']=='managed_execution_wait_deadline',result
    report=budget.snapshot(root)
    assert report['waiting_seconds']>0 and report['seconds_remaining']==0 and report['advance_seconds']<report['consumed_seconds']
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert len(execution.records(root,2))==1 and not (execution.origin(root,2)/'result-identity.json').exists()


def test_missing_session_capability_retains_checkpoint_without_fabricated_wake(tmp_path,composed_registry):
    root=instance(tmp_path,dict(collect=False),resume=False)
    result=json.loads(run_cli(root).stdout)
    assert result['status']=='output_repair_unavailable' and result['reason']=='worker_resume_unsupported',result
    assert (execution.origin(root,2)/'checkpoint.json').exists()
    assert not (execution.origin(root,2)/'result-identity.json').exists()
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('outcome,terminal_state,retained_guidance',[
    ('known_success',None,False),('known_failed','failed',False),('known_failed','cancelled',False),
    ('known_success',None,True),
])
def test_executor_handoff_without_wake_polls_past_three_until_same_call_terminal(tmp_path,monkeypatch,composed_registry,outcome,terminal_state,retained_guidance):
    options=dict(collect=True,follow_handoff=True,terminal_at_query=5,terminal_status=outcome)
    if terminal_state:options['terminal_state']=terminal_state
    root=instance(tmp_path,options,resume=False)
    originals={}
    if retained_guidance:
        from scidiscovery.research_loop import managed_wait
        current_capability=managed_wait.capability
        old_instruction=('poll_original permits only that call, at handoff.poll_interval_seconds and at most handoff.max_queries queries total for this call in this turn; later replies do not reset that bound.')
        def old_capability(*args):
            return dict(current_capability(*args),instruction=old_instruction)
        source_job=c.read(root/'jobs/001.json');source_result=c.validate(c.workspace(root,1))
        role,parents,instruction,evidence_return,_,_=controller._route(root,1,'H',source_result,source_job,None,None)
        with monkeypatch.context() as patch:
            patch.setattr(managed_wait,'capability',old_capability)
            frozen=c.build_job(root,2,role,parents,instruction=instruction,evidence_return=evidence_return)
        paths=(root/'jobs/002.json',c.workspace(root,2)/'science-task.json')
        originals={path:path.read_bytes() for path in paths}
        assert c.build_job(root,2,role,parents,instruction=instruction,evidence_return=evidence_return)==frozen
        assert frozen['managed_execution_wait']['wake_authorized'] is False
        assert all(path.read_bytes()==raw for path,raw in originals.items())
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert reply.returncode==0 and result['status']=='wait',(reply.stdout,reply.stderr)
    work=c.workspace(root,2);handoff=c.read(work/'work/executor-handoff.json')
    assert handoff['action']=='poll_original' and handoff['poll_interval_seconds']==1
    assert 'max_queries' not in handoff
    prompt=(work/'work/initial-prompt').read_text()
    assert 'handoff.action' in prompt and 'poll_original' in prompt and 'poll_interval_seconds' in prompt
    assert 'max_queries' not in prompt
    instruction=c.read(work/'science-task.json')['managed_execution_wait']['instruction']
    if retained_guidance:
        assert instruction==old_instruction
        assert 'Polling instructions supersede older query-count advice only' in prompt
        assert 'frozen wake permission, original identity and budget remain binding' in prompt
        assert all(path.read_bytes()==raw for path,raw in originals.items())
    else:
        assert 'max_queries' not in instruction
    call=(work/'work/original-call').read_text()
    assert c.read(work/'work/poll-queries.json')==[call]*5
    assert (work/'work/reconciled-call').read_text()==call
    assert c.read(work/'work/final-handoff.json')['action']=='consume_terminal'
    assert (work/'work/consumed-result').read_text()==outcome
    assert (work/'work/consumed-output-count').read_text()=='1'
    record=execution.origin(root,2);config=c.read(record/'experiment-config.json')
    assert len(e.Records(config).calls())==len(execution.records(root,2))==1
    assert (work/'work/launches').read_text()=='exec\n'
    assert not (record/'execution-wait.json').exists() and (record/'result-identity.json').exists()
    observed=c.read(e.Records(config).directory(call)/'execution-observation.json')
    e.verify_observation(observed,lambda item:Path(item['path']),interface=config['execution_interface'])
    if terminal_state:assert observed['native_state']==terminal_state
    seal=c.read(record/'result-identity.json');fact=c.read(root/seal['observations']['path'])
    assert fact['managed_calls'][0]['status']==outcome
    assert json.loads(run_cli(root).stdout)['status']=='wait'
    assert len(e.Records(config).calls())==len(execution.records(root,2))==1
    assert all(path.read_bytes()==raw for path,raw in originals.items())


def test_executor_handoff_without_wake_stops_on_unknown_same_call_after_three_queries(tmp_path,composed_registry):
    root=instance(tmp_path,dict(collect=False,follow_handoff=True,unknown_at_query=5),resume=False)
    reply=run_cli(root)
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    work=c.workspace(root,2);call=(work/'work/original-call').read_text()
    assert c.read(work/'work/poll-queries.json')==[call]*5
    assert (work/'work/reconciled-call').read_text()==call
    assert c.read(work/'work/final-handoff.json')['action']=='reconcile_or_stop'
    assert not (work/'work/consumed-result').exists()
    record=execution.origin(root,2);config=c.read(record/'experiment-config.json')
    assert len(e.Records(config).calls())==len(execution.records(root,2))==1
    assert (work/'work/launches').read_text()=='exec\n'
    assert not (record/'execution-wait.json').exists()
    seal=c.read(record/'result-identity.json');fact=c.read(root/seal['observations']['path'])
    assert fact['managed_calls'][0]['status']=='unknown'
    assert not (e.Records(config).directory(call)/'execution-observation.json').exists()


def test_bridge_cached_pending_response_requests_reconciliation_without_rewriting_originals(tmp_path,composed_registry,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root=instance(tmp_path,dict(collect=False));config,call=stop_at_waiting(root)
    work=c.workspace(root,2);config_path=work/'work/experiment-config.json'
    directory=work/bridge.SUBMISSION_DIRECTORY;directory.mkdir(exist_ok=True)
    private=Path(config['record_dir']).parent
    claims=private/'test-worker-submissions';claims.mkdir(exist_ok=True)
    server=SimpleNamespace(work=work,directory=directory,config_path=config_path,configuration=config,
        client=c.read(config_path),claims=claims,diagnostic=private/'test-bridge-error.json',
        record=execution.origin(root,2),stop=threading.Event(),environment={},
        launch=lambda *args:pytest.fail('Query must not launch a process'))
    request=dict(submission_ref='S001',arguments=['query','--call-id',call])
    raw=bridge._encoded(request);bridge._handle(server,raw)
    first=c.read(directory/'response.json')
    assert first['result']['handoff']['action']=='return_control'
    originals={path:path.read_bytes() for path in (claims/'S001').iterdir() if path.is_file()}
    bridge._handle(server,raw)
    cached=c.read(directory/'response.json')
    assert cached['result']['status']=='unknown'
    assert cached['result']['handoff']['action']=='reconcile_or_stop'
    assert cached['result']['handoff']['call_id']==call
    assert all(path.read_bytes()==value for path,value in originals.items())
    assert len(e.Records(config).calls())==len(execution.records(root,2))==1
    assert bridge._unknown()['result']['handoff']['action']=='reconcile_or_stop'
    for number,arguments in ((2,1),(3,{'run':'invalid'}),(4,[{'run':'invalid'}])):
        bridge._handle(server,bridge._encoded(dict(submission_ref=f'S{number:03d}',arguments=arguments)))
        malformed=c.read(directory/'response.json')
        assert malformed['code']==2 and malformed['result']['status']=='caller_error'
    # A malformed ordinary request must not terminate the receiver before a
    # subsequent legitimate query of the same original call.
    bridge._handle(server,bridge._encoded(dict(submission_ref='S005',arguments=['query','--call-id',call])))
    assert c.read(directory/'response.json')['result']['handoff']['action']=='return_control'
    from scidiscovery.research_loop import managed_wait
    real_handoff=managed_wait.handoff
    def large_handoff(body,*args,**kwargs):
        return dict(action='reconcile_or_stop',instruction='x'*bridge.MAX_BYTES) if body is not None else real_handoff(body,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait,'handoff',large_handoff)
        bridge._handle(server,raw)
    oversized=c.read(directory/'response.json')
    assert oversized['code']==3 and oversized['result']['status']=='integrity_error'
    assert oversized['result']['handoff']['action']=='reconcile_or_stop'
    assert len((directory/'response.json').read_bytes())<=bridge.MAX_BYTES
    assert all(path.read_bytes()==value for path,value in originals.items())


def test_persisted_wake_intent_without_process_is_unknown_and_never_relaunched(tmp_path,composed_registry,monkeypatch):
    from scidiscovery.research_loop import runtime
    root=instance(tmp_path,dict(collect=False));config,call=stop_at_waiting(root);e.load(config).complete(call)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'start',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('Fixture crash after durable wake acceptance')))
        result=controller.run(root)
    assert result['status']=='native_outcome_unsealed',result
    current=execution.record(root,2);assert c.read(current/'native-intent.json')['continuation_kind']=='execution_wake'
    before=(current/'native-intent.json').read_bytes()
    result=controller.run(root)
    assert result['status']=='native_outcome_unsealed' and (current/'native-intent.json').read_bytes()==before
    assert len(execution.records(root,2))==2 and len(e.Records(config).calls())==1
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('fault',['accepted','submission','retained-proof'])
def test_wait_or_terminal_binding_tamper_cannot_authorize_wake(tmp_path,composed_registry,fault):
    root=instance(tmp_path,dict(collect=False));config,call=stop_at_waiting(root)
    directory=e.Records(config).directory(call)
    if fault=='retained-proof':
        e.load(config).complete(call);e.record_observation(config,e.load(config),call)
        proof=c.read(directory/'execution-observation.json')['proofs'][-1]
        path=Path(proof['path']);path.chmod(0o600);path.write_bytes(b'changed retained terminal')
    else:
        path=directory/('accepted.json' if fault=='accepted' else 'submission.json');value=c.read(path)
        if fault=='accepted':value['interface']=dict(value['interface'],result_protocol=e.LOCAL_RESULT_PROTOCOL)
        else:value['external_run_id']='different-external'
        c.write(path,value)
    reply=run_cli(root)
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert not (execution.origin(root,2)/'continuations/001/native-intent.json').exists()
    assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('outcome,collect',[('known_success',True),('known_failed',True),('known_success',False)])
def test_sealed_unknown_recovery_binds_new_prefix_materials_and_resolves_original_reservation(tmp_path,composed_registry,monkeypatch,outcome,collect):
    from test_composed_execution_results import case
    from scidiscovery.research_loop import managed_wait, search
    root,job,config,cli,call=case(tmp_path,dict(terminal_status=outcome,collect=collect))
    # Construct the historical pre-fix seal; production never uses this bypass.
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait,'before_seal',lambda *args:None)
        finish(root,2,'W',[1],mechanism_findings=[dict(mechanism=label,basis='blocked',finding='Original backend pending') for label in ('P','Q')])
    old_record=execution.origin(root,2);seal_bytes=(old_record/'result-identity.json').read_bytes()
    old_seal=c.read(old_record/'result-identity.json');old_fact=c.read(root/old_seal['observations']['path'])
    assert old_fact['managed_calls'][0]['status']=='unknown'
    # This A is prepared before recovery; its old input prefix must remain stable.
    a=c.build_job(root,3,'A',[1,2]);old_job=(root/'jobs/003.json').read_bytes()
    finish(root,3,'A',[1,2],action='continue_w',search_choice=dict(dispositions=[],next_focus=dict(mechanism_ref='h001.m1',action='screen')))
    assert search.project(root,4)['opportunity_unknown']
    e.load(config).complete(call);assert cli('reconcile','--call-id',call)['status']==outcome
    with controller._writer(root):
        competing=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli','execution-recover','--root',str(root),'--step','2'],capture_output=True,text=True)
        assert competing.returncode==1 and 'running controller' in competing.stdout
    reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli','execution-recover','--root',str(root),'--step','2'],capture_output=True,text=True)
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    assert json.loads(reply.stdout)['status']=='execution_recovered'
    refs=list((root/'host-history/execution-recoveries/002').glob('*.json'));assert len(refs)==1
    recovery_bytes=refs[0].read_bytes()
    # Repeat confirmation consumes the same terminal projection and recovery fact.
    assert controller.execution_recover(root,2)['status']=='execution_recovered'
    assert refs[0].read_bytes()==recovery_bytes
    assert (old_record/'result-identity.json').read_bytes()==seal_bytes and (root/'jobs/003.json').read_bytes()==old_job
    c.validate(c.workspace(root,2));c.validate(c.workspace(root,3))
    current=search.project(root,4)
    assert not current['opportunity_unknown'] and current['opportunity_counts']=={'h001.m1':1}
    successor=c.build_job(root,4,'W',[1,2,3]);assert len(successor['history']['execution_recoveries'])==1
    assert not successor['search']['breadth']['prior_unknown']
    index=c.read(c.workspace(root,4)/successor['history']['view']['indexes']['experiments'])
    recovered=index['experiments'][0]
    assert recovered['call_id']==call and recovered['execution_status']==outcome
    if collect:assert (c.workspace(root,4)/recovered['outputs'][0]['path']).read_bytes()==b'opaque original job package'
    assert successor['worker_view_version']==1
    assert not {'native_originals_index','native_originals','original'} & recovered.keys()
    assert not (c.workspace(root,4)/f'inputs/recovered/002/{call}/originals').exists()
    # Native control proofs remain reachable through the exact frozen host prefix.
    recovery_ref=successor['history']['execution_recoveries'][0]
    assert root/recovery_ref['path']==refs[0] and refs[0].stat().st_size==recovery_ref['size_bytes']
    recovery=c.read(root/recovery_ref['path']);row=recovery['row']
    assert recovery['source_step']==2 and recovery['seal_ref']==c.record_ref(old_record/'result-identity.json')
    assert row['call_id']==call and row['status']==outcome
    assert row['accepted']==old_fact['managed_calls'][0]['accepted'] and row['scope']==old_fact['managed_calls'][0]['scope']
    history.validate_managed(root,job,[row])
    observation=c.read(root/row['observation']['snapshot'])
    native_originals=[c.read(Path(proof['path'])) for proof in observation['proofs']]
    assert any(item.get('native_payload')=='retained-native-terminal-only' and item['call_id']==call
        and item['scope']==row['scope'] and item['status']==outcome for item in native_originals)
    # A fresh W interprets the appended original; no replacement managed submit.
    finish(root,4,'W',[1,2,3],mechanism_findings=[dict(mechanism='P',basis='observed',finding='Read the recovered original'),dict(mechanism='Q',basis='blocked',finding='No Q observation')])
    finish(root,5,'A',[1,4],action='continue_w',search_choice=dict(dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='Assess the new W interpretation')],next_focus=dict(mechanism_ref='h001.m1',action='improve')))
    assert search.project(root,6)['mechanisms']['h001.m1']['status']=='screened'
    next_job=c.build_job(root,6,'W',[1,4,5]);record=execution.record(root,6)
    node_anchor=budget.accept_node(root,next_job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,next_job))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/006.json'),**anchor))
    next_config=e.configure(root,6,'composed_execution:fixture',dict(synchronous=True),c.workspace(root,6)/'work/config.json')
    package=c.workspace(root,6)/'work/package';package.write_bytes(b'new scientific input')
    assert e.dispatch(next_config,package,new=True,mechanism='P')['status']=='known_success'
    assert len(e.Records(config).calls())==1
    # Tampering a consumed recovery prefix is rejected, while old jobs remain unchanged.
    refs[0].chmod(0o600);value=c.read(refs[0]);value['row']['status']='unknown';c.write(refs[0],value)
    with pytest.raises(ValueError):c.validate(c.workspace(root,4))
    c.validate(c.workspace(root,2));c.validate(c.workspace(root,3))


def legacy_two_calls(tmp_path,monkeypatch):
    from test_composed_execution_results import case
    from scidiscovery.research_loop import managed_wait
    import uuid
    root,job,config,cli,first=case(tmp_path,dict(collect=False))
    # Historical parallel originals: construct the two accepted reservations via
    # the common registry contract, not a modern dispatch retry of unknown work.
    records=e.Records(config);adapter=e.load(config);package=c.workspace(root,2)/'work/package';proof=e.proof(package)
    descriptor=e.LocalFileDescriptor(name='0002-'+uuid.uuid4().hex,local_path=str(package),size_bytes=proof['size_bytes'],media_type='application/octet-stream')
    accepted=records.accept(descriptor,adapter.acceptance_metadata(descriptor,new=True),config['execution_interface'],expected_calls=[first],mechanism='Q')
    adapter.dispatch_lease(accepted);external,state=adapter.submit(descriptor);records.bind_external(descriptor.name,external,state)
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait,'before_seal',lambda *args:None)
        finish(root,2,'W',[1],mechanism_findings=[dict(mechanism=label,basis='blocked',finding='Original backend pending') for label in ('P','Q')])
    return root,job,config,cli,first,descriptor.name


def test_partial_recovery_keeps_other_original_unknown_and_prepared_w_prefix(tmp_path,composed_registry,monkeypatch):
    from scidiscovery.research_loop import search
    root,job,config,cli,first,second=legacy_two_calls(tmp_path,monkeypatch)
    finish(root,3,'A',[1,2],action='continue_w',search_choice=dict(dispositions=[],next_focus=dict(mechanism_ref='h001.m1',action='screen')))
    prepared=c.build_job(root,4,'W',[1,2,3]);saved=(root/'jobs/004.json').read_bytes()
    assert prepared['search']['breadth']['prior_unknown']
    adapter=e.load(config);adapter.complete(first)
    assert controller.execution_recover(root,2)['status']=='execution_recovery_pending'
    assert len(list((root/'host-history/execution-recoveries/002').glob('*.json')))==1
    from scidiscovery.research_loop import execution_recovery
    partial=search.fold(root,history.source_sequence(root,4),recovery_refs=execution_recovery.prefix(root,5))
    assert partial['opportunity_unknown'] and partial['opportunity_counts']=={'h001.m1':1,'h001.m2':1}
    # Existing W retains its exact unknown prefix even after all originals resolve.
    adapter.complete(second);assert controller.execution_recover(root,2)['status']=='execution_recovered'
    assert (root/'jobs/004.json').read_bytes()==saved and search.project(root,4)['opportunity_unknown']
    assert c.build_job(root,4,'W',[1,2,3])==prepared
    finish(root,4,'W',[1,2,3],mechanism_findings=[dict(mechanism=label,basis='blocked',finding='This prepared node lacks the new recovery prefix') for label in ('P','Q')])
    a=c.build_job(root,5,'A',[1,2,4])
    assert len(a['history']['execution_recoveries'])==2
    state=search.project(root,5)
    assert not state['opportunity_unknown'] and state['opportunity_counts']=={'h001.m1':1,'h001.m2':1}
    before={p:p.read_bytes() for p in (root/'host-history/execution-recoveries/002').glob('*.json')}
    assert controller.execution_recover(root,2)['status']=='execution_recovered'
    assert before=={p:p.read_bytes() for p in before}
    c.validate(c.workspace(root,2));c.validate(c.workspace(root,3));c.validate(c.workspace(root,4))


def test_wait_gate_preserves_known_missing_output_repair_diagnosis(tmp_path,composed_registry):
    from scidiscovery.research_loop import continuation, managed_wait
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    root=instance(tmp_path,dict(collect=False));job=c.build_job(root,2,'W',[1]);record=execution.origin(root,2)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),output_status='missing',raw_ref=None,cleanup='observed_group_cleaned'))
    managed_wait.before_seal(root,job,record)
    with pytest.raises(OutputRepairRequired,match='Return the required final scientific JSON'):c.seal_result(root,2)
    assert not (record/'execution-wait.json').exists()


@pytest.mark.parametrize('fault',['noninteger-native-code','unknown-native-usage'])
def test_wait_gate_never_converts_uncertain_native_facts_to_free_waiting(tmp_path,composed_registry,monkeypatch,fault):
    from test_composed_execution_results import case
    from scidiscovery.research_loop import managed_wait
    root,job,config,cli,call=case(tmp_path,dict(collect=False))
    record=execution.origin(root,2);raw=record/'science-output.json';raw.write_text('{}')
    c.write(record/'native-outcome.json',dict(returncode=False if fault=='noninteger-native-code' else 0,
        stop_reason=None,usage=dict(unknown_steps=[2] if fault=='unknown-native-usage' else []),raw_ref=c.file_ref(raw)))
    monkeypatch.setattr(e,'load',lambda *args,**kwargs:pytest.fail('Uncertain native facts must not enter managed waiting'))
    managed_wait.before_seal(root,job,record)
    assert not (record/'execution-wait.json').exists() and not (record/'checkpoint.json').exists()
    assert len(e.Records(config).calls())==1
