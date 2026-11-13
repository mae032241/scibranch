"""Fork storage retains controls only, through real fork transactions; no native calls."""
from pathlib import Path
import shutil

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
    for step, role, parents, action in [(1, 'W', [], 'wait'), (2, 'A', [1], 'continue_w')]:
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


SCIENCE = {'scientific_input', 'historical_science'}


def test_fork_stores_controls_only_and_keeps_scientific_originals_as_references(shared_source, tmp_path):
    parent = shared_source
    child = tmp_path / 'child'
    _create(parent, child, 'controls-only')
    entries = _manifest(child)['entries']
    science = [row for row in entries if row['visibility'] in SCIENCE]
    controls = [row for row in entries if row['visibility'] not in SCIENCE]
    assert any(row['path'].endswith('/shared.bin') for row in science), 'Fixture must declare the opaque original'
    assert controls
    # The opaque scientific original is a reference into the live parent, never a stored copy.
    assert not [row for row in science if _entry_path(child, row).exists()]
    assert all(_entry_path(child, row).is_file() for row in controls)
    assert not [path for path in (child / originals.FORK_DIRECTORY).rglob('shared.bin')]
    fork_sources.validate_manifest(child, full=True)


def test_failed_publication_retains_exact_prepared_stage_until_same_operation_finishes(shared_source, tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent = shared_source
    retry = tmp_path / 'retry'
    preview = fork_service.preview(parent, 2, retry, budget=BUDGET)
    staged = []

    def fail(stage, target):
        staged.extend((row['storage_path'], _entry_path(stage, row).read_bytes())
            for row in _manifest(stage)['entries'] if row['visibility'] not in SCIENCE)
        raise OSError('injected publication failure after staging controls')

    with monkeypatch.context() as patch:
        patch.setattr(instance_archive, '_publish', fail)
        with pytest.raises(OSError, match='injected publication failure'):
            fork_service.create(parent, 2, retry, budget=BUDGET,
                expected_preview_id=preview['preview_id'], operation_id='rollback-retry')
    assert staged
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
    receipt, expected = _create(parent, retry, 'rollback-retry')
    assert expected == preview['preview_id']
    assert fork_service._directory_identity(retry) == record['stage_identity']
    assert not transaction.exists()
    assert c.read(operation_path)['phase'] == 'published'
    assert all(_entry_path(retry, row).read_bytes() == dict(staged)[row['storage_path']]
        for row in _manifest(retry)['entries'] if row['visibility'] not in SCIENCE)
    shutil.rmtree(parent)
    fork_sources.validate_manifest(retry, full=True)
    assert fork_service.create(parent, 2, retry, budget=BUDGET,
        expected_preview_id=expected, operation_id='rollback-retry') == receipt
