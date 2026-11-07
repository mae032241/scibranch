"""Project checks use normal Git semantics; initial seed rows use tracked names."""
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import project_layout as layout
from scidiscovery.research_loop.storage_policy import StorageQuotaExceeded


def git(root, *arguments, allowed=(0,)):
    environment = dict(PATH=os.defpath, HOME=os.devnull, GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid', LC_ALL='C')
    result = subprocess.run(['git', '-C', str(root), *arguments], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert result.returncode in allowed, result.stderr.decode(errors='replace')
    return result


def project(tmp_path, object_format='sha1'):
    root = tmp_path / 'base'
    root.mkdir()
    initialized = git(root, 'init', '--quiet', '--template=', '--object-format=' + object_format,
                      allowed=(0, 128, 129))
    if initialized.returncode:
        pytest.skip('Host Git does not support requested object format')
    (root / 'src').mkdir()
    (root / 'tools').mkdir()
    (root / 'src/main.py').write_bytes(b'print("original")\n')
    (root / 'tools/helper.py').write_bytes(b'VALUE = 1\n')
    (root / 'data.txt').write_bytes(b'base data\n')
    git(root, 'add', '--', 'src/main.py', 'tools/helper.py', 'data.txt')
    git(root, 'commit', '--quiet', '-m', 'base')
    return dict(base_root=str(root), editable=['src', 'tools'], scratch=['build'], outputs=['out'])


def version(*, logical_root='project', entry='src/main.py', selection='directory'):
    return dict(version=1, object_format='sha1', commit='a' * 40, tree_path='.',
                logical_root=logical_root, entry=entry, selection=selection)


@pytest.mark.parametrize('changes', [
    dict(base_root='relative'), dict(editable=[]), dict(editable=['.']),
    dict(editable=['src/../tools']), dict(editable=['src//sub']),
    dict(editable=['src\\sub']), dict(editable=['/src']),
    dict(editable=['src/.git']), dict(editable=['src', 'src/sub']),
    dict(scratch=['src']), dict(outputs=['tools/out']),
    dict(extra=True), dict(base_identity=dict(object_format='sha1', commit='a' * 40)),
])
def test_configuration_rejects_invalid_declarations(changes):
    with pytest.raises(ValueError):
        layout.configuration({**dict(base_root='/project', editable=['src', 'tools']), **changes})


def test_configuration_and_code_mapping_are_pure(tmp_path):
    assert layout.configuration(None) is None
    assert layout.code_roots(None, tmp_path)['root'] == tmp_path / 'code'
    declaration = dict(base_root='/absent/base', editable=['src', 'tools'])
    frozen = {**layout.configuration(declaration),
              'base_identity': dict(object_format='sha1', commit='a' * 40)}
    mapping = layout.code_roots(frozen, tmp_path)
    assert mapping == dict(root=tmp_path / 'project', logical_root='project', editable=[
        dict(relative='src', source=tmp_path / 'project/src', target=Path('/absent/base/src')),
        dict(relative='tools', source=tmp_path / 'project/tools', target=Path('/absent/base/tools'))])
    assert layout.prepare(declaration, protected=[tmp_path / 'instance'], existing=frozen) == frozen
    with pytest.raises(ValueError, match='immutable'):
        layout.prepare({**declaration, 'scratch': ['build']}, protected=[], existing=frozen)


def test_existing_metadata_rejects_protected_alias_inside_base(tmp_path):
    base = tmp_path / 'absent-base'
    declaration = dict(base_root=str(base), editable=['src'])
    frozen = {**layout.configuration(declaration),
              'base_identity': dict(object_format='sha1', commit='a' * 40)}
    protected_alias = tmp_path / 'native-home-alias'
    protected_alias.symlink_to(base / 'native-home', target_is_directory=True)
    with pytest.raises(ValueError, match='overlaps a protected'):
        layout.prepare(declaration, protected=[protected_alias], existing=frozen)


@pytest.mark.parametrize('dimension', ['max_files', 'max_scan_entries'])
def test_seed_inventory_shares_limits_and_counts_implicit_and_empty_directories(monkeypatch, dimension):
    from scidiscovery.research_loop import code_versions
    project = dict(editable=['src', 'tools', 'empty/deep'])
    records = [dict(relative='src/nested/main.py', signature=(0, 0, 0, 7)),
               dict(relative='tools/helper.py', signature=(0, 0, 0, 5))]
    limits = dict(max_file_bytes=7, max_total_bytes=12, max_files=2, max_scan_entries=7)
    monkeypatch.setattr(code_versions, 'DEFAULT_LIMITS', limits)
    assert layout.seed_directories(project, records) == {'src', 'src/nested', 'tools', 'empty', 'empty/deep'}
    monkeypatch.setattr(code_versions, 'DEFAULT_LIMITS', {**limits, dimension: limits[dimension] - 1})
    with pytest.raises(StorageQuotaExceeded, match=dimension):
        layout.seed_directories(project, records)


def test_seed_inventory_keeps_exact_sizes_without_legacy_byte_quota(monkeypatch):
    from scidiscovery.research_loop import code_versions
    monkeypatch.setattr(code_versions, 'DEFAULT_LIMITS',
        dict(max_file_bytes=1, max_total_bytes=1, max_files=2, max_scan_entries=4))
    records = [dict(relative='src/main.py', signature=(0, 0, 0, 7)),
               dict(relative='tools/helper.py', signature=(0, 0, 0, 5))]
    assert layout.seed_directories(dict(editable=['src', 'tools']), records) == {'src', 'tools'}
    assert [row['signature'][3] for row in records] == [7, 5]


def test_validate_code_preserves_legacy_file_and_project_boundaries():
    frozen = dict(base_root='/base', editable=['src', 'tools'], scratch=[], outputs=[],
                  base_identity=dict(object_format='sha1', commit='a' * 40))
    legacy = version(logical_root='code/subdirectory', entry='implementation.json', selection='file')
    assert layout.validate_code(None, legacy, purpose='run') is legacy
    current = version()
    assert layout.validate_code(frozen, current, purpose='run',
                                paths=['src/main.py', 'tools/helper.py']) is current
    assert layout.validate_code(frozen, version(selection='file'), purpose='compiler')
    for invalid in (version(logical_root='code'), version(entry='data/input'), version(selection='file')):
        with pytest.raises(ValueError):
            layout.validate_code(frozen, invalid, purpose='run')
    with pytest.raises(ValueError, match='outside'):
        layout.validate_code(frozen, current, purpose='run', paths=['src/main.py', 'unselected.py'])
    with pytest.raises(ValueError, match='absent'):
        layout.validate_code(frozen, current, purpose='run', paths=['tools/helper.py'])


@pytest.mark.process_e2e
@pytest.mark.parametrize('object_format', ['sha1', 'sha256'])
def test_freezes_head_and_roundtrips(tmp_path, object_format):
    declaration = project(tmp_path, object_format)
    original = copy.deepcopy(declaration)
    frozen = layout.prepare(declaration, protected=[tmp_path / 'instance'])
    assert declaration == original
    assert frozen['base_identity'] == dict(object_format=object_format,
        commit=git(Path(declaration['base_root']), 'rev-parse', 'HEAD').stdout.decode().strip())
    rows = layout.files(frozen)
    assert {row['relative'] for row in rows} == {'data.txt', 'src/main.py', 'tools/helper.py'}
    assert all(row['source'] == Path(declaration['base_root']) / row['relative'] for row in rows)
    assert layout.prepare(declaration, protected=[tmp_path / 'other'], existing=frozen) == frozen


@pytest.mark.process_e2e
def test_rejects_overlap_and_symlinks(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    for protected in ([root / 'instance'], [root.parent]):
        with pytest.raises(ValueError, match='overlaps'):
            layout.prepare(declaration, protected=protected)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='real directory'):
        layout.prepare({**declaration, 'base_root': str(alias)}, protected=[])
    (root / 'linked').symlink_to('data.txt')
    git(root, 'add', '--', 'linked')
    git(root, 'commit', '--quiet', '-m', 'symlink')
    with pytest.raises(ValueError, match='symlink/gitlink'):
        layout.prepare(declaration, protected=[])


@pytest.mark.process_e2e
@pytest.mark.parametrize('flag', ['--assume-unchanged', '--skip-worktree'])
def test_worktree_checks_respect_normal_index_flags(tmp_path, flag):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    git(root, 'update-index', flag, '--', 'data.txt')
    (root / 'data.txt').write_bytes(b'drift hidden from status\n')
    assert {row['relative'] for row in layout.files(frozen)} == {
        'data.txt', 'src/main.py', 'tools/helper.py'}


@pytest.mark.process_e2e
def test_filtered_worktree_uses_normal_git_semantics(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    git(root, 'config', 'core.autocrlf', 'input')
    (root / 'data.txt').write_bytes(b'CRLF data\r\n')
    git(root, 'add', '--', 'data.txt')
    git(root, 'commit', '--quiet', '-m', 'normalized bytes')
    assert git(root, 'show', 'HEAD:data.txt').stdout == b'CRLF data\n'
    frozen = layout.prepare(declaration, protected=[])
    assert {row['relative'] for row in layout.files(frozen)} == {
        'data.txt', 'src/main.py', 'tools/helper.py'}


@pytest.mark.process_e2e
@pytest.mark.parametrize('operation', ['files', 'verify_base'])
def test_repeated_views_do_not_open_read_or_hash_base_files(tmp_path, monkeypatch, operation):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    paths = {root / name for name in ('data.txt', 'src/main.py', 'tools/helper.py')}
    unchanged = (root / 'data.txt').stat()
    os.utime(root / 'data.txt', ns=(unchanged.st_atime_ns, unchanged.st_mtime_ns + 1_000_000_000))
    identities = {(path.stat().st_dev, path.stat().st_ino) for path in paths}
    original_open, original_read, original_path_open = os.open, os.read, Path.open

    def no_source_open(path, *args, **kwargs):
        assert not isinstance(path, (str, bytes, os.PathLike)) or Path(os.fsdecode(path)) not in paths
        return original_open(path, *args, **kwargs)

    def no_source_read(descriptor, *args):
        info = os.fstat(descriptor)
        assert (info.st_dev, info.st_ino) not in identities
        return original_read(descriptor, *args)

    def no_source_path_open(path, *args, **kwargs):
        assert path not in paths
        return original_path_open(path, *args, **kwargs)

    def no_hash(*args, **kwargs):
        raise AssertionError('Project checks must not hash base file bytes in Python')

    def no_source(*args, **kwargs):
        raise AssertionError('Base verification must not inspect each source')

    def no_manifest(*args, **kwargs):
        raise AssertionError('Project checks must not create a manifest')

    monkeypatch.setattr(os, 'open', no_source_open)
    monkeypatch.setattr(os, 'read', no_source_read)
    monkeypatch.setattr(Path, 'open', no_source_path_open)
    monkeypatch.setattr(Path, 'write_bytes', no_manifest)
    monkeypatch.setattr(Path, 'write_text', no_manifest)
    monkeypatch.setattr(hashlib, 'new', no_hash)
    if operation == 'verify_base':
        monkeypatch.setattr(layout, '_source', no_source)
    # Real Git subprocesses may read/hash stat-changed files themselves. Python
    # only consumes bounded Git pipe output and checks exact source metadata.
    for _ in range(3):
        result = getattr(layout, operation)(frozen)
        if operation == 'verify_base':
            assert result is None
        else:
            assert {row['relative'] for row in result} == {
                'data.txt', 'src/main.py', 'tools/helper.py'}


@pytest.mark.process_e2e
def test_untracked_and_ignored_not_in_seed_rows(tmp_path, monkeypatch):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    (root / '.git/info').mkdir(exist_ok=True)
    (root / '.git/info/exclude').write_text('private.key\n')
    (root / 'private.key').write_bytes(b'ignored secret')
    (root / 'untracked').mkdir()
    (root / 'untracked/secret').write_bytes(b'untracked secret')
    # Poisoned inherited Git settings cannot redirect the product subprocess.
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'wrong'))
    monkeypatch.setenv('GIT_INDEX_FILE', str(tmp_path / 'wrong-index'))
    frozen = layout.prepare(declaration, protected=[])
    assert {row['relative'] for row in layout.files(frozen)} == {
        'data.txt', 'src/main.py', 'tools/helper.py'}


@pytest.mark.process_e2e
def test_prepare_seed_ignores_large_readonly_and_untracked_files_without_python_reads(tmp_path, monkeypatch):
    from scidiscovery.research_loop import code_versions
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    oversized = code_versions.DEFAULT_LIMITS['max_file_bytes'] + 1
    # Sparse, bounded files exercise the current limit without a large fixture.
    paths = [root / name for name in ('data.txt', 'src/ignored.py', 'tools/untracked.py')]
    for path in paths:
        with path.open('wb') as stream:
            stream.truncate(oversized)
    (root / '.git/info').mkdir(exist_ok=True)
    (root / '.git/info/exclude').write_text('src/ignored.py\n')
    git(root, 'add', '--', 'data.txt')
    git(root, 'commit', '--quiet', '-m', 'large readonly base file')
    source = layout._source
    original_open, original_path_open = os.open, Path.open
    base_files = {*paths, root / 'src/main.py', root / 'tools/helper.py'}

    def editable_source(base, name, *args):
        assert name in {'src/main.py', 'tools/helper.py'}
        return source(base, name, *args)

    def no_source_open(path, *args, **kwargs):
        assert not isinstance(path, (str, bytes, os.PathLike)) or Path(os.fsdecode(path)) not in base_files
        return original_open(path, *args, **kwargs)

    def no_source_path_open(path, *args, **kwargs):
        assert path not in base_files
        return original_path_open(path, *args, **kwargs)

    def no_hash(*args, **kwargs):
        raise AssertionError('Initial seed preflight must not hash base file bytes')

    monkeypatch.setattr(layout, '_source', editable_source)
    monkeypatch.setattr(os, 'open', no_source_open)
    monkeypatch.setattr(Path, 'open', no_source_path_open)
    monkeypatch.setattr(hashlib, 'new', no_hash)
    frozen = layout.prepare(declaration, protected=[tmp_path / 'instance'])
    assert {row['relative'] for row in layout.seed_files(frozen)} == {'src/main.py', 'tools/helper.py'}
    assert not (tmp_path / 'instance').exists()


@pytest.mark.process_e2e
def test_drift_and_missing_exact_path(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    (root / 'data.txt').unlink()
    with pytest.raises(FileNotFoundError):
        layout.files(frozen)
    (root / 'data.txt').write_bytes(b'ordinary dirty data\n')
    with pytest.raises(ValueError, match='worktree differs'):
        layout.files(frozen)
    (root / 'data.txt').write_bytes(b'base data\n')
    (root / 'src/main.py').write_bytes(b'changed code\n')
    # The hidden host draft cannot replace the selected code commit at run.
    assert [row['relative'] for row in layout.files(frozen, exclude=['src', 'tools'])] == ['data.txt']
    git(root, 'add', '--', 'src/main.py')
    with pytest.raises(ValueError, match='index differs'):
        layout.files(frozen, exclude=['src', 'tools'])
    git(root, 'commit', '--quiet', '-m', 'new HEAD')
    with pytest.raises(ValueError, match='HEAD changed'):
        layout.files(frozen)


@pytest.mark.process_e2e
@pytest.mark.parametrize('exclude', [('src', 'tools', 'build', 'out'), ('build', 'out')])
def test_excluded_mount_sources_do_not_make_view_dirty(tmp_path, exclude):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    for name in ('build', 'out'):
        (root / name).mkdir()
        (root / name / 'tracked.txt').write_bytes(b'tracked mount source\n')
    git(root, 'add', '--', 'build/tracked.txt', 'out/tracked.txt')
    git(root, 'commit', '--quiet', '-m', 'tracked mount roots')
    frozen = layout.prepare(declaration, protected=[])
    for name in exclude:
        source = root / name
        source.rename(root / ('hidden-' + name))
        source.symlink_to('hidden-' + name, target_is_directory=True)
    expected = {'data.txt'} if 'src' in exclude else {
        'data.txt', 'src/main.py', 'tools/helper.py'}
    assert {row['relative'] for row in layout.files(frozen, exclude=exclude)} == expected


@pytest.mark.process_e2e
def test_later_stat_cannot_hide_earlier_file_replacement(tmp_path, monkeypatch):
    declaration = project(tmp_path)
    frozen = layout.prepare(declaration, protected=[])
    original = layout._source
    def replace_earlier(root, name, *args):
        row = original(root, name, *args)
        if name == 'tools/helper.py':
            replacement = root / 'replacement.txt'
            replacement.write_bytes(b'base data\n')
            replacement.replace(root / 'data.txt')
        return row
    monkeypatch.setattr(layout, '_source', replace_earlier)
    with pytest.raises(ValueError, match='changed during verification'):
        layout.files(frozen)


@pytest.mark.process_e2e
def test_rejects_entry_count_and_expired_deadline(tmp_path, monkeypatch):
    declaration = project(tmp_path)
    monkeypatch.setattr(layout, 'MAX_ENTRIES', 2)
    with pytest.raises(StorageQuotaExceeded, match='tracked entries'):
        layout.prepare(declaration, protected=[])
    class Expired:
        def remaining_seconds(self):
            raise TimeoutError('original deadline expired')
    with pytest.raises(TimeoutError, match='deadline expired'):
        layout.prepare(declaration, protected=[], context=Expired())


@pytest.mark.process_e2e
def test_rejects_unmerged_index_and_gitlink(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    blob = git(root, 'rev-parse', 'HEAD:data.txt').stdout.strip().decode()
    # Existing blob, distinct stages: no merge process or worktree discovery.
    payload = ('0 ' + '0' * 40 + '\tdata.txt\n' +
               '100644 ' + blob + ' 1\tdata.txt\n' +
               '100644 ' + blob + ' 2\tdata.txt\n').encode()
    environment = dict(PATH=os.defpath, HOME=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(['git', '-C', str(root), 'update-index', '--index-info'], env=environment,
                   input=payload, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    with pytest.raises(ValueError, match='unmerged'):
        layout.files(frozen)
    git(root, 'reset', '--quiet', 'HEAD', '--', 'data.txt')
    git(root, 'update-index', '--add', '--cacheinfo',
        '160000,' + frozen['base_identity']['commit'] + ',submodule')
    with pytest.raises(ValueError, match='symlink/gitlink'):
        layout.files(frozen)


@pytest.mark.process_e2e
def test_rejects_executable_drift_and_replaced_parent(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    (root / 'src/main.py').chmod(0o755)
    with pytest.raises(ValueError, match='executable mode changed'):
        layout.files(frozen)
    (root / 'src/main.py').chmod(0o644)
    (root / 'src').rename(root / 'original-src')
    (root / 'src').symlink_to('original-src', target_is_directory=True)
    with pytest.raises(ValueError, match='real directory ancestors'):
        layout.files(frozen)


@pytest.mark.process_e2e
def test_existing_configuration_retains_identity_after_base_removal(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    root.rename(tmp_path / 'removed-base')
    assert layout.prepare(declaration, protected=[tmp_path / 'child'], existing=frozen) == frozen
    with pytest.raises(FileNotFoundError):
        layout.files(frozen)


@pytest.mark.process_e2e
def test_missing_promisor_object_cannot_invoke_remote_helper(tmp_path, monkeypatch):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    tree = git(root, 'rev-parse', 'HEAD^{tree}').stdout.decode().strip()
    sentinel = tmp_path / 'remote-helper-ran'
    helper = tmp_path / 'fake-promisor-helper.py'
    helper.write_text('from pathlib import Path\nPath(' + repr(str(sentinel)) +
                      ').write_text("unexpected remote helper")\nraise SystemExit(1)\n')
    # ext transport arguments use their own percent escaping, with no shell.
    remote = 'ext::' + ' '.join(value.replace('%', '%%').replace(' ', '% ')
                                for value in (sys.executable, str(helper)))
    git(root, 'config', 'core.repositoryformatversion', '1')
    git(root, 'config', 'extensions.partialClone', 'origin')
    git(root, 'config', 'remote.origin.promisor', 'true')
    git(root, 'config', 'remote.origin.partialclonefilter', 'blob:none')
    git(root, 'config', 'remote.origin.url', remote)
    git(root, 'config', 'protocol.ext.allow', 'always')
    (root / '.git/objects' / tree[:2] / tree[2:]).unlink()
    # Product isolation must override permissive inherited and repository
    # settings. The fake helper has no network or external submission code.
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'ext:https:ssh:file')
    monkeypatch.setenv('GIT_NO_LAZY_FETCH', '0')
    with pytest.raises(ValueError, match='Project Git verification failed: ls-tree'):
        layout.files(frozen)
    assert not sentinel.exists()
