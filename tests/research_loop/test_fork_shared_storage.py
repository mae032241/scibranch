"""Shared original storage through real fork transactions; no native calls."""
import errno
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
    return originals.identity_key(entry['origin_identity']), entry['path'], entry['sha256'], entry['size_bytes']


def _payload_entries(manifest):
    rows = [row for row in manifest['entries'] if row['path'].endswith('/shared.bin')]
    assert rows, 'Synthetic selected task must retain the opaque original'
    return rows


def test_sibling_forks_share_selected_originals_and_public_views(shared_source, tmp_path, monkeypatch):
    from scidiscovery import archive_io
    parent = shared_source
    plan = fork_sources.inspect_source(parent, 2)
    sources = {fork_sources._key(row['origin_identity'], row['path'], row):
        Path(plan['source_paths'][fork_sources._key(row['origin_identity'], row['path'], row)])
        for row in plan['entries']}
    before = {key: (stat.S_IMODE(path.stat().st_mode), _inode(path)) for key, path in sources.items()}
    first, second = tmp_path / 'first', tmp_path / 'second'
    _create(parent, first, 'shared-first')
    payload_sha = c.digest(parent / 'project/nodes/001/artifacts/shared.bin')
    copy_verified = archive_io.copy_verified

    def no_payload_copy(source, destination, entry):
        assert entry['sha256'] != payload_sha, 'Second fork recopied the opaque selected payload'
        return copy_verified(source, destination, entry)

    with monkeypatch.context() as patch:
        patch.setattr(archive_io, 'copy_verified', no_payload_copy)
        _create(parent, second, 'shared-second')
    first_manifest = _manifest(first)
    first_entries = {_entry_key(row): row for row in first_manifest['entries']}
    second_manifest = _manifest(second)
    direct_readonly = []
    for row in second_manifest['entries']:
        left, right = _entry_path(first, first_entries[_entry_key(row)]), _entry_path(second, row)
        assert _inode(left) == _inode(right)
        assert stat.S_IMODE(right.stat().st_mode) == 0o400
        key = fork_sources._key(row['origin_identity'], row['path'], row)
        source = sources[key]
        assert (stat.S_IMODE(source.stat().st_mode), _inode(source)) == before[key]
        if before[key][0] == 0o400:
            direct_readonly.append(source)
            assert _inode(right) == _inode(source)
        else:
            assert _inode(right) != _inode(source)
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
    payload_sha = c.digest(payload)
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, ['fixture'], max_tokens=4000,
        max_seconds=60, max_steps=7, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', source_restrictions=[
            dict(source=str(path), sha256=c.digest(path), restriction='clear')
            for path in (objective, payload)])
    for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
        _finish_without_native(monkeypatch, parent, step, role, parents, action=action)
    with controller._writer(parent):
        pass

    def declared_original(root):
        document = c.read(root / c.settings(root)['source_provenance']['path'])
        rows = [row for row in document['declarations'] if row['origin']['sha256'] == payload_sha]
        assert len(rows) == 1
        return root / rows[0]['retained']['path']

    retained = declared_original(parent)
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400
    original_inode = _inode(retained)
    copy_bound = c._copy_bound
    validated = []

    def verify_preseeded(source, destination, sha256, **options):
        if sha256 == payload_sha:
            assert destination.exists(), 'Child source freezing recopied the declared opaque payload'
            assert _inode(destination) == original_inode
            validated.append(destination)
        return copy_bound(source, destination, sha256, **options)

    first, second = tmp_path / 'first', tmp_path / 'second'
    with monkeypatch.context() as patch:
        patch.setattr(c, '_copy_bound', verify_preseeded)
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


def test_mutable_original_is_snapshotted_once_without_writable_alias(shared_source, tmp_path):
    parent = shared_source
    original = parent / 'project/nodes/001/artifacts/shared.bin'
    original.chmod(0o600)
    before = _inode(original)
    first, second = tmp_path / 'first', tmp_path / 'second'
    _create(parent, first, 'mutable-first')
    _create(parent, second, 'mutable-second')
    entry = next(row for row in _payload_entries(_manifest(first)) if row['path'] == original.relative_to(parent).as_posix())
    inherited = _entry_path(first, entry)
    assert _inode(inherited) == _inode(_entry_path(second, entry))
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
    parent = shared_source
    child, grandchild = tmp_path / 'child', tmp_path / 'grandchild'
    _create(parent, child, 'nested-first')
    inherited = {_entry_key(row): _inode(_entry_path(child, row)) for row in _payload_entries(_manifest(child))}
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


def test_failed_publication_unlinks_stage_without_discarding_shared_originals(shared_source, tmp_path, monkeypatch):
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
    assert {_entry_key(row): _entry_path(first, row).stat().st_nlink for row in _manifest(first)['entries']} == links_before
    receipt, expected = _create(parent, retry, 'rollback-retry')
    assert expected == preview['preview_id']
    assert all(_inode(_entry_path(retry, row)) == first_entries[_entry_key(row)] for row in _manifest(retry)['entries'])
    shutil.rmtree(parent)
    fork_sources.validate_manifest(first, full=True)
    assert fork_service.create(parent, 2, retry, budget=BUDGET,
        expected_preview_id=expected, operation_id='rollback-retry') == receipt


