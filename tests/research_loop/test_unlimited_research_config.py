"""Unlimited global ceilings retain finite task and operation boundaries."""
import copy

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, configuration_updates as updates,
    contracts as c, controller, experiments, native, research_config,
    storage_policy as storage)


def node_targets():
    return {role: dict(max_observed_tokens=100, max_seconds=20) for role in 'HWAE'}


def declaration(tmp_path, *, tokens='unlimited', seconds='unlimited'):
    public = tmp_path / 'public'
    public.mkdir(exist_ok=True)
    return dict(version=1,
        research=dict(root='instance', public_root='public',
            objective_text='Retain exact observations and unfinished work.'),
        agent=dict(launcher=['never-launched']),
        budget=dict(max_tokens=tokens, max_seconds=seconds, max_steps=8,
            node_targets=node_targets()))


def prepare_options(tmp_path, *, tokens=None, seconds=None):
    public = tmp_path / 'public'
    public.mkdir(exist_ok=True)
    return dict(root=tmp_path / 'instance', objective=public / 'objective.md',
        objective_text='Retain exact observations and unfinished work.',
        public_root=public, launcher=['never-launched'], max_tokens=tokens,
        max_seconds=seconds, max_steps=8, model='fixture-no-model',
        reasoning_effort='high', search_policy=None,
        node_budget_policy=node_targets())


@pytest.mark.parametrize('tokens,seconds', [
    ('unlimited', 'unlimited'), ('unlimited', 120), (1000, 'unlimited'), (1000, 120.5),
])
def test_declaration_roundtrip_preserves_unlimited_and_finite_globals(tmp_path, tokens, seconds):
    document = declaration(tmp_path, tokens=tokens, seconds=seconds)
    original = copy.deepcopy(document)
    options = research_config.from_document(document, base=tmp_path)
    settings = research_config.prepare(validate_only=True, **options)
    assert settings['max_tokens'] == (None if tokens == 'unlimited' else tokens)
    assert settings['max_seconds'] == (None if seconds == 'unlimited' else seconds)
    restored = research_config.to_document(tmp_path / 'instance', settings)['budget']
    assert {key: restored[key] for key in document['budget']} == document['budget']
    assert document == original
    assert not (tmp_path / 'instance').exists()


@pytest.mark.parametrize('field', ['max_tokens', 'max_seconds', 'max_steps'])
def test_unlimited_declaration_does_not_default_missing_required_caps(tmp_path, field):
    document = declaration(tmp_path)
    del document['budget'][field]
    with pytest.raises(ValueError, match=rf'budget\.{field} is required'):
        research_config.from_document(document, base=tmp_path)


@pytest.mark.parametrize('field', ['max_tokens', 'max_seconds'])
@pytest.mark.parametrize('invalid', ['Unlimited', ' unlimited', 'unlimited ', '1000', True, 0, -1, float('inf')])
def test_only_exact_unlimited_literal_relaxes_global_cap(tmp_path, field, invalid):
    document = declaration(tmp_path)
    document['budget'][field] = invalid
    options = research_config.from_document(document, base=tmp_path)
    with pytest.raises(ValueError, match=field + ' must be'):
        research_config.prepare(validate_only=True, **options)


@pytest.mark.parametrize('invalid', ['unlimited', None, float('inf')])
def test_unlimited_globals_keep_step_cap_finite(tmp_path, invalid):
    options = prepare_options(tmp_path)
    options['max_steps'] = invalid
    with pytest.raises(ValueError, match='max_steps must be a positive integer'):
        controller.prepare(validate_only=True, **options)


@pytest.mark.parametrize('tokens,seconds', [(None, None), (None, 120), (1000, None)])
def test_programmatic_prepare_retains_nullable_globals_and_finite_storage_windows(tmp_path, monkeypatch, tokens, seconds):
    windows = []
    original_deadline = storage.Deadline
    def deadline(value, upstream=None):
        windows.append(value)
        return original_deadline(value, upstream)
    monkeypatch.setattr(storage, 'Deadline', deadline)
    options = prepare_options(tmp_path, tokens=tokens, seconds=seconds)
    settings = controller.prepare(**options)
    assert settings['max_tokens'] == tokens
    assert settings['max_seconds'] == seconds
    root = options['root']
    original = (root / 'settings.json').read_bytes()
    preparation_windows = len(windows)
    job = c.build_job(root, 1, 'H', [])
    assert job['role'] == 'H'
    expected = 65 if seconds is None else seconds
    assert windows[0] == expected and expected in windows[preparation_windows:]
    assert all(type(value) in (int, float) and 0 < value < float('inf') for value in windows)
    assert (root / 'settings.json').read_bytes() == original


def test_default_local_execution_retains_65_second_hard_limit(tmp_path, monkeypatch):
    configurations = []
    def configured(value):
        configurations.append(value)
        return value
    monkeypatch.setattr(experiments, 'operator_configuration', configured)
    settings = controller.prepare(**prepare_options(tmp_path),
        experiment_mode='local', validate_only=True)
    assert configurations[0]['configuration']['max_seconds'] == 65
    assert settings['execution_config']['configuration']['max_seconds'] == 65


@pytest.mark.parametrize('standard', [False, True])
@pytest.mark.parametrize('tokens,seconds', [(None, None), (None, 120), (1000, None)])
def test_unlimited_globals_require_explicit_finite_all_role_targets(tmp_path, monkeypatch, standard, tokens, seconds):
    options = prepare_options(tmp_path, tokens=tokens, seconds=seconds)
    del options['node_budget_policy']
    if standard:
        options['launcher'] = ['codex']
        monkeypatch.setattr(research_config.shutil, 'which',
            lambda executable: pytest.fail('Missing targets must be explained before launcher lookup'))
    with pytest.raises(ValueError, match='explicit finite all-role budget.node_targets'):
        research_config.prepare(validate_only=True, **options)
    assert not options['root'].exists()


