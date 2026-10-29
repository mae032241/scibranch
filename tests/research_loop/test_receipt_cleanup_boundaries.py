"""Small local regressions for collection residue and delivery diagnostics."""
import json
import sys

import pytest

from scidiscovery.research_loop import delivery
from tcad_artifact.execution_control import FileDescriptor
from tcad_artifact.ssh_transport import SSHTCADTransport


class _LocalResults:
    """Only collection metadata and tiny local bytes; no submission interface."""
    def __init__(self, outputs):
        self.raw = {name: raw for name, raw in outputs.items()}
        manifest = dict(started_at='2026-10-29T00:00:00Z', completed_at='2026-10-29T00:00:01Z',
            terminal_state='succeeded', exit_code=0, error='', outputs=[dict(name=name,
                relative_path=name, media_type='application/octet-stream', size_bytes=len(raw),
                output_class='transport_derived' if name == 'metadata' else 'solver_native')
                for name, raw in outputs.items()])
        self.raw['tcad_manifest'] = json.dumps(manifest).encode()
        self.raw['tcad_log'] = b'complete\n'
        self.descriptors = [FileDescriptor(name=name, local_path='/synthetic/' + name,
            media_type='application/octet-stream', size_bytes=len(raw)) for name, raw in self.raw.items()]
        self.downloads = []

    def rpc(self, request):
        assert request['params']['name'] == 'tcad_collect'
        return dict(jsonrpc='2.0', id=request['id'], result=dict(structuredContent={
            'outputs': [item.model_dump(mode='json') for item in self.descriptors]}))

    def get_to(self, local_path, destination, max_bytes):
        name = local_path.removeprefix('/synthetic/')
        raw = self.raw[name]
        assert len(raw) <= max_bytes
        self.downloads.append(name)
        destination.write_bytes(raw)


class _InterruptedBatch(_LocalResults):
    interrupted = False

    def get_many_to(self, items, *, on_complete, on_progress):
        for descriptor, path in items:
            path.write_bytes(self.raw[descriptor.name])
            self.downloads.append(descriptor.name)
            if not self.interrupted:
                self.interrupted = True
                raise RuntimeError('interrupted before publication')
            on_complete(descriptor, path)


def test_scientific_collection_removes_empty_stage_and_reuses_partial_progress(tmp_path):
    remote = _InterruptedBatch(dict(first=b'first', second=b'second', metadata=b'meta'))
    private, node = tmp_path / 'private', tmp_path / 'node'
    transport = SSHTCADTransport(remote, local_result_root=private)
    run_id = 'run_' + 'a' * 32
    stage = node / ('.tcad-collection-' + run_id)
    with pytest.raises(RuntimeError, match='interrupted before publication'):
        transport._collect(run_id, output_directory=node)
    first = stage / 'first'
    before = first.stat()
    assert first.read_bytes() == b'first'
    owned = private / 'runs' / run_id
    stale = [owned / 'tcad_manifest.download', owned / 'metadata.download',
        owned / '.metadata.interrupted.download', owned / '.results.interrupted.tar.download',
        stage / 'second.download', stage / '.second.interrupted.download']
    for path in stale:
        path.write_bytes(b'incomplete')
    unrelated = [private / 'tcad_manifest.download', owned / 'unrelated.download',
        node / ('.tcad-collection-run_' + 'b' * 32) / 'second.download']
    for path in unrelated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'other owner')
    result = transport._collect(run_id, output_directory=node)
    assert len(result['outputs']) == 5
    assert not stage.exists()
    assert (node / 'first').read_bytes() == b'first'
    assert (node / 'first').stat().st_ino == before.st_ino
    assert remote.downloads.count('first') == 1
    assert all(not path.exists() for path in stale)
    assert all(path.read_bytes() == b'other owner' for path in unrelated)


def test_fallback_preserves_declared_download_named_outputs_and_unknown_stage_files(tmp_path):
    remote = _LocalResults({'first': b'first', 'first.download': b'valid output',
        'tcad_manifest.download': b'valid manifest-named output'})
    private, node = tmp_path / 'private', tmp_path / 'node'
    transport = SSHTCADTransport(remote, local_result_root=private)
    run_id = 'run_' + 'a' * 32
    result = transport._collect(run_id, output_directory=node)
    stage = node / ('.tcad-collection-' + run_id)
    assert not stage.exists()
    assert (node / 'first.download').read_bytes() == b'valid output'
    assert (node / 'tcad_manifest.download').read_bytes() == b'valid manifest-named output'
    # A restart can contain exact completed staging bytes and a legacy fixed
    # temporary name that is itself another declared completed output.
    (node / 'first').rename(node / 'saved-first')
    stage.mkdir()
    (node / 'saved-first').rename(stage / 'first')
    (node / 'first.download').rename(stage / 'first.download')
    unknown = stage / '.unknown.download'
    unknown.write_bytes(b'preserve evidence')
    assert transport._collect(run_id, output_directory=node) == result
    assert (node / 'first.download').read_bytes() == b'valid output'
    assert unknown.read_bytes() == b'preserve evidence'


@pytest.mark.process_e2e
@pytest.mark.parametrize('returncode, outcome', [(0, 'child_completed'), (7, 'child_failed')])
def test_delivery_reports_completed_child_without_changing_result(tmp_path, returncode, outcome):
    adapter = tmp_path / 'adapter.py'
    adapter.write_text(
        'import json, sys, time\n'
        'json.load(sys.stdin)\n'
        'time.sleep(.05)\n'
        'print(json.dumps({"status": "ok"}))\n'
        f'sys.exit({returncode})\n'
    )
    evidence = tmp_path / 'evidence'
    result = delivery._call(
        {'launcher_argv': [sys.executable], 'adapter': str(adapter)},
        {'operation_id': 'cleanup-boundary', 'phase': 'smoke'},
        5,
        evidence=evidence,
    )
    assert result == ({'status': 'ok'} if returncode == 0 else None)
    diagnostic = json.loads((evidence / 'cleanup-boundary-smoke-1.json').read_text())
    assert diagnostic['outcome'] == outcome
    assert diagnostic['returncode'] == returncode


@pytest.mark.process_e2e
def test_delivery_reports_stopped_child_after_callback_error(tmp_path):
    adapter = tmp_path / 'adapter.py'
    adapter.write_text('import time\ntime.sleep(10)\n')
    evidence = tmp_path / 'evidence'

    def fail_after_start(phase, identity):
        raise RuntimeError('receipt publication failed')

    with pytest.raises(RuntimeError, match='receipt publication failed'):
        delivery._call(
            {'launcher_argv': [sys.executable], 'adapter': str(adapter)},
            {'operation_id': 'cleanup-boundary', 'phase': 'smoke'},
            5,
            on_process=fail_after_start,
            evidence=evidence,
        )
    diagnostic = json.loads((evidence / 'cleanup-boundary-smoke-1.json').read_text())
    assert diagnostic['outcome'] == 'child_stopped'
    assert diagnostic['returncode'] != 0
