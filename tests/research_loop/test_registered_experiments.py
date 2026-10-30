"""Registered source CLI lifecycle checks; no models or wheel installation."""
from scidiscovery.research_loop.protocol import CURRENT
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import experiments, protocol
from scidiscovery.operations.execution import LifecycleCapabilities, lifecycle_capabilities
from execution_fixtures import execution_registry,remote_registry


@pytest.fixture
def managed(tmp_path,execution_registry):
    records=tmp_path/'work/lineage'
    config=tmp_path/'config.json'
    scope=dict(handoff_protocol=CURRENT,step=2,role='W',job_ref=dict(path=str(tmp_path/'jobs/002.json')),execution_key=dict(path=str(tmp_path/'jobs/002.json')),mechanism_ref=None)
    value=dict(admission=dict(version=1,mode='hard'),record_dir=str(records),scope=scope,run_argv=[sys.executable],max_seconds=10)
    executor='local_execution:command'
    config.write_text(json.dumps(experiments.freeze(value,executor,path=config)))
    env=dict(os.environ,PYTHONPATH=str(Path(experiments.__file__).parents[2])+os.pathsep+os.environ.get('PYTHONPATH',''))
    # These standalone registration/admission lifecycle tests retain the real
    # CLI, Records and Local Popen behind the existing trusted-host test seam.
    # Native namespace/permission qualification is covered separately.
    command=[sys.executable,'-c',
        'from scidiscovery.research_loop import experiments, worker_execution_bridge as bridge\n'
        'bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None))\n'
        'raise SystemExit(experiments.main())']

    def invoke(*args,expected=0):
        reply=subprocess.run([*command,'--config',str(config),*map(str,args)],cwd=tmp_path,env=env,
            capture_output=True,text=True,timeout=20)
        diagnostics={}
        diagnostic_root=tmp_path/'work/execution-diagnostics'
        if reply.returncode!=expected and diagnostic_root.is_dir():
            for path in sorted(diagnostic_root.iterdir())[:4]:
                if path.is_file() and not path.is_symlink():
                    with path.open('rb') as stream:
                        diagnostics[path.name]=stream.read(4096).decode('utf-8',errors='replace')
        assert reply.returncode==expected,(reply.stdout,reply.stderr,diagnostics)
        assert not reply.stderr
        return json.loads(reply.stdout)

    def source(name='input',failed=False,slow=False):
        path=tmp_path/(name+'.py')
        path.write_text('import pathlib,time,sys\n'
            'p=pathlib.Path(__file__).parent/"starts.log"\n'
            'with p.open("a") as f:f.write("started\\n")\n'
            +('time.sleep(2)\n' if slow else '')+'print("ordinary public output")\n'+f'sys.exit({3 if failed else 0})\n')
        return path
    return SimpleNamespace(root=tmp_path,records=records,config=config,env=env,command=command,
        cli=invoke,source=source,executor=executor)


