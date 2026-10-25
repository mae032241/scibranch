"""Dispose only live native bundled-SSH transfer copies after acknowledged upload."""

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
from tcad_artifact.execution_control import TCADJobSpec
from tcad_artifact.project_packager import package_execution_package_json
from tcad_artifact.ssh_transport import SSHTCADTransport, SSHTCADTransportConfig
from tests.operations.test_runtime_plugin_configuration import _execution_package


class _Remote:
    def __init__(self):
        self.files = {}
        self.calls = []

    def put_file(self, path, descriptor):
        with Path(descriptor.local_path).open('rb') as source:
            raw = source.read()
        assert len(raw) == descriptor.size_bytes
        self.files[path] = raw

    def put(self, path, raw):
        self.files[path] = raw

    def rpc(self, request):
        self.calls.append(request)
        operation, payload = request['params']['name'], request['params']['arguments']
        if operation == 'tcad_submission_protocol':
            value = dict(version=2, submit='tcad_submit_retirable', retire='tcad_retire_submission')
        elif operation == 'tcad_lookup_submission':
            assert f'transport/{payload["submission_id"]}/job.json' in self.files
            value = dict(found=False)
        elif operation == 'tcad_retire_submission':
            assert f'transport/{payload["submission_id"]}/job.json' in self.files
            value = dict(found=False, retired=True, submission_id=payload['submission_id'])
        else:
            raise AssertionError(operation)
        return dict(jsonrpc='2.0', id=request['id'], result=dict(structuredContent=value))


def _descriptor(path):
    return LocalFileDescriptor(name='execution_payload', local_path=str(path),
        size_bytes=path.stat().st_size, media_type='application/json')


def _setup(tmp_path, monkeypatch, *, bundled=True, version=3):
    document = json.loads(_execution_package())
    document['schema_version'] = version
    raw = canonical_json(document)
    exchange = tmp_path / 'call'
    exchange.mkdir()
    source = exchange / 'reviewed-package.json'
    source.write_bytes(raw)
    configuration = SSHTCADTransportConfig(ssh_executable=sys.executable,
        destination='fixture@local', remote_helper='/remote/helper', remote_config='/remote/config',
        remote_exchange_root='/remote/exchange', max_transfer_bytes=1024 * 1024)
    config_path = tmp_path / 'ssh.json'
    config_path.write_text(configuration.model_dump_json())
    config_path.chmod(0o600)
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,
        arguments=('-m', 'tcad_artifact.ssh_transport', '--config', str(config_path)) if bundled else ()),
        local_result_root=tmp_path / 'results')
    monkeypatch.setattr(adapter, 'capabilities', lambda: (SimpleNamespace(
        content=canonical_json(document['capability'])),))
    remote = _Remote()
    transport = SSHTCADTransport(remote, local_result_root=adapter.local_result_root,
        remote_exchange_root=configuration.remote_exchange_root)
    seen = {}

    def call(operation, payload):
        if operation == 'prepare':
            seen.update(job=Path(payload['job_spec']['local_path']),
                archive=Path(payload['archive']['local_path']))
            seen['job_raw'] = seen['job'].read_bytes()
            seen['archive_raw'] = seen['archive'].read_bytes()
        return transport.handle(operation, payload)

    monkeypatch.setattr(adapter, '_call', call)
    options = dict(preparation_profile=f'tcad.execution-package.v{version}', exchange_directory=exchange)
    return adapter, _descriptor(source), options, remote, seen


def test_native_bundled_ssh_acknowledgement_disposes_only_transfer_copy(tmp_path, monkeypatch):
    adapter, original, options, remote, seen = _setup(tmp_path, monkeypatch)
    unrelated = options['exchange_directory'] / 'prepared' / 'pkg_unrelated'
    unrelated.mkdir(parents=True)
    other = unrelated / 'project.tar'
    other.write_bytes(b'other call')
    submission = adapter.prepare(original, **options)
    job = TCADJobSpec.model_validate_json(seen['job_raw'], strict=True)
    assert remote.files[f'transport/{job.request_id}/project.tar'] == seen['archive_raw']
    uploaded = TCADJobSpec.model_validate_json(remote.files[f'transport/{job.request_id}/job.json'], strict=True)
    assert uploaded == job.model_copy(update={'input_archive': job.input_archive.model_copy(
        update={'local_path': f'/remote/exchange/transport/{job.request_id}/project.tar'})})
    assert not seen['job'].parent.exists()
    assert other.read_bytes() == b'other call'
    assert Path(original.local_path).read_bytes() == canonical_json(json.loads(_execution_package()) | {'schema_version': 3})
    marker = json.loads(Path(submission.local_path).read_bytes())
    assert marker['submission_id'] == job.request_id
    assert adapter.lookup_submission(submission) is None
    assert adapter.retire_submission(submission) is None


