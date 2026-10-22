"""Publication faults preserve exact originals and allow same-call collection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path
import stat
import threading
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry, remote_registry
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import atomic_files as files, contracts as c, experiments as e
from scidiscovery.research_loop.protocol import CURRENT


@pytest.mark.parametrize('replace', [False, True])
@pytest.mark.parametrize('phase', ['write', 'chmod', 'file_fsync', 'publish', 'directory_fsync'])
def test_publication_fault_exposes_only_complete_generations(tmp_path, monkeypatch, replace, phase):
    path = tmp_path / 'receipt.json'
    old, new = b'{"generation": 1}\n', b'{"generation": 2}\n'
    if replace:
        path.write_bytes(old)
    original_temporary = files._temporary
    original_fsync = files.os.fsync

    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'Injected publication failure')

    if phase == 'write':
        class BrokenOutput:
            def __init__(self, output):
                self.output = output

            def write(self, payload):
                self.output.write(payload[:3])
                fail()

        @contextmanager
        def broken_temporary(target):
            with original_temporary(target) as (temporary, output):
                yield temporary, BrokenOutput(output)

        monkeypatch.setattr(files, '_temporary', broken_temporary)
    elif phase == 'chmod':
        monkeypatch.setattr(files.os, 'fchmod', fail)
    elif phase in {'file_fsync', 'directory_fsync'}:
        def sync(descriptor):
            directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
            if directory == (phase == 'directory_fsync'):
                fail()
            original_fsync(descriptor)
        monkeypatch.setattr(files.os, 'fsync', sync)
    else:
        monkeypatch.setattr(files.os, 'replace' if replace else 'link', fail)
    with pytest.raises(OSError):
        (files.replace_bytes if replace else files.publish_bytes)(path, new)
    if phase == 'directory_fsync':
        assert path.read_bytes() == new
    elif replace:
        assert path.read_bytes() == old
    else:
        assert not path.exists()
    assert not list(tmp_path.glob('.*.partial'))


@pytest.mark.parametrize('same_bytes', [False, True])
def test_concurrent_original_publication_is_exclusive_and_explicitly_idempotent(tmp_path, monkeypatch, same_bytes):
    path = tmp_path / 'receipt.json'
    original_link = files.os.link
    barrier = threading.Barrier(2)
    temporaries = []

    def link(source, destination, **kwargs):
        temporaries.append(source)
        barrier.wait(timeout=5)
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(files.os, 'link', link)
    payloads = [b'{"writer": 1}\n', b'{"writer": 1}\n' if same_bytes else b'{"writer": 2}\n']
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(files.publish_bytes, path, payload, existing_ok=True) for payload in payloads]
        errors = [future.exception(timeout=10) for future in futures]
    assert len(set(temporaries)) == 2
    assert path.read_bytes() in payloads
    assert sum(error is None for error in errors) == (2 if same_bytes else 1)
    assert all(error is None or isinstance(error, ValueError) for error in errors)
    assert not list(tmp_path.glob('.*.partial'))


def test_original_permissions_and_fsync_precede_exclusive_publication(tmp_path, monkeypatch):
    path = tmp_path / 'receipt.json'
    events = []
    original_fchmod, original_fsync, original_link = files.os.fchmod, files.os.fsync, files.os.link

    def chmod(descriptor, mode):
        events.append('chmod')
        return original_fchmod(descriptor, mode)

    def sync(descriptor):
        events.append('directory_fsync' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file_fsync')
        return original_fsync(descriptor)

    def link(source, destination, **kwargs):
        assert stat.S_IMODE(Path(source).stat().st_mode) == 0o400
        assert Path(source).read_bytes() == b'complete'
        events.append('publish')
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(files.os, 'fchmod', chmod)
    monkeypatch.setattr(files.os, 'fsync', sync)
    monkeypatch.setattr(files.os, 'link', link)
    files.publish_bytes(path, b'complete')
    assert events == ['chmod', 'file_fsync', 'publish', 'directory_fsync']
    with pytest.raises(FileExistsError):
        files.publish_bytes(path, b'complete')


def test_concurrent_state_replacement_uses_separate_complete_temporaries(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    c.write(path, dict(generation=0))
    original_replace = files.os.replace
    barrier = threading.Barrier(2)
    temporary_payloads = {}

    def replace(source, destination):
        temporary_payloads[source] = Path(source).read_bytes()
        barrier.wait(timeout=5)
        return original_replace(source, destination)

    monkeypatch.setattr(files.os, 'replace', replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(c.write, path, dict(generation=index)) for index in (1, 2)]
        for future in futures:
            future.result(timeout=10)
    assert len(temporary_payloads) == 2
    assert len(set(temporary_payloads.values())) == 2
    assert c.read(path) in [dict(generation=1), dict(generation=2)]
    assert not list(tmp_path.glob('.*.partial'))


class InterruptedCopy:
    def __init__(self):
        self.checks = 0

    def remaining_seconds(self):
        self.checks += 1
        if self.checks == 4:
            raise TimeoutError('Original collection deadline reached')
        return 1


def test_bound_copy_interruption_leaves_no_final_target_and_retry_is_independent(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'retained/original'
    payload = b'a' * (2 * 1024 * 1024)
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    with pytest.raises(TimeoutError):
        c._copy_bound(source, target, digest, context=InterruptedCopy())
    assert not target.exists()
    assert not list(target.parent.glob('.*.partial'))
    c._copy_bound(source, target, digest)
    assert target.read_bytes() == payload
    assert target.stat().st_ino != source.stat().st_ino
    source.write_bytes(b'mutated writable scientific input')
    assert target.read_bytes() == payload


@pytest.mark.parametrize('fault', ['growth', 'source_read', 'disk_full'])
def test_copy_fault_never_publishes_partial_original(tmp_path, monkeypatch, fault):
    source, target = tmp_path / 'source', tmp_path / 'target'
    payload = b'a' * (2 * 1024 * 1024)
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    original_stream, original_temporary = files._regular_stream, files._temporary

    class BrokenStream:
        def __init__(self, stream):
            self.stream, self.blocks = stream, 0

        def fileno(self):
            return self.stream.fileno()

        def read(self, count):
            self.blocks += 1
            if self.blocks == 2:
                if fault == 'source_read':
                    raise OSError(errno.EIO, 'Injected source read failure')
                with source.open('ab') as growing:
                    growing.write(b'growth')
            return self.stream.read(count)

    @contextmanager
    def broken_stream(path):
        with original_stream(path) as stream:
            yield BrokenStream(stream)

    class FullOutput:
        def __init__(self, stream):
            self.stream = stream

        def write(self, block):
            self.stream.write(block[:3])
            raise OSError(errno.ENOSPC, 'Injected full disk')

    @contextmanager
    def full_temporary(path):
        with original_temporary(path) as (temporary, stream):
            yield temporary, FullOutput(stream)

    with monkeypatch.context() as patch:
        if fault == 'disk_full':
            patch.setattr(files, '_temporary', full_temporary)
        else:
            patch.setattr(files, '_regular_stream', broken_stream)
        with pytest.raises((OSError, ValueError)):
            files.copy_original(source, target, sha256=digest, size_bytes=len(payload))
    assert not target.exists()
    assert not list(tmp_path.glob('.*.partial'))
    source.write_bytes(payload)
    files.copy_original(source, target, sha256=digest, size_bytes=len(payload))
    assert target.read_bytes() == payload


def test_existing_damaged_or_linked_original_is_never_overwritten(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_bytes(b'exact')
    target.write_bytes(b'damaged')
    digest = hashlib.sha256(b'exact').hexdigest()
    with pytest.raises(ValueError):
        c._copy_bound(source, target, digest)
    assert target.read_bytes() == b'damaged'
    target.unlink()
    target.symlink_to(source)
    with pytest.raises(ValueError):
        c._copy_bound(source, target, digest)
    assert target.is_symlink() and source.read_bytes() == b'exact'


def test_tcad_original_copy_interruption_preserves_unowned_legacy_partial(tmp_path):
    from tcad_artifact import managed_execution as tcad
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_bytes(b't' * (2 * 1024 * 1024))
    descriptor = LocalFileDescriptor(name='target', local_path=str(source), sha256=c.digest(source),
        size_bytes=source.stat().st_size, media_type='application/octet-stream')
    legacy_partial = tmp_path / '.target.partial'
    legacy_partial.write_bytes(b'Unowned bytes from a prior writer')
    with pytest.raises(TimeoutError):
        tcad._copy_original(descriptor, target, InterruptedCopy())
    assert not target.exists()
    assert legacy_partial.read_bytes() == b'Unowned bytes from a prior writer'
    assert list(tmp_path.glob('.*.partial')) == [legacy_partial]
    tcad._copy_original(descriptor, target, SimpleNamespace(remaining_seconds=lambda: 1))
    assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o440


def test_original_submission_and_collection_recover_without_resubmission(tmp_path, monkeypatch, remote_registry):
    scope = dict(handoff_protocol=CURRENT, step=2, role='W', job_sha256='a' * 64,
        execution_key='b' * 64, mechanism_ref=None)
    config = e.freeze(dict(record_dir=str(tmp_path / 'records'), scope=scope,
        admission=dict(version=1, mode='hard'), max_seconds=10), 'remote_execution:fixture')
    source = tmp_path / 'package'
    source.write_bytes(b'p' * (2 * 1024 * 1024))
    descriptor = LocalFileDescriptor(name='original-call', local_path=str(source),
        sha256=c.digest(source), size_bytes=source.stat().st_size, media_type='application/octet-stream')
    records, adapter = e.Records(config), e.load(config)
    accepted = records.accept(descriptor, adapter.acceptance_metadata(descriptor), config['execution_interface'])
    adapter.dispatch_lease(accepted)
    submits = []
    original_submit = adapter.submit

    def submit(prepared):
        submits.append(prepared.model_dump(mode='json'))
        return original_submit(prepared)

    monkeypatch.setattr(adapter, 'submit', submit)
    external, state = adapter.submit(descriptor)
    original_link = files.os.link

    def interrupted_binding(temporary, path, **kwargs):
        if Path(path).name == 'submission.json':
            raise OSError(errno.ENOSPC, 'Injected original binding interruption')
        return original_link(temporary, path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(files.os, 'link', interrupted_binding)
        with pytest.raises(OSError):
            records.bind_external(descriptor.name, external, state)
    assert records.external(descriptor.name) is None
    assert adapter.lookup_submission(descriptor) == (external, state)
    records.bind_external(descriptor.name, external, state)
    original_copy = c._copy_bound

    def interrupted_retention(source, target, digest, *, context=None):
        if target.parent.name == 'observed-originals' and digest == descriptor.sha256:
            context = InterruptedCopy()
        return original_copy(source, target, digest, context=context)

    with monkeypatch.context() as patch:
        patch.setattr(c, '_copy_bound', interrupted_retention)
        with pytest.raises(TimeoutError):
            e.record_observation(config, adapter, descriptor.name)
    directory = records.directory(descriptor.name)
    assert not (directory / 'observed-originals' / descriptor.sha256).exists()
    assert not (directory / 'execution-observation.json').exists()
    observed = e.record_observation(config, adapter, descriptor.name)
    assert observed['status'] == 'known_success'
    assert observed['external_run_id'] == external
    assert Path(observed['outputs'][0]['path']).read_bytes() == source.read_bytes()
    assert len(submits) == 1 and submits[0] == accepted['prepared']
    assert len(records.calls()) == 1
    assert not list(directory.glob('.*.partial'))


def test_seal_namespace_contention_precedes_history_and_conclusion_materialization(tmp_path, monkeypatch):
    from scidiscovery.research_loop import execution, execution_authority as authority
    from test_execution_authority import prepare
    from test_history_handoff import finish
    from test_worker_delivery import stopped_native, tree

    root, _ = prepare(tmp_path)
    finish(root, 1, 'H', [], action='experiment')
    # Retain an actual completed W payload without invoking the seal under test.
    with monkeypatch.context() as patch:
        patch.setattr(c, 'seal_result', lambda *args: None)
        job, work, record = finish(root, 2, 'W', [1], files={'candidate.txt': 'Retained original candidate'})
    stopped_native(root, record)
    job_identity = c.digest(root / 'jobs/002.json')
    native_identity = execution.identity(root, 2)
    raw_identity = c.digest(record / 'science-output.json')
    assert job['execution_authority']
    with authority.writer(root):
        before = tree(root)
        with pytest.raises(BlockingIOError):
            c.seal_result(root, 2)
        assert tree(root) == before
    assert not (record / 'pending-execution-set.json').exists()
    assert not (work / 'conclusion.json').exists()
    assert not (record / 'result.json').exists()
    assert not (record / 'result-identity.json').exists()
    c.seal_result(root, 2)
    assert c.digest(root / 'jobs/002.json') == job_identity
    assert execution.identity(root, 2) == native_identity
    assert c.digest(record / 'science-output.json') == raw_identity
    assert len(execution.records(root, 2)) == 1
    assert c.read(record / 'result-identity.json')['job_sha256'] == job_identity
    assert (work / 'conclusion.json').read_bytes() == (record / 'science-output.json').read_bytes()
