"""Public-view and inert artifact handoff boundaries; no training or unpickling."""
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from scripts import prepare_covshift_local as local


def prepared(tmp_path, monkeypatch):
    original = tmp_path / 'original'; original.mkdir()
    for name in local.PUBLIC:
        path = original / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'public fixture\n')
    meta = original / 'environment/harness/meta.json'
    meta.write_text(json.dumps(dict(public='keep', **{k:'sensitive fixture' for k in local.PRIVATE_META_KEYS})))
    (meta.parent / 'gen_dataset.py').write_text('must not export')
    # Real git provenance is covered by the actual public prepare evidence.
    monkeypatch.setattr(local.subprocess, 'check_output', lambda *a, **k:local.REVISION + '\n')
    monkeypatch.setattr(local.subprocess, 'run', lambda *a, **k:None)
    root = tmp_path / 'prepared'; manifest = local.prepare(root, original)
    return root, manifest


def test_preparation_reproduces_public_meta_view_without_generator(tmp_path, monkeypatch):
    root, manifest = prepared(tmp_path, monkeypatch)
    exported = root / 'public/environment/harness/meta.json'
    assert json.loads(exported.read_text()) == {'public':'keep'}
    assert not (exported.parent / 'gen_dataset.py').exists()
    assert set(manifest['public_files']) == set(local.PUBLIC)
    assert manifest['public_file_sizes']['environment/harness/meta.json'] == exported.stat().st_size
    assert not any('sha256' in name for name in manifest)
    assert not manifest['launch_ready']
    local.verify_public(root, manifest)


@pytest.mark.parametrize('change', ['added_generator', 'changed_data', 'symlink', 'private_meta'])
def test_changed_public_view_rejected_before_namespace(tmp_path, monkeypatch, change):
    root, manifest = prepared(tmp_path, monkeypatch)
    harness = root / 'public/environment/harness'
    if change == 'added_generator': (harness / 'gen_dataset.py').write_text('extra')
    elif change == 'changed_data': (harness / 'train.csv').write_text('changed')
    elif change == 'symlink':
        path = harness / 'train.csv'; path.unlink(); path.symlink_to(root / 'host-private/canary.txt')
    else:
        path = harness / 'meta.json'; path.write_text(json.dumps({'noise_sigma':'must not export'}))
        manifest['public_file_sizes']['environment/harness/meta.json'] = path.stat().st_size
    with pytest.raises((ValueError, OSError)): local.verify_public(root, manifest)


def test_handoff_has_no_inherited_nbody_two_mb_model_limit_or_unpickle(tmp_path, monkeypatch):
    root, _ = prepared(tmp_path, monkeypatch)
    (root / 'output/submitted.json').write_text('{"run_id":1}')
    # Deliberately not a pickle: trusted host must transfer bytes without loading it.
    raw = b'not a pickle' * 200_000
    assert len(raw) > 2_000_000
    (root / 'output/submitted_model.pkl').write_bytes(raw)
    result = local.handoff(root)
    assert result['submitted_model.pkl'] == dict(path=str(root / 'submission/submitted_model.pkl'),size_bytes=len(raw))
    assert (root / 'submission/submitted_model.pkl').read_bytes() == raw
    assert not json.loads((root / 'handoff.json').read_text())['trusted_host_unpickled']


def test_nonregular_model_rejected_before_either_handoff_file(tmp_path, monkeypatch):
    root, _ = prepared(tmp_path, monkeypatch)
    (root / 'output/submitted.json').write_text('{}')
    (root / 'output/submitted_model.pkl').symlink_to(root / 'host-private/canary.txt')
    with pytest.raises(OSError): local.handoff(root)
    assert not list((root / 'submission').iterdir())


@pytest.mark.parametrize('virtual_environment', [False, True])
def test_smoke_reuses_selected_existing_python_without_creating_environment(tmp_path, monkeypatch, virtual_environment):
    from scripts import run_tests as guard

    root, _ = prepared(tmp_path, monkeypatch)
    base = tmp_path / 'shared-python'
    (base / 'bin').mkdir(parents=True); (base / 'lib').mkdir()
    base_python = base / 'bin/python3'; base_python.write_text('existing interpreter')
    prefix, python = base, base_python
    if virtual_environment:
        prefix = tmp_path / 'shared-venv'
        (prefix / 'bin').mkdir(parents=True); (prefix / 'lib').mkdir()
        python = prefix / 'bin/python3'; python.symlink_to(base_python)
        (prefix / 'pyvenv.cfg').write_text('include-system-site-packages = true\n')
    original_files = {path:path.read_bytes() for directory in (base, prefix)
        for path in directory.rglob('*') if path.is_file()}

    def runtime_probe(command, **kwargs):
        assert command[:3] == [str(python), '-I', '-c']
        assert kwargs == {'text':True}
        return json.dumps([str(prefix), str(base)])

    commands = []

    def run_one(command, environment, limits, output, lock_fd):
        commands.append(command)
        return {'returncode':0, 'stop_reason':'exited'}

    monkeypatch.setattr(local.subprocess, 'check_output', runtime_probe)
    monkeypatch.setattr(guard, 'serial_lock', lambda:nullcontext(SimpleNamespace(fileno=lambda:7)))
    monkeypatch.setattr(guard, 'run_one', run_one)
    monkeypatch.setattr(local, 'handoff', lambda path:{})
    local.smoke(root, python)

    command, = commands
    for directory in {base, prefix}:
        assert any(command[index:index+3] == ['--ro-bind', str(directory / 'lib'), str(directory / 'lib')]
            for index in range(len(command)))
    assert (str(prefix / 'pyvenv.cfg') in command) is virtual_environment
    start = command.index('_task') - 1
    assert command[start:start+6] == ['/runtime/covshift_preflight.py', '_task',
        str(root / 'host-private/canary.txt'), local.os.readlink('/proc/self/ns/net'), str(python), str(prefix)]
    assert {path:path.read_bytes() for directory in (base, prefix)
        for path in directory.rglob('*') if path.is_file()} == original_files
    assert not (root / 'venv').exists()


def test_public_size_validation_does_not_read_dataset_contents(tmp_path, monkeypatch):
    root, manifest = prepared(tmp_path, monkeypatch)
    original = local.helpers().regular_bytes
    def read(path, maximum):
        assert str(path).endswith('/meta.json')
        return original(path, maximum)
    monkeypatch.setattr(local.helpers(), 'regular_bytes', read)
    local.verify_public(root, manifest)