@pytest.mark.parametrize('bundled,version', [(False, 3), (True, 2)])
def test_generic_commands_and_v2_retain_their_generated_inputs(tmp_path, monkeypatch, bundled, version):
    adapter, original, options, _, seen = _setup(tmp_path, monkeypatch, bundled=bundled, version=version)
    adapter.prepare(original, **options)
    assert seen['job'].read_bytes() == seen['job_raw']
    assert seen['archive'].read_bytes() == seen['archive_raw']


@pytest.mark.parametrize('failure', ['unknown', 'invalid_response'])
def test_unknown_or_invalid_prepare_retains_original_transfer_inputs(tmp_path, monkeypatch, failure):
    adapter, original, options, _, seen = _setup(tmp_path, monkeypatch)
    prepare = adapter._call

    def call(operation, payload):
        result = prepare(operation, payload)
        if failure == 'unknown':
            raise RuntimeError('response lost after upload')
        return {'submission': {'invalid': True}}

    monkeypatch.setattr(adapter, '_call', call)
    with pytest.raises((RuntimeError, ValueError)):
        adapter.prepare(original, **options)
    assert seen['job'].read_bytes() == seen['job_raw']
    assert seen['archive'].read_bytes() == seen['archive_raw']


@pytest.mark.parametrize('change', ['borrowed_marker', 'wrong_request', 'wrong_remote',
    'replaced_file', 'symlink_file', 'hardlink_file', 'extra_sibling'])
def test_changed_owner_or_unbound_marker_retains_inputs(tmp_path, monkeypatch, change):
    adapter, original, options, _, seen = _setup(tmp_path, monkeypatch)
    prepare = adapter._call

    def call(operation, payload):
        result = prepare(operation, payload)
        if change == 'borrowed_marker':
            return {'submission': _descriptor(seen['job']).model_dump(mode='python')}
        if change in {'wrong_request', 'wrong_remote'}:
            path = Path(result['submission']['local_path'])
            marker = json.loads(path.read_bytes())
            if change == 'wrong_request':
                marker['submission_id'] = 'b' * 32
            else:
                marker['remote_submission']['local_path'] = '/remote/exchange/other/job.json'
            path.chmod(0o600)
            path.write_bytes(canonical_json(marker))
            return {'submission': _descriptor(path).model_dump(mode='python')}
        archive = seen['archive']
        if change == 'replaced_file':
            replacement = tmp_path / 'replacement'
            replacement.write_bytes(seen['archive_raw'])
            os.replace(replacement, archive)
        elif change == 'symlink_file':
            target = tmp_path / 'borrowed'
            archive.rename(target)
            archive.symlink_to(target)
        elif change == 'hardlink_file':
            os.link(archive, tmp_path / 'borrowed')
        else:
            (archive.parent / 'unowned').write_bytes(b'keep')
        return result

    monkeypatch.setattr(adapter, '_call', call)
    submission = adapter.prepare(original, **options)
    assert Path(submission.local_path).is_file()
    assert seen['job'].read_bytes() == seen['job_raw']
    assert seen['archive'].read_bytes() == seen['archive_raw']
    if change == 'extra_sibling':
        assert (seen['archive'].parent / 'unowned').read_bytes() == b'keep'


def test_cleanup_failure_keeps_acknowledged_success_without_prepare_retry(tmp_path, monkeypatch):
    adapter, original, options, remote, seen = _setup(tmp_path, monkeypatch)
    unlink = Path.unlink

    def fail_archive(path, *args, **kwargs):
        if path.name == 'project.tar':
            raise PermissionError('local cleanup denied')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_archive)
    submission = adapter.prepare(original, **options)
    assert Path(submission.local_path).is_file()
    assert seen['archive'].read_bytes() == seen['archive_raw']
    assert len(remote.files) == 2
    assert adapter.lookup_submission(submission) is None


