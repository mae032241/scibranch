"""Hash-free originals, explicit alias ownership, and retryable file publication."""
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from scidiscovery import archive_io as files
from scidiscovery.research_loop import (atomic_files, contracts as c,
    instance_archive as archive, skill_supply, storage_policy as storage)


@pytest.fixture(autouse=True)
def forbid_content_hash(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Scientific original/Skill/archive work must not hash bytes')
    original = hashlib.sha256
    monkeypatch.setattr(hashlib, 'sha256', denied)
    return original


def frozen(path, payload=b'scientific original'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o400)
    return path


def test_link_requires_exact_inode_and_copy_rejects_equal_size_collision(tmp_path):
    source = frozen(tmp_path / 'source')
    target = frozen(tmp_path / 'foreign', source.read_bytes())
    entry = dict(kind='file', size_bytes=source.stat().st_size, mode=0o400)
    with pytest.raises(files.ArchiveError, match='exact source inode'):
        files.link_original(source, target, entry)
    with pytest.raises(FileExistsError):
        files.copy_original(source, target, entry)
    assert not source.samefile(target)
    alias = tmp_path / 'alias'
    files.link_original(source, alias, entry)
    assert alias.samefile(source)


def test_selected_skill_sources_are_shared_installation_assets_without_hash_or_copy(tmp_path, monkeypatch):
    source = tmp_path / 'library/alpha'
    frozen(source / 'SKILL.md', b'---\nname: alpha\ndescription: original fixture\n---\n')
    frozen(source / 'scripts/run.py', b'print("science")\n')
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source.parent)
    configured_cache = tmp_path / 'must-not-be-created'
    monkeypatch.setenv('SCID_SHARED_STORE', str(configured_cache))
    for root in (tmp_path / 'first', tmp_path / 'second'):
        root.mkdir()
        declared, manifest = skill_supply.prepare(dict(sets={'chosen': dict(items=['alpha'])}),
            backend='codex', launcher=['codex'], role_profiles={'H': dict(skill_set='chosen')})
        value = dict(skills=declared, native_launcher_argv=['codex'],
            role_profiles={'H': dict(skill_set='chosen')})
        skill_supply.freeze(root, value, manifest)
        assert manifest is None and declared['version'] == 3
        assert skill_supply.validate(root, value) == declared
        assert not (root / skill_supply.DIRECTORY).exists()
        assert declared['root'] == str(source.parent)
    assert not configured_cache.exists()


def archive_fixture(tmp_path, monkeypatch, *, independent):
    root = tmp_path / 'instance'; root.mkdir()
    settings = dict(goal_id='exact-instance', public_root=str(tmp_path / 'public'), native_launcher_argv=['codex'])
    (root / 'settings.json').write_text(json.dumps(settings))
    original = frozen(root / 'project/global/original.dat')
    alias = original.with_name('alias.dat'); os.link(original, alias)
    distinct = frozen(original.with_name('distinct.dat'), original.read_bytes())
    monkeypatch.setattr(c, 'settings', lambda *args, **kwargs: settings)
    monkeypatch.setattr(archive, '_owner_locks', lambda *args, **kwargs: None)
    monkeypatch.setattr(archive, '_quiescent', lambda *args, **kwargs: None)
    monkeypatch.setattr(archive, '_configuration_dependencies', lambda *args, **kwargs: iter(()))
    bindings = {path: dict(size_bytes=path.stat().st_size) for path in (original, distinct)}
    monkeypatch.setattr(archive, '_immutable_bindings', lambda *args: bindings)
    package = tmp_path / 'package'
    archive._export(root, package, independent=independent)
    rows = [json.loads(line) for line in (package / 'manifest.jsonl').read_text().splitlines()]
    return root, package, {row['path']: row for row in rows}, original


@pytest.mark.parametrize('independent', [False, True])
def test_archive_uses_source_aliases_without_content_dedup(tmp_path, monkeypatch, independent):
    root, package, rows, source = archive_fixture(tmp_path, monkeypatch, independent=independent)
    original = rows['project/global/original.dat']
    alias = rows['project/global/alias.dat']
    distinct = rows['project/global/distinct.dat']
    assert original['storage'] == alias['storage'] == 'immutable'
    assert original['blob'] == alias['blob']
    assert original['blob'] != distinct['blob']
    assert (package / original['blob']).samefile(source) == (not independent)
    assert all('source_stat' not in row and 'sha256' not in row for row in rows.values())
    assert not (root / 'host-history/fork-cache').exists()
    result = archive.archive_inspect(package)
    assert result['status'] == 'archive_inspected'
    assert result['unique_blobs'] == 3  # settings + one alias group + distinct original


