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


def test_stopped_deliverables_ignore_legacy_byte_quotas_and_keep_exact_sizes(tmp_path):
    root=tmp_path/'instance';work=c.workspace(root,1)
    (work/'artifacts').mkdir(parents=True)
    first=work/'artifacts/array.bin';first.write_bytes(b'array payload')
    second=work/'artifacts/report.txt';second.write_bytes(b'original report')
    inventory=c.CurrentInventory(root,dict(step=1,role='H'),c.host_record(work),0,[],{},
        policy=dict(max_output_item_bytes=1,max_output_total_bytes=1))
    rows,mutable=c._material_inventory(root,1,inventory=inventory)
    assert [(row['path'],row['size_bytes']) for row in rows]==[
        ('inputs/parents/001/artifacts/array.bin',len(b'array payload')),
        ('inputs/parents/001/artifacts/report.txt',len(b'original report'))]
    assert set(mutable)=={first,second}
    inventory.materials=rows;inventory.mutable_materials=mutable
    assert c.collect_materials(root,1,inventory=inventory)==rows
    assert all(path.stat().st_mode&0o777==0o400 for path in mutable)
    assert first.read_bytes()==b'array payload' and second.read_bytes()==b'original report'


def test_assessment_retains_full_materials_and_accepted_legacy_omission():
    material=dict(path='inputs/parents/002/artifacts/array.bin',size_bytes=50)
    prior=dict(step=2,role='A')
    current=dict(step=3,role='W',decision_context=dict(
        source_original='inputs/parents/002/conclusion.json',source_action='continue_w'))
    sources={2:dict(job=prior,seal=dict(materials=[material]),seal_ref={'path':'seal-2'}),
        3:dict(job=current,seal_ref={'path':'seal-3'})}
    members={step:dict(seal_ref={'path':f'seal-{step}'},navigation=dict(path=f'{step}.json')) for step in (2,3)}
    job=dict(step=4,role='A',instruction='assess',direct_materials=[],
        direct_refs=[dict(path='inputs/parents/003/conclusion.json')])
    policy=dict(max_output_item_bytes=1,max_output_total_bytes=1)
    handoff=c._assessment_handoff(job,sources=sources,members=members,policy=policy)
    assert handoff['previous_a']['materials']==[material['path']]
    legacy=dict(handoff,previous_a=dict(handoff['previous_a'],materials=[],
        materials_unavailable_reason='input_material_bound'))
    job['assessment_handoff']=legacy
    assert c._assessment_handoff(job,sources=sources,members=members,policy=policy)==legacy


@pytest.mark.process_e2e
@pytest.mark.parametrize('option,value',[
    ('--change-intent','Training-only calibration'),
    ('--parent-plan','prior-candidate'),
],ids=['change-intent','parent-plan'])
def test_local_cli_optional_parent_options_are_ignored_without_lineage(local,monkeypatch,capsys,option,value):
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
    assert e.main([*command,option,value])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='known_success'
    assert 'ignores --parent-plan and --change-intent' in result['note']
    assert effects==['prepare','submit'] and len(e.Records(config).calls())==1
    accepted=e.Records(config).accepted(result['call_id'])
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
def test_standalone_scientific_logs_share_explicit_records_namespace(local):
    root,script,config,_=local
    script.write_text('import pathlib\nprint(pathlib.Path(__file__).parent)\n')
    result=e.dispatch(config,script,command_options=dict(experiment_directory=str(root),arguments=[]))
    directory=e.Records(config).directory(result['call_id'])
    observation=c.read(directory/'execution-observation.json')
    saved={path:(path.read_bytes(),path.stat().st_ino) for path in
        (directory/'outputs/execution-logs/stdout.txt',directory/'outputs/execution-logs/stderr.txt')}
    views=[]
    for _ in range(2):
        outputs=e.scientific_outputs(config,observation,include_logs=True)
        stdout=next(item for item in outputs if item.name.endswith('/stdout.txt'))
        path=Path(stdout.local_path);views.append(path)
        assert path.is_relative_to(directory/'outputs/execution-logs')
        assert str(Path(config['record_dir'])) not in path.read_text()
    assert views[0]==views[1]
    assert all((path.read_bytes(),path.stat().st_ino)==original for path,original in saved.items())
    assert views[0].stat().st_mode & 0o777==0o400
