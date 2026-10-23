"""Managed originals freeze once and share read-only aliases with exact identities."""
import errno
import hashlib
import os
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
from scidiscovery import archive_io
from scidiscovery.operations.execution import LocalFileDescriptor


def _identity(path):
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size_bytes=path.stat().st_size)


def _inode(path):
    info=path.stat()
    return info.st_dev,info.st_ino


def test_calls_and_history_share_bytes_without_merging_scientific_sources(tmp_path,monkeypatch,remote_registry):
    root,job,config,_=case(tmp_path,monkeypatch)
    raw=b'opaque retained material\n'*32768
    payload_sha=hashlib.sha256(raw).hexdigest()
    copies=[];copy=atomic_files.copy_original
    def counted(source,target,**options):
        if options['sha256']==payload_sha:copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
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
    from scidiscovery.research_loop import managed_wait
    managed_wait.wake_packet(root,job,dict(execution_wake=dict(observations=[
        e.proof(e.Records(config).directory(call['call_id'])/'execution-observation.json') for call in calls])))
    for number,call in enumerate(calls,1):
        wake=c.workspace(root,2)/f'work/execution-results/{number:03d}/outputs'/call['outputs'][0]['name']
        assert _inode(wake)==_inode(original)
    finish(root,2,'W',[1],preserve_native_intent=True,mechanism_findings=[
        dict(mechanism=label,basis='observed',finding='Synthetic retained observation.') for label in ('P','Q')])
    successor=c.build_job(root,3,'A',[1,2])
    materials=[row for row in successor['direct_materials'] if row['sha256']==first['sha256']]
    assert len(materials)==2 and len({row['path'] for row in materials})==2
    first_view=c.workspace(root,3)/materials[0]['path']
    sibling=c.workspace(root,3)/materials[1]['path']
    assert _inode(first_view)==_inode(sibling)==_inode(original)
    assert first_view.stat().st_mode & 0o777 == 0o400
    scratch=c.workspace(root,3)/'work/editable-material'
    scratch.write_bytes(first_view.read_bytes())
    scratch.write_bytes(b'changed working file')
    assert _inode(scratch)!=_inode(original)
    assert original.read_bytes()==first_view.read_bytes()==sibling.read_bytes()==raw
    assert copies==[len(raw)], 'Accepted scripts, observation, collect and next-node inputs must freeze payload only once'
    # Canonical verification still binds both original call identities.
    for call in calls:e.verify_observation(call,lambda item:Path(item['path']))


@pytest.mark.parametrize('error_number',[errno.EXDEV,errno.EOPNOTSUPP,errno.EPERM,errno.EMLINK])
def test_managed_alias_requires_hardlinks_without_copy_fallback(tmp_path,monkeypatch,error_number):
    root=tmp_path/'instance';root.mkdir()
    source=tmp_path/'source';source.write_bytes(b'opaque original'*8192)
    identity=_identity(source)
    original=shared.retain(root,source,source_kind='mutable',**identity)
    def unsupported(*args,**kwargs):raise OSError(error_number,'sharing unsupported')
    def forbidden(*args,**kwargs):pytest.fail('Managed alias copied the frozen payload')
    monkeypatch.setattr(shared.os,'link',unsupported)
    monkeypatch.setattr(atomic_files,'copy_original',forbidden)
    target=root/'project/nodes/001/inputs/material'
    with pytest.raises(archive_io.SharingUnsupported,match='hardlink'):
        c._bind_original(original,target,identity['sha256'],root=root,policy=storage.configuration(),
            size_bytes=identity['size_bytes'],source_kind='managed_frozen')
    assert not target.exists() and original.read_bytes()==source.read_bytes()


def test_local_completion_freezes_once_before_observation_without_writable_alias(tmp_path,monkeypatch):
    records=tmp_path/'records';records.mkdir()
    config=dict(record_dir=str(records),run_argv=['never-launched'],max_seconds=30)
    adapter=LocalExecution(config)
    completed=[];artifacts=[]
    raw=b'large retained result\n'*16384
    copies=[];copy=atomic_files.copy_original
    def counted(source,target,**options):
        copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
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
    assert copies==[len(raw)]
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
    original=shared.retain(root,source,source_kind='mutable',policy=limits,**_identity(source))
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
        try:results.append(shared.retain(root,source,source_kind='mutable',policy=limits,**_identity(source)))
        except storage.StorageQuotaExceeded:results.append('limited')
    threads=[threading.Thread(target=retain,args=(source,)) for source in sources]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len([value for value in results if value=='limited'])==1
    assert observed==[65536] and storage.usage(root)==65536
    assert len(list((root/'host-history/objects').iterdir()))==1