def test_archive_inspect_reads_metadata_without_opening_scientific_blobs(tmp_path, monkeypatch):
    _, package, rows, _ = archive_fixture(tmp_path, monkeypatch, independent=True)
    payloads = {package / row['blob'] for row in rows.values()
        if row.get('kind') == 'file' and row['path'] != 'settings.json'}
    original_open = Path.open
    def metadata_only(path, *args, **kwargs):
        if path in payloads:
            raise AssertionError('Archive inspection opened a scientific payload')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', metadata_only)
    assert archive.archive_inspect(package)['status'] == 'archive_inspected'


def material_fixture(tmp_path):
    root = tmp_path / 'instance'; root.mkdir()
    blob = frozen(tmp_path / 'archive/blobs/000000000001')
    target = root / 'host-history/objects/original-object'
    entry = dict(kind='file', mode=0o400, size_bytes=blob.stat().st_size)
    request = dict(root=str(root), step=1, index=1, package=str(blob.parents[1]),
        blob='blobs/000000000001', source=str(target), producer=dict(path=str(root / 'seal.json')),
        ownership='independent')
    return root, target, blob, entry, request


@pytest.mark.parametrize('crash', ['before_publish', 'after_publish'])
def test_independent_material_restore_recognizes_only_its_prepared_inode(tmp_path, monkeypatch, crash):
    root, target, blob, entry, request = material_fixture(tmp_path)
    copied = []
    original_copy = atomic_files.copy_original
    def counted(*args, **kwargs):
        copied.append(args[1]); return original_copy(*args, **kwargs)
    monkeypatch.setattr(atomic_files, 'copy_original', counted)
    with monkeypatch.context() as patch:
        if crash == 'before_publish':
            original_link = files.link_original
            def interrupted(source, destination, entry, **kwargs):
                if destination == target: raise OSError('fixture crash before publication')
                return original_link(source, destination, entry, **kwargs)
            patch.setattr(files, 'link_original', interrupted)
        else:
            original_json = files.durable_json
            def interrupted(path, value):
                if value.get('phase') == 'published': raise OSError('fixture crash after publication')
                return original_json(path, value)
            patch.setattr(files, 'durable_json', interrupted)
        with pytest.raises(OSError, match='fixture crash'):
            archive._restore_material_original(root, target, blob, entry, request,
                context=storage.Deadline(10))
    archive._restore_material_original(root, target, blob, entry, request, context=storage.Deadline(10))
    assert not target.samefile(blob)
    assert len(copied) == 1
    assert archive._restore_material_original(root, target, blob, entry, request,
        context=storage.Deadline(10)) is False
    assert len(copied) == 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o400


def test_independent_material_restore_rejects_foreign_same_size_destination(tmp_path):
    root, target, blob, entry, request = material_fixture(tmp_path)
    frozen(target, blob.read_bytes())
    with pytest.raises(files.ArchiveError, match='not this restore publication'):
        archive._restore_material_original(root, target, blob, entry, request,
            context=storage.Deadline(10))
    assert not target.samefile(blob)


