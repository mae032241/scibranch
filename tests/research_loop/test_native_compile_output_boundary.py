"""Synthetic trusted compiler context; no solver, backend, or scientific verdict."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, execution_authority, execution_inputs, experiment_client, protocol


class BuilderReached(Exception):
    pass


@pytest.fixture
def compiler(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    job = dict(step=2, role='W', handoff_protocol=protocol.CURRENT,
        worker_view_version=1, lineage_projection_version=1, direct_materials=[], selected_inputs=[])
    (root / 'jobs').mkdir(parents=True)
    (root / 'jobs/002.json').write_text(json.dumps(job))
    work = c.workspace(root, 2)
    source = work / 'work/author.json'
    source.parent.mkdir(parents=True)
    source.write_text('{"scientific_source": "exact author content"}')
    host = execution_inputs.NativeInputHost.__new__(execution_inputs.NativeInputHost)
    host.root, host.job = root, job
    host.intent = c.record_ref(root/'jobs/002.json')
    host.config = dict(worker_view_version=1, record_dir=str(experiment_client.record_directory(root, 2)),
        native_attempt=dict(number=0), scope=dict(step=2), execution_interface={})
    c.write(root/'host-runtime/experiment-control/002/interfaces/000.json', host.config)
    monkeypatch.setattr(execution_authority, '_remaining', lambda *args: (None, 1.))
    monkeypatch.setattr(c, 'settings', lambda root, job=None: {})
    monkeypatch.setattr(c, 'output_quota_exceeded', lambda *args: False)
    # This output-boundary fixture supplies the trusted exact opaque-packet
    # source port. Actual source admission and compiler provenance have separate
    # integration coverage; these ports grant no scientific qualification.
    monkeypatch.setattr(experiment_client, 'code_path', lambda config, path: path)
    monkeypatch.setattr(experiment_client, 'code_selection', lambda config, path: None)
    monkeypatch.setattr(experiment_client, 'input_reference', lambda config, path: c.file_ref(path))

    def compile(output):
        def builder(raw, resolved):
            assert raw == source.read_bytes() and resolved == []
            raise BuilderReached
        return host.compile(source, output, slots=[], aliases={}, capability=None, contract=None, builder=builder)

    return host, work, compile


def test_future_compile_only_reaches_builder_for_exact_private_package_namespace(compiler):
    host, _, compile = compiler
    from pathlib import Path
    output = Path(host.config['record_dir']).parent / 'implementations/I001/package.json'
    with pytest.raises(BuilderReached):
        compile(output)
    assert not output.exists()


@pytest.mark.parametrize('location', ['science_artifacts', 'wrong_name', 'short_reference', 'traversal', 'wrong_owner', 'symlink'])
def test_future_compile_rejects_non_host_or_redirected_output_before_builder(compiler, tmp_path, location):
    from pathlib import Path
    host, work, compile = compiler
    base = Path(host.config['record_dir']).parent / 'implementations'
    outputs = dict(science_artifacts=work / 'artifacts/package.json', wrong_name=base / 'I001/other.json',
        short_reference=base / 'I1/package.json', traversal=base / 'I001/../I002/package.json',
        wrong_owner=tmp_path / 'another-host/implementations/I001/package.json')
    if location == 'symlink':
        target = tmp_path / 'redirected'
        target.mkdir()
        base.mkdir(parents=True)
        (base / 'I001').symlink_to(target, target_is_directory=True)
        output = base / 'I001/package.json'
    else:
        output = outputs[location]
    with pytest.raises(ValueError, match='host-owned output namespace'):
        compile(output)
    assert not output.exists()


@pytest.mark.parametrize('marker', [None, True, 2])
def test_future_compile_rejects_configuration_marker_tampering(compiler, marker):
    from pathlib import Path
    host, _, compile = compiler
    output = Path(host.config['record_dir']).parent / 'implementations/I001/package.json'
    if marker is None:
        host.config.pop('worker_view_version')
    else:
        host.config['worker_view_version'] = marker
    with pytest.raises(ValueError, match='host-owned output namespace'):
        compile(output)
    assert not output.exists()


def test_future_compile_cannot_retarget_configuration_record_directory(compiler, tmp_path):
    host, _, compile = compiler
    host.config['record_dir'] = str(tmp_path / 'forged/records')
    with pytest.raises(ValueError, match='host-owned output namespace'):
        compile(tmp_path / 'forged/implementations/I001/package.json')


def test_old_job_cannot_publish_a_public_compile_package(compiler, tmp_path):
    host, work, compile = compiler
    host.job.pop('worker_view_version')
    host.config.pop('worker_view_version')
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific job view'):
        compile(work / 'artifacts/package.json')
    with pytest.raises(protocol.UnsupportedProtocol, match='Scientific job view'):
        compile(tmp_path / 'implementations/I001/package.json')