@pytest.mark.parametrize('quota_enabled',[False,True])
def test_same_content_competitors_freeze_once_even_with_quota_disabled(tmp_path,monkeypatch,quota_enabled):
    root=tmp_path/'instance';root.mkdir()
    sources=[]
    for index in range(2):
        path=tmp_path/f'same-source-{index}';path.write_bytes(b'same exact bytes'*4096);sources.append(path)
    identity=_identity(sources[0])
    limits=dict(enabled=quota_enabled,max_instance_bytes=identity['size_bytes'])
    copy=atomic_files.copy_original;copies=[]
    def counted(source,target,**options):
        copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
    if not quota_enabled:
        def forbidden(*args,**kwargs):pytest.fail('Disabled quota scanned usage')
        monkeypatch.setattr(storage,'usage',forbidden)
    start=threading.Barrier(2)
    def retain(path):
        start.wait(timeout=5)
        return shared.retain(root,path,source_kind='mutable',policy=limits,**identity)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(retain,path) for path in sources]
        originals=[future.result(timeout=10) for future in futures]
    assert originals[0]==originals[1]
    assert copies==[identity['size_bytes']]
    if quota_enabled:assert storage.usage(root)==identity['size_bytes']
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
    # A shared input consumes no additional payload bytes, even at the frozen
    # physical limit. A new independent freeze still requires real capacity.
    source=next(path for path in (child/'host-runtime/fork/inherited').rglob('*') if path.is_file() and path.stat().st_size)
    identity=_identity(source)
    original=shared.retain(child,source,source_kind='managed_frozen',policy=limits,**identity)
    exact_limit=dict(limits,max_instance_bytes=storage.usage(child))
    target=child/'project/nodes/002/inputs/quota-alias'
    shared.link(original,target,root=child,policy=exact_limit,**identity)
    assert _inode(target)==_inode(source) and storage.usage(child)==exact_limit['max_instance_bytes']
    mutable=tmp_path/'new-material';mutable.write_bytes(b'a new independent input')
    with pytest.raises(storage.StorageQuotaExceeded,match='max_instance_bytes'):
        shared.retain(child,mutable,source_kind='mutable',policy=exact_limit,**_identity(mutable))



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
    with pytest.raises(InterruptedError):shared.retain(root,source,source_kind='mutable',**identity)
    assert not list((root/'host-history/objects').iterdir())
    monkeypatch.setattr(atomic_files,'_finish',finish_copy)
    original=shared.retain(root,source,source_kind='mutable',**identity)
    assert original.read_bytes()==source.read_bytes()
    original.chmod(0o600);original.write_bytes(b'corrupt')
    with pytest.raises(ValueError):shared.retain(root,source,source_kind='mutable',**identity)
    assert original.read_bytes()==b'corrupt'


def test_mutable_readonly_source_with_open_writer_freezes_independently(tmp_path):
    root=tmp_path/'instance';root.mkdir()
    source=tmp_path/'external';raw=b'unchanged original'
    source.write_bytes(raw)
    descriptor=os.open(source,os.O_WRONLY)
    try:
        source.chmod(0o400)
        inode=_inode(source)
        original=shared.retain(root,source,source_kind='mutable',**_identity(source))
        alias=root/'inputs/original'
        shared.link(original,alias,root=root,**_identity(original))
        os.pwrite(descriptor,b'altered writer FD',0)
        os.fsync(descriptor)
        assert _inode(source)==inode and source.stat().st_mode & 0o777 == 0o400
        assert _inode(original)==_inode(alias)!=inode
        assert source.read_bytes()!=raw and original.read_bytes()==alias.read_bytes()==raw
    finally:os.close(descriptor)


