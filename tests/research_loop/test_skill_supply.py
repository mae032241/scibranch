"""Exact Skill supply through real preparation/lifecycle; no native/model calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from scidiscovery.archive_io import ArchiveError

from scidiscovery.research_loop import (configuration_updates, contracts as c, controller,
    execution, fork_runtime, fork_service, instance_archive as archive, profiles,
    research_config, skill_supply)


DEFAULTS = dict(model='fixture', reasoning_effort='high', service_tier='default')


def library(tmp_path):
    source = tmp_path / 'library'
    for item in ('alpha', 'beta', 'unselected'):
        directory = source / item
        (directory / 'scripts').mkdir(parents=True)
        (directory / 'empty').mkdir()
        (directory / 'SKILL.md').write_text(
            f'---\nname: {item}\ndescription: Selected {item} fixture\n---\nRead scripts/run.py.\n')
        (directory / 'scripts/run.py').write_text(f'print({item!r})\n')
    return source


def prepare(tmp_path, *, source=None, roles=None, workers=None, sets=None, launcher=None, **changes):
    source = library(tmp_path) if source is None else source
    public = tmp_path / 'public'; public.mkdir(exist_ok=True)
    objective = public / 'objective'; objective.write_text('Compare the exact declared inputs.')
    root = tmp_path / 'instance'
    options = dict(max_tokens=2000, max_seconds=60, max_steps=6, search_policy=None,
        experiment_mode='external', **DEFAULTS,
        skills=dict(root=str(source), sets=sets if sets is not None else
            dict(first=dict(items=['alpha']), second=dict(items=['beta']))),
        role_profiles=roles if roles is not None else {'H': dict(skill_set='first')},
        worker_profiles=workers)
    options.update(changes)
    controller.prepare(root, objective, public, launcher or ['codex'], **options)
    return root, source


def _object(root, item='alpha'):
    manifest = skill_supply.validate(root, c.settings(root))
    tree = manifest['objects'][item]
    return root / skill_supply.DIRECTORY / 'objects' / tree['tree_sha256']


def _finish(monkeypatch, root, step, role, parents, *, action):
    # Existing pure-code scientific fixture; retain the new exact intent fields.
    from test_history_handoff import finish
    write = c.write
    def bound(path, value):
        if path.name == 'native-intent.json':
            job = c.read(root / 'jobs' / f'{step:03d}.json')
            value = dict(value, **profiles.binding(configuration_updates.for_job(root, job), job))
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False, cleanup='observed_group_cleaned')
        return write(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', bound)
        return finish(root, step, role, parents, action=action)


def test_readonly_source_freezes_declared_union_once_and_projects_only_each_role(tmp_path, monkeypatch):
    source = library(tmp_path)
    # A bad unselected tree is outside the finite declaration and is never read.
    (source / 'unselected' / 'escape').symlink_to(tmp_path)
    for item in ('alpha', 'beta'):
        for path in (source / item / 'SKILL.md', source / item / 'scripts/run.py'):
            path.chmod(0o444)
        (source / item).chmod(0o555)
    source.chmod(0o555)
    root, _ = prepare(tmp_path, source=source,
        roles={'H': dict(skill_set='first'), 'W': dict(worker='analyst')},
        workers={'analyst': dict(model='another-model', skill_set='second')})
    h, _, _ = _finish(monkeypatch, root, 1, 'H', [], action='experiment')
    w = c.build_job(root, 2, 'W', [])
    settings = c.settings(root)
    assert set(skill_supply.validate(root, settings)['objects']) == {'alpha', 'beta'}
    for job, item in ((h, 'alpha'), (w, 'beta')):
        projection = c.workspace(root, job['step']) / '.agents/skills'
        assert {path.name for path in projection.iterdir()} == {item}
        assert (projection / item).resolve() == _object(root, item)
        assert (projection / item / 'empty').is_dir()
        assert not (projection / item).readlink().is_absolute()
        assert stat.S_IMODE((projection / item / 'SKILL.md').stat().st_mode) == 0o400
        assert 'skill_binding' not in c.read(c.workspace(root, job['step']) / 'science-task.json')
    assert w['native_profile']['model'] == 'another-model'
    assert w['skill_binding']['worker'] == 'analyst'
    assert h['skill_binding']['selection_sha256'] != w['skill_binding']['selection_sha256']
    assert set((_object(root).parent).iterdir()) == {_object(root, 'alpha'), _object(root, 'beta')}
    assert (_object(root) / 'SKILL.md').stat().st_ino != (source / 'alpha/SKILL.md').stat().st_ino


def test_source_edits_and_removal_do_not_change_existing_jobs_or_supply(tmp_path):
    root, source = prepare(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    original = (c.workspace(root, 1) / '.agents/skills/alpha/SKILL.md').read_bytes()
    (source / 'alpha/SKILL.md').write_text('Changed mutable installation source')
    shutil.rmtree(source)
    projection = c.workspace(root, 1) / '.agents/skills'
    (projection / 'alpha').unlink(); projection.rmdir()
    assert c.build_job(root, 1, 'H', []) == job
    assert (projection / 'alpha/SKILL.md').read_bytes() == original
    intent = profiles.binding(c.settings(root), job)
    profiles.validate_intent(c.settings(root), job, intent)


@pytest.mark.parametrize('damage', ['bytes', 'missing', 'extra', 'link'])
def test_frozen_object_changes_fail_before_any_native_launch(tmp_path, damage):
    root, _ = prepare(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    target = _object(root) / 'scripts/run.py'
    target.parent.chmod(0o700)  # Deliberate trusted same-user tampering.
    if damage == 'bytes':
        target.chmod(0o600); target.write_text('changed'); target.chmod(0o400)
    elif damage == 'missing':
        target.unlink()
    elif damage == 'extra':
        target.with_name('extra.py').write_text('unselected')
    else:
        target.unlink(); target.symlink_to(tmp_path / 'outside')
    with pytest.raises((ValueError, OSError, ArchiveError)):
        skill_supply.project(root, c.settings(root), job)


@pytest.mark.parametrize('damage', ['extra', 'target', 'projection', 'parent'])
def test_projection_rejects_unselected_or_escaping_links(tmp_path, damage):
    root, source = prepare(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    projection = c.workspace(root, 1) / '.agents/skills'
    if damage == 'extra':
        (projection / 'beta').symlink_to(_object(root, 'beta'))
    elif damage == 'target':
        (projection / 'alpha').unlink(); (projection / 'alpha').symlink_to(source / 'alpha')
    else:
        (projection / 'alpha').unlink(); projection.rmdir()
        if damage == 'projection':
            projection.symlink_to(source)
        else:
            projection.parent.rmdir(); projection.parent.symlink_to(source)
    with pytest.raises((ValueError, ArchiveError)):
        list(archive._inventory(root))


def test_configuration_only_switches_frozen_sets_for_next_node(tmp_path, monkeypatch):
    root, source = prepare(tmp_path)
    first, _, _ = _finish(monkeypatch, root, 1, 'H', [], action='wait')
    document = research_config.to_document(root)
    document['agent']['roles']['H']['skill_set'] = 'second'
    shutil.rmtree(source)
    configuration_updates.submit(root, document, expected_revision=0)
    second = c.build_job(root, 2, 'H', [])
    assert c.build_job(root, 1, 'H', []) == first
    assert second['skill_binding']['skill_set'] == 'second'
    assert second['skill_binding']['configuration_revision'] == second['configuration_revision']
    assert set(path.name for path in (c.workspace(root, 2) / '.agents/skills').iterdir()) == {'beta'}
    document = research_config.to_document(root, configuration_updates.current(root))
    document['skills']['sets']['new'] = dict(items=['unselected'])
    with pytest.raises(ValueError, match='new instance'):
        research_config.validate_update(root, document)


def test_named_worker_edit_reexpands_only_explicit_role_references(tmp_path):
    root, _ = prepare(tmp_path, roles={'W': dict(worker='selected')}, workers={
        'selected': dict(model='first-model', skill_set='first'),
        'unused': dict(model='unused-model', skill_set='second')})
    document = research_config.to_document(root)
    assert document['agent']['roles'] == {'W': {'worker': 'selected'}}
    document['agent']['workers']['selected'].update(model='next-model', skill_set='second')
    settings = research_config.validate_update(root, document)
    assert profiles.selected(settings, 'W')['model'] == 'next-model'
    assert profiles.selected(settings, 'H')['model'] == DEFAULTS['model']
    assert settings['role_profiles']['W']['skill_set'] == 'second'


def test_job_and_resume_intent_retain_exact_binding_not_latest_configuration(tmp_path):
    root, _ = prepare(tmp_path)
    first = c.build_job(root, 1, 'H', [])
    original = configuration_updates.for_job(root, first)
    intent = dict(task_sha256=c.digest(root / 'jobs/001.json'), **profiles.binding(original, first))
    c.write(execution.origin(root, 1) / 'native-intent.json', intent)
    document = research_config.to_document(root)
    document['agent']['roles']['H']['skill_set'] = 'second'
    configuration_updates.submit(root, document, expected_revision=0)
    assert profiles.validate_intent(configuration_updates.for_step(root, 1), first, intent) == first['native_profile']
    assert list(execution.accepted_intents(root, 1))[0][1] == intent
    changed = copy.deepcopy(intent); changed['skill_binding']['skill_set'] = 'second'
    with pytest.raises(ValueError, match='Skill binding changed'):
        profiles.validate_intent(original, first, changed)
    c.write(execution.origin(root, 1) / 'native-intent.json', changed)
    with pytest.raises(ValueError, match='Skill binding changed'):
        execution.records(root, 1)


def test_archive_retains_objects_once_and_rejects_other_agents_links(tmp_path, monkeypatch):
    root, _ = prepare(tmp_path)
    _finish(monkeypatch, root, 1, 'H', [], action='wait')
    c.build_job(root, 2, 'H', [])
    package = tmp_path / 'archive'
    archive.archive_export(root, package)
    rows = [json.loads(line) for line in (package / 'manifest.jsonl').read_text().splitlines()]
    projected = [row for row in rows if row.get('reason') == 'verified_derived_skill_projection']
    assert len(projected) == 2
    assert all(row['kind'] == 'excluded' for row in projected)
    manual = _object(root) / 'SKILL.md'
    retained = [row for row in rows if row['path'] == manual.relative_to(root).as_posix()]
    assert len(retained) == 1 and (package / retained[0]['blob']).read_bytes() == manual.read_bytes()
    (c.workspace(root, 1) / '.agents/unknown').symlink_to(tmp_path)
    with pytest.raises(ArchiveError, match='Symbolic link'):
        list(archive._inventory(root))


def test_fork_child_owns_readonly_skill_links_after_parent_and_source_removal(tmp_path, monkeypatch):
    root, source = prepare(tmp_path)
    _finish(monkeypatch, root, 1, 'H', [], action='analyze')
    _finish(monkeypatch, root, 2, 'A', [1], action='finish')
    child = tmp_path / 'child'
    limits = dict(max_tokens=200, max_seconds=30, max_steps=2)
    preview = fork_service.preview(root, 1, child, budget=limits)
    assert preview['status'] == 'fork_preview_ready'
    fork_service.create(root, 1, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='skills-child')
    parent_object, child_object = _object(root), _object(child)
    assert (parent_object / 'SKILL.md').stat().st_ino == (child_object / 'SKILL.md').stat().st_ino
    manifest = c.read(child / 'host-runtime/fork/manifest.json')
    assert all(row['visibility'] == 'private_snapshot' for row in manifest['entries']
               if row['path'].startswith(skill_supply.DIRECTORY + '/'))
    for directory in (root / skill_supply.DIRECTORY / 'objects').rglob('*'):
        if directory.is_dir():directory.chmod(0o700)
    shutil.rmtree(root); shutil.rmtree(source)
    job = fork_runtime.build_selected_job(child, 1)
    skill_supply.project(child, configuration_updates.for_job(child, job), job)
    projected = c.workspace(child, 1) / '.agents/skills/alpha/SKILL.md'
    assert projected.resolve() == child_object / 'SKILL.md'
    assert b'name: alpha' in projected.read_bytes()


def test_empty_sets_keep_custom_backend_and_native_permissions_unchanged(tmp_path, monkeypatch):
    def no_store():
        raise AssertionError('An empty Skill selection must not access the shared store')
    monkeypatch.setattr(skill_supply, '_shared_store', no_store)
    permissions = dict(workspace='danger-full-access', network=True, web='live', subagents=True)
    root, _ = prepare(tmp_path, sets={'empty': dict(items=[])}, roles={'H': dict(skill_set='empty')},
        launcher=['custom'], native_backend='protocol', native_options=permissions)
    job = c.build_job(root, 1, 'H', [])
    assert c.settings(root)['native_options'] == permissions
    assert list((c.workspace(root, 1) / '.agents/skills').iterdir()) == []
    assert skill_supply.native_mounts(root, job) == [(str(c.workspace(root, 1) / '.agents/skills'),) * 2]


def test_custom_backend_with_selected_skills_fails_before_creation(tmp_path):
    with pytest.raises(ValueError, match='Skill loading contract'):
        prepare(tmp_path, launcher=['custom'], native_backend='protocol')
    assert not (tmp_path / 'instance').exists()


def test_default_resource_root_is_overridable_and_frozen_from_wrapper_environment(tmp_path, monkeypatch):
    source = library(tmp_path)
    monkeypatch.setenv('SCID_SKILLS_ROOT', str(source))
    declared, _ = skill_supply.prepare(dict(sets={'selected': dict(items=['alpha'])}),
        backend='codex', launcher=['codex'])
    assert declared['root'] == str(source)
    monkeypatch.setenv('SCID_SKILLS_ROOT', str(tmp_path / 'different'))
    assert declared['root'] == str(source)
    assert skill_supply.configuration(dict(root=str(source)))['root'] == str(source)


def test_registered_module_supply_is_generic_and_does_not_select_an_executor(tmp_path, monkeypatch):
    from dataclasses import replace
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    package = tmp_path / 'fixture_launcher'; package.mkdir()
    (package / '__init__.py').write_text('')
    module = package / 'module.py'; module.write_text('raise RuntimeError("never execute this fixture")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    declared = ExecutionInterfaceRegistration('fixture_provider', '1', RuntimePluginFactory(lambda _: None),
        native_launcher_module='fixture_launcher.module')
    class Entry:
        name = 'fixture_provider'
        value = 'fixture_provider:REGISTRATION'
        def load(self):
            return declared
    class Entries:
        def select(self, **filters):
            assert filters['group'] == 'scidiscovery.execution_interfaces'
            return [Entry()] if filters.get('name', 'fixture_provider') == 'fixture_provider' else []
    monkeypatch.setattr(skill_supply.importlib.metadata, 'entry_points', lambda: Entries())
    root, _ = prepare(tmp_path, launcher=[sys.executable, '-B', '-I', '-m', 'fixture_launcher.module',
        '--config', str(tmp_path / 'operator.json')])
    settings = c.settings(root)
    assert settings['experiment_mode'] == 'external' and 'execution_config' not in settings
    assert settings['skills']['launcher']['provider']['plugin_id'] == 'fixture_provider'
    job = c.build_job(root, 1, 'H', [])
    mounts = skill_supply.native_mounts(root, job)
    assert {source for source, target in mounts} == {
        str(c.workspace(root, 1) / '.agents/skills'), str(_object(root))}
    assert all(source == target and 'manifest.json' not in source for source, target in mounts)
    declared = replace(declared, version='2')
    with pytest.raises(ValueError, match='provider changed'):
        skill_supply.project(root, settings, job)
    declared = replace(declared, version='1')
    module.write_text('changed installed launcher module\n')
    with pytest.raises(ValueError, match='launcher identity changed'):
        skill_supply.project(root, settings, job)


def test_ambiguous_registered_module_is_not_selected_automatically(tmp_path, monkeypatch):
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    class Entry:
        value = 'fixture_provider:REGISTRATION'
        def __init__(self, name):
            self.name = name
        def load(self):
            return ExecutionInterfaceRegistration(self.name, '1', RuntimePluginFactory(lambda _: None),
                native_launcher_module='other_domain.launcher')
    class Entries:
        def select(self, **filters):
            return [Entry('first_provider'), Entry('second_provider')]
    monkeypatch.setattr(skill_supply.importlib.metadata, 'entry_points', lambda: Entries())
    with pytest.raises(ValueError, match='one exact registered provider'):
        prepare(tmp_path, launcher=[sys.executable, '-m', 'other_domain.launcher', '--config', 'operator.json'])
    assert not (tmp_path / 'instance').exists()


def test_cold_restore_rebuilds_skill_paths_without_source_library(tmp_path, monkeypatch):
    from scidiscovery.research_loop import budget
    executable = tmp_path / 'bin/codex'
    executable.parent.mkdir(); executable.write_text('#!/bin/sh\nexit 99\n'); executable.chmod(0o700)
    root, source = prepare(tmp_path, launcher=[str(executable)])
    _finish(monkeypatch, root, 1, 'H', [], action='wait')
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'Explicit cold fixture boundary')
    anchor = tmp_path / 'anchor'; anchor.mkdir()
    archive.checkpoint_enroll(root, anchor)
    package = tmp_path / 'checkpoint'
    archive.checkpoint_export(root, package)
    root.rename(tmp_path / 'old-instance')
    shutil.rmtree(source)
    result = archive.checkpoint_restore(root, package)
    assert result['status'] == 'checkpoint_restored'
    projection = c.workspace(root, 1) / '.agents/skills/alpha/SKILL.md'
    assert projection.resolve() == _object(root) / 'SKILL.md'
    assert b'name: alpha' in projection.read_bytes()


def test_skill_selection_keeps_frozen_native_home_and_permissions(tmp_path):
    executable = tmp_path / 'bin/codex'
    executable.parent.mkdir(); executable.write_text('#!/bin/sh\nexit 99\n'); executable.chmod(0o700)
    home = tmp_path / 'original-home'; home.mkdir()
    (home / 'config.toml').write_text('model = "operator-native-default"\n')
    permissions = dict(workspace='read-only', network=False, web='cached', subagents=True)
    root, _ = prepare(tmp_path, launcher=[str(executable)], native_options=permissions,
        node_budget_policy={role: dict(max_observed_tokens=500, max_seconds=30) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn',
            output_only=True, output_repair=True, execution_wake=True, native_home=str(home)))
    settings = c.settings(root)
    assert settings['native_options'] == permissions
    assert settings['resume_policy']['native_home'] == str(home)
    assert settings['resume_policy']['output_only'] is True
    assert (home / 'config.toml').read_text() == 'model = "operator-native-default"\n'
    c.build_job(root, 1, 'H', [])
    assert skill_supply.validate(root, settings) is not None


@pytest.mark.parametrize('damage', ['missing', 'manual', 'escape', 'native_name'])
def test_invalid_selected_bundle_fails_before_instance_creation(tmp_path, damage):
    source = library(tmp_path)
    directory = source / 'alpha'
    if damage == 'missing':
        shutil.rmtree(directory)
    elif damage == 'manual':
        (directory / 'SKILL.md').write_text('No native Skill metadata')
    elif damage == 'escape':
        (directory / 'escape').symlink_to(tmp_path)
    else:
        manual = directory / 'SKILL.md'
        manual.write_text(manual.read_text().replace('name: alpha', 'name: misleading'))
    with pytest.raises((ValueError, OSError)):
        prepare(tmp_path, source=source)
    assert not (tmp_path / 'instance').exists()


def test_toml_declaration_resolves_source_and_keeps_worker_selection_explicit(tmp_path):
    declaration = dict(version=1,
        research=dict(root='instance', public_root='public', objective_text='An explicit objective.'),
        agent=dict(launcher=['codex'], workers=dict(selected=dict(model='chosen', skill_set='tcad')),
            roles=dict(W=dict(worker='selected'))),
        skills=dict(root='relative-library', sets=dict(tcad=dict(items=['sentaurus-tcad-code']))),
        budget=dict(max_tokens=1000, max_seconds=60, max_steps=3))
    options = research_config.from_document(declaration, base=tmp_path)
    assert options['skills']['root'] == str(tmp_path / 'relative-library')
    assert options['worker_profiles']['selected']['model'] == 'chosen'
    assert options['role_profiles'] == {'W': {'worker': 'selected'}}



def test_archive_rejects_noncanonical_node_projection_instead_of_verifying_another_node(tmp_path):
    root, _ = prepare(tmp_path)
    c.build_job(root, 1, 'H', [])
    outside = root / 'project/nodes/0001/.agents/skills'
    outside.parent.mkdir(parents=True)
    outside.symlink_to(tmp_path)
    with pytest.raises(ArchiveError, match=r'Symbolic link.*project/nodes/0001/\.agents/skills'):
        list(archive._inventory(root))


@pytest.mark.process_e2e
def test_real_frozen_science_skill_clis_do_not_mutate_objects(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'skills'
    items = ['curve-analysis', 'scientific-paper-evidence']
    root, _ = prepare(tmp_path, source=source, sets={'science': dict(items=items)},
        roles={'H': dict(skill_set='science')})
    settings = c.settings(root)
    job = c.build_job(root, 1, 'H', [])
    before = skill_supply.validate(root, settings)
    environment = dict(os.environ)
    environment.pop('PYTHONDONTWRITEBYTECODE', None)
    environment.pop('PYTHONPYCACHEPREFIX', None)
    work = c.workspace(root, 1)
    for item, entry in [('curve-analysis', 'curve_analysis.py'),
                        ('scientific-paper-evidence', 'figure_evidence.py')]:
        output = work / 'artifacts' / (item + '-schema.json')
        script = work / '.agents/skills' / item / 'scripts' / entry
        result = subprocess.run([sys.executable, str(script), 'schema', '--output', str(output)],
            cwd=work, env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(output.read_bytes())
    assert skill_supply.validate(root, settings) == before
    for item in items:
        frozen = _object(root, item)
        declared = before['objects'][item]
        actual_files = sorted(path.relative_to(frozen).as_posix() for path in frozen.rglob('*') if path.is_file())
        assert actual_files == sorted(row['path'] for row in declared['files'])
    _finish(monkeypatch, root, 1, 'H', [], action='wait')
    c.build_job(root, 2, 'H', [])
    assert skill_supply.validate(root, configuration_updates.for_step(root, 2)) == before
    assert profiles.validate_intent(settings, job, profiles.binding(settings, job)) == job['native_profile']


def _freeze_selected(root, source, item):
    root.mkdir()
    declared, manifest = skill_supply.prepare(dict(root=str(source), sets={'selected': dict(items=[item])}),
        backend='codex', launcher=['codex'])
    settings = dict(skills=declared, native_launcher_argv=['codex'])
    skill_supply.freeze(root, settings, manifest)
    return settings, root / skill_supply.DIRECTORY / 'objects' / manifest['objects'][item]['tree_sha256']


def test_independent_instances_share_content_across_trees_and_keep_their_own_lifetime(tmp_path, monkeypatch):
    from scidiscovery.research_loop import atomic_files
    source = library(tmp_path)
    payload = b'print("one shared resource")\n'
    for item in ('alpha', 'beta'):
        (source / item / 'scripts/run.py').write_bytes(payload)
    copied = []
    original_copy = atomic_files.copy_original
    def counted(source, destination, **kwargs):
        copied.append(kwargs['sha256'])
        return original_copy(source, destination, **kwargs)
    monkeypatch.setattr(atomic_files, 'copy_original', counted)
    first, alpha = _freeze_selected(tmp_path / 'first', source, 'alpha')
    second, beta = _freeze_selected(tmp_path / 'second', source, 'beta')
    digest = c.digest(alpha / 'scripts/run.py')
    blob = Path(os.environ['SCID_SHARED_STORE']) / 'skills/objects' / digest
    assert copied.count(digest) == 1
    assert (alpha / 'scripts/run.py').stat().st_ino == (beta / 'scripts/run.py').stat().st_ino == blob.stat().st_ino
    assert (source / 'alpha/scripts/run.py').stat().st_ino != blob.stat().st_ino
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o500 for path in (alpha, alpha / 'scripts', alpha / 'empty'))
    shutil.rmtree(source)
    shutil.rmtree(Path(os.environ['SCID_SHARED_STORE']))
    assert skill_supply.validate(tmp_path / 'first', first)
    assert skill_supply.validate(tmp_path / 'second', second)
    assert (alpha / 'scripts/run.py').read_bytes() == (beta / 'scripts/run.py').read_bytes() == payload


def test_concurrent_different_skill_trees_freeze_shared_file_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from scidiscovery.research_loop import atomic_files
    import threading
    source = library(tmp_path)
    for item in ('alpha', 'beta'):
        (source / item / 'scripts/run.py').write_bytes(b'identical resource across different trees')
    digest = c.digest(source / 'alpha/scripts/run.py')
    original_copy = atomic_files.copy_original
    copied = []
    def counted(source, destination, **kwargs):
        copied.append(kwargs['sha256'])
        return original_copy(source, destination, **kwargs)
    monkeypatch.setattr(atomic_files, 'copy_original', counted)
    barrier = threading.Barrier(2)
    def freeze(item):
        barrier.wait(timeout=5)
        return _freeze_selected(tmp_path / item, source, item)[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha, beta = list(pool.map(freeze, ('alpha', 'beta')))
    assert copied.count(digest) == 1
    assert (alpha / 'scripts/run.py').stat().st_ino == (beta / 'scripts/run.py').stat().st_ino


def test_skill_cache_damage_is_rejected_without_repair(tmp_path):
    from scidiscovery.archive_io import ArchiveError
    source = library(tmp_path)
    _, alpha = _freeze_selected(tmp_path / 'first', source, 'alpha')
    blob = Path(os.environ['SCID_SHARED_STORE']) / 'skills/objects' / c.digest(alpha / 'scripts/run.py')
    blob.chmod(0o600); blob.write_bytes(b'damaged private cache'); blob.chmod(0o400)
    with pytest.raises(ArchiveError):
        _freeze_selected(tmp_path / 'second', source, 'alpha')
    assert blob.read_bytes() == b'damaged private cache'


def test_skill_shared_link_failure_is_explicit_and_never_copies_again(tmp_path, monkeypatch):
    import errno
    from scidiscovery import archive_io
    from scidiscovery.research_loop import atomic_files
    source = library(tmp_path)
    _freeze_selected(tmp_path / 'first', source, 'alpha')
    def no_copy(*args, **kwargs):
        raise AssertionError('A cache hit must never copy a resource')
    def unsupported(*args, **kwargs):
        raise OSError(errno.EXDEV, 'fixture requires another filesystem')
    monkeypatch.setattr(atomic_files, 'copy_original', no_copy)
    monkeypatch.setattr(archive_io.os, 'link', unsupported)
    with pytest.raises(archive_io.SharingUnsupported):
        _freeze_selected(tmp_path / 'second', source, 'alpha')
    assert not (tmp_path / 'second' / skill_supply.MANIFEST).exists()


def test_skill_frozen_scan_limits_reject_before_hashing(tmp_path, monkeypatch):
    from scidiscovery import archive_io
    from scidiscovery.research_loop import storage_policy as storage
    source = library(tmp_path)
    def no_hash(*args, **kwargs):
        raise AssertionError('File size admission must precede hashing')
    monkeypatch.setattr(archive_io, 'hash_file', no_hash)
    with pytest.raises(storage.StorageQuotaExceeded, match='max_file_bytes'):
        skill_supply.prepare(dict(root=str(source), sets={'selected': dict(items=['alpha'])}),
            backend='codex', launcher=['codex'], policy=dict(enabled=True, max_file_bytes=1))