@pytest.mark.parametrize('published', [False, True])
def test_interrupted_mutable_import_cannot_publish_partial_cache_object(shared_source, tmp_path, monkeypatch, published):
    from scidiscovery import archive_io
    parent, child = shared_source, tmp_path / 'retry-import'
    original = parent / 'project/nodes/001/artifacts/shared.bin'
    original.chmod(0o600)
    payload_sha = c.digest(original)
    cache = parent / 'host-history/objects' / payload_sha
    cache_before = set(cache.parent.iterdir()) if cache.parent.exists() else set()
    preview = fork_service.preview(parent, 2, child, budget=BUDGET)
    assert (set(cache.parent.iterdir()) if cache.parent.exists() else set()) == cache_before
    link_verified = archive_io.link_verified
    interrupted = []

    def fail_import(source, destination, entry):
        if Path(destination) == cache:
            interrupted.append(Path(source))
            if published:
                link_verified(source, destination, entry)
            raise OSError('injected interruption during normalized import')
        return link_verified(source, destination, entry)

    with monkeypatch.context() as patch:
        patch.setattr(archive_io, 'link_verified', fail_import)
        with pytest.raises(fork_service.ForkError, match='injected interruption'):
            fork_service.create(parent, 2, child, budget=BUDGET,
                expected_preview_id=preview['preview_id'], operation_id='import-retry')
    assert interrupted and not child.exists()
    assert all(not path.exists() for path in interrupted)
    assert cache.exists() is published
    if published:
        assert cache.read_text() == PAYLOAD and stat.S_IMODE(cache.stat().st_mode) == 0o400
    assert original.read_text() == PAYLOAD and stat.S_IMODE(original.stat().st_mode) == 0o600
    _create(parent, child, 'import-retry')
    entry = next(row for row in _payload_entries(_manifest(child)) if row['path'] == original.relative_to(parent).as_posix())
    assert _inode(cache) == _inode(_entry_path(child, entry))
    assert stat.S_IMODE(cache.stat().st_mode) == 0o400


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
        patch.setattr(archive_io, 'copy_verified', forbid_fallback)
        with pytest.raises(fork_service.ForkError) as error:
            fork_service.create(parent, 2, child, budget=BUDGET,
                expected_preview_id=preview['preview_id'], operation_id='device-retry')
    assert attempts and error.value.code == 'fork_storage_unsupported'
    assert not child.exists()
    _create(parent, child, 'device-retry')
    fork_sources.validate_manifest(child, full=True)


def test_conflicting_cache_object_is_rejected_without_damaging_existing_branch(shared_source, tmp_path):
    parent = shared_source
    first, second = tmp_path / 'first', tmp_path / 'cache-conflict'
    _create(parent, first, 'cache-conflict-first')
    original = parent / 'project/nodes/001/artifacts/shared.bin'
    cache = parent / 'host-history/objects' / c.digest(original)
    original_inode = _inode(cache)
    cache.unlink()
    cache.write_bytes(b'Conflicting object at the original content address.\n')
    cache.chmod(0o400)
    preview = fork_service.preview(parent, 2, second, budget=BUDGET)
    with pytest.raises(fork_service.ForkError, match='cache original changed') as error:
        fork_service.create(parent, 2, second, budget=BUDGET,
            expected_preview_id=preview['preview_id'], operation_id='cache-conflict-second')
    assert error.value.code == 'fork_origin_invalid' and not second.exists()
    entry = next(row for row in _payload_entries(_manifest(first)) if row['path'] == original.relative_to(parent).as_posix())
    assert _inode(_entry_path(first, entry)) == original_inode
    assert _entry_path(first, entry).read_text() == PAYLOAD
    fork_sources.validate_manifest(first, full=True)


@pytest.mark.parametrize('warm', [False, True])
def test_readonly_cache_requires_prior_normalization(shared_source, tmp_path, warm):
    parent = shared_source
    if warm:
        _create(parent, tmp_path / 'first', 'readonly-cache-first')
    cache = parent / 'host-history/objects'
    cache.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(cache.stat().st_mode)
    cache.chmod(0o500)
    child = tmp_path / 'readonly-cache-child'
    try:
        preview = fork_service.preview(parent, 2, child, budget=BUDGET)
        if warm:
            assert preview['status'] == 'fork_preview_ready'
            _create(parent, child, 'readonly-cache-reuse')
            fork_sources.validate_manifest(child, full=True)
        else:
            assert preview['status'] == 'fork_preview_blocked'
            assert any(row['code'] == 'fork_storage_unsupported' for row in preview['blockers'])
            with pytest.raises(fork_service.ForkError) as error:
                fork_service.create(parent, 2, child, budget=BUDGET,
                    expected_preview_id=preview['preview_id'], operation_id='readonly-cache-cold')
            assert error.value.code == 'fork_storage_unsupported' and not child.exists()
            assert not list(cache.iterdir())
    finally:
        cache.chmod(mode)
