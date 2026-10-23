"""Full TCAD protocol records exceed previews while explicit policy still applies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.plugin_runtime.collection import run_bounded
from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
from tcad_artifact.project_packager import ExecutionPackage, RuntimeAttestation, TCADRuntimeManifest
from tcad_artifact.ssh_transport import SSHRemoteClient, SSHTCADTransportConfig
from tcad_artifact.transport_logs import preserve_log
from tests.operations.test_runtime_plugin_configuration import _execution_package
from tests.operations.test_tcad_archive_collection import _client


LARGE = 8 * 1024 * 1024 + 1  # Just beyond the historical transport/log limit.


@pytest.mark.stress
def test_full_transport_stdout_and_stderr_exceed_eight_mib():
    result = run_bounded([sys.executable, '-c',
        'import sys; sys.stdin.buffer.read(); '
        f'sys.stdout.buffer.write(b"o"*{LARGE}+b"END"); sys.stdout.buffer.flush(); '
        f'sys.stderr.buffer.write(b"e"*{LARGE}+b"END")'], input=b'', timeout=10)
    assert result.returncode == 0
    for raw,byte in ((result.stdout,b'o'),(result.stderr,b'e')):
        assert len(raw) == LARGE + 3 and raw.endswith(b'END')
        assert raw.count(byte) == LARGE


@pytest.mark.process_e2e
def test_explicit_transport_quota_and_timeout_are_preserved():
    with pytest.raises(ValueError, match='transport response exceeds'):
        run_bounded([sys.executable, '-c', 'print("x"*10000)'], input=b'', timeout=5, max_output_bytes=100)
    # Explicit diagnostic capture quotas retain their existing excerpt semantics.
    result = run_bounded([sys.executable, '-c', 'import sys; sys.stderr.write("e"*10000)'],
        input=b'', timeout=5, max_output_bytes=100)
    assert result.stderr == b'e' * 100 and result.returncode == 0
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_bounded([sys.executable, '-c', 'import os,time; os.write(2,b"pending"); time.sleep(30)'],
            input=b'', timeout=.2)
    assert caught.value.stderr == b'pending' and caught.value.timeout_kind == 'operation_total'


@pytest.mark.stress
def test_command_standin_collect_retains_large_descriptor_response_and_logs(tmp_path):
    root = tmp_path / 'results'
    original = root
    for number in range(7):
        original /= str(number) + 'n' * 135
    original.mkdir(parents=True)
    original /= 'data'
    original.write_bytes(b'original')
    record = dict(schema_version=1, name='original', local_path=str(original),
        media_type='application/octet-stream', size_bytes=8)
    count = (8 * 1024 * 1024) // len(canonical_json(dict(record,name='output-0000'))) + 1
    metadata = tmp_path / 'descriptor.json'; metadata.write_text(json.dumps(record))
    script = tmp_path / 'transport.py'
    script.write_text('import json,sys\n'
        'request=json.load(sys.stdin)\n'
        'record=json.load(open(sys.argv[1]))\n'
        f'outputs=[dict(record,name="output-%04d"%i) for i in range({count})]\n'
        'json.dump(dict(schema_version=2,operation=request["operation"],ok=True,payload=dict(outputs=outputs)),sys.stdout)\n'
        'sys.stdout.flush()\n'
        f'sys.stderr.write("e"*{LARGE}+"END")\n')
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,
        arguments=(str(script), str(metadata))), local_result_root=root)
    outputs = adapter.collect('run_' + 'a' * 32)
    assert len(outputs) == count and outputs[-1].name == f'output-{count-1:04d}'
    assert sum(len(canonical_json(item.model_dump(mode='json'))) for item in outputs) > 8 * 1024 * 1024
    log, = (root / 'logs').glob('*.stderr.log')
    assert log.stat().st_size == LARGE + 3
    with log.open('rb') as stream:
        remaining=LARGE
        while remaining:
            size=min(64*1024,remaining)
            assert stream.read(size)==b'e'*size
            remaining-=size
        assert stream.read()==b'END'
    assert log.name.endswith('.stderr.log') and len(log.name.split('.')[0])==32


def test_small_descriptors_and_logs_keep_identity_and_path_guards(tmp_path):
    root=tmp_path/'results';root.mkdir()
    original=root/'data';original.write_bytes(b'original')
    record=dict(schema_version=1,name='original',local_path=str(original),media_type='application/octet-stream',
        size_bytes=8)
    adapter=CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable),local_result_root=root)
    original.write_bytes(b'tampered size')
    with pytest.raises(RuntimeError, match='differs from its descriptor'):
        adapter._collected_outputs(dict(outputs=[record]))
    outside = tmp_path / 'outside'; outside.write_bytes(b'original')
    with pytest.raises(RuntimeError, match='outside its result root'):
        adapter._collected_outputs(dict(outputs=[dict(record, local_path=str(outside))]))
    raw=b'diagnostic END';log=preserve_log(root,'stderr',raw)
    again=preserve_log(root,'stderr',raw)
    assert again!=log and log.read_bytes()==again.read_bytes()==raw


@pytest.mark.stress
def test_ssh_standin_keeps_large_rpc_response_header(tmp_path):
    ssh = tmp_path / 'ssh'
    ssh.write_text('#!' + sys.executable + '\nimport json,sys\n'
        'request=json.loads(sys.stdin.buffer.readline())\n'
        'response=dict(source_bytes=len(request["payload"]["request"]["original"]),original="x"*%d)\n' % LARGE +
        'json.dump(dict(schema_version=2,operation="rpc",ok=True,payload=dict(response=response)),sys.stdout)\n'
        'sys.stdout.write("\\n")\n')
    ssh.chmod(0o700)
    client = SSHRemoteClient(SSHTCADTransportConfig(ssh_executable=str(ssh), destination='fixture@localhost',
        remote_helper='/local-standin', remote_config='/local-standin', remote_exchange_root='/local-standin',
        max_transfer_bytes=1))
    response = client.rpc(dict(original='small request'))
    assert response['source_bytes'] == len('small request')
    assert len(response['original']) == LARGE and response['original'].count('x') == LARGE


@pytest.mark.stress
def test_runner_via_local_ssh_standin_accepts_full_request_and_keeps_explicit_transfer_quota(tmp_path):
    client, config, _, _ = _client(tmp_path)
    original = Path(config['result_root'])
    for number in range(7): original /= str(number) + 'r' * 135
    original.mkdir(parents=True)
    original /= 'zero'; original.write_bytes(b'')
    record = dict(local_path=str(original), size_bytes=0)
    count = (1024 * 1024) // len(canonical_json(record)) + 1
    records = [record] * count
    assert len(canonical_json(dict(files=records))) > 1024 * 1024
    destination = tmp_path / 'complete.tar'
    bound = len(canonical_json(records)) + 1024 * (len(records) + 1) + 10240
    response, _ = client._call('get_archive', dict(files=records), b'', download_to=destination, download_limit=bound)
    assert response['payload']['files'] == count
    with tarfile.open(destination) as archive:
        manifest = archive.next()
        assert json.load(archive.extractfile(manifest)) == records
        assert sum(1 for item in archive if item.name.startswith('files/')) == count
    with pytest.raises(subprocess.CalledProcessError):
        client._call('get_archive', dict(files=[dict(record, size_bytes=client.config.max_transfer_bytes + 1)]),
            b'', download_to=tmp_path / 'rejected.tar', download_limit=bound)


@pytest.mark.stress
def test_transport_cli_via_local_ssh_standin_accepts_full_request_and_returns_original_status(tmp_path):
    client, config, _, _ = _client(tmp_path)
    run_id = 'run_' + 'b' * 32
    directory = Path(config['result_root']) / run_id; directory.mkdir(parents=True)
    (directory / 'submitted_at').write_text('2026-10-16T00:00:00Z')
    (directory / 'done').touch()
    (directory / 'output_manifest.json').write_text(json.dumps(dict(terminal_state='succeeded',exit_code=0,outputs=[])))
    path = tmp_path / 'transport.json'; path.write_text(client.config.model_dump_json()); path.chmod(0o600)
    adapter = CommandTCADExecutorAdapter(CommandAdapterConfig(executable=sys.executable,
        arguments=('-m', 'tcad_artifact.ssh_transport', '--config', str(path)),
        environment={'PYTHONPATH': os.pathsep.join(sys.path),
            'SCIDISCOVERY_TCAD_RESULT_ROOT':str(tmp_path / 'local')}), local_result_root=tmp_path / 'local')
    value = adapter._call('status', dict(run_id=run_id, original_metadata='r' * (1024 * 1024 + 1)))
    assert value['state'] == 'succeeded'
    from tcad_artifact import remote_runner_py36 as runner
    config['runner']['max_manifest_bytes'] = 1
    with pytest.raises(ValueError, match='configured byte bound'):
        runner._status(config, run_id)


def manifest_document(count):
    outputs=[]
    for number in range(count):
        path=f'frames/{number}.dat'
        outputs.append(dict(name='generated_'+str(number),relative_path=path,
            media_type='application/octet-stream',size_bytes=1))
    return dict(started_at=None,completed_at='2026-10-16T00:00:00Z',terminal_state='succeeded',exit_code=0,
        error='',outputs=outputs)




@pytest.mark.parametrize('fault', ['duplicate_name', 'duplicate_path', 'escape'])
def test_manifest_keeps_uniqueness_and_path_guards(fault):
    document = manifest_document(2)
    if fault == 'duplicate_name': document['outputs'][-1]['name'] = document['outputs'][0]['name']
    elif fault == 'duplicate_path': document['outputs'][-1]['relative_path'] = document['outputs'][0]['relative_path']
    else: document['outputs'][-1]['relative_path'] = '../escape.dat'
    with pytest.raises(ValueError): TCADRuntimeManifest.model_validate_json(canonical_json(document), strict=True)
