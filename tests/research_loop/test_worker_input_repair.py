"""Selected task files fail before acceptance; Local owns runtime preparation."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_nonlocal_execution_observation import case
from scidiscovery.research_loop import contracts as c, execution, experiment_client, experiments as e
from scidiscovery.research_loop import worker_delivery, worker_execution_bridge as bridge


def _receiver(root, config, monkeypatch, *, launch=None):
    """Run the real host handler synchronously when the real client publishes."""
    work=c.workspace(root,2);slot=work/bridge.SUBMISSION_DIRECTORY;slot.mkdir()
    claims=Path(config['record_dir']).parent/'worker-submissions';claims.mkdir()
    server=SimpleNamespace(directory=slot,claims=claims,work=work,
        config_path=work/'work/experiment-config.json',client=c.read(work/'work/experiment-config.json'),
        configuration=config,environment={},record=execution.record(root,2),
        diagnostic=claims.parent/'diagnostic.json',stop=threading.Event(),launch=launch)
    publish=bridge._publish
    def deliver(path, raw):
        publish(path,raw)
        if Path(path)==slot/'submit.json':bridge._handle(server,raw)
    monkeypatch.setattr(bridge,'_publish',deliver)
    return server


def _assert_refusal(reply, root):
    assert reply['code']==2
    result=reply['result']
    assert result['status']=='caller_error' and result['accepted'] is False
    assert result['error'] in {'scientific_input_not_found','scientific_input_unusable'}
    assert result['handoff']['action']=='correct_request'
    assert str(root) not in json.dumps(result)
    return result


@pytest.mark.process_e2e
def test_real_bridge_missing_runtime_directory_runs_fixed_local_code(tmp_path,monkeypatch,execution_registry):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    work=c.workspace(root,2);source=work/'code/screen/run.py';source.parent.mkdir(parents=True)
    source.write_text("import pathlib\nassert pathlib.Path.cwd().name=='direct'\nprint('inert-local-completed')\n")
    runtime=work/'experiments/direct'
    assert not runtime.exists()
    launched=[]
    def launch(body,accepted,command,cwd):
        # This fixture executes the fixed export through the real Local Popen
        # path; native namespace/permission projection has its own regressions.
        launched.append(accepted['call_id'])
        assert Path(cwd)==runtime and runtime.is_dir()
        command=list(command);command[accepted['script_index']]=accepted['script']['path']
        return command,cwd,None
    server=_receiver(root,config,monkeypatch,launch=launch)
    for directory in ('code/screen','scratch/runtime','experiments/nested/runtime'):
        refused=bridge.client(server.directory,['run','--experiment-dir',directory,
            '--script','code/screen/run.py','--mechanism','h001.m1'])
        assert refused['code']==2 and refused['result']['status']=='caller_error'
        assert refused['result']['accepted'] is False
    assert not (work/'scratch').exists() and not (work/'experiments/nested').exists()
    assert not e.Records(config).calls() and launched==[]
    arguments=['run','--experiment-dir','experiments/direct','--script','code/screen/run.py',
        '--new-plan','--mechanism','h001.m1']
    reply=bridge.client(server.directory,arguments)
    assert reply['code']==0 and reply['result']['status']=='known_success'
    call=reply['result']['call_id'];assert launched==[call]
    accepted=e.Records(config).accepted(call)
    assert accepted['source_script']==str(source)
    assert accepted['cwd']==accepted['experiment_directory']==str(runtime)
    assert accepted['code_version']['entry']=='screen/run.py'
    assert (e.Records(config).directory(call)/'stdout.raw').read_text()=='inert-local-completed\n'
    original=(server.directory/'submit.json').read_bytes()
    bridge._handle(server,original)
    assert c.read(server.directory/'response.json')['result']['call_id']==call
    assert launched==[call] and len(e.Records(config).calls())==1
    usage=e.cli_help('run')['usage']
    assert 'task workspace' in usage and 'created if absent' in usage


@pytest.mark.process_e2e
def test_real_client_caches_actionable_input_refusals_for_worker_correction(tmp_path,monkeypatch,execution_registry):
    root,job,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    work=c.workspace(root,2);(work/'code/directory').mkdir(parents=True)
    server=_receiver(root,config,monkeypatch,launch=lambda *args:pytest.fail('Refusal launched a process'))
    cases=[['run','--payload','code/directory','--mechanism','h001.m1'],
        ['compile','--implementation','code/missing.json','--scientific-files','code/directory','--output','artifacts/new.json'],
        ['compile','--scientific-files','code/directory','--implementation','code/missing.json','--output','artifacts/new.json'],
        ['run','--experiment-dir','experiments/direct','--script','run.py','--mechanism','h001.m1']]
    for arguments in cases:
        _assert_refusal(bridge.client(server.directory,arguments),root)
        request=c.read(server.directory/'submit.json');claim=server.claims/request['submission_ref']
        assert c.read(claim/'outcome.json')['calls']==[]
        assert c.read(claim/'response.json')['result']['accepted'] is False
    assert not (work/'experiments/direct').exists()
    original=(server.directory/'submit.json').read_bytes()
    originals={name:(claim/name).read_bytes() for name in ('submit.json','binding.json','response.json','outcome.json')}
    bridge._handle(server,original)
    _assert_refusal(c.read(server.directory/'response.json'),root)
    assert all((claim/name).read_bytes()==raw for name,raw in originals.items())
    record=execution.record(root,2)
    refusal=bridge.terminal_caller_error(root,job,record)
    assert refusal['arguments']==cases[-1] and 'run.py' in refusal['diagnostic']
    correction=worker_delivery._caller_correction(root,job,record)
    assert correction['mechanism_ref']=='h001.m1' and 'run.py' in correction['diagnostic']
    assert not e.Records(config).calls()


@pytest.mark.process_e2e
def test_shared_input_reference_types_direct_cli_inputs(tmp_path,monkeypatch,execution_registry,capsys):
    root,_,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    work=c.workspace(root,2);directory=work/'code/directory';directory.mkdir(parents=True)
    for path,error in ((work/'code/missing.py','scientific_input_not_found'),(directory,'scientific_input_unusable')):
        with pytest.raises(e.CallerError) as caught:experiment_client.input_reference(config,path)
        assert caught.value.error==error and str(root) not in str(caught.value)
    for arguments in (['run','--payload',str(directory),'--mechanism','h001.m1'],
            ['compile','--scientific-files',str(directory),'--implementation',str(work/'code/missing.json'),
                '--output',str(work/'artifacts/new.json')]):
        assert e.main(['--config',str(work/'work/experiment-config.json'),*arguments])==2
        result=json.loads(capsys.readouterr().out)
        assert result['status']=='caller_error' and result['error']=='scientific_input_unusable'
        assert result['accepted'] is False and str(root) not in json.dumps(result)
        assert result['handoff']['action']=='correct_request'
    assert not e.Records(config).calls()


@pytest.mark.process_e2e
def test_missing_bound_original_remains_integrity_failure_at_bridge_and_dispatch(tmp_path,monkeypatch,execution_registry):
    from test_mutable_run_inputs import _frozen_material
    from scidiscovery.research_loop import originals
    root,job,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    work=c.workspace(root,2);logical=work/'inputs/bound.py'
    material,_,_=_frozen_material(root,work,logical,b'print("bound")\n',name='bound.py')
    job['direct_materials']=[*job.get('direct_materials',[]),material]
    c.write(root/'jobs/002.json',job)
    original=originals.material_path(root,material)
    original.unlink()
    # The alias cannot turn a lost bound original into an author-owned typo.
    if logical.exists():logical.unlink()
    with pytest.raises(e.IntegrityError):experiment_client.input_reference(config,logical)
    with pytest.raises(e.IntegrityError):e.dispatch(config,logical,new=True,mechanism='h001.m1')
    server=_receiver(root,config,monkeypatch,launch=lambda *args:pytest.fail('Broken original launched'))
    reply=bridge.client(server.directory,['run','--script','inputs/bound.py','--mechanism','h001.m1'])
    assert reply['code']==3 and reply['result']['status']=='integrity_error'
    assert reply['result'].get('accepted') is not False
    assert reply['result']['handoff']['action']=='reconcile_or_stop'
    assert bridge.terminal_caller_error(root,job,execution.record(root,2)) is None
    assert not e.Records(config).calls()


@pytest.mark.process_e2e
def test_unknown_accepted_original_never_becomes_worker_correction(tmp_path,monkeypatch,remote_registry):
    root,job,config,_=case(tmp_path,monkeypatch,outcome='unknown')
    work=c.workspace(root,2);source=work/'code/source.json';source.parent.mkdir(exist_ok=True);source.write_text('{}')
    server=_receiver(root,config,monkeypatch,launch=lambda *args:pytest.fail('Remote fixture launched Local'))
    reply=bridge.client(server.directory,['run','--payload','code/source.json','--new-plan','--mechanism','h001.m1'])
    assert reply['result']['status']=='unknown'
    call=reply['result']['call_id'];directory=e.Records(config).directory(call)
    before={name:(directory/name).read_bytes() for name in ('accepted.json','completed.json','submission.json')}
    _assert_refusal(bridge.client(server.directory,['run','--script','code/missing.py','--mechanism','h001.m1']),root)
    assert bridge.terminal_caller_error(root,job,execution.record(root,2)) is None
    assert worker_delivery._caller_correction(root,job,execution.record(root,2)) is None
    assert len(e.Records(config).calls())==1
    assert all((directory/name).read_bytes()==raw for name,raw in before.items())


def test_missing_accepted_scientific_input_remains_integrity_error(tmp_path):
    missing=tmp_path/'accepted-source.py'
    accepted=dict(script=dict(path=str(missing),size_bytes=1,file_stat=[1,2,1,3]),
        prepared=dict(local_path=str(missing)),snapshot_files=[])
    with pytest.raises(e.IntegrityError,match='Accepted scientific input original'):
        e.verify_scientific_inputs(accepted)


def test_standalone_missing_selected_input_remains_caller_error(tmp_path):
    config=dict(record_dir=str(tmp_path/'standalone-records'))
    with pytest.raises(e.CallerError) as caught:
        experiment_client.input_reference(config,tmp_path/'missing.py')
    assert caught.value.error=='scientific_input_not_found'
    assert str(tmp_path) not in str(caught.value)
