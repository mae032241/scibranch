"""Many-file collection uses the real runner protocol, bounded archive IO and checkpoints."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

import pytest

from tcad_artifact import remote_runner_py36 as runner
from tcad_artifact.collection_download import unpack_results
from tcad_artifact.execution_control import FileDescriptor
from tcad_artifact.ssh_transport import SSHRemoteClient, SSHTCADTransport, SSHTCADTransportConfig
from scidiscovery.plugin_runtime.collection import CollectionContext


def _descriptor(path, name=None):
    raw = path.read_bytes()
    return FileDescriptor(name=name or path.name, local_path=str(path), media_type="application/octet-stream",
                          size_bytes=len(raw))


def _record(descriptor):
    return {"local_path": descriptor.local_path, "size_bytes": descriptor.size_bytes}


def _archive(tmp_path, *, invalid=None):
    source = tmp_path / 'source'
    source.write_bytes(b'valid')
    descriptor = _descriptor(source)
    records = [_record(descriptor)]
    archive_path = tmp_path / 'archive.tar'
    with tarfile.open(archive_path, 'w') as archive:
        def add(name, data, type=tarfile.REGTYPE):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.type = type
            info.linkname = '/outside'
            archive.addfile(info, io.BytesIO(data))
        add('manifest.json', json.dumps([] if invalid == 'manifest' else records).encode())
        if invalid != 'missing':
            add('../escape' if invalid == 'path' else 'files/0',
                b'wrong-size' if invalid == 'size' else b'valid',
                tarfile.SYMTYPE if invalid == 'symlink' else tarfile.REGTYPE)
        if invalid == 'extra':
            add('files/1', b'extra')
    return archive_path, [(descriptor, tmp_path / 'output')], records


@pytest.mark.parametrize('invalid', ['manifest', 'missing', 'path', 'symlink', 'size', 'extra'])
def test_archive_rejects_invalid_members_without_accepting_collection(tmp_path, invalid):
    archive, items, records = _archive(tmp_path, invalid=invalid)
    with pytest.raises(RuntimeError):
        unpack_results(archive, items, records, context=CollectionContext.for_seconds(5), on_complete=lambda *_: None)
    assert not list(tmp_path.glob('.*.download'))
    if invalid != 'extra':
        assert not items[0][1].exists()
    assert not (tmp_path.parent / 'escape').exists()


def test_archive_extraction_keeps_only_verified_files_after_failure(tmp_path):
    archive, items, records = _archive(tmp_path, invalid='extra')
    completed = []
    with pytest.raises(RuntimeError, match='undeclared'):
        unpack_results(archive, items, records, context=None, on_complete=lambda *x: completed.append(x))
    assert len(completed) == 1 and items[0][1].read_bytes() == b'valid'


def _client(tmp_path):
    # A local SSH stand-in execs the actual standalone runner. Count invocations
    # and resolver calls so the old N-connection implementation fails this test.
    count = tmp_path / 'ssh-count'
    ssh = tmp_path / 'ssh'
    ssh.write_text('#!' + sys.executable + '\nimport os,shlex,sys\n'
        + 'with open(' + repr(str(count)) + ',"a") as f: f.write("call\\n")\n'
        + 'os.execv(sys.executable,[sys.executable,*shlex.split(sys.argv[-1])])\n')
    ssh.chmod(0o700)
    values = json.loads((Path(__file__).parents[2] / 'plugins/tcad_artifact/config/remote-runner.example.json').read_bytes())
    state = tmp_path / 'remote'
    values.update(exchange_root=str(tmp_path/'exchange'), state_root=str(state), result_root=str(state/'runs'),
                  tools=[dict(profile_id='fixture', solver_kind='deterministic_tool', executable=sys.executable,
                              arguments=[], environment={}, release_evidence='fixture')])
    config = tmp_path / 'runner.json'
    config.write_text(json.dumps(values))
    resolver_count = tmp_path / 'resolver-count'
    resolver = tmp_path / 'resolver.py'
    resolver.write_text('from pathlib import Path\np=Path(' + repr(str(resolver_count)) + ')\n'
                        'with p.open("a") as f: f.write("call\\n")\nprint("127.0.0.1")\n')
    client = SSHRemoteClient(SSHTCADTransportConfig(ssh_executable=str(ssh), destination='fixture@local',
        destination_resolver=(sys.executable, str(resolver)), remote_helper=runner.__file__,
        remote_config=str(config), remote_exchange_root=str(tmp_path/'exchange'), max_transfer_bytes=2000000000))
    return client, runner._load_config(str(config)), count, resolver_count


@pytest.mark.process_e2e
def test_900_generated_files_use_one_archive_and_reuse_bound_copies(tmp_path):
    client, config, count, resolver_count = _client(tmp_path)
    run_id = 'run_' + 'a' * 32
    root = Path(config['result_root']) / run_id
    work = root / 'work'
    work.mkdir(parents=True)
    outputs = []
    for index in range(900):
        relative = f'profile_{index}.dat'
        path = work / relative
        path.write_bytes(b'' if index == 0 else (str(index).encode() + b'\n') * 128)
        descriptor = _descriptor(path, 'generated_' + str(index))
        outputs.append(dict(name=descriptor.name, relative_path=relative, media_type=descriptor.media_type,
                            size_bytes=descriptor.size_bytes))
    (root/'submitted_at').write_text('2026-09-27T00:00:00Z')
    (root/'done').touch()
    (root/'worker.log').write_bytes(b'done\n')
    (root/'output_manifest.json').write_text(json.dumps(dict(terminal_state='succeeded', exit_code=0, outputs=outputs)))
    transport = SSHTCADTransport(client, local_result_root=tmp_path/'local')
    progress = []
    context = CollectionContext.for_seconds(45, report_progress=progress.append)
    client.context = context
    started = time.monotonic()
    result = transport._collect(run_id, context=context)
    elapsed = time.monotonic() - started
    assert len(result['outputs']) == 902
    assert len(count.read_text().splitlines()) == 2  # manifest + one archive
    assert len(resolver_count.read_text().splitlines()) == 2
    assert {x.get('phase') for x in progress} >= {'inspect_manifest', 'download_archive', 'extract_archive'}
    assert progress[-1]['completed_files'] == 902
    assert not list((tmp_path/'local').rglob('*.download'))
    stale = tmp_path/'local/runs'/run_id/'.results.aborted.tar.download'
    stale.write_bytes(b'unaccepted partial archive')
    assert transport._collect(run_id, context=context) == result
    assert not stale.exists()
    assert len(count.read_text().splitlines()) == 3  # only manifest, no data
    Path(result['outputs'][1]['local_path']).write_bytes(b'wrong-size')
    assert transport._collect(run_id, context=context) == result
    assert len(count.read_text().splitlines()) == 5  # one-file repair archive
    evidence = Path(os.environ['SCID_TEST_LOG_DIR'])
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'archive-900.json').write_text(json.dumps(dict(files=902, elapsed_seconds=round(elapsed, 3),
        ssh_calls=2, resolver_calls=2, total_bytes=sum(x['size_bytes'] for x in result['outputs']))))


@pytest.mark.process_e2e
def test_remote_archive_failure_cleans_partial_package_and_can_retry(tmp_path):
    client, config, _, _ = _client(tmp_path)
    source = Path(config['result_root'])/'file'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'good')
    descriptor = _descriptor(source)
    destination = tmp_path / 'downloaded'
    source.unlink()
    with pytest.raises(subprocess.CalledProcessError):
        client.get_many_to([(descriptor, destination)], on_complete=lambda *_: None, on_progress=lambda _: None)
    assert not destination.exists() and not list(tmp_path.glob('*.download'))
    source.write_bytes(b'good')
    client.get_many_to([(descriptor, destination)], on_complete=lambda *_: None, on_progress=lambda _: None)
    assert destination.read_bytes() == b'good' and not list(tmp_path.glob('*.download'))


@pytest.mark.parametrize('damage', ['none', 'truncated', 'overflow'])
def test_archive_transport_fragmentation_and_cleanup(tmp_path, monkeypatch, damage):
    client, config, _, _ = _client(tmp_path)
    source = Path(config['result_root']) / 'source'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'payload')
    descriptor = _descriptor(source)
    wire = io.BytesIO()
    runner._stream_get_archive(config, {'files': [_record(descriptor)]}, wire)
    raw = wire.getvalue()
    if damage == 'truncated':
        header,body=raw.split(b'\n',1)
        raw=header+b'\n'+body[:1539]  # Three of seven bytes in the actual file member.
    elif damage == 'overflow':
        raw += b'x' * 20000
    monkeypatch.setattr(client, '_destination', lambda: 'fixture@local')
    context = CollectionContext.for_seconds(10, file_timeout_seconds=1)
    client.context = context
    def transfer(command, **kwargs):
        assert kwargs['timeout'] > context.file_timeout_seconds
        for offset in range(0, len(raw), 37):
            kwargs['sink'](raw[offset:offset+37])
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr('tcad_artifact.ssh_transport.run_bounded', transfer)
    target = tmp_path / 'result'
    if damage == 'none':
        client.get_many_to([(descriptor, target)], on_complete=lambda *_: None, on_progress=lambda _: None)
        assert target.read_bytes() == b'payload'
    else:
        with pytest.raises((RuntimeError,tarfile.ReadError)):
            client.get_many_to([(descriptor, target)], on_complete=lambda *_: None, on_progress=lambda _: None)
        assert not target.exists()
    assert not list(tmp_path.glob('*.download'))


def test_archive_request_paginates_metadata_without_per_file_calls(tmp_path, monkeypatch):
    client, _, _, _ = _client(tmp_path)
    source = tmp_path / 'source'
    source.write_bytes(b'x')
    item = (_descriptor(source), tmp_path / 'destination')
    monkeypatch.setattr('tcad_artifact.ssh_transport.HEADER_LIMIT', 1024)
    batches = []
    def batch(items, records, *_):
        request = {'schema_version': 2, 'operation': 'get_archive', 'payload': {'files': records}}
        from tcad_artifact.ssh_transport import _canonical
        assert len(_canonical(request)) + 1 <= 1024
        batches.append(len(items))
    monkeypatch.setattr(client, '_get_many_batch', batch)
    client.get_many_to([item] * 30, on_complete=lambda *_: None, on_progress=lambda _: None)
    assert sum(batches) == 30 and 1 < len(batches) < 30


@pytest.mark.process_e2e
def test_archive_streams_large_member_without_full_file_buffers(tmp_path):
    client, config, _, _ = _client(tmp_path)
    source = Path(config['result_root'])/'large'
    source.parent.mkdir(parents=True)
    block = b'0123456789abcdef' * 65536
    with source.open('wb') as stream:
        for _ in range(32):
            stream.write(block)
    descriptor = FileDescriptor(name='large', local_path=str(source), media_type='application/octet-stream',
                                size_bytes=32*1024*1024)
    target = tmp_path / 'result'
    progress = []
    client.context = CollectionContext.for_seconds(20)
    client.get_many_to([(descriptor, target)], on_complete=lambda *_: None, on_progress=progress.append)
    assert target.stat().st_size == descriptor.size_bytes
    with source.open('rb') as expected,target.open('rb') as observed:
        while True:
            expected_block=expected.read(1024*1024)
            assert observed.read(1024*1024)==expected_block
            if not expected_block:break
    assert len(progress) >= 2 and progress[-1] >= descriptor.size_bytes
    assert not list(tmp_path.glob('*.download'))