def test_existing_cas_still_rejects_changed_source_and_independent_alias(tmp_path):
    root=tmp_path/'instance';root.mkdir()
    source=tmp_path/'source';source.write_bytes(b'exact original')
    identity=_identity(source)
    original=shared.retain(root,source,source_kind='mutable',**identity)
    source.write_bytes(b'changed source')
    with pytest.raises(ValueError,match='binding'):
        shared.retain(root,source,source_kind='mutable',**identity)
    target=root/'independent-same-bytes';target.write_bytes(original.read_bytes());target.chmod(0o400)
    with pytest.raises(ValueError,match='shared inode'):
        shared.link(original,target,root=root,**identity)
    assert _inode(target)!=_inode(original) and target.read_bytes()==original.read_bytes()


def test_cas_first_new_bytes_and_managed_aliases_never_rewrite_payload(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    raw=b'host generated complete original'*8192
    published=[];publish=atomic_files.publish_bytes
    def counted(target,payload,**options):
        published.append(len(payload))
        return publish(target,payload,**options)
    def forbidden(*args,**kwargs):pytest.fail('A managed frozen original was copied')
    monkeypatch.setattr(atomic_files,'publish_bytes',counted)
    monkeypatch.setattr(atomic_files,'copy_original',forbidden)
    original=shared.publish_bytes(root,raw)
    first=root/'project/global/objective.md'
    shared.link(original,first,root=root,**_identity(original))
    retained=shared.retain(root,first,source_kind='managed_frozen',**_identity(first))
    assert retained==shared.publish_bytes(root,raw)==original
    second=root/'project/nodes/001/inputs/global/objective.md'
    shared.link(retained,second,root=root,**_identity(first))
    assert published==[len(raw)]
    assert _inode(first)==_inode(second)==_inode(original)
    first.unlink();original.unlink()
    assert second.read_bytes()==raw


@pytest.mark.parametrize('quota_enabled',[False,True])
def test_streamed_cas_competitors_write_once_and_hits_do_not_replay(tmp_path,monkeypatch,quota_enabled):
    root=tmp_path/'instance';root.mkdir()
    raw=b'projected complete log\n'*16
    identity=dict(sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
    limits=dict(enabled=quota_enabled,max_instance_bytes=len(raw))
    publish=atomic_files.publish_stream;written=[]
    def counted(target,blocks,**options):
        def counted_blocks():
            for block in blocks:
                written.append(len(block))
                yield block
        return publish(target,counted_blocks(),**options)
    monkeypatch.setattr(atomic_files,'publish_stream',counted)
    if not quota_enabled:
        monkeypatch.setattr(storage,'usage',lambda *a,**k:pytest.fail('Disabled quota scanned usage'))
    start=threading.Barrier(2)
    def retain():
        start.wait(timeout=5)
        return shared.publish_stream(root,lambda:iter((raw[:7],raw[7:])),policy=limits,**identity)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(retain) for _ in range(2)]
        originals=[future.result(timeout=10) for future in futures]
    assert originals[0]==originals[1] and sum(written)==len(raw)
    def forbidden():pytest.fail('An existing CAS projection was physically generated again')
    assert shared.publish_stream(root,forbidden,policy=limits,**identity)==originals[0]
    alias=root/'views/log.txt'
    shared.link(originals[0],alias,root=root,policy=limits,**identity)
    assert _inode(alias)==_inode(originals[0]) and alias.read_bytes()==raw
    originals[0].chmod(0o600);originals[0].write_bytes(b'corrupt')
    with pytest.raises(ValueError):shared.publish_stream(root,forbidden,policy=limits,**identity)


def test_streamed_cas_capacity_rejects_before_creating_a_partial(tmp_path):
    root=tmp_path/'instance';root.mkdir()
    raw=b'new bytes'
    def forbidden():pytest.fail('Quota rejection began writing the projection')
    with pytest.raises(storage.StorageQuotaExceeded,match='max_instance_bytes'):
        shared.publish_stream(root,forbidden,sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),
                              policy=dict(enabled=True,max_instance_bytes=1))
    assert not (root/'host-history/objects').exists()


