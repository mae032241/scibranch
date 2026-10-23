"""Managed originals share bytes while writable scientific views remain isolated."""
import errno
import hashlib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_history_handoff import finish
from test_nonlocal_execution_observation import case
from scidiscovery.research_loop import contracts as c, experiments as e, history, controller, runtime
from scidiscovery.research_loop import atomic_files, shared_originals as shared, storage_policy as storage
from scidiscovery.research_loop.local_experiments import LocalExecution


def _identity(path):
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size_bytes=path.stat().st_size)


def _inode(path):
    info=path.stat()
    return info.st_dev,info.st_ino


def test_calls_and_history_share_bytes_without_merging_scientific_sources(tmp_path,monkeypatch,remote_registry):
    root,job,config,_=case(tmp_path,monkeypatch)
    raw=b'opaque retained material\n'*32768
    calls=[]
    for label in ('P','Q'):
        payload=c.workspace(root,2)/'work'/label
        payload.write_bytes(raw)
        result=e.dispatch(config,payload,new=True,mechanism=label)
        directory=e.Records(config).directory(result['call_id'])
        observed=c.read(directory/'execution-observation.json')
        calls.append(observed)
    first,second=[call['outputs'][0] for call in calls]
    assert calls[0]['call_id']!=calls[1]['call_id']
    assert first['source']['path']!=second['source']['path']
    assert _inode(Path(first['path']))==_inode(Path(second['path']))
    archived=history._proof(root,Path(first['path']).relative_to(root).as_posix())
    original=root/archived['snapshot']
    assert _inode(original)==_inode(Path(first['path']))
    finish(root,2,'W',[1],preserve_native_intent=True,mechanism_findings=[
        dict(mechanism=label,basis='observed',finding='Synthetic retained observation.') for label in ('P','Q')])
    successor=c.build_job(root,3,'A',[1,2])
    materials=[row for row in successor['direct_materials'] if row['sha256']==first['sha256']]
    assert len(materials)==2 and len({row['path'] for row in materials})==2
    writable=c.workspace(root,3)/materials[0]['path']
    sibling=c.workspace(root,3)/materials[1]['path']
    assert _inode(writable)!=_inode(original)
    writable.chmod(0o600);writable.write_bytes(b'changed writable view')
    assert original.read_bytes()==sibling.read_bytes()==raw
    # Canonical verification still binds both original call identities.
    for call in calls:e.verify_observation(call,lambda item:Path(item['path']))


