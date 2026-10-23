"""Storage provenance and publication use no scientific content hashes."""
import hashlib
from pathlib import Path
import sys

import pytest

from execution_fixtures import execution_registry

from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import atomic_files, contracts as c, experiments as e
from scidiscovery.research_loop import scientific_log_projection as logs, shared_originals as shared
from scidiscovery.research_loop import storage_policy as storage


def _forbid_hash(*args, **kwargs):
    pytest.fail('Scientific storage attempted to compute a content hash')


def test_file_proofs_only_stat_exact_originals(tmp_path, monkeypatch):
    source=tmp_path/'dataset.bin';source.write_bytes(b'large scientific original')
    monkeypatch.setattr(hashlib,'sha256',_forbid_hash)
    monkeypatch.setattr(Path,'read_bytes',lambda self:pytest.fail('Proof read scientific bytes'))
    assert e.proof(source)==dict(path=str(source),size_bytes=25)
    assert c.record_ref(source)==dict(path=str(source))
    assert not hasattr(c,'digest') and not hasattr(c,'verified_digests')
    assert not hasattr(shared,'verified')
    link=tmp_path/'alias';link.symlink_to(source)
    with pytest.raises(ValueError):e.proof(link)


def test_first_freeze_is_independent_and_managed_alias_retry_shares_inode(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    source=tmp_path/'mutable';source.write_bytes(b'payload')
    monkeypatch.setattr(hashlib,'sha256',_forbid_hash)
    original=shared.retain(root,source,size_bytes=7,source_kind='mutable')
    assert not original.samefile(source)
    assert len(original.name)==32
    first=root/'inputs/first';second=root/'inputs/second'
    shared.link(original,first,size_bytes=7,root=root)
    shared.link(original,first,size_bytes=7,root=root)
    retained=shared.retain(root,first,size_bytes=7,source_kind='managed_frozen')
    shared.link(retained,second,size_bytes=7,root=root)
    source.write_bytes(b'changed')
    assert first.samefile(second) and second.samefile(original)
    assert second.read_bytes()==b'payload'
    collision=root/'inputs/collision';collision.write_bytes(b'payload');collision.chmod(0o400)
    with pytest.raises(ValueError,match='exact shared inode'):
        shared.link(original,collision,size_bytes=7,root=root)


def test_atomic_copy_rejects_existing_destination_and_wrong_size(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'abc')
    target=tmp_path/'target';target.write_bytes(b'xyz')
    monkeypatch.setattr(hashlib,'sha256',_forbid_hash)
    with pytest.raises(FileExistsError):atomic_files.copy_original(source,target,size_bytes=3)
    assert target.read_bytes()==b'xyz'
    with pytest.raises(ValueError,match='before retention'):
        atomic_files.copy_original(source,tmp_path/'fresh',size_bytes=2)
    assert not (tmp_path/'fresh').exists()


def test_projected_log_publishes_in_one_bounded_pass(tmp_path,monkeypatch):
    root=tmp_path/'instance';root.mkdir()
    source=root/'stdout.raw';source.write_bytes(b'/private/source xy /private/source')
    monkeypatch.setattr(hashlib,'sha256',_forbid_hash)
    calls=[]
    def blocks():
        calls.append(1)
        return logs.project(source,size_bytes=source.stat().st_size,
            replacements=[(b'/private/source',b'public')],context=storage.Deadline(10),block_size=3)
    original=shared.publish_stream(root,blocks)
    assert calls==[1]
    assert original.read_bytes()==b'public xy public'


@pytest.mark.process_e2e
def test_real_local_call_prepare_accept_collect_and_retry_without_hash(tmp_path,monkeypatch,execution_registry):
    from scidiscovery.research_loop.protocol import CURRENT
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    source=tmp_path/'science.py';source.write_text('print("retained observation")\n')
    job=tmp_path/'job.json';job.write_text('{}\n')
    scope=dict(handoff_protocol=CURRENT,step=1,role='W',job_ref=c.record_ref(job),
        execution_key=c.record_ref(job),mechanism_ref=None)
    config=e.freeze(dict(record_dir=str(tmp_path/'records'),scope=scope,
        admission=dict(version=1,mode='hard'),run_argv=[sys.executable],max_seconds=20),
        'local_execution:command',path=tmp_path/'config.json')
    monkeypatch.setattr(hashlib,'sha256',_forbid_hash)
    # A trusted host seam launches the real synthetic child. Namespace protection
    # has separate process coverage; this test exercises storage and call reuse.
    launch=bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None))
    try:
        result=e.dispatch(config,source,new=True)
    finally:
        bridge._LOCAL_LAUNCH.reset(launch)
    assert result['status']=='known_success'
    records=e.Records(config);accepted=records.accepted(result['call_id'])
    assert 'sha256' not in accepted['prepared']
    observed=e.record_observation(config,e.load(config),result['call_id'])
    again=e.record_observation(config,e.load(config),result['call_id'])
    assert observed==again
    for descriptor in e.load(config).collect_original(result['call_id']):
        assert isinstance(descriptor,LocalFileDescriptor)
        assert not hasattr(descriptor,'sha256')
        assert e.proof(descriptor.local_path)['size_bytes']==descriptor.size_bytes
