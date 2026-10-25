"""Selected providers offer Skill sets; existing role profiles opt in explicitly."""
import copy
from pathlib import Path

import pytest

from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
from scidiscovery.research_loop import (contracts as c, controller, experiments, profiles,
    research_config, skill_supply)


@pytest.fixture
def offered(tmp_path, monkeypatch):
    calls = []
    selection = {'domain': dict(items=['alpha'])}
    def defaults(configuration, *, root):
        calls.append((copy.deepcopy(configuration), root))
        return dict(optional_skill_sets=copy.deepcopy(selection))
    def no_runtime(_):
        raise AssertionError('Skill declarations must not start a plugin runtime')
    declared = ExecutionInterfaceRegistration('offered', '1', RuntimePluginFactory(no_runtime),
        research_defaults=defaults)
    def registration(executor):
        assert executor == 'offered:binding'
        return declared, 'binding'
    monkeypatch.setattr(experiments, 'registration', registration)
    # Defaults receive only the existing normalized operator configuration.
    monkeypatch.setattr(experiments, 'operator_configuration', lambda value:
        dict(executor=value['executor'], configuration=copy.deepcopy(value['configuration'])))
    source = tmp_path / 'skills'; directory = source / 'alpha'; directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text('---\nname: alpha\ndescription: Plugin offered fixture\n---\n')
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective'; objective.write_text('Retain declared selections.')
    options = dict(root=tmp_path / 'instance', objective=objective, public_root=public,
        launcher=['not-invoked'], model='fixture', max_tokens=1000, max_seconds=60, max_steps=3,
        search_policy=None, execution_config=dict(executor='offered:binding', configuration=dict(value=7)))
    return options, calls, selection, source


def test_installed_unselected_plugin_is_never_queried(offered, monkeypatch):
    options, calls, selection, source = offered
    del options['execution_config']
    def forbidden(*args):
        raise AssertionError('Unselected installed providers must not be inspected for Skills')
    monkeypatch.setattr(experiments, 'registration', forbidden)
    assert 'skills' not in research_config.normalize(options)
    assert calls == []


def test_selected_plugin_only_offers_sets_without_role_injection(offered):
    options, calls, selection, source = offered
    prepared = research_config.normalize(options)
    assert prepared['skills'] == dict(sets=selection)
    assert 'role_profiles' not in prepared
    settings = controller.prepare(**prepared)
    job = c.build_job(options['root'], 1, 'H', [])
    assert job['skill_binding']['items'] == []
    assert list((c.workspace(options['root'], 1) / '.agents/skills').iterdir()) == []
    assert 'launcher' not in settings['skills']
    assert len(calls) == 1


def test_role_opt_in_freezes_expanded_set_without_recalling_plugin_for_resume(offered, monkeypatch):
    options, calls, selection, source = offered
    # Reuse the native loading contract without invoking a model or native process.
    executable = options['root'].parent / 'codex'
    executable.write_text('#!/bin/sh\nexit 99\n'); executable.chmod(0o700)
    options['launcher'] = [str(executable)]
    monkeypatch.setattr(research_config.shutil, 'which', lambda _: str(executable))
    options['role_profiles'] = {'H': dict(worker='analyst')}
    options['worker_profiles'] = {'analyst': dict(model='worker-model', skill_set='domain')}
    prepared = research_config.normalize(options)
    # Suppress auto-resume only in the fixture: this test concerns the exact
    # already-expanded provider declaration, not executable discovery.
    prepared.pop('resume_policy', None); prepared.pop('node_budget_policy', None)
    settings = controller.prepare(**prepared)
    root = options['root']; job = c.build_job(root, 1, 'H', [])
    intent = profiles.binding(settings, job)
    assert job['skill_binding']['items'] == ['alpha']
    assert job['skill_binding']['worker'] == 'analyst'
    assert job['native_profile']['model'] == 'worker-model'
    selection['domain']['items'].append('later-plugin-addition')
    def forbidden(*args):
        raise AssertionError('Retained job projection/resume must not re-query plugin defaults')
    monkeypatch.setattr(experiments, 'registration', forbidden)
    assert skill_supply.project(root, settings, job)['alpha'] == source / 'alpha'
    assert profiles.validate_intent(settings, job, intent) == job['native_profile']
    assert settings['skills']['sets']['domain']['items'] == ['alpha']
    assert len(calls) == 1