def snapshot(root):
    return {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.process_e2e
def test_registered_cli_lifecycle_query_collect_and_byte_identity(managed):
    result=managed.cli('run','--script',managed.source(),'--new-plan')
    assert result['status']=='known_success'
    before=snapshot(managed.records)
    first=managed.cli('lineage-status');again=managed.cli('lineage-status')
    assert first['counts']==again['counts'] and first['counts']['known_success']==1
    assert snapshot(managed.records)==before
    call=result['call_id']
    assert managed.cli('query','--call-id',call)['state']=='succeeded'
    collected=managed.cli('collect','--call-id',call)
    assert collected['status']=='collected' and collected['outputs']
    for item in collected['outputs']:
        assert experiments.proof(item['local_path'])==dict(path=item['local_path'],size_bytes=item['size_bytes'])
    assert first['calls'][0]['metric'] is first['calls'][0]['candidate_id'] is None
    managed.cli('cancel','--call-id',call,expected=2)


@pytest.mark.process_e2e
def test_registered_cli_known_failure_does_not_become_success(managed):
    result=managed.cli('run','--script',managed.source(failed=True),expected=1)
    assert result['status']=='known_failed'
    counts=managed.cli('lineage-status')['counts']
    assert counts['known_failed']==1 and counts['known_success']==0


@pytest.mark.process_e2e
def test_registered_running_call_visible_before_return(managed):
    source=managed.source(slow=True)
    process=subprocess.Popen([*managed.command,'--config',str(managed.config),'run','--script',str(source),'--new-plan'],
        cwd=managed.root,env=managed.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        until=time.monotonic()+10
        while not list(managed.records.glob('calls/*/process.json')) and time.monotonic()<until:time.sleep(.01)
        assert list(managed.records.glob('calls/*/process.json'))
        assert managed.cli('lineage-status')['counts']['running']==1
        out,err=process.communicate(timeout=15)
        assert process.returncode==0 and not err and json.loads(out)['status']=='known_success'
    finally:
        if process.poll() is None:process.terminate();process.communicate(timeout=10)


@pytest.mark.process_e2e
def test_original_unknown_submission_is_preserved_without_second_launch(managed):
    result=managed.cli('run','--script',managed.source(),'--new-plan')
    directory=managed.records/'calls'/result['call_id']
    (directory/'terminal.json').unlink();(directory/'completed.json').unlink()
    before=snapshot(managed.root)
    status=managed.cli('lineage-status')
    assert status['counts']['unknown']==1
    retry=managed.cli('run','--script',managed.source('same-operation'),'--new-plan',expected=2)
    assert retry['status']=='unknown' and len(list(managed.records.glob('calls/*/accepted.json')))==1
    assert snapshot(managed.records)=={key:value for key,value in before.items() if Path(key).is_relative_to(managed.records)}
    assert managed.cli('query','--call-id',result['call_id'])['state']=='unknown'


@pytest.mark.process_e2e
def test_lookup_restores_same_external_binding_after_lost_reply(managed):
    result=managed.cli('run','--script',managed.source(),'--new-plan')
    directory=managed.records/'calls'/result['call_id']
    original=(directory/'submission.json').read_bytes()
    (directory/'submission.json').unlink()
    before=snapshot(managed.records)
    found=managed.cli('lookup','--call-id',result['call_id'])
    assert found['state']=='succeeded'
    assert (directory/'submission.json').read_bytes()==original
    assert {key:value for key,value in snapshot(managed.records).items() if not key.endswith('/submission.json')}==before
    assert len(list(managed.records.glob('calls/*/process.json')))==1


@pytest.mark.process_e2e
def test_local_executes_frozen_payload_when_original_changes_before_interpreter_read(managed):
    barrier=managed.root/'go'
    bootstrap=managed.root/'bootstrap.py'
    bootstrap.write_text('import os,pathlib,sys,time\n'
        +f'while not pathlib.Path({str(barrier)!r}).exists():time.sleep(.01)\n'
        +'os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n')
    config=json.loads(managed.config.read_text());config.pop('execution_interface')
    config['run_argv']=[sys.executable,str(bootstrap)]
    managed.config.write_text(json.dumps(experiments.freeze(config,managed.executor,path=managed.config)))
    source=managed.root/'mutable.py';source.write_text('print("prepared A")\n')
    process=subprocess.Popen([*managed.command,'--config',str(managed.config),'run','--script',str(source)],
        cwd=managed.root,env=managed.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        until=time.monotonic()+10
        while not list(managed.records.glob('calls/*/process.json')) and time.monotonic()<until:time.sleep(.01)
        assert list(managed.records.glob('calls/*/process.json'))
        source.write_text('print("changed B")\n');barrier.write_text('release')
        out,err=process.communicate(timeout=15)
        assert process.returncode==0 and not err
        directory=managed.records/'calls'/json.loads(out)['call_id']
        assert (directory/'stdout.raw').read_text()=='prepared A\n'
        assert (directory/'source.py').read_text()=='print("prepared A")\n'
    finally:
        if process.poll() is None:process.terminate();process.communicate(timeout=10)


@pytest.mark.process_e2e
def test_cli_discovery_and_lifecycle_do_not_load_control_plane_services(managed):
    script='''import json,sys
from scidiscovery.research_loop import experiments
config=json.load(open(sys.argv[1]));adapter=experiments.load(config)
print(json.dumps({'loaded': sorted(sys.modules), 'flags': vars(adapter.lifecycle_capabilities())}))
'''
    reply=subprocess.run([sys.executable,'-c',script,str(managed.config)],env=managed.env,
        capture_output=True,text=True,timeout=10)
    assert reply.returncode==0,reply.stderr
    value=json.loads(reply.stdout)
    assert value['flags']['query'] and not value['flags']['resume_original']
    assert not any(name.startswith(('scidiscovery.artifact_agent.interfaces','scidiscovery.artifact_agent.service',
        'scidiscovery.operations.catalog')) for name in value['loaded'])


def test_missing_lifecycle_declaration_is_unavailable_not_method_inference():
    class Undeclared:
        def status(self,identity):return 'succeeded'
    assert lifecycle_capabilities(Undeclared())==LifecycleCapabilities()


def test_unregistered_key_and_preserved_config_never_launch(tmp_path,execution_registry):
    before=snapshot(tmp_path)
    with pytest.raises(experiments.UnsupportedExecution):experiments.registration('missing_execution:command')
    with pytest.raises(protocol.UnsupportedProtocol,match='current research-loop protocol'):
        experiments.load(dict(record_dir=str(tmp_path)))
    assert snapshot(tmp_path)==before


@pytest.mark.process_e2e
def test_nonlocal_contract_retains_different_external_identity_without_core_process_assumptions(tmp_path,remote_registry):
    config=tmp_path/'remote.json'
    value=dict(admission=dict(version=1,mode='hard'),record_dir=str(tmp_path/'records'),scope=dict(handoff_protocol=CURRENT,step=2,role='W',job_ref=dict(path=str(tmp_path/'jobs/002.json')),
        execution_key=dict(path=str(tmp_path/'jobs/002.json')),mechanism_ref=None))
    config.write_text(json.dumps(experiments.freeze(value,'remote_execution:fixture',path=config)))
    source=tmp_path/'opaque-package';source.write_bytes(b'not a local script')
    env=dict(os.environ,PYTHONPATH=str(Path(experiments.__file__).parents[2])+os.pathsep+os.environ.get('PYTHONPATH',''))
    command=[sys.executable,'-m','scidiscovery.research_loop.experiments']
    def invoke(*args):
        reply=subprocess.run([*command,'--config',str(config),*map(str,args)],env=env,capture_output=True,text=True,timeout=10)
        assert reply.returncode==0,(reply.stdout,reply.stderr)
        return json.loads(reply.stdout)
    result=invoke('run','--payload',source)
    directory=tmp_path/'records/calls'/result['call_id']
    binding=json.loads((directory/'submission.json').read_text())
    assert binding['external_run_id']=='remote-'+result['call_id']
    assert not (directory/'process.json').exists() and not (directory/'terminal.json').exists()
    assert invoke('query','--call-id',result['call_id'])['state']=='succeeded'
    assert Path(invoke('collect','--call-id',result['call_id'])['outputs'][0]['local_path']).read_bytes()==source.read_bytes()
    (directory/'submission.json').unlink()
    assert invoke('lookup','--call-id',result['call_id'])['state']=='succeeded'
    assert json.loads((directory/'submission.json').read_text())==binding
    assert len(list((tmp_path/'records/calls').iterdir()))==1
