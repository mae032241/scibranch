"""R1 local receipts and R2 bounded results through real processes; no models."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import contracts as c, controller, experiments as e, history, native, experiment_client
from scidiscovery.research_loop.local_experiments import LocalExecution
from scidiscovery.research_loop.protocol import CURRENT
from scidiscovery.research_loop import worker_execution_bridge as bridge


@pytest.fixture
def local(tmp_path, execution_registry):
    root=tmp_path/'experiment';root.mkdir()
    (root/'inputs').mkdir();(root/'inputs/value.txt').write_text('42')
    script=root/'run.py'
    script.write_text('import json,pathlib,sys\n'
        'value=int(pathlib.Path("inputs/value.txt").read_text())\n'
        'pathlib.Path("artifacts/value.txt").write_text(str(value))\n'
        'pathlib.Path("artifacts/result.json").write_text(json.dumps({"version":1,"metrics":[{"name":"value","value":value}],'
        '"outputs":[{"path":"artifacts/value.txt","label":"observation"}]}))\n'
        'print(json.dumps(sys.argv[1:]))\n')
    config_path=tmp_path/'config.json'
    config=e.freeze(dict(admission=dict(version=1,mode='hard'),record_dir=str(tmp_path/'receipts'),
        scope=dict(handoff_protocol=CURRENT,step=2,role='W',job_ref=dict(path='/fixture/jobs/002.json'),execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'),mechanism_ref=None),
        run_argv=[sys.executable],max_seconds=10),'local_execution:command',path=config_path)
    config_path.write_text(json.dumps(config))
    # Explicit trusted host fixture seam; this is not namespace-isolation evidence.
    def trusted_callback(body,accepted,command,cwd):
        assert body==accepted['interface']['config_record']
        frozen=dict(body,execution_interface=accepted['interface'])
        assert accepted['scope']==body['scope']
        assert e.Records(frozen).accepted(accepted['call_id'])==accepted
        return command,cwd,None
    token=bridge._LOCAL_LAUNCH.set(trusted_callback)
    try:yield root,script,config,config_path
    finally:bridge._LOCAL_LAUNCH.reset(token)


def test_local_process_launch_without_original_host_owner_is_rejected(tmp_path):
    token=bridge._LOCAL_LAUNCH.set(None)
    try:
        with pytest.raises(e.UnsupportedExecution,match='no bare local subprocess fallback'):
            bridge.local_process_launch({}, {}, [sys.executable], tmp_path)
    finally:bridge._LOCAL_LAUNCH.reset(token)


@pytest.mark.process_e2e
@pytest.mark.parametrize('option,value',[
    ('--change-intent','Training-only calibration'),
    ('--parent-plan','prior-candidate'),
],ids=['change-intent','parent-plan'])
def test_local_cli_unsupported_parent_options_can_be_corrected_before_acceptance(local,monkeypatch,capsys,option,value):
    root,script,config,path=local
    effects=[]
    prepare,submit=LocalExecution.prepare,LocalExecution.submit
    def prepared(self,*args,**kwargs):
        effects.append('prepare')
        return prepare(self,*args,**kwargs)
    def submitted(self,*args,**kwargs):
        effects.append('submit')
        return submit(self,*args,**kwargs)
    monkeypatch.setattr(LocalExecution,'prepare',prepared)
    monkeypatch.setattr(LocalExecution,'submit',submitted)
    command=['--config',str(path),'run','--experiment-dir',str(root),'--script',str(script)]
    assert e.main([*command,option,value])==2
    refused=json.loads(capsys.readouterr().out)
    assert refused['status']=='caller_error' and refused['accepted'] is False
    assert 'call_id' not in refused and e.Records(config).calls()==[] and effects==[]
    assert 'Local execution does not support --parent-plan or --change-intent' in refused['note']
    assert 'Remove these options and submit the corrected request' in refused['note']
    assert refused['handoff']['action']=='correct_request'
    assert refused['handoff']['reason']=='execution_not_accepted'
    assert 'no original call from this request to look up' in refused['handoff']['instruction']
    assert e.main(command)==0
    corrected=json.loads(capsys.readouterr().out)
    assert corrected['status']=='known_success'
    assert effects==['prepare','submit'] and len(e.Records(config).calls())==1
    accepted=e.Records(config).accepted(corrected['call_id'])
    assert accepted['parent']==dict(status='unknown',candidate_id=None,source='none')
    assert accepted['change_intent'] is None


@pytest.mark.process_e2e
def test_r1_snapshot_argv_facts_and_original_lookup(local,monkeypatch):
    root,script,config,_=local
    (root/'inputs/empty').mkdir()
    (root/'artifacts').mkdir();prior=root/'artifacts/prior.txt';prior.write_text('prior original')
    original=LocalExecution._execute
    def changed(self,*args):
        (root/'inputs/value.txt').write_text('99')
        original(self,*args)
    monkeypatch.setattr(LocalExecution,'_execute',changed)
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=['literal $(never-run)','two words']))
    assert result['status']=='known_success' and result['returncode']==0 and result['wall_seconds']>=0
    call=result['call_id'];directory=e.Records(config).directory(call)
    accepted=json.loads((directory/'accepted.json').read_text())
    assert 'code_version' not in accepted
    assert {'run.py','inputs/value.txt'} <= {item['name'] for item in accepted['snapshot_files']}
    assert not any(item['name'].startswith('artifacts/') for item in accepted['snapshot_files'])
    assert accepted['command'][-2:]==['literal $(never-run)','two words']
    assert accepted['cwd']==str(directory/'inputs')
    assert json.loads((directory/'stdout.raw').read_text())==accepted['command'][-2:]
    assert (directory/'outputs/value.txt').read_text()=='42'
    assert prior.read_text()=='prior original' and not (root/'artifacts/value.txt').exists()
    adapter=e.load(config)
    assert adapter.lookup_submission(e.LocalFileDescriptor.model_validate(accepted['prepared']))==(call,'succeeded')
    assert adapter.collect_original(call)
    assert len(e.Records(config).calls())==1
    assert e.progress(config)['calls'][0]['result']['status']=='available'


@pytest.mark.process_e2e
@pytest.mark.parametrize('content,status',[(None,'missing'),('invalid','invalid'),
    ('{"version":1,"metrics":[{"name":"x","value":NaN}],"outputs":[]}','invalid'),
    ('{"version":1,"metrics":[],"outputs":[{"path":"../escape","label":"bad"}]}','available')])
def test_r2_bad_or_missing_result_preserves_process_success(local,content,status):
    root,script,config,_=local
    script.write_text('import pathlib\n'+('pathlib.Path("artifacts/result.json").write_text('+repr(content)+')\n'
        if content is not None else 'pathlib.Path("artifacts/empty").mkdir()\n'))
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success' and result['returncode']==0
    assert result['result']['status']==status and result['result']['metrics']==[]
    if content is not None and '../escape' in content:
        assert result['result']['outputs']==[]
        assert result['result']['diagnostics']==[dict(field='outputs[0]',code='output_original_mapping_invalid')]
    if content is not None:
        assert any(item['name']=='result.json' for item in result['outputs'])
    else:
        assert result['outputs']==[]
        assert (e.Records(config).directory(result['call_id'])/'completed.json').is_file()


@pytest.mark.process_e2e
def test_r1_input_link_rejected_before_acceptance_and_output_link_is_collection_failure(local):
    root,script,config,_=local
    (root/'inputs/link').symlink_to(root/'inputs/value.txt')
    with pytest.raises(ValueError):e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert e.Records(config).calls()==[]
    (root/'inputs/link').unlink()
    script.write_text('import pathlib\npathlib.Path("artifacts/link").symlink_to("/etc/passwd")\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success' and result['result']['status']=='unavailable'
    assert 'code_version' not in e.Records(config).accepted(result['call_id'])
    assert result['outputs']==[]


@pytest.mark.process_e2e
def test_r2_unknown_cleanup_never_upgrades_execution_or_collects(local,monkeypatch):
    from scidiscovery.research_loop import runtime
    root,script,config,_=local
    monkeypatch.setattr(runtime,'cleanup_owned_group',lambda identity:'unknown')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='unknown' and result['result']['status']=='unavailable' and result['outputs']==[]
    assert e.dispatch(config,script)['status']=='unknown'
    assert len(e.Records(config).calls())==1


@pytest.mark.parametrize('launcher,mode,expected',[(['codex'],None,'local'),(['custom'],None,'external'),
    (['codex'],'external','external'),(['custom'],'local','local')])
def test_prepare_freezes_effective_mode(tmp_path,launcher,mode,expected):
    public=tmp_path/'public';public.mkdir();objective=public/'goal.md';objective.write_text('Bounded goal')
    root=tmp_path/'instance'
    settings=controller.prepare(root,objective,public,launcher,max_tokens=1000,max_seconds=30,max_steps=3,
        model='fake',reasoning_effort='high',search_policy=None,experiment_mode=mode)
    assert settings['experiment_mode']==expected
    job=c.build_job(root,1,'H',[])
    assert job['experiment_mode']==expected
    assert ('not automatically recorded' in c.science_view(job,root)['experiment_execution'])==(expected=='external')


NATIVE='''import json,os,pathlib,subprocess,sys
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE']
task=json.loads((work/'science-task.json').read_text());prompt=sys.stdin.read()
payload=dict(summary=role+' finding',scientific_result='Synthetic observation only.',limitations=['No scientific qualification.'],implementation_selection=None)
if role=='H':payload.update(next_action='experiment',route_reason='Measure explicit input',search_plan=None)
if role=='W':
 assert 'Managed local experiments:' in prompt
 exp=work/'experiments/one';exp.mkdir(parents=True)
 (exp/'input.txt').write_text('42')
 (exp/'run.py').write_text('import json,pathlib\\nvalue=int(pathlib.Path("input.txt").read_text())\\npathlib.Path("artifacts/value.txt").write_text(str(value))\\npathlib.Path("artifacts/result.json").write_text(json.dumps({"version":1,"metrics":[{"name":"observation","value":value}],"outputs":[{"path":"artifacts/value.txt","label":"measured value"}]}))\\n')
 run=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/experiment-config.json'),'run','--experiment-dir',str(exp),'--script',str(exp/'run.py')],capture_output=True,text=True)
 assert run.returncode==0,(run.stdout,run.stderr)
 payload.update(stage_intent='review',mechanism_findings=None)
if role=='A':
 index=json.loads((work/task['lineage']['indexes']['experiments']).read_text())
 call=index['experiments'][0]
 assert call['execution_status']=='known_success' and call['result_status']=='available',call
 assert call['metrics']==[{'name':'observation','value':42}]
 observed=next(row['path'] for row in call['outputs'] if row['path'].endswith('/value.txt'))
 assert (work/observed).read_text()=='42'
 payload.update(next_action='finish',route_reason='Retain bounded evidence',candidate_recommendation=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None,
  scientific_result='Read exact retained observation: [value]('+observed+').')
args=sys.argv[1:];pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps({'type':'thread.started','thread_id':'fixture-'+role}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':5}}),flush=True)
'''


@pytest.mark.process_e2e
def test_r1_r2_real_native_hwa_without_search_archives_results_for_a_and_export(tmp_path,execution_registry,monkeypatch):
    source=Path(__file__).parents[2]/'src'
    monkeypatch.setenv('PYTHONPATH',str(source)+os.pathsep+os.environ.get('PYTHONPATH',''))
    public=tmp_path/'public';public.mkdir();objective=public/'goal.md';objective.write_text('Read the explicit observation; preserve uncertainty.')
    launcher=tmp_path/'native.py';launcher.write_text(NATIVE)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=1000,max_seconds=60,max_steps=5,
        model='fake',reasoning_effort='high',search_policy=None,experiment_mode='local',node_budget_mode='soft')
    result=controller.run(root)
    assert result['status']=='finish',result
    work=c.workspace(root,2);seal=c.read(c.host_record(work)/'result-identity.json')
    observation=c.read(root/seal['observations']['path']);call=observation['managed_calls'][0]
    assert call['mechanism_binding'] is None and call['status']=='known_success'
    assert observation['candidates']==[]
    assert all(item['retention']=='archived' for item in [*call['originals'],*call['outputs']])
    assert 'Read exact retained observation' in c.validate(c.workspace(root,3))['scientific_result']
    before={p:p.read_bytes() for p in (work/'work/lineage/calls').rglob('*') if p.is_file()}
    assert controller.run(root)==result
    assert before=={p:p.read_bytes() for p in (work/'work/lineage/calls').rglob('*') if p.is_file()}
    assert controller.closeout(root,export_only=True)['status']=='exported_unsubmitted'
    assert not list((root/'host-delivery').glob('**/*.intent.json'))
    output=next(item for item in call['outputs'] if item['name']=='value.txt')
    assert (root/output['snapshot']).read_text()=='42'


@pytest.mark.process_e2e
@pytest.mark.parametrize('direction',[[],{}])
def test_r2_structured_scientific_direction_preserves_completion(local,direction):
    root,script,config,_=local
    value=dict(version=1,metrics=[dict(name='x',value=1,direction=direction)],outputs=[])
    script.write_text('import pathlib\npathlib.Path("artifacts/result.json").write_text('+repr(json.dumps(value))+')\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success' and result['result']['status']=='available'
    assert result['result']['metrics']==value['metrics']
    assert any(item['name']=='result.json' for item in result['outputs'])


@pytest.mark.process_e2e
def test_legacy_current_job_without_mode_keeps_science_view_and_observer_shape(tmp_path):
    from test_history_handoff import finish
    public=tmp_path/'public';public.mkdir();objective=public/'goal.md';objective.write_text('Legacy bounded observation')
    observer=tmp_path/'observer.py';observer.write_text('import json,sys\njson.load(sys.stdin)\njson.dump(dict(candidates=[],current_candidate=None,actual_submission=None,coverage=dict(status="complete",note="Legacy public facts")),sys.stdout)\n')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['custom'],max_tokens=1000,max_seconds=30,max_steps=4,model='fake',reasoning_effort='high',
        search_policy=None,history_observer=dict(adapter=str(observer),config={}))
    # Construct the exact pre-feature current-protocol contract before any job exists.
    settings=c.read(root/'settings.json');settings.pop('experiment_mode');c.write(root/'settings.json',settings)
    c.write(root/'settings-identity.json',dict(path='settings.json'))
    finish(root,1,'H',[],action='experiment')
    w=c.build_job(root,2,'W',[1]);record=c.host_record(c.workspace(root,2))
    c.write(record/'experiment-config.json',{'legacy_configuration':True})
    finish(root,2,'W',[1])
    a=c.build_job(root,3,'A',[2])
    before={p:p.read_bytes() for p in [root/'jobs/002.json',root/'jobs/003.json',record/'result-identity.json',c.workspace(root,2)/'science-task.json']}
    from scidiscovery.research_loop.cli import status
    assert 'execution' not in status(root)['nodes'][1]
    assert 'experiment_execution' not in c.science_view(w,root)
    assert 'experiments' not in a['history']['view']['indexes']
    observed=c.read(root/c.read(record/'result-identity.json')['observations']['path'])
    assert 'managed_calls' not in observed
    assert c.validate(c.workspace(root,2))['role']=='W'
    assert c.build_job(root,3,'A',[2])==a
    assert all(path.read_bytes()==content for path,content in before.items())


LONG_NATIVE='''import json,os,pathlib,signal,subprocess,sys,time
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE'];sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'long-fixture-'+role}),flush=True)
payload=dict(summary=role+' bounded observation',scientific_result='Cancelled process remains unknown.',limitations=['Synthetic transport only.'],implementation_selection=None)
if role=='H':payload.update(next_action='experiment',route_reason='Bounded process experiment',search_plan=None)
if role=='W':
 exp=work/'experiments/long';exp.mkdir(parents=True)
 (exp/'run.py').write_text('import os,pathlib,time\\npathlib.Path("artifacts/started").write_text(str(os.getpid()))\\npathlib.Path(os.environ["THIN_HWA_WORKSPACE"],"work","local-started").write_text(str(os.getpid()))\\ntime.sleep(60)\\n')
 driver=subprocess.Popen([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/experiment-config.json'),'run','--experiment-dir',str(exp),'--script',str(exp/'run.py')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 deadline=time.monotonic()+10
 while not (work/'work/local-started').exists():
  if driver.poll() is not None:
   out,err=driver.communicate()
   (work/'work/local-driver.stdout').write_bytes(out);(work/'work/local-driver.stderr').write_bytes(err)
   raise AssertionError((driver.returncode,out.decode(errors='replace'),err.decode(errors='replace')))
  assert time.monotonic()<deadline,'Local execution did not publish its test-only ready marker'
  time.sleep(.02)
 if os.environ.get('LOCAL_STOP_CASE')=='driver':
  os.kill(driver.pid,signal.SIGKILL);driver.wait()
 else:driver.communicate()
 payload.update(stage_intent='review',mechanism_findings=None)
args=sys.argv[1:];pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':5}}),flush=True)
'''


def long_instance(tmp_path,monkeypatch,case):
    source=Path(__file__).parents[2]/'src'
    monkeypatch.setenv('PYTHONPATH',str(source)+os.pathsep+os.environ.get('PYTHONPATH',''))
    monkeypatch.setenv('LOCAL_STOP_CASE',case)
    public=tmp_path/'public';public.mkdir();objective=public/'goal.md';objective.write_text('Bounded local process cleanup')
    launcher=tmp_path/'native.py';launcher.write_text(LONG_NATIVE)
    root=tmp_path/'instance'
    policy={role:dict(max_seconds=15,max_observed_tokens=100) for role in ('H','W','A','E')}
    controller.prepare(root,objective,public,[sys.executable,str(launcher)],max_tokens=1000,max_seconds=45,max_steps=2,
        model='fake',reasoning_effort='high',search_policy=None,experiment_mode='local',node_budget_mode='soft',node_budget_policy=policy)
    return root


def retain_local_process_diagnostics(root,label):
    directory=Path(os.environ.get('SCID_TEST_LOG_DIR',str(root.parent/'diagnostics')))/label
    for step in (1,2):
        record=c.host_record(c.workspace(root,step))
        for name in ('native-intent.json','native-outcome.json','runner-process.json','native-process.json',
                     'usage.json','native.stderr','runner.stderr','native.jsonl','runner-cleanup.json'):
            source=record/name
            if source.is_file():
                target=directory/f'{step:03d}'/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(source.read_bytes())
    sources=list((c.workspace(root,2)/'work').glob('local-driver.*'))
    sources.extend((c.workspace(root,2)/'work').glob('lost-identity.json'))
    for source in sources:
        target=directory/source.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
    if (root/'jobs/002.json').exists():
        for call in (experiment_client.record_directory(root,2)/'calls').glob('*'):
            for name in ('accepted.json','process.json','terminal.json','completed.json'):
                source=call/name
                if source.is_file():
                    target=directory/'managed-calls'/call.name/name;target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(source.read_bytes())


@pytest.mark.process_e2e
def test_r1_native_owner_cleans_detached_local_group_after_driver_kill(tmp_path,execution_registry,monkeypatch):
    from scidiscovery.research_loop import budget,runtime
    from test_history_handoff import finish
    root=long_instance(tmp_path,monkeypatch,'driver')
    finish(root,1,'H',[],action='experiment')
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Real local Worker cleanup fixture')
    c.build_job(root,2,'W',[1])
    try:result=native.execute_native(root,2)
    finally:retain_local_process_diagnostics(root,'local-driver-cleanup')
    record=c.host_record(c.workspace(root,2));outcome=c.read(record/'native-outcome.json')
    assert result['stage_intent']=='review'
    assert outcome['raw_ref']==c.file_ref(record/'science-output.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['cleanup']=='observed_group_cleaned'
    directory=next((experiment_client.record_directory(root,2)/'calls').iterdir())
    identity=c.read(directory/'process.json')
    assert not runtime.process_matches(identity,identity['task_ref'])
    assert not (directory/'completed.json').exists()
    assert len(list(directory.parent.iterdir()))==1
    originals={p:p.read_bytes() for p in (directory/'accepted.json',directory/'process.json',
        record/'native-intent.json',record/'native-outcome.json',record/'science-output.json')}
    with monkeypatch.context() as repeated:
        repeated.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Collect this same original without a new native process'))
        repeated.setattr(e,'dispatch',lambda *a,**kw:pytest.fail('Collect this same original without resubmitting its experiment'))
        assert native.wait_native(root,2)==result
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert len(list(directory.parent.iterdir()))==1 and not (root/'jobs/003.json').exists()


@pytest.mark.process_e2e
def test_r1_watchdog_signals_only_exact_local_group_after_runner_kill(tmp_path,execution_registry,monkeypatch):
    import signal,time
    from scidiscovery.research_loop import runtime
    root=long_instance(tmp_path,monkeypatch,'runner')
    process=subprocess.Popen([sys.executable,'-c','import json,sys;from pathlib import Path;from scidiscovery.research_loop import controller;print(json.dumps(controller.run(Path(sys.argv[1]))))',str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    directory=None
    try:
        until=time.monotonic()+15
        while time.monotonic()<until:
            matches=(list((experiment_client.record_directory(root,2)/'calls').glob('*/inputs/artifacts/started'))
                     if (root/'jobs/002.json').exists() else [])
            if matches:directory=matches[0].parents[2];break
            time.sleep(.05)
        assert directory is not None
        identity=c.read(directory/'process.json')
        record=c.host_record(c.workspace(root,2));runner=c.read(record/'runner-process.json')
        assert runtime.process_matches(runner,runner['task_ref'])
        os.kill(runner['pid'],signal.SIGKILL)
        until=time.monotonic()+10
        while runtime.process_matches(identity,identity['task_ref']) and time.monotonic()<until:time.sleep(.05)
        assert not runtime.process_matches(identity,identity['task_ref'])
        assert not (directory/'completed.json').exists()
        process.communicate(timeout=15)
        assert c.read(record/'runner-cleanup.json')['managed_experiments']=='cleanup_unknown'
        assert len(list(directory.parent.iterdir()))==1
    finally:
        if process.poll() is None:process.terminate();process.communicate(timeout=10)


@pytest.mark.process_e2e
def test_r1_lost_local_process_original_keeps_budget_cleanup_blocked(tmp_path,execution_registry,monkeypatch):
    import signal
    from scidiscovery.research_loop import budget,runtime
    root=long_instance(tmp_path,monkeypatch,'driver')
    launcher=tmp_path/'native.py'
    text=launcher.read_text().replace("if os.environ.get('LOCAL_STOP_CASE')=='driver':",'''from scidiscovery.research_loop.experiment_client import record_directory
 original=next((record_directory(work.parents[2],int(work.name))/'calls').glob('*/process.json'))
 (work/'work/lost-identity.json').write_bytes(original.read_bytes());original.unlink()
 if os.environ.get('LOCAL_STOP_CASE')=='driver':''')
    launcher.write_text(text)
    identity=None
    try:
        result=controller.run(root)
        retain_local_process_diagnostics(root,'local-lost-identity')
        record=c.host_record(c.workspace(root,2))
        identity=c.read(c.workspace(root,2)/'work/lost-identity.json')
        assert result['status']=='native_stopped',result
        assert c.read(record/'native-outcome.json')['stop_reason']=='cleanup_unknown'
        assert budget.execution_blocked(root)
        accepted=list((experiment_client.record_directory(root,2)/'calls').glob('*/accepted.json'))
        assert len(accepted)==1
        controller.run(root)
        assert list((experiment_client.record_directory(root,2)/'calls').glob('*/accepted.json'))==accepted
    finally:
        # The deliberately destroyed owner original cannot authorize production cleanup.
        # This test retains its exact identity separately solely to clean the negative fixture.
        if identity is not None and runtime.process_matches(identity,identity['task_ref']):
            os.kill(identity['pid'],signal.SIGKILL)


@pytest.mark.process_e2e
def test_r2_cov_missing_receipt_fallback_keeps_domain_context_shape(local,monkeypatch):
    from cov_execution.experiments import CovExecution
    from cov_execution import lineage as cov_lineage
    root,script,config,_=local
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    directory=e.Records(config).directory(result['call_id'])
    accepted=c.read(directory/'accepted.json');accepted.pop('local_observation_version')
    monkeypatch.setattr(cov_lineage,'_receipt',lambda *args,**kwargs:None)
    adapter=CovExecution(dict(config,runs_root=str(root/'runs')))
    fallback=adapter.completion(directory,accepted,0,True,True)
    assert set(fallback)=={'call_id','status','candidate_id','run_id','model','parent','change_intent','scope','proofs','note',
        'result','returncode','wall_seconds'}
    assert fallback['status']=='known_success' and fallback['returncode']==0
    assert fallback['result']['status']=='unavailable'
    assert fallback['candidate_id'] is fallback['run_id'] is fallback['model'] is None


@pytest.mark.stress
def test_r2_output_metadata_capacity_preserves_known_execution(local):
    root,script,config,path=local
    nested=path.parent
    for _ in range(10):nested=nested/('deep-'+'d'*195)
    nested.mkdir(parents=True)
    body={key:value for key,value in config.items() if key!='execution_interface'}
    body['record_dir']=str(nested/'receipts')
    config=e.freeze(body,'local_execution:command',path=path)
    script.write_text('import pathlib\n'
        'for number in range(127):pathlib.Path("artifacts",str(number)+"x"*195).write_text("x")\n'
        'pathlib.Path("artifacts/result.json").write_text(\'{"version":1,"metrics":[],"outputs":[]}\')\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success' and result['returncode']==0
    assert result['result']['status']=='available'
    assert len(result['outputs'])==128
    directory=e.Records(config).directory(result['call_id'])
    assert (directory/'completed.json').stat().st_size>e.MAX_BYTES
    assert len(e.encoded(LocalExecution.public_result(result)))<20_000
    assert len(list((directory/'outputs').iterdir()))==128


@pytest.mark.process_e2e
def test_r2_excessive_json_depth_is_invalid_and_raw_result_is_retained(local):
    root,script,config,_=local
    content='['*1500+'0'+']'*1500
    script.write_text('import pathlib\npathlib.Path("artifacts/result.json").write_text('+repr(content)+')\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success' and result['result']['status']=='invalid'
    assert Path(result['outputs'][0]['path']).read_text()==content


@pytest.mark.stress
def test_full_execution_proof_and_snapshot_stream_above_old_asset_cap(local,monkeypatch):
    root,script,config,_=local
    large=root/'inputs/large.bin'
    with large.open('wb') as stream:stream.truncate(33*1024*1024)
    original_read=Path.read_bytes
    def forbid_read_bytes(path):
        if path.stat().st_size>32*1024*1024:raise AssertionError('Complete original must be copied as a stream')
        return original_read(path)
    monkeypatch.setattr(Path,'read_bytes',forbid_read_bytes)
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    assert result['status']=='known_success'
    accepted=e.Records(config).accepted(result['call_id'])
    item=next(item for item in accepted['snapshot_files'] if item['name']=='inputs/large.bin')
    frozen=e.Records(config).directory(result['call_id'])/'inputs'/item['name']
    assert frozen.stat().st_size==33*1024*1024
    with frozen.open('rb') as retained,large.open('rb') as original:
        while block:=original.read(1024*1024):
            assert retained.read(len(block))==block
        assert retained.read(1)==b''


@pytest.mark.process_e2e
def test_standalone_scientific_logs_share_explicit_records_namespace(local,monkeypatch):
    from scidiscovery.research_loop import atomic_files
    root,script,config,_=local
    script.write_text('import pathlib\nprint(pathlib.Path(__file__).parent)\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    directory=e.Records(config).directory(result['call_id'])
    observation=c.read(directory/'execution-observation.json')
    published=[];publish=atomic_files.publish_stream
    def counted(path,blocks,**options):
        published.append(path)
        return publish(path,blocks,**options)
    monkeypatch.setattr(atomic_files,'publish_stream',counted)
    views=[]
    for _ in range(2):
        outputs=e.scientific_outputs(config,observation,include_logs=True)
        stdout=next(item for item in outputs if item.name.endswith('/stdout.txt'))
        path=Path(stdout.local_path);views.append(path)
        assert path.is_relative_to(Path(config['record_dir'])/'scientific-output-views')
        assert not path.is_relative_to(Path(config['record_dir'])/'calls')
        assert str(Path(config['record_dir'])) not in path.read_text()
    assert views[0]==views[1]
    assert len(published)==2
    assert views[0].stat().st_mode & 0o777==0o400
