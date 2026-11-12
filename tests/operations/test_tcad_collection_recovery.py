"""Same-run collection recovery through real local SSH/runner stand-ins."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tcad_artifact.ssh_transport import SSHTCADTransport
from tests.operations.test_tcad_archive_collection import _client


def _terminal_outputs(tmp_path):
    client, config, count, _ = _client(tmp_path)
    private = tmp_path / 'private'
    client.diagnostic_root = private
    run_id = 'run_' + 'c' * 32
    root = Path(config['result_root']) / run_id
    work = root / 'work'
    work.mkdir(parents=True)
    outputs = []
    for name in ('first', 'second'):
        (work / name).write_bytes((name + '\n').encode())
        outputs.append(dict(name=name, relative_path=name, media_type='application/octet-stream',
                            size_bytes=(work / name).stat().st_size))
    (root / 'submitted_at').write_text('2026-10-28T00:00:00Z')
    (root / 'done').touch()
    (root / 'worker.log').write_bytes(b'original execution log\n')
    manifest = dict(started_at='2026-10-28T00:00:00Z', completed_at='2026-10-28T00:00:01Z',
                    terminal_state='succeeded', exit_code=0, error='', outputs=outputs)
    (root / 'output_manifest.json').write_text(json.dumps(manifest))
    return client, private, run_id, count


@pytest.mark.process_e2e
@pytest.mark.parametrize('interruption', ['staged', 'published', 'returned'])
def test_same_run_scientific_collection_resumes_before_receipt(tmp_path, interruption):
    client, private, run_id, count = _terminal_outputs(tmp_path)
    node = tmp_path / 'node'
    program = r'''
import json, os, sys
from pathlib import Path
sys.path[:0] = json.loads(sys.argv[1])
from tcad_artifact.ssh_transport import SSHRemoteClient, SSHTCADTransport, SSHTCADTransportConfig
config = SSHTCADTransportConfig.model_validate_json(sys.argv[2], strict=True)
private, node = Path(sys.argv[3]), Path(sys.argv[4])
run_id, interruption = sys.argv[5:7]
client = SSHRemoteClient(config, diagnostic_root=private)
replace = os.replace
def interrupt(source, destination):
    replace(source, destination)
    destination = Path(destination)
    if destination.name == 'first' and (
        (interruption == 'staged' and destination.parent.name == '.tcad-collection-' + run_id)
        or (interruption == 'published' and destination.parent == node)):
        os._exit(47)
os.replace = interrupt
SSHTCADTransport(client, local_result_root=private)._collect(run_id, output_directory=node)
assert interruption == 'returned'
os._exit(47)
'''
    child = subprocess.run([sys.executable, '-c', program, json.dumps(sys.path),
        client.config.model_dump_json(), str(private), str(node), run_id, interruption],
        capture_output=True, timeout=10)
    assert child.returncode == 47, child.stderr
    first = node / 'first'
    staged = node / ('.tcad-collection-' + run_id) / 'first'
    prior = (first if first.exists() else staged).stat()
    # The new transport process has no memory of the interrupted collector.
    result = SSHTCADTransport(client, local_result_root=private)._collect(run_id, output_directory=node)
    assert len(result['outputs']) == 4
    assert first.read_bytes() == b'first\n'
    assert (first.stat().st_dev, first.stat().st_ino) == (prior.st_dev, prior.st_ino)
    assert (node / 'second').read_bytes() == b'second\n'
    assert SSHTCADTransport(client, local_result_root=private)._collect(run_id, output_directory=node) == result
    # Only inspect/collect/archive RPCs are used; fixture has no job to submit.
    assert len(count.read_text().splitlines()) <= 8


@pytest.mark.process_e2e
def test_scientific_collection_rejects_foreign_same_size_output(tmp_path):
    client, private, run_id, _ = _terminal_outputs(tmp_path)
    node = tmp_path / 'node'
    node.mkdir()
    foreign = node / 'first'
    foreign.write_bytes(b'other\n')
    transport = SSHTCADTransport(client, local_result_root=private)
    with pytest.raises(RuntimeError, match='matching collection progress'):
        transport._collect(run_id, output_directory=node)
    assert foreign.read_bytes() == b'other\n'


@pytest.mark.process_e2e
def test_saved_scientific_progress_rejects_same_size_replacement(tmp_path):
    client, private, run_id, _ = _terminal_outputs(tmp_path)
    node = tmp_path / 'node'
    transport = SSHTCADTransport(client, local_result_root=private)
    transport._collect(run_id, output_directory=node)
    replacement = node / 'replacement'
    replacement.write_bytes(b'other\n')
    os.replace(replacement, node / 'first')
    with pytest.raises(RuntimeError, match='matching collection progress'):
        transport._collect(run_id, output_directory=node)
    assert (node / 'first').read_bytes() == b'other\n'


@pytest.mark.process_e2e
def test_scientific_collection_binding_rejects_changed_native_manifest(tmp_path):
    client, private, run_id, _ = _terminal_outputs(tmp_path)
    node = tmp_path / 'node'
    transport = SSHTCADTransport(client, local_result_root=private)
    transport._collect(run_id, output_directory=node)
    manifest_path = Path(client.config.remote_config).parent / 'remote' / 'runs' / run_id / 'output_manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['error'] = 'changed remote terminal facts'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='existing local transport file differs'):
        transport._collect(run_id, output_directory=node)


def _managed_receipt_case(tmp_path):
    """Real managed acceptance/gate/records; only the external adapter is DI."""
    from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission, LocalFileDescriptor
    from scidiscovery.execution_security import request_digest
    from scidiscovery.plugin_runtime.collection import CollectionContext
    from scidiscovery.research_loop import experiments as e
    from scidiscovery.research_loop.protocol import CURRENT
    from tcad_artifact.command_adapter import CommandAdapterConfig
    from tcad_artifact.managed_execution import TCADManagedExecution
    from tests.operations.test_runtime_plugin_configuration import _ManagedTCADBackend, _execution_package

    trace = tmp_path / 'adapter-events'
    class Backend(_ManagedTCADBackend):
        config = CommandAdapterConfig(executable=sys.executable)

        def status_details(self, external):
            with trace.open('a') as stream: stream.write('query\n')
            return dict(super().status_details(external), progress=dict(observed_at=time.time_ns()))

        def collect_with_budget(self, external, *, context, output_directory):
            with trace.open('a') as stream: stream.write('collect\n')
            outputs = super().collect_with_budget(external, context=context)
            node = Path(output_directory)
            node.mkdir(parents=True, exist_ok=True)
            retained = []
            for item in outputs:
                target = self.root / 'private-manifest' if item.name == 'tcad_manifest' else node / item.name
                # A second collect would replace the manifest's inode, exactly
                # the unsafe operation the managed receipt restart must avoid.
                os.replace(item.local_path, target)
                retained.append(item.model_copy(update=dict(local_path=str(target))))
            return tuple(retained)

    job = tmp_path / 'job.json'
    e.write(job, dict(step=1, role='W'))
    scope = dict(step=1, role='W', job_ref=dict(path=str(job)), execution_key=dict(path=str(job)),
                 mechanism_ref=None, handoff_protocol=CURRENT)
    command = tmp_path / 'command.json'
    e.write(command, dict(executable=sys.executable))
    config = dict(transport='command', command_config_path=str(command), scope=scope,
        record_dir=str(tmp_path / 'records'), native_attempt=dict(number=0),
        admission=dict(version=1, mode='hard'), max_seconds=60)
    config['execution_interface'] = dict(version=e.VERSION, executor='tcad_execution:tcad',
        observation_version=e.OBSERVATION_VERSION, terminal_evidence='adapter_result', result_protocol=None,
        config_record=dict(config), capabilities=vars(TCADManagedExecution.lifecycle_capabilities()))
    source = tmp_path / 'package.json'
    source.write_bytes(_execution_package())
    payload = LocalFileDescriptor(name='original-call', local_path=str(source),
        size_bytes=source.stat().st_size, media_type='application/json')
    owner = tmp_path / 'authority.json'
    e.write(owner, dict(call_id=payload.name, scope=scope, payload=payload.model_dump(mode='json')))
    receipt = e.proof(owner)
    admission = ExecutionAdmission(outcome='policy', policy_digest=request_digest({}), policy={},
        budget=dict(max_storage_bytes=2_000_000_000, wall_time_seconds=60),
        allowance=dict(max_storage_bytes=2_000_000_000, wall_time_seconds=60),
        outside_allowance='deny', budget_key=request_digest(dict(owner=str(owner))), reason='trusted test DI')
    def gate(phase, **bound):
        assert bound['scope'] == scope and bound['call_id'] == payload.name
        assert bound['payload'] == payload
        return dict(receipt=receipt, authorization=admission, context=CollectionContext.for_seconds(30))
    backend = Backend(tmp_path)
    wrapper = TCADManagedExecution(config, backend, gate=gate)
    prepared = wrapper.prepare(payload, preparation_profile='research_loop.v1', exchange_directory=wrapper.records.root)
    accepted = wrapper.records.accept(prepared, wrapper.acceptance_metadata(prepared), config['execution_interface'])
    wrapper.dispatch_lease(accepted)
    wrapper.records.bind_external(payload.name, *wrapper.submit(prepared))
    backend.state = 'succeeded'
    return wrapper, backend, payload.name, trace


@pytest.mark.parametrize('interruption', ['exception',
    pytest.param('process_exit', marks=pytest.mark.process_e2e)])
def test_managed_result_resumes_terminal_receipt_before_collected(tmp_path, interruption):
    from scidiscovery.research_loop import experiments as e
    from tcad_artifact.managed_execution import TCADManagedExecution
    wrapper, backend, call, trace = _managed_receipt_case(tmp_path)
    directory = wrapper.records.directory(call)
    terminal = directory / 'tcad-terminal-binding.json'
    publish = wrapper.records.publish
    def interrupt(call, filename, value):
        if filename == 'collected.json':
            assert terminal.exists()
            if interruption == 'process_exit':
                os._exit(47)
            raise RuntimeError('interrupted between terminal receipt and collected')
        return publish(call, filename, value)
    wrapper.records.publish = interrupt
    if interruption == 'exception':
        assert wrapper.result(call)['status'] == 'unknown'
    else:
        child = multiprocessing.get_context('fork').Process(target=wrapper.result, args=(call,))
        child.start()
        try:
            child.join(timeout=10)
            assert child.exitcode == 47
        finally:
            if child.is_alive():
                child.kill()
                child.join(timeout=3)
    assert terminal.exists() and not (directory / 'collected.json').exists()
    raw = terminal.read_bytes()
    receipt = json.loads(raw)
    originals = [e.proof(item['local_path']) for item in receipt['outputs']]
    terminal_proof = e.proof(terminal)
    events = trace.read_bytes()
    # A fresh facade must use the original receipt's status observation and
    # original files; it cannot rely on the killed collector's Python state.
    resumed = TCADManagedExecution(wrapper.config, backend, gate=wrapper.gate)
    assert resumed.result(call)['status'] == 'known_success'
    assert len(resumed.collect_original(call)) == 2
    assert trace.read_bytes() == events == b'query\ncollect\n'
    assert terminal.read_bytes() == raw and e.proof(terminal) == terminal_proof
    assert [e.proof(item['local_path']) for item in receipt['outputs']] == originals
    assert json.loads((directory / 'collected.json').read_bytes())['outputs'] == receipt['outputs']
    assert backend.events.count('submit') == 1
