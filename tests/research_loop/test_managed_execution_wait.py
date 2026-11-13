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
        repeat_output_repair=False,temporary_wake_failure=False,seconds=120,node_seconds=20):
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
    c.write(nodes,{role:dict(max_observed_tokens=200,max_seconds=node_seconds) for role in 'HWAE'})
    launcher=['codex'] if wake=='default' and not temporary_wake_failure else [str(executable)]
    options=dict(collect=True,terminal_status='known_success',adaptive_batches=adaptive_batches,worker_first=True,
        same_focus=same_focus,repeat_output_repair=repeat_output_repair,temporary_wake_failure=temporary_wake_failure)
    selection=tmp_path/'execution.json';c.write(selection,dict(executor='composed_execution:fixture',configuration=options))
    arguments=['prepare','--root',str(root),'--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json',json.dumps(launcher),'--execution-config',str(selection),'--max-tokens','1000',
        '--max-seconds',str(seconds),'--max-steps','7','--model','no-model','--reasoning-effort','high',
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


def test_wait_restart_pause_and_same_call_terminal_notification_wakes_once(tmp_path,composed_registry,monkeypatch):
    root,_=standard_instance(tmp_path,monkeypatch,adaptive_batches=1)
    config,call=stop_at_waiting(root)
    assert budget.snapshot(root)['clock_phase']=='waiting'
    with pytest.raises(RuntimeError,match='no running or unknown original execution'):
        budget.amend(root,max_seconds=150,authorization_note='Fixture must not amend a live execution')
    (root/'pause.request').write_text('Operator pause before wake')
    e.load(config).complete(call)
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert result['status']=='waiting_execution' and result['reason']=='paused_before_execution_wake',result
    assert (e.Records(config).directory(call)/'execution-observation.json').exists()
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert not (execution.origin(root,1)/'result-identity.json').exists()
    (root/'pause.request').unlink()
    assert json.loads(run_cli(root).stdout)['status']=='finish'
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    assert len(e.Records(config).calls())==1 and len(execution.records(root,1))==2
    assert json.loads(run_cli(root).stdout)['status']=='finish'
    c.validate(c.workspace(root,1))


def test_waiting_longer_than_time_budget_consumes_nothing_and_pause_stops_without_replay(tmp_path,monkeypatch,composed_registry):
    root,_=standard_instance(tmp_path,monkeypatch,adaptive_batches=1,seconds=8,node_seconds=6)
    settings_bytes=(root/'settings.json').read_bytes()
    def outlast_budget_then_pause(record,config,call):
        # Use the current W-first CLI contract; outlast its whole shared clock.
        time.sleep(9.);(root/'pause.request').write_text('')
    reply=run_cli(root,on_wait=outlast_budget_then_pause);result=json.loads(reply.stdout)
    assert result['status']=='waiting_execution' and result['reason']=='paused_before_execution_wake',result
    report=budget.snapshot(root)
    assert report['waiting_seconds']>=9 and report['consumed_seconds']==report['advance_seconds']<8
    assert report['seconds_remaining']>0
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert len(execution.records(root,1))==1 and not (execution.origin(root,1)/'result-identity.json').exists()
    assert (root/'settings.json').read_bytes()==settings_bytes


def test_persisted_wake_intent_without_process_is_unknown_and_never_relaunched(tmp_path,composed_registry,monkeypatch):
    from scidiscovery.research_loop import runtime
    root,_=standard_instance(tmp_path,monkeypatch,adaptive_batches=1);config,call=stop_at_waiting(root);e.load(config).complete(call)
    with monkeypatch.context() as patch:
        patch.setattr(runtime,'start',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('Fixture crash after durable wake acceptance')))
        result=controller.run(root)
    assert result['status']=='native_outcome_unsealed',result
    current=execution.record(root,1);assert c.read(current/'native-intent.json')['continuation_kind']=='execution_wake'
    before=(current/'native-intent.json').read_bytes()
    result=controller.run(root)
    assert result['status']=='native_outcome_unsealed' and (current/'native-intent.json').read_bytes()==before
    assert len(execution.records(root,1))==2 and len(e.Records(config).calls())==1
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'


@pytest.mark.parametrize('fault',['accepted','submission','retained-proof'])
def test_wait_or_terminal_binding_tamper_cannot_authorize_wake(tmp_path,composed_registry,monkeypatch,fault):
    root,_=standard_instance(tmp_path,monkeypatch,adaptive_batches=1);config,call=stop_at_waiting(root)
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
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
    assert not (execution.origin(root,1)/'continuations/001/native-intent.json').exists()
    assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('fault',['noninteger-native-code','unknown-native-usage'])
def test_wait_gate_never_converts_uncertain_native_facts_to_free_waiting(tmp_path,composed_registry,monkeypatch,fault):
    from test_composed_execution_results import case
    from scidiscovery.research_loop import managed_wait
    root,job,config,cli,call=case(tmp_path,dict(collect=False))
    record=execution.origin(root,2);raw=record/'science-output.json';raw.write_text('{}')
    c.write(record/'native-outcome.json',dict(returncode=False if fault=='noninteger-native-code' else 0,
        stop_reason=None,usage=dict(unknown_steps=[2] if fault=='unknown-native-usage' else []),raw_ref=c.file_ref(raw)))
    monkeypatch.setattr(e,'load',lambda *args,**kwargs:pytest.fail('Uncertain native facts must not enter managed waiting'))
    # Invalid native facts return before the inventory or adapter is consumed.
    managed_wait.before_seal(root,job,record,inventory=None)
    assert not (record/'execution-wait.json').exists() and not (record/'checkpoint.json').exists()
    assert len(e.Records(config).calls())==1
