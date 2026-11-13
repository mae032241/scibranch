"""Managed originals freeze once and share read-only aliases with exact identities."""
import errno
import os
from pathlib import Path
import threading

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_registered_experiments import managed
from scidiscovery.research_loop import contracts as c, experiments as e, controller
from scidiscovery.research_loop import atomic_files, shared_originals as shared, storage_policy as storage
from scidiscovery.research_loop.local_experiments import LocalExecution
from scidiscovery import archive_io
from scidiscovery.operations.execution import LocalFileDescriptor


def _identity(path):
    return dict(size_bytes=path.stat().st_size)


def _inode(path):
    info=path.stat()
    return info.st_dev,info.st_ino


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
        c._bind_original(original,target,root=root,policy=storage.configuration(),
            size_bytes=identity['size_bytes'],source_kind='managed_frozen')
    assert not target.exists() and original.read_bytes()==source.read_bytes()


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
    'max_scan_entries','max_files'])
def test_explicit_scan_limits_reject_before_retaining_files(tmp_path,dimension):
    root=tmp_path/'inputs';root.mkdir()
    (root/'a').write_bytes(b'aa');(root/'b').write_bytes(b'bb')
    (root/'empty').mkdir()
    with pytest.raises(storage.StorageQuotaExceeded,match=dimension):
        LocalExecution.files(root,policy=dict(enabled=True,**{dimension:1}))
    assert not list(root.glob('.scid-*'))


def test_legacy_instance_limit_allows_concurrent_imports_without_usage_admission(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    sources=[]
    for index in range(2):
        source=tmp_path/f'source-{index}';source.write_bytes(bytes([index])*65536);sources.append(source)
    limits=dict(enabled=True,max_file_bytes=1,max_total_bytes=1,max_instance_bytes=1)
    finish_copy=atomic_files._finish
    observed=[]
    def observe_partial(output,mode):
        output.flush()
        observed.append(output.tell())
        finish_copy(output,mode)
    monkeypatch.setattr(atomic_files,'_finish',observe_partial)
    monkeypatch.setattr(storage,'usage',lambda *a,**k:pytest.fail('Byte quota scanned instance usage'))
    start=threading.Barrier(2);results=[]
    def retain(source):
        start.wait()
        results.append(shared.retain(root,source,source_kind='mutable',policy=limits,**_identity(source)))
    threads=[threading.Thread(target=retain,args=(source,)) for source in sources]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results)==2 and observed==[65536,65536]
    assert {path.read_bytes() for path in results}=={source.read_bytes() for source in sources}
    assert len(list((root/'host-history/objects').iterdir()))==2


def test_fork_retains_new_original_despite_legacy_frozen_instance_quota(tmp_path,monkeypatch):
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
    mutable=tmp_path/'new-material';mutable.write_bytes(b'a new independent input')
    original=shared.retain(child,mutable,source_kind='mutable',
        policy=dict(limits,max_instance_bytes=1),**_identity(mutable))
    assert original.read_bytes()==mutable.read_bytes() and _inode(original)!=_inode(mutable)


def test_interrupted_import_leaves_no_final_or_reused_partial(tmp_path,monkeypatch):
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
    replacement=shared.retain(root,source,source_kind='mutable',**identity)
    assert replacement!=original and _inode(replacement)!=_inode(original)
    assert replacement.read_bytes()==source.read_bytes()
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


def test_stream_preserves_original_bytes_despite_legacy_byte_limits(tmp_path):
    root=tmp_path/'instance';root.mkdir()
    raw=b'new bytes'
    original=shared.publish_stream(root,lambda:iter((raw[:2],raw[2:])),size_bytes=len(raw),
        policy=dict(enabled=True,max_file_bytes=1,max_total_bytes=1,max_instance_bytes=1))
    assert original.read_bytes()==raw and original.stat().st_size==len(raw)
    with pytest.raises(ValueError):
        shared.publish_stream(root,lambda:iter((raw,)),size_bytes=len(raw)-1)


def test_legacy_instance_bytes_do_not_measure_undeclared_stream_twice(tmp_path):
    root=tmp_path/'instance';root.mkdir()
    calls=[]
    def blocks():
        calls.append(True)
        assert len(calls)==1, 'Generated originals require one publication pass'
        yield b'first'
        yield b'second'
    original=shared.publish_stream(root,blocks,
        policy=dict(enabled=True,max_file_bytes=1,max_total_bytes=1,max_instance_bytes=1))
    assert calls==[True] and original.read_bytes()==b'firstsecond'
    assert original.stat().st_size==11


def test_stream_uses_original_deadline_during_publication(tmp_path):
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
        shared.publish_stream(root,blocks,size_bytes=2,context=context)
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


def test_exact_same_root_aliases_preserve_identity_without_legacy_byte_quota(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    raw=b'previously retained bytes'
    identity=dict(size_bytes=len(raw))
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
    assert shared.publish_bytes(root,b'x',policy=limits).read_bytes()==b'x'
    with storage.mutation(root,0,policy=limits):
        pass


@pytest.mark.parametrize('change_source',[False,True])
def test_accepted_script_and_snapshot_entry_share_one_first_freeze(tmp_path,monkeypatch,remote_registry,change_source):
    from scidiscovery.research_loop.protocol import CURRENT
    scope=dict(handoff_protocol=CURRENT,step=2,role='W',job_ref=dict(path='/fixture/jobs/002.json'),
        execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'),mechanism_ref=None)
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
        result=copy(source,target,**options)
        if change_source:Path(source).write_bytes(b'x'*identity['size_bytes'])
        return result
    monkeypatch.setattr(atomic_files,'copy_original',counted)
    records=e.Records(config)
    if change_source:
        with pytest.raises(ValueError):
            records.accept(descriptor,metadata,config['execution_interface'])
        assert not (records.root/'calls'/descriptor.name).exists()
        assert copies==[identity['size_bytes']]
        return
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
def test_closed_host_log_is_shared_through_observation_and_projection(managed):
    # Standalone Local transport component; no native Worker/profile is launched.
    raw=b'host-owned closed log\n'*1024
    payload=managed.source('log')
    payload.write_text('import sys\nsys.stdout.write('+repr(raw.decode())+')\n')
    config=c.read(managed.config)
    result=managed.cli('run','--script',payload,'--new-plan')
    assert result['status']=='known_success'
    directory=e.Records(config).directory(result['call_id'])
    observed=c.read(directory/'execution-observation.json')
    retained=next(item for item in observed['proofs'] if item['source']['path']==str(directory/'stdout.raw'))
    original=Path(retained['path'])
    assert original.read_bytes()==raw
    assert _inode(directory/'stdout.raw')==_inode(original)
    projected=None
    for _ in range(2):
        logs=e.scientific_outputs(config,observed,include_logs=True)
        stdout=next(item for item in logs if item.name.endswith('/stdout.txt'))
        path=Path(stdout.local_path)
        assert path.read_bytes()==raw and stdout.size_bytes==len(raw)
        current=(path,_inode(path))
        if projected is None:projected=current
        else:assert current==projected
