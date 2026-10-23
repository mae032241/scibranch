"""Recovery boundaries: no replay, no false absence, no in-flight sealing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_nonlocal_execution_observation import case
from scidiscovery.research_loop import contracts as c, controller, execution, experiments as e
from scidiscovery.research_loop import execution_recovery, execution_resume, runtime
from scidiscovery.research_loop import candidate_audit, worker_execution_bridge as bridge


def test_pending_capture_contention_never_publishes_missing_evidence(tmp_path, monkeypatch):
    root=tmp_path.resolve()
    job=dict(role='W',step=1,execution_authority=True)
    monkeypatch.setattr(execution_resume,'_capture_pending',lambda *args: (_ for _ in ()).throw(BlockingIOError()))
    with pytest.raises(BlockingIOError):execution_resume.capture_pending(root,job)
    assert not list(root.glob('**/pending-execution-set.json'))


def test_sealed_accepted_unknown_remains_a_global_blocker(tmp_path, monkeypatch):
    from scidiscovery.research_loop import originals,budget
    root=tmp_path.resolve();seal_path=root/'native-evidence/002/result-identity.json'
    seal_path.parent.mkdir(parents=True)
    job_ref=dict(path=str(root/'jobs/002.json'))
    c.write(seal_path,dict(job_ref=job_ref,pending_executions=dict(path='pending.json'),observations=dict(path='observed.json')))
    c.write(root/'pending.json',dict(job=dict(path='jobs/002.json'),allocations=[dict(
        call_id='original',disposition='possibly_prepared',accepted=dict(path='accepted.json'),allocation=dict(path='allocation.json'))]))
    c.write(root/'allocation.json',dict(call_id='original',binding=dict(scope=dict(job_ref=job_ref))))
    c.write(root/'observed.json',dict(managed_calls=[dict(call_id='original',status='unknown')]))
    monkeypatch.setattr(originals,'node',lambda *_:type('Source', (), {'original_root':str(root)})())
    monkeypatch.setattr(originals,'source_for_ref',lambda source,_:source)
    monkeypatch.setattr(originals,'resolve_ref',lambda _,ref:root/ref['path'])
    monkeypatch.setattr(execution_recovery,'prefix',lambda *_:[])
    monkeypatch.setattr(budget,'historical_step_limit',lambda *_:2)
    assert execution_recovery.pending(root)
    assert execution_recovery.pending(root,unknown_only=True)
    assert not execution_recovery.pending(root,ignore=(2,'original'))
    # Only an appended exact-call terminal fact supersedes the frozen unknown.
    monkeypatch.setattr(execution_recovery,'validate',lambda *_ ,**__: [dict(version=2,source_step=2,call_id='original',phase='terminal',row=dict(call_id='original',status='known_success'))])
    assert not execution_recovery.pending(root)


@pytest.mark.parametrize('kind',[ValueError,RuntimeError])
def test_cli_exception_text_is_private(tmp_path,monkeypatch,remote_registry,capsys,kind):
    root,_,config,_=case(tmp_path,monkeypatch)
    secret='/private/control/token=opaque-nonhex-secret'
    def broken(*_,**__):raise kind(secret)
    monkeypatch.setattr(e,'load',broken)
    code=e.main(['--config',str(c.workspace(root,2)/'work/experiment-config.json'),'capabilities'])
    public=capsys.readouterr()
    assert code in {2,3} and secret not in public.out+public.err
    assert json.loads(public.out)['status'] in {'unknown','unresolved'}
    diagnostics=list((Path(config['record_dir']).parent/'execution-diagnostics').glob('*.json'))
    assert len(diagnostics)==1 and c.read(diagnostics[0])['note']==secret


@pytest.mark.parametrize('owner_live,fenced,settles',[(False,False,True),(True,False,False),(False,True,False)])
def test_audit_preflight_recovery_requires_exclusive_owner_and_no_fence(tmp_path,monkeypatch,remote_registry,owner_live,fenced,settles):
    root,_,_,_=case(tmp_path,monkeypatch)
    operation='c'*64
    owner=runtime.process_identity(os.getpid(),operation)
    if not owner_live:owner['start_ticks']+=1
    intent=dict(operation_id=operation,candidate_id='selected',decision=dict(step=2),fence_protocol=1,owner=owner)
    directory=candidate_audit._directory(root,2)
    candidate_audit._save(directory/'intent.json',intent)
    if fenced:candidate_audit._save(directory/'dispatch.json',dict(original='possible Popen with no process receipt'))
    monkeypatch.setattr(candidate_audit,'validate_intent',lambda *_:None)
    with pytest.raises(RuntimeError,match='writer'):candidate_audit._settle_preflight(root,intent)
    with controller._writer(root):
        assert candidate_audit._settle_preflight(root,intent) is settles
    assert (directory/'outcome.json').exists() is settles


@pytest.mark.process_e2e
def test_bridge_process_isolates_globals_reaps_real_descendants_and_never_replays(tmp_path,monkeypatch,remote_registry,capsys):
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);record=execution.origin(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    marker=tmp_path/'real-descendants.json';counter=tmp_path/'dispatch-count'
    monkeypatch.setenv('SCID_BRIDGE_PRIVATE_ENV','parent')
    source=('import json,os,pathlib,subprocess,sys,time;'
        'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True);'
        f'pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]));time.sleep(60)')
    def blocked_main(_):
        assert os.environ['SCID_BRIDGE_PRIVATE_ENV']=='bridge'
        counter.write_text(counter.read_text()+'x' if counter.exists() else 'x')
        subprocess.Popen([sys.executable,'-c',source],start_new_session=True)
        time.sleep(60)
    monkeypatch.setattr(e,'main',blocked_main)
    monkeypatch.setattr(bridge,'_bridge_deadline',lambda *_:time.clock_gettime(time.CLOCK_BOOTTIME)+1.)
    request=dict(submission_ref='S001',arguments=['capabilities'])
    identities=[]
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *_:None,
            environment={'SCID_BRIDGE_PRIVATE_ENV':'bridge'}):
        c.write(slot/'submit.json',request)
        until=time.monotonic()+5
        while not marker.exists() and time.monotonic()<until:time.sleep(.02)
        assert marker.exists()
        identities=[runtime.process_identity(pid,'fixture') for pid in json.loads(marker.read_text())]
        with pytest.raises(BlockingIOError):bridge.stopped(record,execution.identity(root,2))
        print('parent stdout stays live')
        assert os.environ['SCID_BRIDGE_PRIVATE_ENV']=='parent'
    assert 'parent stdout stays live' in capsys.readouterr().out
    assert all(not runtime.process_matches(row,'fixture') for row in identities)
    bridge.stopped(record,execution.identity(root,2))
    assert counter.read_text()=='x'
    # A durable claim interrupted in flight is unknown on the same request;
    # restarting the receiver may not enter e.main again.
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *_:None):
        until=time.monotonic()+5
        while not (slot/'response.json').exists() and time.monotonic()<until:time.sleep(.02)
        assert c.read(slot/'response.json')['result']['status']=='unknown'
    assert counter.read_text()=='x'


def test_tcad_retire_found_binds_original_without_cancel(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from tcad_artifact.managed_execution import TCADManagedExecution
    from scidiscovery.operations.execution import LocalFileDescriptor
    directory=tmp_path/'records/calls/original';directory.mkdir(parents=True)
    c.write(directory/'accepted.json',dict(original=True))
    payload=LocalFileDescriptor(name='original',local_path=str(directory/'accepted.json'),
        **{key:e.proof(directory/'accepted.json')[key] for key in ('size_bytes',)},media_type='application/json')
    wrapper=TCADManagedExecution.__new__(TCADManagedExecution)
    wrapper.config=dict(record_dir=str(tmp_path/'records'));wrapper.target=dict(original=True)
    bindings=[]
    wrapper.records=SimpleNamespace(root=tmp_path/'records',directory=lambda _:directory,
        lease=lambda _:nullcontext(),bind_external=lambda *args:bindings.append(args))
    wrapper._bound=lambda _:(dict(scope={},tcad=dict(authority={},payload=payload.model_dump(mode='json'))),payload)
    wrapper._check=lambda *_,**__:None
    wrapper._binding=lambda *args:None
    wrapper.adapter=SimpleNamespace(validate_submission_protocol=lambda:dict(original=True),
        retire_submission_details=lambda _:dict(found=True,run_id='same-job',state='running'))
    wrapper.cancel_original=lambda _:pytest.fail('retire may not cancel the existing job')
    monkeypatch.setattr(e,'retirement',lambda *_:None)
    with wrapper.retire_original('original') as result:
        assert result['status']=='running' and result['external_run_id']=='same-job'
    assert bindings==[('original','same-job','running')]


def test_owner_run_settles_audit_preflight_before_admission(tmp_path,monkeypatch,remote_registry):
    from scidiscovery.research_loop import audit_recovery,fork_runtime
    root,_,_,_=case(tmp_path,monkeypatch)
    calls=[]
    intent=dict(original='preflight')
    monkeypatch.setattr(candidate_audit,'facts',lambda *_:iter([(intent,None)]))
    monkeypatch.setattr(audit_recovery,'facts',lambda *_:iter([]))
    def settle(actual_root,actual_intent):
        controller.require_writer(actual_root)
        assert actual_intent is intent
        calls.append('settled')
    monkeypatch.setattr(candidate_audit,'_settle_preflight',settle)
    def stop_after_recovery(_):
        assert calls==['settled']
        raise RuntimeError('next original control phase')
    monkeypatch.setattr(fork_runtime,'bootstrap',stop_after_recovery)
    with controller._writer(root),pytest.raises(RuntimeError,match='next original control phase'):
        controller._run(root)


def test_transferred_controller_descriptor_retains_exact_pid_writer_scope(tmp_path,monkeypatch,remote_registry):
    root,_,_,_=case(tmp_path,monkeypatch)
    descriptor=os.open(root/'host-run.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        with pytest.raises(RuntimeError,match='writer'):controller.require_writer(root)
        with controller._local_writer(root,descriptor=descriptor):
            controller.require_writer(root)
        os.fstat(descriptor)  # The detached owner still owns and closes this fd.
        with pytest.raises(RuntimeError,match='writer'):controller.require_writer(root)
    finally:os.close(descriptor)