def test_explicit_identical_set_is_idempotent_but_conflicting_set_fails(offered):
    options, calls, selection, source = offered
    options['skills'] = dict(sets=copy.deepcopy(selection))
    assert research_config.normalize(options)['skills'] == options['skills']
    options['skills']['sets']['domain']['items'] = ['different']
    with pytest.raises(ValueError, match='Skill set conflicts'):
        research_config.normalize(options)


@pytest.mark.parametrize('selection', [
    {'domain': {'items': ['alpha', 'alpha']}},
    {'domain': {'items': ['../alpha']}},
    {'domain': {'root': '/arbitrary', 'items': ['alpha']}},
    {'domain': ['alpha']},
])
def test_plugin_offers_only_existing_simple_set_schema(offered, selection):
    options, calls, offered_sets, source = offered
    offered_sets.clear(); offered_sets.update(selection)
    with pytest.raises(ValueError, match='Skill|simple directory'):
        research_config.normalize(options)


def test_unknown_role_selection_fails_without_instance_creation(offered):
    options, calls, selection, source = offered
    options['role_profiles'] = {'H': dict(skill_set='unknown')}
    with pytest.raises(ValueError, match='unknown Skill set'):
        controller.prepare(**research_config.normalize(options))
    assert not Path(options['root']).exists()


def test_fork_normalization_retains_frozen_sets_when_provider_offers_change(offered):
    options, calls, selection, source = offered
    options['skills'] = dict(sets=copy.deepcopy(selection))
    frozen = dict(version=3, root=str(source), sets=copy.deepcopy(selection))
    selection['domain']['items'] = ['changed-offer']
    selection['new'] = dict(items=['later-addition'])
    prepared = research_config.normalize(options, frozen_skill_selection=frozen)
    assert prepared['skills']['sets'] == frozen['sets']
    assert len(calls) == 1


def test_fork_without_original_skills_does_not_enable_new_provider_offers(offered):
    options, calls, selection, source = offered
    assert 'skills' not in research_config.normalize(options, frozen_skill_selection={'sets': {}})
    assert len(calls) == 1


def test_frozen_skill_selection_preserves_observer_and_delivery_provider_gates(offered, monkeypatch):
    options, calls, selection, source = offered
    observer = dict(adapter='declared-observer', contract_version=1)
    adapter = dict(adapter='declared-delivery')
    declared = ExecutionInterfaceRegistration('offered', '1', RuntimePluginFactory(lambda _: None),
        research_defaults=lambda configuration, root: dict(optional_skill_sets=selection,
            history_observer=observer, delivery_adapter=adapter))
    monkeypatch.setattr(experiments, 'registration', lambda executor: (declared, 'binding'))
    options['delivery_policy'] = dict(adapter={})
    normalized = research_config.normalize(options, frozen_skill_selection={'sets': {}})
    assert normalized['history_observer'] == observer
    assert normalized['delivery_policy']['adapter'] == adapter
    assert 'skills' not in normalized
    options['history_observer'] = dict(adapter='conflicting-observer')
    with pytest.raises(ValueError, match='history observer conflicts'):
        research_config.normalize(options, frozen_skill_selection={'sets': {}})
    options.pop('history_observer')
    options['delivery_policy']['adapter']['adapter'] = 'conflicting-delivery'
    with pytest.raises(ValueError, match='delivery adapter conflicts'):
        research_config.normalize(options, frozen_skill_selection={'sets': {}})


@pytest.mark.parametrize('malformed_offer', [
    {'domain': ['alpha']},
    {'domain': {'items': ['../alpha']}},
    {'domain': {'root': '/arbitrary', 'items': ['alpha']}},
])
@pytest.mark.parametrize('original_skills', [False, True], ids=['no-original-skills', 'selected-original-skills'])
def test_frozen_fork_ignores_malformed_new_offer_but_fresh_preparation_rejects_it(
        offered, malformed_offer, original_skills):
    options, calls, selection, source = offered
    frozen = dict(sets=copy.deepcopy(selection) if original_skills else {})
    if original_skills:
        options['skills'] = copy.deepcopy(frozen)
    selection.clear(); selection.update(malformed_offer)
    normalized = research_config.normalize(options, frozen_skill_selection=frozen)
    if original_skills:
        assert normalized['skills'] == frozen
    else:
        assert 'skills' not in normalized
    assert len(calls) == 1
    with pytest.raises(ValueError, match='Skill|simple directory'):
        research_config.prepare(**options)
    assert len(calls) == 2
    assert not options['root'].exists()