def test_oversized_marker_is_retained_without_reading_its_body(tmp_path, monkeypatch):
    adapter, original, options, _, seen = _setup(tmp_path, monkeypatch)
    prepare = adapter._call
    marker_paths = set()
    reads = []
    read_bytes = Path.read_bytes

    def call(operation, payload):
        result = prepare(operation, payload)
        marker = Path(result['submission']['local_path'])
        marker_paths.add(marker)
        marker.chmod(0o600)
        with marker.open('ab') as output:
            output.write(b' ' * 1024)
        return {'submission': _descriptor(marker).model_dump(mode='python')}

    def track_reads(path):
        if path in marker_paths:
            reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(adapter, '_call', call)
    monkeypatch.setattr(Path, 'read_bytes', track_reads)
    submission = adapter.prepare(original, **options)
    assert Path(submission.local_path).is_file() and reads == []
    assert seen['job'].read_bytes() == seen['job_raw']
    assert seen['archive'].read_bytes() == seen['archive_raw']


def test_development_debug_preserves_caller_owned_originals(tmp_path, monkeypatch):
    adapter, original, options, _, _ = _setup(tmp_path, monkeypatch)
    package = package_execution_package_json(_execution_package(),
        output_root=options['exchange_directory'] / 'caller')
    job_path = Path(package.job_spec_file.local_path)
    job_path.chmod(0o600)
    job_path.write_bytes(canonical_json(package.job_spec.model_copy(
        update={'execution_purpose': 'development_debug'}).model_dump(mode='json')))
    job = _descriptor(job_path)
    archive = LocalFileDescriptor.model_validate(package.archive.model_dump(mode='python'), strict=True)
    originals = job_path.read_bytes(), Path(archive.local_path).read_bytes()
    submission = adapter.prepare_development_debug(job_spec_file=job, archive=archive,
        exchange_directory=options['exchange_directory'])
    assert Path(submission.local_path).is_file()
    assert (job_path.read_bytes(), Path(archive.local_path).read_bytes()) == originals
    assert Path(original.local_path).is_file()


def test_cleanup_diagnostic_cannot_follow_borrowed_logs_or_lose_success(tmp_path, monkeypatch):
    adapter, original, options, _, seen = _setup(tmp_path, monkeypatch)
    borrowed = tmp_path / 'borrowed-logs'
    borrowed.mkdir()
    sentinel = borrowed / 'original'
    sentinel.write_bytes(b'preserve borrowed logs')
    before = borrowed.stat()
    (adapter.local_result_root / 'logs').symlink_to(borrowed, target_is_directory=True)
    unlink = Path.unlink

    def fail_archive(path, *args, **kwargs):
        if path.name == 'project.tar':
            raise PermissionError('local cleanup denied')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_archive)
    submission = adapter.prepare(original, **options)
    assert Path(submission.local_path).is_file()
    assert seen['archive'].read_bytes() == seen['archive_raw']
    assert adapter.lookup_submission(submission) is None
    assert sentinel.read_bytes() == b'preserve borrowed logs'
    after = borrowed.stat()
    assert (after.st_size, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_size, before.st_mtime_ns, before.st_ctime_ns)


@pytest.mark.process_e2e
def test_real_command_local_ssh_upload_then_lookup_survives_local_disposal(tmp_path, monkeypatch):
    from tests.operations.test_tcad_archive_collection import _client

    adapter, original, options, _, _ = _setup(tmp_path, monkeypatch)
    client, _, ssh_count, _ = _client(tmp_path)
    config_path = tmp_path / 'actual-ssh.json'
    config_path.write_text(client.config.model_dump_json())
    config_path.chmod(0o600)
    adapter.config = CommandAdapterConfig(executable=sys.executable,
        arguments=('-m', 'tcad_artifact.ssh_transport', '--config', str(config_path)),
        environment={'PYTHONPATH': os.pathsep.join(sys.path),
            'SCIDISCOVERY_TCAD_RESULT_ROOT': str(adapter.local_result_root)})
    monkeypatch.setattr(adapter, '_call', CommandTCADExecutorAdapter._call.__get__(adapter))
    packaged = []
    from tcad_artifact import command_adapter
    package = command_adapter.package_execution_package_json

    def capture(*args, **kwargs):
        result = package(*args, **kwargs)
        packaged.append(result)
        return result

    monkeypatch.setattr(command_adapter, 'package_execution_package_json', capture)
    submission = adapter.prepare(original, **options)
    assert len(packaged) == 1 and not packaged[0].package_directory.exists()
    request_id = packaged[0].job_spec.request_id
    remote = Path(client.config.remote_exchange_root) / 'transport' / request_id
    assert (remote / 'project.tar').is_file() and (remote / 'job.json').is_file()
    assert len(ssh_count.read_text().splitlines()) == 2
    assert adapter.lookup_submission(submission) is None
    assert len(ssh_count.read_text().splitlines()) == 3