def test_streamed_cas_uses_original_deadline_during_publication(tmp_path):
    root=tmp_path/'instance';root.mkdir()
    class Deadline:
        expired=False
        def remaining_seconds(self):
            if self.expired:raise TimeoutError('same original deadline')
            return 1
    context=Deadline()
    def blocks():
        yield b'a'
        context.expired=True
        yield b'b'
    with pytest.raises(TimeoutError,match='same original deadline'):
        shared.publish_stream(root,blocks,sha256=hashlib.sha256(b'ab').hexdigest(),size_bytes=2,context=context)
    assert not list((root/'host-history/objects').iterdir())


def test_usage_tolerates_only_entries_and_descendant_directories_that_disappear(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    (root/'kept').write_bytes(b'keep');os.link(root/'kept',root/'alias')
    (root/'temporary.partial').write_bytes(b'12')
    (root/'vanished').write_bytes(b'not counted')
    child=root/'child';child.mkdir()
    scandir=storage.os.scandir
    class Entry:
        def __init__(self,entry):self.entry=entry;self.path=entry.path
        def stat(self,**options):
            if self.entry.name=='vanished':Path(self.path).unlink()
            return self.entry.stat(**options)
    class Entries:
        def __init__(self,entries):self.entries=entries
        def __enter__(self):self.entries.__enter__();return self
        def __exit__(self,*args):return self.entries.__exit__(*args)
        def __iter__(self):return (Entry(entry) for entry in self.entries)
    def disappearing(directory):
        if Path(directory)==child:child.rmdir()
        return Entries(scandir(directory))
    monkeypatch.setattr(storage.os,'scandir',disappearing)
    assert storage.usage(root)==6


@pytest.mark.parametrize('failure',[PermissionError('denied'),NotADirectoryError('changed'),OSError(errno.EIO,'IO error')])
def test_usage_does_not_hide_non_enoent_scan_failures(tmp_path,monkeypatch,failure):
    root=tmp_path/'instance';root.mkdir()
    def broken(directory):raise failure
    monkeypatch.setattr(storage.os,'scandir',broken)
    with pytest.raises(type(failure)):storage.usage(root)


def test_usage_missing_root_is_not_zero(tmp_path):
    with pytest.raises(FileNotFoundError):storage.usage(tmp_path/'missing')


def test_exact_same_root_aliases_allow_zero_growth_over_quota_without_admitting_new_bytes(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    raw=b'previously retained bytes'
    identity=dict(sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
    original=shared.publish_bytes(root,raw)
    limits=dict(enabled=True,max_instance_bytes=1)
    first=root/'views/first';second=root/'views/second'
    with monkeypatch.context() as check:
        check.setattr(storage,'usage',lambda *a,**k:pytest.fail('A strict alias scanned usage'))
        shared.link(original,first,root=root,policy=limits,**identity)
        shared.link(original,first,root=root,policy=limits,**identity)
        shared.link(original,second,root=root,policy=limits,**identity)
        assert _inode(first)==_inode(second)==_inode(original)
        collision=root/'independent';collision.write_bytes(raw);collision.chmod(0o400)
        with pytest.raises(ValueError,match='shared inode'):
            shared.link(original,collision,root=root,policy=limits,**identity)
        outside=tmp_path/'outside';outside.write_bytes(raw);outside.chmod(0o400)
        with pytest.raises(ValueError,match='escaped'):
            shared.link(outside,root/'views/external',root=root,policy=limits,**identity)
    with pytest.raises(storage.StorageQuotaExceeded,match='max_instance_bytes'):
        shared.publish_bytes(root,b'x',policy=limits)
    # Zero growth is proven only by strict link, not inferred by mutation(0).
    with pytest.raises(storage.StorageQuotaExceeded,match='max_instance_bytes'):
        with storage.mutation(root,0,policy=limits):pytest.fail('Generic zero-growth mutation bypassed admission')


def test_accepted_script_and_snapshot_entry_share_one_first_freeze(tmp_path,monkeypatch,remote_registry):
    from scidiscovery.research_loop.protocol import CURRENT
    scope=dict(handoff_protocol=CURRENT,step=2,role='W',job_sha256='a'*64,
        execution_key='b'*64,mechanism_ref=None)
    config=e.freeze(dict(record_dir=str(tmp_path/'records'),scope=scope,
        admission=dict(version=1,mode='hard'),max_seconds=10),'remote_execution:fixture')
    source=tmp_path/'source.py';source.write_bytes(b'opaque script payload'*8192)
    identity=_identity(source)
    descriptor=LocalFileDescriptor(name='exact-call',local_path=str(source),media_type='text/plain',**identity)
    adapter=e.load(config);metadata=adapter.acceptance_metadata(descriptor)
    metadata=dict(metadata,snapshot_files=[dict(name='source.py',path=str(source),**identity)])
    copy=atomic_files.copy_original;copies=[]
    def counted(source,target,**options):
        copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
    records=e.Records(config)
    accepted=records.accept(descriptor,metadata,config['execution_interface'])
    directory=records.directory(descriptor.name)
    assert copies==[identity['size_bytes']]
    assert _inode(directory/'source.py')==_inode(directory/'inputs/source.py')
    assert accepted['source_script']==str(source)
    assert accepted['snapshot_files'][0]['path']==str(source)
    assert accepted['script']['path']!=accepted['snapshot_files'][0]['path']


@pytest.mark.parametrize('phase',['link','directory_fsync'])
def test_alias_source_change_removes_only_new_alias(tmp_path,monkeypatch,phase):
    root=tmp_path/'instance';root.mkdir()
    source=root/'managed';atomic_files.publish_bytes(source,b'opaque original')
    identity=_identity(source)
    sibling=root/'existing-sibling';shared.link(source,sibling,root=root,**identity)
    target=root/'inputs/new-alias'
    def change():
        source.chmod(0o600)
        source.write_bytes(b'changed source!')
        source.chmod(0o400)
    if phase=='link':
        link=shared._hardlink
        def mutated(original,destination):
            link(original,destination)
            change()
        monkeypatch.setattr(shared,'_hardlink',mutated)
    else:
        sync=atomic_files._sync_directory
        def mutated(directory):
            sync(directory)
            if target.exists():change()
        monkeypatch.setattr(atomic_files,'_sync_directory',mutated)
    with pytest.raises(ValueError,match='(changed|binding)'):
        shared.link(source,target,root=root,**identity)
    assert not target.exists()
    assert source.exists() and sibling.exists() and _inode(source)==_inode(sibling)


def test_writer_lock_is_reentrant_without_quota_usage_scan(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    def forbidden(*args,**kwargs):pytest.fail('Disabled quota scanned usage')
    monkeypatch.setattr(storage,'usage',forbidden)
    context=storage.Deadline(2)
    with storage.writer_lock(root,context=context):
        with storage.writer_lock(root,context=context), storage.mutation(root,0,policy=dict(enabled=False),context=context):
            original=shared.publish_bytes(root,b'one original',policy=dict(enabled=False),context=context)
    assert original.read_bytes()==b'one original'
    assert (root/'host-runtime/storage.lock').is_file()
    assert sorted(path.name for path in (root/'host-runtime').iterdir())==['storage.lock']


@pytest.mark.process_e2e
def test_closed_host_log_is_shared_through_observation_and_projection(tmp_path,monkeypatch,execution_registry):
    root,job,config,_=case(tmp_path,monkeypatch,executor='local_execution:command')
    raw=b'host-owned closed log\n'*1024
    payload=c.workspace(root,2)/'work/log.py'
    payload.write_text('import sys\nsys.stdout.write('+repr(raw.decode())+')\n')
    log_sha=hashlib.sha256(raw).hexdigest()
    copies=[];copy=atomic_files.copy_original
    def counted(source,target,**options):
        if options['sha256']==log_sha:copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
    result=e.dispatch(config,payload,new=True,mechanism='P')
    assert result['status']=='known_success'
    directory=e.Records(config).directory(result['call_id'])
    observed=c.read(directory/'execution-observation.json')
    retained=next(item for item in observed['proofs'] if item['source']['path']==str(directory/'stdout.raw'))
    cas=root/'host-history/objects'/log_sha
    assert _inode(directory/'stdout.raw')==_inode(Path(retained['path']))==_inode(cas)
    for _ in range(2):
        logs=e.scientific_outputs(config,observed,include_logs=True)
        stdout=next(item for item in logs if item.name.endswith('/stdout.txt'))
        assert _inode(Path(stdout.local_path))==_inode(cas)
    assert copies==[]