def test_unlimited_globals_do_not_allow_unlimited_node_targets(tmp_path):
    options = prepare_options(tmp_path)
    options['node_budget_policy']['W']['max_seconds'] = None
    with pytest.raises(ValueError, match='finite'):
        controller.prepare(validate_only=True, **options)


@pytest.mark.parametrize('tokens,seconds,observed,reason', [
    (None, None, 1000000, None), (100, None, 100, 'token_limit'),
    (None, 0, 0, 'wall_limit'), (100, 1, 99, None),
])
def test_launch_admission_ignores_only_absent_global_dimensions(monkeypatch, tokens, seconds, observed, reason):
    monkeypatch.setattr(budget, 'snapshot', lambda *args: dict(max_tokens=tokens, seconds_remaining=seconds))
    monkeypatch.setattr(native, 'usage', lambda *args: dict(observed_tokens=observed, unknown_steps=[]))
    assert controller._launch_limit(None, {}) == reason


def test_unlimited_globals_preserve_unknown_usage_and_closeout_step_checks(monkeypatch):
    limits = dict(max_tokens=None, seconds_remaining=None)
    meter = dict(observed_tokens=1000000, unknown_steps=[1])
    monkeypatch.setattr(budget, 'snapshot', lambda *args: limits)
    monkeypatch.setattr(native, 'usage', lambda *args: meter)
    assert controller._launch_limit(None, {}) == 'usage_unknown'
    settings = dict(delivery_policy=dict(closeout_a=dict(max_observed_tokens=100, max_seconds=20)))
    monkeypatch.setattr(updates, 'current', lambda *args: settings)
    monkeypatch.setattr(budget, 'closeout_floor', lambda *args, **kwargs: dict(seconds=5, steps=1))
    monkeypatch.setattr(budget, 'effective_max_steps', lambda *args: 8)
    monkeypatch.setattr(budget, 'used_steps', lambda *args: 0)
    assert not controller._closeout_funds(None)
    meter['unknown_steps'] = []
    assert controller._closeout_funds(None)
    monkeypatch.setattr(budget, 'used_steps', lambda *args: 7)
    assert not controller._closeout_funds(None)


@pytest.mark.parametrize('fields', [('max_tokens',), ('max_seconds',), ('max_tokens', 'max_seconds')])
def test_configuration_binding_applies_unlimited_once_and_can_restore_finite_caps(tmp_path, fields):
    options = prepare_options(tmp_path, tokens=1000, seconds=120)
    del options['node_budget_policy']
    root = options['root']
    controller.prepare(**options)
    original = (root / 'settings.json').read_bytes()
    finite = budget.ceilings(root)
    document = updates.status(root)['configuration']
    document['budget']['node_targets'] = node_targets()
    for field in fields:
        document['budget'][field] = 'unlimited'
    with pytest.raises(ValueError, match='authorization note'):
        updates.submit(root, document, expected_revision=0)
    saved = updates.submit(root, document, expected_revision=0,
        authorization_note='Explicitly remove the selected global ceilings.')
    assert saved['pending']
    assert updates.revisions(root)[0]['budget_changes'] == {field: None for field in fields}
    assert budget.ceilings(root) == finite
    first = updates.bind_new_job(root, 1)
    assert updates.bind_new_job(root, 1) == first
    ceilings = budget.ceilings(root)
    assert all(ceilings[field] is None for field in fields)
    assert all(updates.for_new_job(root, 1)[field] is None for field in fields)
    assert updates.for_new_job(root, 1)['node_budget_policy'] == node_targets()
    events = budget._events(root)[0]
    assert len([event for event in events if event['kind'] == 'budget_amend']) == 1
    state = updates.status(root)
    assert all(state['configuration']['budget'][field] == 'unlimited' for field in fields)
    assert updates.submit(root, state['configuration'], expected_revision=state['revision'])['status'] == 'configuration_unchanged'
    for field in fields:
        state['configuration']['budget'][field] = finite[field]
    updates.submit(root, state['configuration'], expected_revision=state['revision'],
        authorization_note='Explicitly restore finite global ceilings for future work.')
    updates.bind_new_job(root, 2)
    assert budget.ceilings(root) == finite
    assert (root / 'settings.json').read_bytes() == original


def test_configuration_retry_recognizes_own_unlimited_amendment_after_interruption(tmp_path, monkeypatch):
    options = prepare_options(tmp_path, tokens=1000, seconds=120)
    root = options['root']
    controller.prepare(**options)
    document = updates.status(root)['configuration']
    document['budget'].update(max_tokens='unlimited', max_seconds='unlimited')
    updates.submit(root, document, authorization_note='Remove the shared caps with retained finite node targets.')
    def interrupted_binding(selected_root, step, value, *, base):
        assert selected_root == root and step == 1
        raise InterruptedError('Crash after the amendment before binding')
    with monkeypatch.context() as patch:
        patch.setattr(accepted_budget, 'accept_configuration_binding_from_base', interrupted_binding)
        with pytest.raises(InterruptedError):
            updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] is None
    assert budget.ceilings(root)['max_seconds'] is None
    budget.amend(root, max_tokens=2000, authorization_note='A newer finite ceiling retains the original operation.')
    events = list(budget._events(root)[0])
    updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 2000
    assert list(budget._events(root)[0]) == events