def test_isolated_materialization_has_copy_fallback_and_never_aliases_cas(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    source=tmp_path/'source';source.write_bytes(b'opaque original'*8192)
    identity=_identity(source)
    original=shared.retain(root,source,**identity)
    def unsupported(*args):raise OSError(errno.EXDEV,'cross-filesystem reflink')
    monkeypatch.setattr(shared.fcntl,'ioctl',unsupported)
    target=root/'project/nodes/001/inputs/material'
    c._copy_bound(original,target,identity['sha256'],root=root,policy=storage.configuration())
    assert _inode(target)!=_inode(original)
    target.chmod(0o600);target.write_bytes(b'changed')
    source.write_bytes(b'also changed')
    assert original.read_bytes()==b'opaque original'*8192


def test_local_completion_freezes_once_before_observation_without_writable_alias(tmp_path):
    records=tmp_path/'records';records.mkdir()
    config=dict(record_dir=str(records),run_argv=['never-launched'],max_seconds=30)
    adapter=LocalExecution(config)
    completed=[];artifacts=[]
    raw=b'large retained result\n'*16384
    for number in (1,2):
        directory=records/'calls'/str(number);directory.mkdir(parents=True)
        accepted=dict(call_id=str(number),scope={},parent=dict(candidate_id=None),
            local_observation_version=1,entry='main.py')
        for name in ('accepted.json','process.json'):e.write(directory/name,accepted)
        e.write(directory/'terminal.json',dict(returncode=0,cleanup_confirmed=True,transport_complete=True))
        for name in ('source.py','stdout.raw','stderr.raw'):(directory/name).write_bytes(b'')
        artifact=directory/'inputs/artifacts/result.bin';artifact.parent.mkdir(parents=True);artifact.write_bytes(raw)
        artifacts.append(artifact)
        completed.append(adapter.completion(directory,accepted,0,True,True))
    first,second=[Path(value['outputs'][0]['path']) for value in completed]
    cas=records/'host-history/objects'/hashlib.sha256(raw).hexdigest()
    assert _inode(first)==_inode(second)==_inode(cas)
    artifacts[0].write_bytes(b'changed writable artifact')
    assert first.read_bytes()==second.read_bytes()==cas.read_bytes()==raw


def test_disabled_limits_preserve_values_without_scanning_instance_usage(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    for index in range(1100):(root/f'd{index}').mkdir()
    source=root/'source';source.write_bytes(b'uncapped')
    limits=dict(enabled=False,**{name:1 for name in storage.DIMENSIONS})
    assert len(LocalExecution.files(root,policy=limits))==1
    def forbidden(*args,**kwargs):pytest.fail('Disabled quota scanned instance usage')
    monkeypatch.setattr(storage,'usage',forbidden)
    original=shared.retain(root,source,policy=limits,**_identity(source))
    assert original.read_bytes()==b'uncapped'


@pytest.mark.parametrize('dimension',[
    'max_scan_entries','max_files','max_file_bytes','max_total_bytes'])
def test_explicit_scan_limits_reject_before_retaining_files(tmp_path,dimension):
    root=tmp_path/'inputs';root.mkdir()
    (root/'a').write_bytes(b'aa');(root/'b').write_bytes(b'bb')
    (root/'empty').mkdir()
    with pytest.raises(storage.StorageQuotaExceeded,match=dimension):
        LocalExecution.files(root,policy=dict(enabled=True,**{dimension:1}))
    assert not list(root.glob('.scid-*'))


def test_instance_limit_serializes_concurrent_imports_and_counts_partial(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    sources=[]
    for index in range(2):
        source=tmp_path/f'source-{index}';source.write_bytes(bytes([index])*65536);sources.append(source)
    limits=dict(enabled=True,max_instance_bytes=65536)
    finish_copy=atomic_files._finish
    observed=[]
    def observe_partial(output,mode):
        # The entire private partial is charged before it can become an original.
        output.flush()
        observed.append(storage.usage(root))
        finish_copy(output,mode)
    monkeypatch.setattr(atomic_files,'_finish',observe_partial)
    start=threading.Barrier(2);results=[]
    def retain(source):
        start.wait()
        try:results.append(shared.retain(root,source,policy=limits,**_identity(source)))
        except storage.StorageQuotaExceeded:results.append('limited')
    threads=[threading.Thread(target=retain,args=(source,)) for source in sources]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len([value for value in results if value=='limited'])==1
    assert observed==[65536] and storage.usage(root)==65536
    assert len(list((root/'host-history/objects').iterdir()))==1


def test_same_content_competitors_recheck_growth_after_lock_acquisition(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    sources=[]
    for index in range(2):
        path=tmp_path/f'same-source-{index}';path.write_bytes(b'same exact bytes'*4096);sources.append(path)
    identity=_identity(sources[0])
    limits=dict(enabled=True,max_instance_bytes=identity['size_bytes'])
    before_lock=threading.Barrier(2)
    mutation=storage.mutation
    @contextmanager
    def compete(root,growth,**options):
        before_lock.wait(timeout=5)
        with mutation(root,growth,**options):yield
    monkeypatch.setattr(storage,'mutation',compete)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(shared.retain,root,path,policy=limits,**identity) for path in sources]
        originals=[future.result(timeout=10) for future in futures]
    assert originals[0]==originals[1]
    assert storage.usage(root)==identity['size_bytes']
    assert len(list((root/'host-history/objects').iterdir()))==1


def test_fork_selected_input_materialization_enforces_frozen_instance_quota(tmp_path,monkeypatch):
    from test_fork_source import completed_source
    from scidiscovery.research_loop import fork_service, fork_runtime
    limits=dict(enabled=True,max_instance_bytes=4*1024*1024)
    prepare=controller.prepare
    def configure_storage(*args,**options):
        return prepare(*args,storage_policy=limits,**options)
    with monkeypatch.context() as setup:
        setup.setattr(controller,'prepare',configure_storage)
        parent=completed_source(tmp_path)
    child=tmp_path/'branch'
    budget=dict(max_tokens=200,max_seconds=30,max_steps=2)
    preview=fork_service.preview(parent,2,child,budget=budget)
    fork_service.create(parent,2,child,budget=budget,
        expected_preview_id=preview['preview_id'],operation_id='quota-bootstrap')
    filler=child/'quota-filled-before-bootstrap'
    with filler.open('wb') as stream:
        stream.truncate(limits['max_instance_bytes']-storage.usage(child))
    with controller._writer(child),pytest.raises(storage.StorageQuotaExceeded,match='max_instance_bytes'):
        fork_runtime.build_selected_job(child,2)
    assert not (child/'jobs/002.json').exists()
    assert not list((c.workspace(child,2)/'inputs').rglob('*.partial'))


@pytest.mark.process_e2e
def test_post_launch_quota_exhaustion_retains_identity_and_cleans_original_process(tmp_path,monkeypatch,execution_registry):
    limits=dict(enabled=True,max_instance_bytes=4*1024*1024)
    prepare=controller.prepare
    def configure_storage(*args,**options):
        return prepare(*args,storage_policy=limits,**options)
    with monkeypatch.context() as setup:
        setup.setattr(controller,'prepare',configure_storage)
        root,job,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    payload=c.workspace(root,2)/'work/quota.py'
    payload.write_text('import time\nprint("retained identity",flush=True)\ntime.sleep(60)\n')
    from scidiscovery.research_loop import local_experiments
    popen=local_experiments.subprocess.Popen
    started=[]
    def fill_after_launch(*args,**options):
        process=popen(*args,**options);started.append(process)
        filler=root/'quota-filled-after-launch'
        with filler.open('wb') as stream:
            stream.truncate(limits['max_instance_bytes']-storage.usage(root))
        return process
    monkeypatch.setattr(local_experiments.subprocess,'Popen',fill_after_launch)
    result=e.dispatch(config,payload,new=True,mechanism='P')
    assert len(started)==1 and started[0].poll() is not None
    directory=e.Records(config).directory(result['call_id'])
    identity=c.read(directory/'process.json');terminal=c.read(directory/'terminal.json')
    assert identity['pid']==started[0].pid
    assert not runtime.process_matches(identity,identity['task_sha256'])
    assert terminal['cleanup_confirmed'] and terminal['storage_limit_reached']
    assert terminal['transport_complete'] is False
    assert c.read(directory/'completed.json')['status']=='unknown'
    assert e.Records(config).external(result['call_id'])==result['call_id']
    again=e.dispatch(config,payload,new=True,mechanism='P')
    assert again['status']=='unknown' and again['call_id']==result['call_id'] and len(started)==1


def test_interrupted_cas_import_leaves_no_final_or_reused_partial(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    source=tmp_path/'source';source.write_bytes(b'opaque'*65536)
    identity=_identity(source)
    finish_copy=atomic_files._finish
    def interrupted(*args):raise InterruptedError('injected before publication')
    monkeypatch.setattr(atomic_files,'_finish',interrupted)
    with pytest.raises(InterruptedError):shared.retain(root,source,**identity)
    assert not list((root/'host-history/objects').iterdir())
    monkeypatch.setattr(atomic_files,'_finish',finish_copy)
    original=shared.retain(root,source,**identity)
    assert original.read_bytes()==source.read_bytes()
    original.chmod(0o600);original.write_bytes(b'corrupt')
    with pytest.raises(ValueError):shared.retain(root,source,**identity)
    assert original.read_bytes()==b'corrupt'