@pytest.mark.parametrize('independent', [False, True])
def test_archive_keeps_managed_object_and_input_alias_in_one_immutable_group(tmp_path, monkeypatch, independent):
    root = tmp_path / 'instance'; root.mkdir()
    settings = dict(goal_id='exact-instance', public_root=str(tmp_path / 'public'), native_launcher_argv=['codex'])
    (root / 'settings.json').write_text(json.dumps(settings))
    original = frozen(root / 'host-history/objects/original-object')
    alias = root / 'project/nodes/001/inputs/original.dat'
    alias.parent.mkdir(parents=True); os.link(original, alias)
    monkeypatch.setattr(c, 'settings', lambda *args, **kwargs: settings)
    monkeypatch.setattr(archive, '_owner_locks', lambda *args, **kwargs: None)
    monkeypatch.setattr(archive, '_quiescent', lambda *args, **kwargs: None)
    monkeypatch.setattr(archive, '_configuration_dependencies', lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(archive, '_immutable_bindings', lambda *args: {})
    package = tmp_path / 'package'
    archive._export(root, package, independent=independent)
    rows = {row['path']: row for row in map(json.loads, (package / 'manifest.jsonl').read_text().splitlines())}
    managed, projected = [rows[path.relative_to(root).as_posix()] for path in (original, alias)]
    assert managed['storage'] == projected['storage'] == 'immutable'
    assert managed['blob'] == projected['blob']
    restored = tmp_path / 'restored'
    first = restored / original.relative_to(root)
    second = restored / alias.relative_to(root)
    if independent:
        files.copy_independent(package / managed['blob'], first, managed)
        files.link_original(first, second, projected)
        assert not first.samefile(package / managed['blob'])
    else:
        files.link_original(package / managed['blob'], first, managed)
        files.link_original(package / projected['blob'], second, projected)
    assert first.samefile(second)


@pytest.mark.parametrize('change', [
    'entry_added', 'entry_size', 'entry_path', 'entry_visibility',
    'cut', 'permissions', 'inherited_costs', 'directory_inventory',
])
def test_fork_preview_binds_display_and_exact_closure_metadata(tmp_path, monkeypatch, forbid_content_hash, change):
    import copy
    from scidiscovery.research_loop import fork_service, fork_sources
    # Approval binding is the explicit small-control digest whitelist. No source
    # file is opened by this test or by the request digest.
    monkeypatch.setattr(hashlib, 'sha256', forbid_content_hash)
    def no_read(*args, **kwargs):
        raise AssertionError('Fork approval must not read scientific bodies')
    monkeypatch.setattr(Path, 'read_bytes', no_read)
    identity = dict(goal_id='source', settings_ref=dict(path=str(tmp_path / 'source/settings.json')))
    entry = dict(origin_identity=identity, path='jobs/001.json', size_bytes=20,
        mode=0o400, storage_path='inherited/source/jobs/001.json', visibility='host_proof')
    plan = dict(source=dict(origin_identity=identity, step=1,
            job_ref=dict(path='jobs/001.json', size_bytes=20),
            science_task_ref=dict(path='project/nodes/001/science-task.json', size_bytes=30)),
        cut=dict(history=dict(nodes=[dict(step=0)])), entries=[entry],
        inherited_costs=dict(tokens=dict(value=10)),
        selected_settings=dict(model='fixture', native_options=dict(workspace='read-only', network=False, web='disabled', subagents=False)),
        directory_inventory=[], source_paths={'temporary': str(tmp_path / 'temporary-a')},
        source_root=str(tmp_path / 'source'))
    monkeypatch.setattr(fork_sources, 'inspect_source', lambda *args: copy.deepcopy(plan))
    monkeypatch.setattr(fork_sources, 'storage_blockers', lambda *args: [])
    request = dict(parent_root=str(tmp_path / 'source'), child_root=str(tmp_path / 'child'),
        step=1, budget=dict(max_tokens=100, max_seconds=10, max_steps=1), guidance_text=None)
    before = fork_service._inspect(request)[1]['preview_id']
    # Mechanical source locators can change without changing the approved cut.
    plan['source_paths']['temporary'] = str(tmp_path / 'temporary-b')
    assert fork_service._inspect(request)[1]['preview_id'] == before
    if change == 'entry_added':
        plan['entries'].append(dict(entry, path='jobs/002.json', storage_path='inherited/source/jobs/002.json'))
    elif change == 'entry_size':
        plan['entries'][0]['size_bytes'] += 1
    elif change == 'entry_path':
        plan['entries'][0]['path'] = 'jobs/002.json'
    elif change == 'entry_visibility':
        plan['entries'][0]['visibility'] = 'scientific_input'
    elif change == 'cut':
        plan['cut']['history']['nodes'] = []
    elif change == 'permissions':
        plan['selected_settings']['native_options']['network'] = True
    elif change == 'inherited_costs':
        plan['inherited_costs']['tokens']['value'] += 1
    else:
        plan['directory_inventory'].append(dict(origin_identity=identity, entries=[dict(path='new',kind='directory',mode=0o700)]))
    assert fork_service._inspect(request)[1]['preview_id'] != before
