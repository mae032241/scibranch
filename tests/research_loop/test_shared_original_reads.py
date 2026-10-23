"""Retained originals resolve by exact path and declared size, without hashes."""
import os

import pytest

from scidiscovery.research_loop import originals


@pytest.fixture
def frozen_original(tmp_path):
    raw = b'Exact small immutable original.\n'
    identity = dict(goal_id='shared-read-fixture', settings_ref=dict(path=str(tmp_path / 'producer/settings.json')))
    logical = 'payload.bin'
    storage = f'inherited/{originals.identity_key(identity)}/{logical}'
    root = tmp_path / 'retained'
    path = root / storage
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    path.chmod(0o400)
    entry = dict(origin_identity=identity, path=logical, storage_path=storage, mode=0o400,
        size_bytes=len(raw))
    source = originals.NodeSource(identity, 1, str(tmp_path / 'producer'), root, entries=(entry,))
    return path, source, entry, raw


def test_readonly_hardlink_alias_keeps_the_same_original(frozen_original, tmp_path):
    path, source, reference, raw = frozen_original
    alias = tmp_path / 'sibling-original'
    os.link(path, alias)
    assert originals.resolve_ref(source, reference) == path
    assert path.read_bytes() == alias.read_bytes() == raw
    assert path.stat().st_ino == alias.stat().st_ino


@pytest.mark.parametrize('fault', ['size', 'missing', 'symlink'])
def test_original_resolution_rejects_missing_or_changed_declared_original(frozen_original, tmp_path, fault):
    path, source, reference, raw = frozen_original
    if fault == 'size':
        path.chmod(0o600)
        path.write_bytes(raw + b'changed')
    elif fault == 'missing':
        path.unlink()
    else:
        alias = tmp_path / 'unbound-original'
        path.rename(alias)
        path.symlink_to(alias)
    with pytest.raises((OSError, ValueError)):
        originals.resolve_ref(source, reference)


@pytest.mark.parametrize('resolver', ['source', 'root'])
def test_original_resolver_honors_the_operation_deadline(frozen_original, tmp_path, resolver):
    path, source, reference, _ = frozen_original

    class Deadline:
        def remaining_seconds(self):
            raise TimeoutError('Original resolution deadline')

    with pytest.raises(TimeoutError, match='resolution deadline'):
        if resolver == 'source':
            originals.resolve_ref(source, reference, context=Deadline())
        else:
            originals.resolve_root_ref(tmp_path, dict(path=path.relative_to(tmp_path).as_posix(),
                size_bytes=reference['size_bytes']), context=Deadline())
