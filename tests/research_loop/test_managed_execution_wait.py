"""Real controller CLI, native session transport and registered async backend; no models."""
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
from scidiscovery.research_loop import budget, contracts as c, controller, execution, experiments as e, history

pytestmark = pytest.mark.process_e2e

LAUNCHER = r'''import json,os,pathlib,subprocess,sys,time
from scidiscovery.research_loop import experiments as e
args=sys.argv[1:];prompt=sys.stdin.read();work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME']);role=os.environ['THIN_HWA_ROLE']
root=work.parents[2];step=int(work.name);resume='resume' in args
thread='11111111-2222-4333-8444-555555555555'
with (work/'work/launches').open('a') as f:f.write('resume\n' if resume else 'exec\n')
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
p=home/'sessions/rollout.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
if not p.exists():p.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
payload=dict(summary='Synthetic original execution',scientific_result='Only fixture engineering facts.',limitations=['No model or solver.'],implementation_selection=None)
if role=='W':
 task=json.loads((work/'science-task.json').read_text())
 nominations=task.get('candidate_nomination_mechanisms',[])
 if nominations:
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='This transport fixture declares no scientifically suitable candidate; retain the missing scientific assessment.') for row in nominations if row['status']!='waived'])
 if not resume:
  options=json.loads((root/'fixture-options.json').read_text())
  config=e.configure(root,step,'composed_execution:fixture',options,work/'work/config.json')
  package=work/'work/package';package.write_bytes(b'opaque original job package')
  command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/config.json'),'run','--payload',str(package),'--new-plan','--mechanism','P']
  if options.get('synchronous_first'):
   first=subprocess.run(command,capture_output=True,text=True);assert first.returncode==0,(first.stdout,first.stderr)
   command[-1]='Q'
  reply=subprocess.run(command,capture_output=True,text=True)
  assert reply.returncode==2,(reply.stdout,reply.stderr)
  result=json.loads(reply.stdout);assert result['status']==options.get('query_state','running'),result
  (work/'work/original-call').write_text(result['call_id'])
  (work/'work/executor-handoff.json').write_text(json.dumps(result['handoff']))
  if options.get('follow_handoff') and result['handoff']['action']=='poll_original':
   handoff=result['handoff'];queries=0
   while queries<handoff['max_queries']:
    time.sleep(handoff['poll_interval_seconds'])
    query=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/config.json'),'query','--call-id',result['call_id']],capture_output=True,text=True)
    assert query.returncode==0,(query.stdout,query.stderr)
    next_handoff=json.loads(query.stdout)['handoff']
    assert next_handoff['action']=='poll_original' and next_handoff['max_queries']==handoff['max_queries']
    assert next_handoff['call_id']==result['call_id']
    queries+=1
   (work/'work/poll-count').write_text(str(queries))
 else:
  assert thread in args and (work/'work/original-call').exists()
  assert 'original managed execution has finished' in prompt
  (work/'work/wake-prompt').write_text(prompt)
  packet=json.loads((work/'work/execution-results.json').read_text())
  assert len(packet['executions'])==1
  row=packet['executions'][0]
  assert not {'native_originals','original'} & row.keys()
  result=json.loads((work/row['result']).read_text())
  assert result['status']==row['status']
  assert not {'proofs','scope','accepted','submission','sha256','config_sha256'} & result.keys()
  assert not (work/row['result']).parent.joinpath('originals').exists()
  for item in packet['executions'][0]['outputs']:assert (work/item['path']).read_bytes()==b'opaque original job package'
  (work/'work/consumed-result').write_text(packet['executions'][0]['status'])
  if 'native_state' in packet['executions'][0]:(work/'work/consumed-native-state').write_text(packet['executions'][0]['native_state'])
  options=json.loads((root/'fixture-options.json').read_text())
  if options.get('probe_new_call'):
   forbidden=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/config.json'),'run','--payload',str(work/'work/package'),'--new-plan','--mechanism','Q'],capture_output=True,text=True)
   assert forbidden.returncode==2,(forbidden.stdout,forbidden.stderr)
   denied=json.loads(forbidden.stdout)
   assert denied['status']=='unavailable' and 'Output-only execution wake' in denied['note']
   (work/'work/new-call-denied').write_text(denied['status'])
 payload.update(stage_intent='review' if resume else options.get('stage_intent','review'),mechanism_findings=[dict(mechanism='P',basis='observed' if resume else 'blocked',finding='Read exact original terminal' if resume else 'Original accepted execution still running'),dict(mechanism='Q',basis='blocked',finding='No Q experiment requested in this fixture')])
elif role=='A':
 payload.update(candidate_recommendation=None,progress_assessment=None,portfolio_review=None,search_review=dict(dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='Review original W result')],next_focus=dict(mechanism_ref='h001.m1',action='improve')),next_action='wait',route_reason='Bounded engineering test complete',decision_request=dict(decision='mechanism',question='What changes the mechanism?',change_criterion='A discriminating observation'))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=10,cached_input_tokens=2,output_tokens=2))),flush=True)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


def instance(tmp_path,options,*,fragment=False,resume=True,seconds=120,node_seconds=20):
    public=tmp_path/'public';public.mkdir();goal=public/'objective.md';goal.write_text('Read an async original and retain scientific limits.')
    launcher=tmp_path/'launcher.py';launcher.write_text(LAUNCHER)
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,[sys.executable,str(launcher)],max_tokens=1000,max_seconds=seconds,max_steps=7,
        model='no-model',reasoning_effort='high',node_budget_mode='soft',experiment_mode='external',
        node_budget_policy={role:dict(max_observed_tokens=200,max_seconds=node_seconds) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn') if resume else None,
        worker_fragment_policy=dict(context='same_session') if fragment else None)
    c.write(root/'fixture-options.json',options)
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two mechanisms',commitments=[
        dict(label=label,minimal_test='Read original backend fact',existing_ref=None) for label in ('P','Q')],focus=dict(label=None,action='screen')))
    return root


def run_cli(root):
    return subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)],capture_output=True,text=True,timeout=35)


def standard_instance(tmp_path,monkeypatch,*,wake='default'):
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
    launcher=['codex'] if wake=='default' else [str(executable)]
    arguments=['prepare','--root',str(root),'--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json',json.dumps(launcher),'--experiment-mode','external','--max-tokens','1000',
        '--max-seconds','120','--max-steps','7','--model','no-model','--reasoning-effort','high',
        '--node-budget-policy',str(nodes)]
    if wake!='default':
        policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
            output_repair=True,output_only=True,native_home=str(home))
        if wake!='missing':policy['execution_wake']=wake
        path=tmp_path/'resume.json';c.write(path,policy)
        arguments+=['--worker-resume-policy',str(path)]
    prepared=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',*arguments],
        capture_output=True,text=True,timeout=15)
    assert prepared.returncode==0,(prepared.stdout,prepared.stderr)
    c.write(root/'fixture-options.json',dict(collect=True,terminal_status='known_success',probe_new_call=True))
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two mechanisms',commitments=[
        dict(label=label,minimal_test='Read original backend fact',existing_ref=None) for label in ('P','Q')],
        focus=dict(label=None,action='screen')))
    return root,home


def test_default_cli_wakes_original_output_only_w_and_cannot_start_another_managed_call(tmp_path,monkeypatch,composed_registry):
    root,home=standard_instance(tmp_path,monkeypatch)
    policy=c.settings(root)['resume_policy']
    assert policy['execution_wake'] is True and policy['output_only'] is True and policy['output_repair'] is True
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'settings-identity.json',home/'config.toml')}
    config,call=stop_at_waiting(root)
    work=c.workspace(root,2)
    capability=c.read(work/'science-task.json')['managed_execution_wait']
    assert capability['wake_authorized'] is True and 'normal scientific JSON' in capability['instruction']
    handoff=c.read(work/'work/executor-handoff.json')
    assert handoff['action']=='return_control' and handoff['call_id']==call
    assert str(home) not in json.dumps(capability)
    assert len(execution.records(root,2))==1 and len(e.Records(config).calls())==1
    e.load(config).complete(call)
    original_command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/config.json')]
    terminal_query=subprocess.run([*original_command,'query','--call-id',call],capture_output=True,text=True,timeout=10)
    assert terminal_query.returncode==0,(terminal_query.stdout,terminal_query.stderr)
    assert json.loads(terminal_query.stdout)['handoff']['action']=='reconcile_or_stop'
    terminal=subprocess.run([*original_command,'reconcile','--call-id',call],capture_output=True,text=True,timeout=10)
    assert terminal.returncode==0,(terminal.stdout,terminal.stderr)
    assert json.loads(terminal.stdout)['handoff']['action']=='consume_terminal'
    from scidiscovery.research_loop import managed_wait
    assert managed_wait.handoff(config,dict(call_id=call,status='unknown'),'query')['action']=='reconcile_or_stop'
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert reply.returncode==0 and result['status']=='wait',(reply.stdout,reply.stderr)
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    assert (work/'work/consumed-result').read_text()=='known_success'
    assert (work/'work/new-call-denied').read_text()=='unavailable'
    prompt=(work/'work/wake-prompt').read_text()
    assert 'This output-only wake' in prompt and 'Do not start new experiments or training' in prompt
    assert 'handoff.action' in prompt
    chain=execution.records(root,2);assert len(chain)==2
    intent=c.read(chain[1]/'native-intent.json')
    assert intent['continuation_kind']=='execution_wake' and intent['session']['home']==str(home)
    assert execution.home(root,2,0)==execution.home(root,2,1)==home
    assert intent['budget_slice']['previous_tokens']==12
    assert (chain[0]/'execution-wait.json').exists() and (chain[0]/'result-identity.json').exists()
    assert c.read(chain[1]/'native-outcome.json')['usage']['unknown_steps']==[]
    # Both public dispatch and an adapter's direct acceptance must consult the
    # current host intent, even with the original pre-wake configuration.
    with pytest.raises(e.UnsupportedExecution,match='Output-only execution wake'):
        e.dispatch(config,work/'work/package',new=True,mechanism='Q')
    accepted=e.Records(config).accepted(call)
    from scidiscovery.operations.execution import LocalFileDescriptor
    with pytest.raises(e.UnsupportedExecution,match='Output-only execution wake'):
        e.Records(config).accept(LocalFileDescriptor.model_validate(accepted['prepared']),{},accepted['interface'])
    assert len(e.Records(config).calls())==1
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert json.loads(run_cli(root).stdout)['status']=='wait'
    assert (work/'work/launches').read_text()=='exec\nresume\n'


@pytest.mark.parametrize('wake',['missing',False])
def test_output_only_without_explicit_wake_diagnoses_before_waiting_for_terminal(tmp_path,monkeypatch,composed_registry,wake):
    root,_=standard_instance(tmp_path,monkeypatch,wake=wake)
    original=(root/'settings.json').read_bytes()
    reply=run_cli(root);result=json.loads(reply.stdout)
    assert reply.returncode==2 and result['status']=='output_repair_unavailable',result
    assert result['reason']=='worker_execution_wake_unauthorized'
    record=execution.origin(root,2);wait=c.read(record/'execution-wait.json')
    item=wait['calls'][0];config=c.read(Path(item['config']['path']))
    call=c.read(Path(item['accepted']['path']))['call_id']
    assert e.load(config).status_details(call)['state']=='running'
    assert c.read(c.workspace(root,2)/'work/executor-handoff.json')['action']=='poll_original'
    assert c.read(c.workspace(root,2)/'science-task.json')['managed_execution_wait']['wake_authorized'] is False
    assert not (record/'result-identity.json').exists() and (record/'checkpoint.json').exists()
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    e.load(config).complete(call);e.record_observation(config,e.load(config),call)
    assert json.loads(run_cli(root).stdout)['reason']=='worker_execution_wake_unauthorized'
    assert (root/'settings.json').read_bytes()==original and len(execution.records(root,2))==1
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
    deadline=time.monotonic()+15;path=root/'native-evidence/002/execution-wait.json'
    while not path.exists():
        assert process.poll() is None,process.communicate()
        assert time.monotonic()<deadline
        time.sleep(.03)
    # A competing run cannot acquire the same control owner or launch anything.
    competing=run_cli(root);assert competing.returncode==1 and 'running controller' in competing.stdout
    # It is the control owner, not the completed original native runner.
    process.terminate();process.communicate(timeout=5)
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


def test_executor_handoff_without_wake_uses_bounded_same_call_queries_then_returns(tmp_path,composed_registry):
    root=instance(tmp_path,dict(collect=False,follow_handoff=True),resume=False)
    result=json.loads(run_cli(root).stdout)
    assert result['status']=='output_repair_unavailable' and result['reason']=='worker_resume_unsupported'
    work=c.workspace(root,2);handoff=c.read(work/'work/executor-handoff.json')
    assert handoff['action']=='poll_original' and handoff['poll_interval_seconds']==1
    assert int((work/'work/poll-count').read_text())==handoff['max_queries']==3
    config=c.read(execution.origin(root,2)/'experiment-config.json')
    assert len(e.Records(config).calls())==len(execution.records(root,2))==1
    assert (execution.origin(root,2)/'execution-wait.json').exists()
    assert not (execution.origin(root,2)/'result-identity.json').exists()


def test_bridge_cached_pending_response_requests_reconciliation_without_rewriting_originals(tmp_path,composed_registry,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    root=instance(tmp_path,dict(collect=False));config,call=stop_at_waiting(root)
    work=c.workspace(root,2);config_path=work/'work/config.json'
    directory=work/bridge.SUBMISSION_DIRECTORY;directory.mkdir(exist_ok=True)
    private=Path(config['record_dir']).parent
    claims=private/'worker-submissions';claims.mkdir(exist_ok=True)
    server=SimpleNamespace(work=work,directory=directory,config_path=config_path,configuration=config,
        client=c.read(config_path),claims=claims,diagnostic=private/'test-bridge-error.json',
        stop=threading.Event(),environment={},launch=lambda *args:pytest.fail('Query must not launch a process'))
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
    assert root/recovery_ref['path']==refs[0] and c.digest(refs[0])==recovery_ref['sha256']
    recovery=c.read(root/recovery_ref['path']);row=recovery['row']
    assert recovery['source_step']==2 and recovery['seal_sha256']==c.digest(old_record/'result-identity.json')
    assert row['call_id']==call and row['status']==outcome
    assert row['accepted']==old_fact['managed_calls'][0]['accepted'] and row['scope']==old_fact['managed_calls'][0]['scope']
    history.validate_managed(root,job,[row])
    observation=c.read(root/row['observation']['snapshot'])
    native_originals=[c.read(root/'host-history/objects'/proof['sha256']) for proof in observation['proofs']]
    assert any(item.get('native_payload')=='retained-native-terminal-only' and item['call_id']==call
        and item['scope']==row['scope'] and item['status']==outcome for item in native_originals)
    # A fresh W interprets the appended original; no replacement managed submit.
    finish(root,4,'W',[1,2,3],mechanism_findings=[dict(mechanism='P',basis='observed',finding='Read the recovered original'),dict(mechanism='Q',basis='blocked',finding='No Q observation')])
    finish(root,5,'A',[1,4],action='continue_w',search_choice=dict(dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='Assess the new W interpretation')],next_focus=dict(mechanism_ref='h001.m1',action='improve')))
    assert search.project(root,6)['mechanisms']['h001.m1']['status']=='screened'
    next_job=c.build_job(root,6,'W',[1,4,5]);record=execution.record(root,6)
    node_anchor=budget.accept_node(root,next_job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,next_job))
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/006.json'),**anchor))
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
    descriptor=e.LocalFileDescriptor(name='0002-'+uuid.uuid4().hex,local_path=str(package),sha256=proof['sha256'],size_bytes=proof['size_bytes'],media_type='application/octet-stream')
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
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/002.json')))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=dict(unknown_steps=[]),output_status='missing',raw_sha256=None,cleanup='observed_group_cleaned'))
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
        stop_reason=None,usage=dict(unknown_steps=[2] if fault=='unknown-native-usage' else []),raw_sha256=c.digest(raw)))
    monkeypatch.setattr(e,'load',lambda *args,**kwargs:pytest.fail('Uncertain native facts must not enter managed waiting'))
    managed_wait.before_seal(root,job,record)
    assert not (record/'execution-wait.json').exists() and not (record/'checkpoint.json').exists()
    assert len(e.Records(config).calls())==1
