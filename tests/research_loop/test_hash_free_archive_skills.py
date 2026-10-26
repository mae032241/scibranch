"""Hash-free current originals, installed Skill assets, and fork control bindings."""
import hashlib
from pathlib import Path

import pytest

from scidiscovery import archive_io as files
from scidiscovery.research_loop import skill_supply


@pytest.fixture(autouse=True)
def forbid_content_hash(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Scientific originals and Skill assets must not hash bytes')
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
        declared = skill_supply.prepare(dict(sets={'chosen': dict(items=['alpha'])}),
            backend='codex', launcher=['codex'], role_profiles={'H': dict(skill_set='chosen')})
        value = dict(skills=declared, native_launcher_argv=['codex'],
            role_profiles={'H': dict(skill_set='chosen')})
        assert declared['version'] == 3
        assert skill_supply.validate(root, value) == declared
        assert not (root / 'host-control/skills').exists()
        assert declared['root'] == str(source.parent)
    assert not configured_cache.exists()


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
