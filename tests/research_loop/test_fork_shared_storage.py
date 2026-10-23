"""Shared original storage through real fork transactions; no native calls."""
import errno
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts as c, controller, fork_service, fork_sources, originals
from test_execution_authority import prepare
from test_history_handoff import finish


BUDGET = dict(max_tokens=200, max_seconds=30, max_steps=2)
PAYLOAD = 'Opaque fixture original: retained bytes are shared.\n' * 256


def _finish_without_native(monkeypatch, root, step, role, parents, **options):
    write = c.write

    def declared_write(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', declared_write)
        return finish(root, step, role, parents, **options)


@pytest.fixture
def shared_source(tmp_path, monkeypatch):
    root, _ = prepare(tmp_path, enabled=False)
    for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
        _finish_without_native(monkeypatch, root, step, role, parents, action=action,
            files={'shared.bin': PAYLOAD} if step == 1 else None)
    with controller._writer(root):
        pass
    return root


def _create(parent, child, operation):
    preview = fork_service.preview(parent, 2, child, budget=BUDGET)
    assert preview['status'] == 'fork_preview_ready'
    receipt = fork_service.create(parent, 2, child, budget=BUDGET,
        expected_preview_id=preview['preview_id'], operation_id=operation)
    return receipt, preview['preview_id']


def _manifest(root):
    return c.read(root / originals.FORK_DIRECTORY / 'manifest.json')


def _entry_path(root, entry):
    return root / originals.FORK_DIRECTORY / entry['storage_path']


def _inode(path):
    value = path.stat()
    return value.st_dev, value.st_ino


def _entry_key(entry):
    return originals.identity_key(entry['origin_identity']), entry['path'], entry['size_bytes']


def _payload_entries(manifest):
    rows = [row for row in manifest['entries'] if row['path'].endswith('/shared.bin')]
    assert rows, 'Synthetic selected task must retain the opaque original'
    return rows


def test_sibling_forks_share_selected_originals_and_public_views(shared_source, tmp_path, monkeypatch):
    from scidiscovery.research_loop import atomic_files
    parent = shared_source
    plan = fork_sources.inspect_source(parent, 2)
    sources = {fork_sources._key(row['origin_identity'], row['path'], row):
        Path(plan['source_paths'][fork_sources._key(row['origin_identity'], row['path'], row)])
        for row in plan['entries']}
    before = {key: (stat.S_IMODE(path.stat().st_mode), _inode(path)) for key, path in sources.items()}
    first, second = tmp_path / 'first', tmp_path / 'second'
    _create(parent, first, 'shared-first')
    payload_path = parent / 'project/nodes/001/artifacts/shared.bin'
    copy_original = atomic_files.copy_original

    def no_payload_copy(source, destination, **options):
        assert Path(source) != payload_path, 'Second fork recopied the opaque selected payload'
        return copy_original(source, destination, **options)

    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'copy_original', no_payload_copy)
        _create(parent, second, 'shared-second')
    first_manifest = _manifest(first)
    first_entries = {_entry_key(row): row for row in first_manifest['entries']}
    second_manifest = _manifest(second)
    direct_readonly = []
    for row in second_manifest['entries']:
        left, right = _entry_path(first, first_entries[_entry_key(row)]), _entry_path(second, row)
        assert left.read_bytes() == right.read_bytes() == sources[fork_sources._key(row['origin_identity'],row['path'],row)].read_bytes()
        assert stat.S_IMODE(right.stat().st_mode) == 0o400
        key = fork_sources._key(row['origin_identity'], row['path'], row)
        source = sources[key]
        assert (stat.S_IMODE(source.stat().st_mode), _inode(source)) == before[key]
        if before[key][0] == 0o400 and _inode(right) == _inode(source):
            direct_readonly.append(source)
            assert _inode(left) == _inode(right) == _inode(source)
    assert direct_readonly, 'Fixture must exercise direct links from retained read-only originals'
    assert {_inode(_entry_path(second, row)) for row in _payload_entries(second_manifest)} == {
        _inode(_entry_path(first, row)) for row in _payload_entries(first_manifest)}
    for snapshot in first_manifest['public_snapshots']:
        reference = snapshot['original_ref']
        entry = first_entries[_entry_key(reference)]
        public = first / originals.FORK_DIRECTORY / 'public' / snapshot['path']
        assert _inode(public) == _inode(_entry_path(first, entry))
        assert stat.S_IMODE(public.stat().st_mode) == 0o400
    assert first_manifest['public_snapshots'], 'Fixture must exercise retained public source visibility'


