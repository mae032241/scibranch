"""Pure reads tolerate bounded readonly hard-link churn without accepting tampering."""
import hashlib
import os

import pytest

from scidiscovery import archive_io
from scidiscovery.research_loop import contracts as c, originals


READERS = ('archive', 'digest', 'original')


@pytest.fixture
def frozen_original(tmp_path):
    raw = b'Exact small immutable original.\n'
    identity = dict(goal_id='shared-read-fixture', settings_sha256=hashlib.sha256(b'settings').hexdigest())
    logical = 'payload.bin'
    storage = f'inherited/{originals.identity_key(identity)}/{logical}'
    root = tmp_path / 'retained'
    path = root / storage
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    path.chmod(0o400)
    entry = dict(origin_identity=identity, path=logical, storage_path=storage, mode=0o400,
        sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    source = originals.NodeSource(identity, 1, str(tmp_path / 'producer'), root, entries=(entry,))
    return path, source, entry, raw


def _read(reader, path, source, reference):
    if reader == 'archive':
        return archive_io.hash_file(path)['sha256']
    if reader == 'digest':
        return c.digest(path)
    assert originals.resolve_ref(source, reference) == path
    return reference['sha256']


def _during_read(monkeypatch, reader, action):
    if reader == 'original':
        digest = c.digest

        def mutate_after_digest(path):
            value = digest(path)
            action()
            return value

        monkeypatch.setattr(c, 'digest', mutate_after_digest)
        return
    sha256 = hashlib.sha256

    class HashWithMutation:
        def __init__(self, *args, **options):
            self.actual = sha256(*args, **options)

        def update(self, data):
            self.actual.update(data)
            action()

        def __getattr__(self, name):
            return getattr(self.actual, name)

    monkeypatch.setattr(hashlib, 'sha256', HashWithMutation)


@pytest.mark.parametrize('reader', READERS)
def test_read_retries_single_real_hardlink_without_changing_bound_bytes(frozen_original, tmp_path, monkeypatch, reader):
    path, source, reference, raw = frozen_original
    linked = []
    initial = path.stat()

    def link_once():
        if not linked:
            alias = tmp_path / 'sibling-original'
            os.link(path, alias)
            linked.append(alias)

    streamed = []
    stream_digest = c._stream_digest

    def count_stream(actual):
        streamed.append(actual)
        return stream_digest(actual)

    monkeypatch.setattr(c, '_stream_digest', count_stream)
    _during_read(monkeypatch, reader, link_once)
    with c.verified_digests():
        assert _read(reader, path, source, reference) == reference['sha256']
        if reader == 'digest':
            reads = len(streamed)
            assert c.digest(path) == reference['sha256']
            assert len(streamed) == reads, 'Successful retry must cache the final file generation'
            os.link(path, tmp_path / 'later-sibling-original')
            assert c.digest(path) == reference['sha256']
            assert len(streamed) == reads + 1, 'A later link must invalidate the old cached generation'
    assert len(linked) == 1
    final = path.stat()
    assert (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) == (
        initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns)
    assert (linked[0].stat().st_dev, linked[0].stat().st_ino) == (final.st_dev, final.st_ino)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('reader', READERS)
def test_continuous_link_churn_has_a_finite_read_retry_bound(frozen_original, tmp_path, monkeypatch, reader):
    path, source, reference, _ = frozen_original
    linked = []

    def keep_linking():
        alias = tmp_path / f'churning-sibling-{len(linked)}'
        os.link(path, alias)
        linked.append(alias)

    _during_read(monkeypatch, reader, keep_linking)
    with c.verified_digests(), pytest.raises((archive_io.ArchiveError, ValueError), match='changed'):
        _read(reader, path, source, reference)
    assert 1 <= len(linked) <= 3, 'Shared-original retries must remain bounded under continual churn'


@pytest.mark.parametrize('reader', READERS)
def test_mtime_change_is_not_retried_as_hardlink_churn(frozen_original, monkeypatch, reader):
    path, source, reference, _ = frozen_original
    changes = []

    def change_mtime():
        value = path.stat()
        os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000))
        changes.append(True)

    _during_read(monkeypatch, reader, change_mtime)
    with c.verified_digests(), pytest.raises((archive_io.ArchiveError, ValueError), match='changed'):
        _read(reader, path, source, reference)
    assert len(changes) == 1


