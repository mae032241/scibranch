"""Installation Skill selection through real preparation; no native/model calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from scidiscovery.archive_io import ArchiveError
from scidiscovery.research_loop import (configuration_updates, contracts as c, controller,
    execution, profiles, research_config, skill_supply)

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
        skills=dict(sets=sets if sets is not None else
            dict(first=dict(items=['alpha']), second=dict(items=['beta']))),
        role_profiles=roles if roles is not None else {'H': dict(skill_set='first')},
        worker_profiles=workers)
    options.update(changes)
    with patch.object(skill_supply, 'source_root', return_value=source):
        controller.prepare(root, objective, public, launcher or ['codex'], **options)
    return root, source


def _finish(monkeypatch, root, step, role, parents, *, action):
    from test_history_handoff import finish
    write = c.write
    def bound(path, value):
        if path.name == 'native-intent.json':
            job = c.read(root / 'jobs' / f'{step:03d}.json')
            value = dict(value, **profiles.binding(configuration_updates.for_job(root, job), job))
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False, cleanup='observed_group_cleaned')
        return write(path, value)
    with monkeypatch.context() as changed:
        changed.setattr(c, 'write', bound)
        return finish(root, step, role, parents, action=action)


def test_readonly_installation_projects_only_explicit_role_and_worker_sets(tmp_path, monkeypatch):
    source = library(tmp_path)
    # Declared but unselected bundles are not traversed or even opened.
    shutil.rmtree(source / 'unselected')
    for item in ('alpha', 'beta'):
        for relative in ('SKILL.md', 'scripts/run.py'):
            (source / item / relative).chmod(0o444)
        (source / item).chmod(0o555)
    source.chmod(0o555)
    root, _ = prepare(tmp_path, source=source,
        sets=dict(first=dict(items=['alpha']), second=dict(items=['beta']), unused=dict(items=['unselected'])),
        roles={'H': dict(skill_set='first'), 'W': dict(worker='analyst')},
        workers={'analyst': dict(model='another-model', skill_set='second')})
    h, _, _ = _finish(monkeypatch, root, 1, 'H', [], action='experiment')
    w = c.build_job(root, 2, 'W', [])
    settings = c.settings(root)
    assert settings['skills']['version'] == 3
    assert 'manifest' not in settings['skills']
    assert not (root / skill_supply.DIRECTORY).exists()
    for job, item in ((h, 'alpha'), (w, 'beta')):
        projection = c.workspace(root, job['step']) / '.agents/skills'
        assert {path.name for path in projection.iterdir()} == {item}
        assert (projection / item).resolve() == source / item
        assert (projection / item / 'SKILL.md').samefile(source / item / 'SKILL.md')
        assert not (projection / item).readlink().is_absolute()
        assert 'skill_binding' not in c.read(c.workspace(root, job['step']) / 'science-task.json')
        assert job['skill_binding']['root'] == str(source)
        assert 'manifest' not in job['skill_binding']
    assert w['native_profile']['model'] == 'another-model'
    assert w['skill_binding']['worker'] == 'analyst'


def test_two_instances_share_one_installation_without_copying_or_hardlinking(tmp_path, monkeypatch):
    from scidiscovery.research_loop import atomic_files
    source = library(tmp_path)
    original_copy, original_link = atomic_files.copy_original, os.link
    def guarded_copy(original, *args, **kwargs):
        assert not Path(original).absolute().is_relative_to(source), 'Skill resource bytes must not be copied'
        return original_copy(original, *args, **kwargs)
    def guarded_link(original, *args, **kwargs):
        assert not Path(original).absolute().is_relative_to(source), 'Skill resource bytes must not be hardlinked'
        return original_link(original, *args, **kwargs)
    monkeypatch.setattr(atomic_files, 'copy_original', guarded_copy)
    monkeypatch.setattr(os, 'link', guarded_link)
    first = tmp_path / 'first'; first.mkdir()
    second = tmp_path / 'second'; second.mkdir()
    roots = [prepare(base, source=source)[0] for base in (first, second)]
    targets = []
    for root in roots:
        job = c.build_job(root, 1, 'H', [])
        targets.append(skill_supply.project(root, c.settings(root), job)['alpha'])
        assert not (root / skill_supply.DIRECTORY).exists()
    assert targets == [source / 'alpha', source / 'alpha']


def test_source_is_live_installation_asset_and_missing_selection_has_repair_diagnostic(tmp_path):
    root, source = prepare(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    resource = source / 'alpha/scripts/run.py'
    resource.write_text('print("updated installation resource")\n')
    projection = c.workspace(root, 1) / '.agents/skills/alpha'
    assert (projection / 'scripts/run.py').read_bytes() == resource.read_bytes()
    shutil.rmtree(source / 'alpha')
    with pytest.raises(ValueError) as error:
        skill_supply.project(root, c.settings(root), job)
    assert 'alpha' in str(error.value) and str(source / 'alpha') in str(error.value)
    assert 'install or restore' in str(error.value)


@pytest.mark.parametrize('damage', ['extra', 'target', 'projection', 'parent'])
def test_projection_rejects_unselected_or_escaping_links(tmp_path, damage):
    root, source = prepare(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    projection = c.workspace(root, 1) / '.agents/skills'
    if damage == 'extra':
        (projection / 'beta').symlink_to(source / 'beta')
    elif damage == 'target':
        (projection / 'alpha').unlink(); (projection / 'alpha').symlink_to(source / 'beta')
    else:
        (projection / 'alpha').unlink(); projection.rmdir()
        if damage == 'projection':
            projection.symlink_to(source)
        else:
            projection.parent.rmdir(); projection.parent.symlink_to(source)
    with pytest.raises((ValueError, ArchiveError)):
        skill_supply.project(root, c.settings(root), job)


def test_configuration_switches_frozen_sets_for_next_node_only(tmp_path, monkeypatch):
    root, _ = prepare(tmp_path)
    first, _, _ = _finish(monkeypatch, root, 1, 'H', [], action='wait')
    document = research_config.to_document(root)
    assert set(document['skills']) == {'sets'}
    document['agent']['roles']['H']['skill_set'] = 'second'
    configuration_updates.submit(root, document, expected_revision=0)
    second = c.build_job(root, 2, 'H', [])
    assert c.build_job(root, 1, 'H', []) == first
    assert second['skill_binding']['skill_set'] == 'second'
    assert second['skill_binding']['configuration_revision'] == second['configuration_revision']
    assert {path.name for path in (c.workspace(root, 2) / '.agents/skills').iterdir()} == {'beta'}
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


def test_job_and_resume_intent_retain_binding_not_latest_configuration(tmp_path):
    root, _ = prepare(tmp_path)
    first = c.build_job(root, 1, 'H', [])
    original = configuration_updates.for_job(root, first)
    intent = dict(task_ref=c.record_ref(root / 'jobs/001.json'), **profiles.binding(original, first))
    c.write(execution.origin(root, 1) / 'native-intent.json', intent)
    document = research_config.to_document(root)
    document['agent']['roles']['H']['skill_set'] = 'second'
    configuration_updates.submit(root, document, expected_revision=0)
    assert profiles.validate_intent(configuration_updates.for_step(root, 1), first, intent) == first['native_profile']
    assert list(execution.accepted_intents(root, 1))[0][1] == intent
    changed = copy.deepcopy(intent); changed['skill_binding']['skill_set'] = 'second'
    with pytest.raises(ValueError, match='Skill binding changed'):
        profiles.validate_intent(original, first, changed)


def test_no_role_selection_does_not_require_source_or_native_loading_contract(tmp_path):
    source = tmp_path / 'missing-installation-assets'
    root, _ = prepare(tmp_path, source=source, roles={},
        launcher=['custom'], native_backend='protocol')
    job = c.build_job(root, 1, 'H', [])
    assert job['skill_binding']['items'] == []
    assert list((c.workspace(root, 1) / '.agents/skills').iterdir()) == []
    assert 'launcher' not in c.settings(root)['skills']


def test_empty_sets_keep_custom_backend_and_native_permissions(tmp_path):
    permissions = dict(workspace='danger-full-access', network=True, web='live', subagents=True)
    root, _ = prepare(tmp_path, sets={'empty': dict(items=[])}, roles={'H': dict(skill_set='empty')},
        launcher=['custom'], native_backend='protocol', native_options=permissions)
    job = c.build_job(root, 1, 'H', [])
    assert c.settings(root)['native_options'] == permissions
    assert skill_supply.native_mounts(root, job) == [(str(c.workspace(root, 1) / '.agents/skills'),) * 2]


def test_custom_backend_with_selected_skills_fails_before_creation(tmp_path):
    with pytest.raises(ValueError, match='Skill loading contract'):
        prepare(tmp_path, launcher=['custom'], native_backend='protocol')
    assert not (tmp_path / 'instance').exists()


@pytest.mark.parametrize('layout', ['source', 'flat', 'wheel'])
def test_root_follows_loaded_module_without_environment_override(tmp_path, monkeypatch, layout):
    prefix = tmp_path / layout
    if layout == 'source':
        module = prefix / 'src/scidiscovery/research_loop/skill_supply.py'
    elif layout == 'flat':
        module = prefix / 'site/scidiscovery/research_loop/skill_supply.py'
    else:
        module = prefix / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/scidiscovery/research_loop/skill_supply.py'
    monkeypatch.setattr(skill_supply, '__file__', str(module))
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    monkeypatch.setenv('SCID_SKILLS_ROOT', str(tmp_path / 'ignored'))
    assert skill_supply.source_root() == prefix / 'skills'
    with pytest.raises(ValueError, match='only sets'):
        skill_supply.configuration(dict(root=str(tmp_path)))


def test_registered_module_supply_is_generic_and_keeps_exact_provider_identity(tmp_path, monkeypatch):
    from dataclasses import replace
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    package = tmp_path / 'fixture_launcher'; package.mkdir()
    (package / '__init__.py').write_text('')
    module = package / 'module.py'; module.write_text('raise RuntimeError("never execute")\n')
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
    root, source = prepare(tmp_path, launcher=[sys.executable, '-B', '-I', '-m', 'fixture_launcher.module',
        '--config', str(tmp_path / 'operator.json')])
    settings = c.settings(root)
    assert settings['experiment_mode'] == 'external' and 'execution_config' not in settings
    job = c.build_job(root, 1, 'H', [])
    mounts = skill_supply.native_mounts(root, job)
    assert {source_path for source_path, target in mounts} == {
        str(c.workspace(root, 1) / '.agents/skills'), str(source / 'alpha')}
    assert all(source_path == target and 'manifest.json' not in source_path for source_path, target in mounts)
    declared = replace(declared, version='2')
    with pytest.raises(ValueError, match='provider changed'):
        skill_supply.project(root, settings, job)


def test_skill_selection_preserves_frozen_home_and_native_permissions(tmp_path):
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
    assert (home / 'config.toml').read_text() == 'model = "operator-native-default"\n'


@pytest.mark.parametrize('damage', ['missing', 'manual', 'linked_directory', 'linked_manual', 'native_name'])
def test_invalid_selected_bundle_fails_before_instance_creation(tmp_path, damage):
    source = library(tmp_path)
    directory = source / 'alpha'
    if damage == 'missing':
        shutil.rmtree(directory)
    elif damage == 'manual':
        (directory / 'SKILL.md').write_text('No native Skill metadata')
    elif damage == 'linked_directory':
        shutil.rmtree(directory); directory.symlink_to(source / 'beta')
    elif damage == 'linked_manual':
        (directory / 'SKILL.md').unlink(); (directory / 'SKILL.md').symlink_to(source / 'beta/SKILL.md')
    else:
        manual = directory / 'SKILL.md'
        manual.write_text(manual.read_text().replace('name: alpha', 'name: misleading'))
    with pytest.raises((ValueError, OSError)):
        prepare(tmp_path, source=source)
    assert not (tmp_path / 'instance').exists()


def test_toml_declaration_keeps_source_installation_owned_and_worker_selection_explicit(tmp_path):
    declaration = dict(version=1,
        research=dict(root='instance', public_root='public', objective_text='An explicit objective.'),
        agent=dict(launcher=['codex'], workers=dict(selected=dict(model='chosen', skill_set='tcad')),
            roles=dict(W=dict(worker='selected'))),
        skills=dict(sets=dict(tcad=dict(items=['sentaurus-tcad-code']))),
        budget=dict(max_tokens=1000, max_seconds=60, max_steps=3))
    options = research_config.from_document(declaration, base=tmp_path)
    assert options['skills'] == declaration['skills']
    assert options['role_profiles'] == {'W': {'worker': 'selected'}}
    declaration['skills']['root'] = 'relative-library'
    with pytest.raises(ValueError, match='skills'):
        research_config.from_document(declaration, base=tmp_path)


def test_retained_v2_manifest_and_job_bindings_remain_readonly(tmp_path):
    root = tmp_path / 'legacy'; root.mkdir()
    directory = root / skill_supply.DIRECTORY / 'objects/alpha'
    directory.mkdir(parents=True)
    manual = directory / 'SKILL.md'
    manual.write_text('---\nname: alpha\ndescription: Retained fixture\n---\n')
    manual.chmod(0o400); directory.chmod(0o500)
    sets = dict(first=dict(items=['alpha']))
    manifest = dict(version=2, freeze_id='retained', sets=sets, objects=dict(alpha=dict(
        files=[dict(path='SKILL.md', size_bytes=manual.stat().st_size)], directories=[],
        metadata=dict(name='alpha', description='Retained fixture'))))
    c.write(root / skill_supply.MANIFEST, manifest); (root / skill_supply.MANIFEST).chmod(0o400)
    declared = dict(root='/old/deployment/skills', sets=sets, manifest=dict(path=skill_supply.MANIFEST, version='retained'),
        launcher=dict(kind='codex_node_cwd', version=1))
    settings = dict(skills=declared, native_launcher_argv=['codex'], role_profiles={'H': dict(skill_set='first')}, **DEFAULTS)
    assert skill_supply.validate(root, settings) == manifest
    retained, pending = skill_supply.prepare(dict(sets=sets), backend='codex', launcher=['codex'], existing=settings)
    assert retained == declared and pending is None
    job = dict(role='H', step=1)
    job['skill_binding'] = skill_supply.binding(settings, job)
    assert job['skill_binding']['version'] == 2 and job['skill_binding']['manifest'] == declared['manifest']
    assert 'root' not in job['skill_binding']
    assert skill_supply.project(root, settings, job)['alpha'] == directory


def _legacy_fork(tmp_path, monkeypatch):
    from scidiscovery.research_loop import fork_service
    root, source = prepare(tmp_path, sets=dict(first=dict(items=['alpha'])))
    settings = c.settings(root)
    directory = root / skill_supply.DIRECTORY / 'objects/alpha'
    directory.mkdir(parents=True)
    manual = directory / 'SKILL.md'
    manual.write_bytes((source / 'alpha/SKILL.md').read_bytes())
    manual.chmod(0o400); directory.chmod(0o500)
    sets = settings['skills']['sets']
    manifest = dict(version=2, freeze_id='retained-fork', sets=sets, objects=dict(alpha=dict(
        files=[dict(path='SKILL.md', size_bytes=manual.stat().st_size)], directories=[],
        metadata=dict(name='alpha', description='Selected alpha fixture'))))
    c.write(root / skill_supply.MANIFEST, manifest); (root / skill_supply.MANIFEST).chmod(0o400)
    settings['skills'] = dict(root=str(source), sets=sets,
        manifest=dict(path=skill_supply.MANIFEST, version='retained-fork'),
        launcher=dict(kind='codex_node_cwd', version=1))
    c.write(root / 'settings.json', settings)
    _finish(monkeypatch, root, 1, 'H', [], action='analyze')
    _finish(monkeypatch, root, 2, 'A', [1], action='finish')
    child = tmp_path / 'child'
    limits = dict(max_tokens=200, max_seconds=30, max_steps=2)
    preview = fork_service.preview(root, 1, child, budget=limits)
    assert preview['status'] == 'fork_preview_ready'
    from scidiscovery import archive_io as files
    original_copy, original_link = files.copy_original, files.link_original
    def forbid_skill_copy(original, target, *args, **kwargs):
        assert not Path(original).is_relative_to(root / skill_supply.DIRECTORY)
        return original_copy(original, target, *args, **kwargs)
    def forbid_skill_link(original, target, *args, **kwargs):
        assert not Path(original).is_relative_to(root / skill_supply.DIRECTORY)
        return original_link(original, target, *args, **kwargs)
    monkeypatch.setattr(files, 'copy_original', forbid_skill_copy)
    monkeypatch.setattr(files, 'link_original', forbid_skill_link)
    fork_service.create(root, 1, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='legacy-shared-skills')
    return root, child, directory


def test_legacy_fork_uses_exact_parent_original_without_new_skill_bytes(tmp_path, monkeypatch):
    from scidiscovery.research_loop import fork_runtime
    parent, child, directory = _legacy_fork(tmp_path, monkeypatch)
    assert not (child / skill_supply.DIRECTORY).exists()
    assert c.settings(child)['skills'] == c.settings(parent)['skills']
    job = fork_runtime.build_selected_job(child, 1)
    assert skill_supply.project(child, c.settings(child), job)['alpha'] == directory
    projected = c.workspace(child, 1) / '.agents/skills/alpha/SKILL.md'
    assert projected.samefile(directory / 'SKILL.md')
    assert projected.read_bytes() == (directory / 'SKILL.md').read_bytes()
    manifest = c.read(child / 'host-runtime/fork/manifest.json')
    assert not any(row['path'].startswith(skill_supply.DIRECTORY + '/') for row in manifest['entries'])


@pytest.mark.parametrize('damage', ['missing_parent', 'identity'])
def test_legacy_fork_rejects_missing_or_replaced_registered_parent(tmp_path, monkeypatch, damage):
    parent, child, directory = _legacy_fork(tmp_path, monkeypatch)
    if damage == 'missing_parent':
        directory.chmod(0o700)
        shutil.rmtree(parent)
        expected = 'Legacy Skill source unavailable'
    else:
        settings = c.settings(parent); settings['goal_id'] = 'replaced-parent'
        c.write(parent / 'settings.json', settings)
        expected = 'Legacy Skill source identity changed'
    with pytest.raises(ValueError, match=expected):
        skill_supply.validate(child, c.settings(child))


@pytest.mark.process_e2e
def test_real_installation_science_skill_clis_use_original_assets(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'skills'
    items = ['curve-analysis', 'scientific-paper-evidence']
    root, _ = prepare(tmp_path, source=source, sets={'science': dict(items=items)},
        roles={'H': dict(skill_set='science')})
    settings = c.settings(root); job = c.build_job(root, 1, 'H', [])
    work = c.workspace(root, 1)
    before = {item: (source / item / 'SKILL.md').read_bytes() for item in items}
    for item, entry in [('curve-analysis', 'curve_analysis.py'), ('scientific-paper-evidence', 'figure_evidence.py')]:
        output = work / 'artifacts' / (item + '-schema.json')
        script = work / '.agents/skills' / item / 'scripts' / entry
        assert script.samefile(source / item / 'scripts' / entry)
        result = subprocess.run([sys.executable, '-B', str(script), 'schema', '--output', str(output)],
            cwd=work, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(output.read_bytes())
    assert before == {item: (source / item / 'SKILL.md').read_bytes() for item in items}
    assert not (root / skill_supply.DIRECTORY).exists()
    assert profiles.validate_intent(settings, job, profiles.binding(settings, job)) == job['native_profile']