def test_declared_public_payload_is_shared_through_child_source_freeze(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the exact declared opaque public original.')
    payload = public / 'large.bin'
    chunk = bytes(range(256)) * 256
    with payload.open('wb') as stream:
        for _ in range(16):
            stream.write(chunk)
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, ['fixture'], max_tokens=4000,
        max_seconds=60, max_steps=7, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', source_restrictions=[
            dict(source=str(path),  restriction='clear')
            for path in (objective, payload)])
    for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
        _finish_without_native(monkeypatch, parent, step, role, parents, action=action)
    with controller._writer(parent):
        pass

    def declared_original(root):
        document = c.read(root / c.settings(root)['source_provenance']['path'])
        rows = [row for row in document['declarations'] if row['origin']['path'] == str(payload)]
        assert len(rows) == 1
        return root / rows[0]['retained']['path']

    retained = declared_original(parent)
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400
    original_inode = _inode(retained)
    bind_original = c._bind_original
    validated = []

    def verify_preseeded(source, destination, **options):
        if options['size_bytes'] == payload.stat().st_size:
            assert destination.exists(), 'Child source freezing recopied the declared opaque payload'
            assert _inode(destination) == original_inode
            validated.append(destination)
        return bind_original(source, destination, **options)

    first, second = tmp_path / 'first', tmp_path / 'second'
    with monkeypatch.context() as patch:
        patch.setattr(c, '_bind_original', verify_preseeded)
        _create(parent, first, 'declared-public-first')
        _create(parent, second, 'declared-public-second')
    assert len(validated) >= 2, 'Both child source-freeze paths must verify the retained payload'
    for child in (first, second):
        manifest = _manifest(child)
        snapshot = next(row for row in manifest['public_snapshots'] if row['path'] == 'large.bin')
        entry = next(row for row in manifest['entries'] if _entry_key(row) == _entry_key(snapshot['original_ref']))
        aliases = [declared_original(child), _entry_path(child, entry),
            child / originals.FORK_DIRECTORY / 'public/large.bin']
        assert all(_inode(path) == original_inode for path in aliases)
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o400 for path in aliases)
    shutil.rmtree(parent)
    assert declared_original(second).stat().st_size == len(chunk) * 16
    fork_sources.validate_manifest(second, full=True)


def test_mutable_original_is_snapshotted_independently_without_writable_alias(shared_source, tmp_path):
    parent = shared_source
    original = parent / 'project/nodes/001/artifacts/shared.bin'
    original.chmod(0o600)
    before = _inode(original)
    first, second = tmp_path / 'first', tmp_path / 'second'
    _create(parent, first, 'mutable-first')
    _create(parent, second, 'mutable-second')
    entry = next(row for row in _payload_entries(_manifest(first)) if row['path'] == original.relative_to(parent).as_posix())
    inherited = _entry_path(first, entry)
    assert _inode(inherited) != _inode(_entry_path(second, entry))
    assert _inode(inherited) != before
    assert _inode(original) == before and stat.S_IMODE(original.stat().st_mode) == 0o600
    original.write_text('The mutable producer name can change independently.\n')
    assert inherited.read_text() == PAYLOAD
    assert _entry_path(second, entry).read_text() == PAYLOAD
    assert stat.S_IMODE(inherited.stat().st_mode) == 0o400


@pytest.mark.process_e2e
def test_cli_shared_branch_survives_source_and_sibling_removal(shared_source, tmp_path):
    parent = shared_source
    first, second = tmp_path / 'first', tmp_path / 'second'
    _create(parent, first, 'service-sibling')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    common = ['--root', str(parent), '--step', '2', '--child-root', str(second),
        '--max-tokens', '200', '--max-seconds', '30', '--max-steps', '2']

    def invoke(command, *extra):
        result = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli', command, *common, *extra],
            env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    preview = invoke('fork-preview')
    options = ['--expected-preview-id', preview['preview_id'], '--operation-id', 'cli-shared']
    receipt = invoke('fork-create', *options)
    entry = _payload_entries(_manifest(second))[0]
    assert _inode(_entry_path(first, entry)) == _inode(_entry_path(second, entry))
    shutil.rmtree(parent)
    shutil.rmtree(first)
    assert _entry_path(second, entry).read_text() == PAYLOAD
    assert fork_sources.selected_task(second, full=True)['job']['step'] == 2
    assert invoke('fork-create', *options) == receipt
    assert receipt['state'] == 'paused' and not (second / 'native-evidence').exists()