@pytest.mark.parametrize('reader', READERS)
def test_writable_original_does_not_gain_shared_read_retries(frozen_original, tmp_path, monkeypatch, reader):
    path, source, reference, _ = frozen_original
    path.chmod(0o600)
    linked = []

    def link_writable():
        alias = tmp_path / f'writable-sibling-{len(linked)}'
        os.link(path, alias)
        linked.append(alias)

    _during_read(monkeypatch, reader, link_writable)
    with c.verified_digests(), pytest.raises((archive_io.ArchiveError, ValueError), match='changed'):
        _read(reader, path, source, reference)
    assert len(linked) == 1


def test_frozen_binding_rejects_same_size_tampering_with_restored_mtime(frozen_original, monkeypatch):
    path, source, reference, raw = frozen_original
    initial = path.stat()
    tampered = []

    def mutate_once():
        if tampered:
            return
        path.chmod(0o600)
        path.write_bytes(b'X' * len(raw))
        path.chmod(0o400)
        os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        tampered.append(True)

    _during_read(monkeypatch, 'original', mutate_once)
    with c.verified_digests(), pytest.raises(ValueError, match='Original bytes changed'):
        originals.resolve_ref(source, reference)
    assert len(tampered) == 1
    assert path.stat().st_size == len(raw) and path.stat().st_mtime_ns == initial.st_mtime_ns


@pytest.mark.parametrize('reader', READERS)
def test_link_churn_cannot_rebind_read_to_identical_bytes_on_a_new_inode(frozen_original, tmp_path, monkeypatch, reader):
    path, source, reference, raw = frozen_original
    replacement = tmp_path / 'replacement-original'
    replacement.write_bytes(raw)
    replacement.chmod(0o400)
    original = path.stat()
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replaced = []

    def replace_once():
        if replaced:
            return
        os.link(path, tmp_path / 'old-inode-sibling')
        os.replace(replacement, path)
        replaced.append(True)

    _during_read(monkeypatch, reader, replace_once)
    with c.verified_digests(), pytest.raises((archive_io.ArchiveError, ValueError), match='changed'):
        _read(reader, path, source, reference)
    assert len(replaced) == 1 and path.stat().st_ino != original.st_ino
    assert path.read_bytes() == raw


def test_link_during_cached_digest_hit_requires_a_fresh_hash(frozen_original, tmp_path, monkeypatch):
    path, _, reference, _ = frozen_original
    generation = c._digest_generation
    stream_digest = c._stream_digest
    generations, streamed = [], []
    alias = tmp_path / 'cached-read-sibling'

    def link_on_hit_recheck(actual):
        value = generation(actual)
        generations.append(value)
        if len(generations) == 2:
            os.link(path, alias)
            return generation(actual)
        return value

    def count_stream(actual):
        streamed.append(actual)
        return stream_digest(actual)

    with c.verified_digests():
        assert c.digest(path) == reference['sha256']
        monkeypatch.setattr(c, '_digest_generation', link_on_hit_recheck)
        monkeypatch.setattr(c, '_stream_digest', count_stream)
        assert c.digest(path) == reference['sha256']
        assert len(streamed) == 1, 'Metadata churn during a cache hit must force fresh byte validation'
        assert c.digest(path) == reference['sha256']
        assert len(streamed) == 1, 'The stable refreshed generation should be cached'
    assert (alias.stat().st_dev, alias.stat().st_ino) == (path.stat().st_dev, path.stat().st_ino)


@pytest.mark.parametrize('resolver',['source','root'])
def test_original_resolver_checks_deadline_during_content_hash(frozen_original,tmp_path,resolver,monkeypatch):
    path,source,reference,_=frozen_original
    def forbidden(*args,**kwargs):pytest.fail('Bounded original resolution used an unbounded digest')
    monkeypatch.setattr(c,'digest',forbidden)
    class Deadline:
        checks=0
        def remaining_seconds(self):
            self.checks+=1
            if self.checks==3:raise TimeoutError('Original content deadline')
            return 1
    context=Deadline()
    with pytest.raises(TimeoutError,match='content deadline'):
        if resolver=='source':originals.resolve_ref(source,reference,context=context)
        else:
            originals.resolve_root_ref(tmp_path,dict(path=path.relative_to(tmp_path).as_posix(),
                sha256=reference['sha256'],size_bytes=reference['size_bytes']),context=context)
    assert context.checks==3
