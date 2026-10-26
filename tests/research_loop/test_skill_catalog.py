"""Explicit configuration catalogs read installed metadata without selecting it."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import skill_supply


def bundle(source, name, description=None):
    directory = source / name
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: {description or name + " fixture"}\n---\n', encoding='utf-8')
    return directory


def installed(monkeypatch, source):
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)


def test_catalog_lists_native_names_and_descriptions_in_order(tmp_path, monkeypatch):
    source = tmp_path / 'skills'
    bundle(source, 'zeta', 'Use the last fixture.')
    bundle(source, 'alpha', 'Use the first fixture.')
    (source / 'README.md').write_text('Installation notes, not a Skill.')
    installed(monkeypatch, source)
    assert skill_supply.catalog() == dict(items=[
        dict(name='alpha', description='Use the first fixture.'),
        dict(name='zeta', description='Use the last fixture.')], errors=[])


@pytest.mark.parametrize('damage', ['missing_manual', 'frontmatter', 'name', 'utf8', 'oversized'])
def test_catalog_keeps_valid_neighbors_and_reports_damaged_bundle(tmp_path, monkeypatch, damage):
    source = tmp_path / 'skills'
    directory = bundle(source, 'broken')
    manual = directory / 'SKILL.md'
    if damage == 'missing_manual':
        manual.unlink()
    elif damage == 'frontmatter':
        manual.write_text('No frontmatter')
    elif damage == 'name':
        manual.write_text('---\nname: another\ndescription: Wrong native name\n---\n')
    elif damage == 'utf8':
        manual.write_bytes(b'---\nname: broken\ndescription: \xff\n---\n')
    else:
        manual.write_text('x' * 256_001)
    bundle(source, 'working')
    installed(monkeypatch, source)
    result = skill_supply.catalog()
    assert result['items'] == [dict(name='working', description='working fixture')]
    assert len(result['errors']) == 1
    assert result['errors'][0]['name'] == 'broken'
    assert result['errors'][0]['message']


@pytest.mark.parametrize('link', ['directory', 'manual', 'root'])
def test_catalog_rejects_linked_installation_assets(tmp_path, monkeypatch, link):
    source = tmp_path / 'skills'
    directory = bundle(source, 'alpha')
    outside = bundle(tmp_path / 'outside', 'other')
    if link == 'directory':
        (source / 'linked').symlink_to(outside, target_is_directory=True)
    elif link == 'manual':
        (directory / 'SKILL.md').unlink()
        (directory / 'SKILL.md').symlink_to(outside / 'SKILL.md')
    else:
        linked = tmp_path / 'linked-root'
        linked.symlink_to(source, target_is_directory=True)
        source = linked
    installed(monkeypatch, source)
    result = skill_supply.catalog()
    assert [row['name'] for row in result['errors']] == [dict(
        directory='linked', manual='alpha', root='skills')[link]]
    assert [row['name'] for row in result['items']] == (['alpha'] if link == 'directory' else [])


@pytest.mark.parametrize('kind', ['missing', 'file', 'empty', 'unreadable'])
def test_catalog_returns_diagnostics_without_failing_configuration(tmp_path, monkeypatch, kind):
    source = tmp_path / 'skills'
    if kind == 'file':
        source.write_text('not a directory')
    elif kind in {'empty', 'unreadable'}:
        source.mkdir()
    installed(monkeypatch, source)
    if kind == 'unreadable':
        def denied(path):
            assert path == source
            raise PermissionError('fixture directory is unreadable')
        monkeypatch.setattr(skill_supply.os, 'scandir', denied)
    result = skill_supply.catalog()
    assert result['items'] == []
    if kind == 'empty':
        assert result['errors'] == []
    else:
        assert [row['name'] for row in result['errors']] == ['skills']
        assert str(source) in result['errors'][0]['message']
        assert 'install or restore' in result['errors'][0]['message']


def test_catalog_reads_only_native_manuals_and_never_descends(tmp_path, monkeypatch):
    source = tmp_path / 'skills'
    directory = bundle(source, 'alpha')
    (directory / 'resources').mkdir()
    (directory / 'resources' / 'private.txt').write_text('Not catalog evidence')
    installed(monkeypatch, source)
    original_read = Path.read_text
    original_scandir = skill_supply.os.scandir
    reads, scans = [], []
    def read_text(path, *args, **kwargs):
        assert path == directory / 'SKILL.md'
        reads.append(path)
        return original_read(path, *args, **kwargs)
    def scandir(path):
        assert path == source
        scans.append(path)
        return original_scandir(path)
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(skill_supply.os, 'scandir', scandir)
    assert skill_supply.catalog()['errors'] == []
    assert reads == [directory / 'SKILL.md']
    assert scans == [source]


def test_catalog_has_a_fixed_entry_bound(tmp_path, monkeypatch):
    source = tmp_path / 'skills'
    source.mkdir()
    installed(monkeypatch, source)
    observed = []
    class Entry:
        def __init__(self, index):
            self.name = f'fixture-{index}'
        def is_symlink(self):
            return False
        def is_dir(self, *, follow_symlinks):
            assert follow_symlinks is False
            return True
    class Entries:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def __iter__(self):
            for index in range(10_000):
                observed.append(index)
                yield Entry(index)
    monkeypatch.setattr(skill_supply.os, 'scandir', lambda path: Entries())
    monkeypatch.setattr(skill_supply, '_metadata',
        lambda path: dict(name=path.name, description='Bounded fixture'))
    result = skill_supply.catalog()
    assert len(result['items']) == 256
    assert len(observed) == 257
    assert [row['name'] for row in result['errors']] == ['skills']
    assert 'limited to 256' in result['errors'][0]['message']


def test_catalog_does_not_enable_or_bind_discovered_skills(tmp_path, monkeypatch):
    source = tmp_path / 'skills'
    bundle(source, 'alpha')
    installed(monkeypatch, source)
    def unexpected(*args, **kwargs):
        pytest.fail('Catalog must not select roles, launch Workers, or prepare instance state')
    monkeypatch.setattr(skill_supply, '_selected', unexpected)
    monkeypatch.setattr(skill_supply, '_launcher_contract', unexpected)
    monkeypatch.setattr(skill_supply, 'freeze', unexpected)
    assert skill_supply.catalog()['items'] == [dict(name='alpha', description='alpha fixture')]
    assert skill_supply.configuration() == dict(sets={})
    assert skill_supply.prepare(None, backend='protocol', launcher=['custom']) == (None, None)