def test_nested_fork_reuses_original_inodes_after_ancestors_are_removed(shared_source, tmp_path, monkeypatch):
    from scidiscovery.research_loop import atomic_files
    parent = shared_source
    child, grandchild = tmp_path / 'child', tmp_path / 'grandchild'
    _create(parent, child, 'nested-first')
    inherited = {_entry_key(row): _inode(_entry_path(child, row)) for row in _payload_entries(_manifest(child))}
    payload_paths = {_entry_path(child,row) for row in _payload_entries(_manifest(child))}
    copy_original = atomic_files.copy_original
    def no_second_freeze(source, destination, **options):
        assert Path(source) not in payload_paths, 'Inherited readonly payload was independently frozen again'
        return copy_original(source, destination, **options)
    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'copy_original', no_second_freeze)
        _finish_without_native(monkeypatch, child, 2, 'A', [1], action='finish')
        with controller._writer(child):
            pass
        _create(child, grandchild, 'nested-second')
    reused = [row for row in _payload_entries(_manifest(grandchild)) if _entry_key(row) in inherited]
    assert reused, 'Nested selected task must retain its original ancestor identity'
    assert all(_inode(_entry_path(grandchild, row)) == inherited[_entry_key(row)] for row in reused)
    shutil.rmtree(parent)
    shutil.rmtree(child)
    fork_sources.validate_manifest(grandchild, full=True)
    assert all(_entry_path(grandchild, row).read_text() == PAYLOAD for row in reused)


def test_failed_publication_retains_exact_prepared_stage_until_same_operation_finishes(shared_source, tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent = shared_source
    first, retry = tmp_path / 'first', tmp_path / 'retry'
    _create(parent, first, 'rollback-first')
    first_entries = {_entry_key(row): _inode(_entry_path(first, row)) for row in _manifest(first)['entries']}
    links_before = {_entry_key(row): _entry_path(first, row).stat().st_nlink for row in _manifest(first)['entries']}
    preview = fork_service.preview(parent, 2, retry, budget=BUDGET)

    def fail(stage, target):
        for row in _manifest(stage)['entries']:
            assert _inode(_entry_path(stage, row)) == first_entries[_entry_key(row)]
        raise OSError('injected publication failure after shared attachment')

    with monkeypatch.context() as patch:
        patch.setattr(instance_archive, '_publish', fail)
        with pytest.raises(OSError, match='injected publication failure'):
            fork_service.create(parent, 2, retry, budget=BUDGET,
                expected_preview_id=preview['preview_id'], operation_id='rollback-retry')
    assert not retry.exists()
    assert not [path for path in tmp_path.iterdir() if path.is_dir()
        and path.name.startswith('.scid-fork-') and path.name != '.scid-fork-operations']
    operation_path = next(path for path in (tmp_path / '.scid-fork-operations').glob('*.json')
        if c.read(path)['operation_id'] == 'rollback-retry')
    record = c.read(operation_path)
    assert record['phase'] == 'prepared'
    transaction = Path(record['transaction_path'])
    stage = transaction / 'child'
    assert fork_service._directory_identity(stage) == record['stage_identity']
    stage_links = Counter(_inode(path) for path in stage.rglob('*') if path.is_file())
    for row in _manifest(first)['entries']:
        key = _entry_key(row)
        assert _entry_path(first, row).stat().st_nlink == links_before[key] + stage_links[first_entries[key]]
    receipt, expected = _create(parent, retry, 'rollback-retry')
    assert expected == preview['preview_id']
    assert fork_service._directory_identity(retry) == record['stage_identity']
    assert not transaction.exists()
    assert c.read(operation_path)['phase'] == 'published'
    for row in _manifest(first)['entries']:
        key = _entry_key(row)
        assert _entry_path(first, row).stat().st_nlink == links_before[key] + stage_links[first_entries[key]]
    assert all(_inode(_entry_path(retry, row)) == first_entries[_entry_key(row)] for row in _manifest(retry)['entries'])
    shutil.rmtree(parent)
    fork_sources.validate_manifest(first, full=True)
    assert fork_service.create(parent, 2, retry, budget=BUDGET,
        expected_preview_id=expected, operation_id='rollback-retry') == receipt


def test_cross_device_link_failure_is_explicit_without_copy_fallback(shared_source, tmp_path, monkeypatch):
    from scidiscovery import archive_io
    parent = shared_source
    _create(parent, tmp_path / 'warm-cache', 'device-warm-cache')
    child = tmp_path / 'unsupported-device'
    preview = fork_service.preview(parent, 2, child, budget=BUDGET)
    attempts = []

    def cross_device(source, destination, **options):
        attempts.append((source, destination))
        raise OSError(errno.EXDEV, 'injected cross-device hard-link failure')

    def forbid_fallback(*args, **options):
        pytest.fail('Hard-link failure silently fell back to copying an original')

    with monkeypatch.context() as patch:
        patch.setattr(archive_io.os, 'link', cross_device)
        patch.setattr(archive_io, 'copy_original', forbid_fallback)
        with pytest.raises(fork_service.ForkError) as error:
            fork_service.create(parent, 2, child, budget=BUDGET,
                expected_preview_id=preview['preview_id'], operation_id='device-retry')
    assert attempts and error.value.code == 'fork_storage_unsupported'
    assert not child.exists()
    _create(parent, child, 'device-retry')
    fork_sources.validate_manifest(child, full=True)
