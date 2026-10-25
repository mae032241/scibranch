"""Project views use exact Git names and raw bytes, without worktree discovery."""
import copy
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
def test_raw_blob_identity_ignores_index_flags(tmp_path, flag):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    git(root, 'update-index', flag, '--', 'data.txt')
    (root / 'data.txt').write_bytes(b'drift hidden from status\n')
    with pytest.raises(ValueError, match='raw bytes.*data.txt'):
        layout.files(frozen)


@pytest.mark.process_e2e
def test_filtered_worktree_bytes_rejected(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    git(root, 'config', 'core.autocrlf', 'input')
    (root / 'data.txt').write_bytes(b'CRLF data\r\n')
    git(root, 'add', '--', 'data.txt')
    git(root, 'commit', '--quiet', '-m', 'normalized bytes')
    assert git(root, 'show', 'HEAD:data.txt').stdout == b'CRLF data\n'
    with pytest.raises(ValueError, match='raw bytes.*CRLF/filter'):
        layout.prepare(declaration, protected=[])


@pytest.mark.process_e2e
def test_untracked_and_ignored_not_visible(tmp_path, monkeypatch):
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
def test_drift_and_missing_exact_path(tmp_path):
    declaration = project(tmp_path)
    root = Path(declaration['base_root'])
    frozen = layout.prepare(declaration, protected=[])
    (root / 'data.txt').unlink()
    with pytest.raises(FileNotFoundError):
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
def test_later_read_cannot_hide_earlier_file_replacement(tmp_path, monkeypatch):
    declaration = project(tmp_path)
    frozen = layout.prepare(declaration, protected=[])
    original = layout._raw
    def replace_earlier(root, name, *args):
        row = original(root, name, *args)
        if name == 'tools/helper.py':
            (root / 'data.txt').write_bytes(b'changed after earlier hash\n')
        return row
    monkeypatch.setattr(layout, '_raw', replace_earlier)
    with pytest.raises(ValueError, match='changed during verification'):
        layout.files(frozen)


@pytest.mark.process_e2e
def test_rejects_metadata_capacity_and_expired_deadline(tmp_path, monkeypatch):
    declaration = project(tmp_path)
    monkeypatch.setattr(layout, 'MAX_METADATA_BYTES', 5)
    with pytest.raises(StorageQuotaExceeded, match='metadata capability'):
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
