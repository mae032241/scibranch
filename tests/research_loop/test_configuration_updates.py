"""Operational edits bind new nodes without rewriting the research originals."""
import copy

import pytest

from scidiscovery.research_loop import budget, configuration_updates as updates, contracts as c, controller, history, research_config


def instance(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare alternatives and retain limitations.')
    root = tmp_path / 'research'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=10000,
                       max_seconds=120, max_steps=8, model='original-model', reasoning_effort='high', search_policy=None)
    return root


def edit(root, **agent):
    state = updates.status(root)
    document = copy.deepcopy(state['configuration'])
    document['agent'].update(agent)
    return updates.submit(root, document, expected_revision=state['revision'])


def test_pending_revision_binds_only_new_jobs_and_keeps_original_bytes(tmp_path, monkeypatch):
    root = instance(tmp_path)
    monkeypatch.setattr(history, 'source_sequence', lambda *args, **kwargs: [])
    base = (root / 'settings.json').read_bytes()
    first = c.build_job(root, 1, 'H', [])
    first_bytes = (root / 'jobs/001.json').read_bytes()
    saved = edit(root, model='new-model')
    assert saved['pending'] and saved['active_revision'] == 0
    assert updates.for_job(root, first)['model'] == 'original-model'
    second = c.build_job(root, 2, 'H', [])
    assert updates.for_job(root, second)['model'] == 'new-model'
    assert updates.status(root)['active_revision'] == 1
    assert c.build_job(root, 1, 'H', []) == first
    assert (root / 'jobs/001.json').read_bytes() == first_bytes
    assert (root / 'settings.json').read_bytes() == base
    third = c.build_job(root, 3, 'H', [])
    assert third['configuration_revision'] == second['configuration_revision']


def test_stale_invalid_and_noop_updates_do_not_publish_revision(tmp_path):
    root = instance(tmp_path)
    before = updates.status(root)
    assert updates.submit(root, before['configuration'], expected_revision=0)['status'] == 'configuration_unchanged'
    edit(root, model='next-model')
    with pytest.raises(ValueError, match='reload'):
        updates.submit(root, before['configuration'], expected_revision=0)
    invalid = updates.status(root)['configuration']
    invalid['research']['objective_text'] = 'new objective'
    with pytest.raises(ValueError, match='immutable'):
        updates.submit(root, invalid, expected_revision=1)
    assert len(updates.revisions(root)) == 1


def test_reservation_survives_interruption_and_later_save(tmp_path):
    root = instance(tmp_path)
    edit(root, model='one')
    first = updates.bind_new_job(root, 1)
    edit(root, model='two')
    assert updates.bind_new_job(root, 1) == first
    assert updates.for_new_job(root, 1)['model'] == 'one'
    assert updates.for_new_job(root, 2)['model'] == 'two'


def test_bound_history_does_not_depend_on_future_pending_integrity(tmp_path):
    root = instance(tmp_path)
    edit(root, model='one')
    ref = updates.bind_new_job(root, 1)
    edit(root, model='two')
    latest = root / 'host-control/configuration/revisions/000002.json'
    latest.chmod(0o600)
    latest.write_text('{}')
    assert updates.for_job(root, {'configuration_revision': ref})['model'] == 'one'
    with pytest.raises(ValueError, match='original changed'):
        updates.current(root)


def test_budget_edit_requires_user_note_and_amends_once_without_refunding(tmp_path):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    document['budget']['max_steps'] = 10
    with pytest.raises(ValueError, match='authorization note'):
        updates.submit(root, document, expected_revision=0)
    updates.submit(root, document, expected_revision=0, authorization_note='Increase ceilings for one more comparison')
    assert budget.snapshot(root)['max_tokens'] == 10000
    updates.bind_new_job(root, 1)
    assert budget.snapshot(root)['max_tokens'] == 12000
    budget._amend_locked(root, max_tokens=14000, authorization_note='Additional comparison')
    updates.bind_new_job(root, 2)
    assert budget.snapshot(root)['max_tokens'] == 14000
    assert updates.status(root)['configuration']['budget']['max_tokens'] == 14000
    assert updates.submit(root, updates.status(root)['configuration'], expected_revision=1)['status'] == 'configuration_unchanged'


def test_model_edit_preserves_hidden_native_and_role_guidance_options(tmp_path):
    root = instance(tmp_path)
    from scidiscovery.research_loop import backends
    original = c.settings(root)
    original.update(backends.configuration('codex', original['native_launcher_argv'], None))
    original.pop('role_guidance', None)
    c.write(root / 'settings.json', original)
    c.write(root / 'settings-identity.json', dict(sha256=c.digest(root / 'settings.json')))
    assert original['native_options'] is None
    edit(root, model='changed')
    changed = updates.current(root)
    assert {key: value for key, value in changed.items() if key != 'model'} == {
        key: value for key, value in original.items() if key != 'model'}


def test_model_only_pending_save_does_not_revert_intervening_budget_amendment(tmp_path):
    root = instance(tmp_path)
    edit(root, model='changed')
    budget._amend_locked(root, max_tokens=14000, authorization_note='Separate operator change')
    updates.bind_new_job(root, 1)
    assert budget.snapshot(root)['max_tokens'] == 14000


def test_step_generator_applies_pending_step_increase_before_old_limit(tmp_path):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_steps'] = 10
    updates.submit(root, document, expected_revision=0, authorization_note='Allow two additional nodes')
    generator = controller._step_numbers(root, 9)
    assert next(generator) == 9
    assert budget.snapshot(root)['max_steps'] == 10


def test_second_pending_model_edit_keeps_intervening_ledger_amendment(tmp_path):
    root = instance(tmp_path)
    edit(root, model='one')
    budget._amend_locked(root, max_tokens=14000, authorization_note='Separate operator change')
    assert updates.status(root)['configuration']['budget']['max_tokens'] == 14000
    edit(root, model='two')
    updates.bind_new_job(root, 1)
    assert budget.snapshot(root)['max_tokens'] == 14000


def test_current_policy_read_does_not_read_budget_or_results(tmp_path, monkeypatch):
    root = instance(tmp_path)
    edit(root, model='queued')
    monkeypatch.setattr(budget, 'snapshot', lambda *args, **kwargs: pytest.fail('Policy reads cannot sample admission'))
    assert updates.current(root)['model'] == 'queued'


def test_configuration_limits_reject_changed_amendment_predecessor(tmp_path):
    root = instance(tmp_path)
    budget.amend(root, max_tokens=12000, authorization_note='Allow another comparison')
    path = root / 'control-ledger/000001.json'
    event = c.read(path)
    event['before']['max_tokens'] += 1
    c.write(path, event)
    with pytest.raises(ValueError, match='Budget amendment predecessor changed'):
        updates.status(root)


def test_paused_step_boundary_does_not_apply_queued_configuration(tmp_path):
    root = instance(tmp_path)
    edit(root, model='queued')
    (root / 'pause.request').write_text('Pause before the next node.')
    assert next(controller._step_numbers(root, 1)) == 1
    result = controller.run(root)
    assert result['status'] == 'paused'
    assert not (root / 'jobs/001.json').exists()
    assert updates.status(root)['pending']
    assert not (root / 'host-control/configuration/bindings/000001.json').exists()


def test_configuration_budget_change_is_not_a_final_resume_statement():
    event = dict(kind='budget_amend', seq=2, operation_id='configuration-1',
                 before=dict(max_tokens=10, max_seconds=10), after=dict(max_tokens=20, max_seconds=10))
    assert not budget._threshold_raised([event], 1)
    explicit = {key: value for key, value in event.items() if key != 'operation_id'}
    assert budget._threshold_raised([explicit], 1)


def resource_instance(tmp_path, *, enabled):
    import sys
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Preserve cumulative resource ownership.')
    root = tmp_path / 'research'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=10000,
        max_seconds=120, max_steps=8, model='original-model', reasoning_effort='high',
        experiment_mode='local', search_policy=None,
        execution_config=dict(executor='local_execution:command', configuration=dict(run_argv=[sys.executable], max_seconds=10)),
        formal_review_policy=dict(version=1), execution_authority_policy=dict(version=1),
        execution_resource_policy=(dict(version=1, allowance=dict(wall_time_seconds=100, max_storage_bytes=10000)) if enabled else None))
    return root


def test_model_only_revision_preserves_existing_execution_resource_grant(tmp_path):
    root = resource_instance(tmp_path, enabled=True)
    original = budget.execution_grant(root)
    events, _ = budget._events(root, c.settings(root))
    edit(root, model='new-model')
    updates.bind_new_job(root, 1)
    assert budget.execution_grant(root) == original
    assert budget._events(root, c.settings(root))[0] == events


def test_first_resource_enable_uses_revision_origin_and_retains_grant_when_disabled(tmp_path):
    root = resource_instance(tmp_path, enabled=False)
    document = updates.status(root)['configuration']
    document['executor']['resource_policy'] = dict(version=1, allowance=dict(wall_time_seconds=100, max_storage_bytes=10000))
    updates.submit(root, document, authorization_note='Authorize a finite execution resource allowance')
    updates.bind_new_job(root, 1)
    original = budget.execution_grant(root)
    document = updates.status(root)['configuration']
    document['executor'].pop('resource_policy')
    updates.submit(root, document, authorization_note='Disable future execution resources')
    updates.bind_new_job(root, 2)
    assert budget.execution_grant(root) == original
    assert budget.execution_accounting(root)['remaining'] == original['allowance']


def test_model_only_edit_preserves_separate_execution_resource_amendment(tmp_path):
    root = resource_instance(tmp_path, enabled=True)
    budget.execution_grant(root)
    revised = budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=20000),
                                           authorization_note='Increase cumulative execution allowance')
    events, _ = budget._events(root, c.settings(root))
    edit(root, model='changed')
    updates.bind_new_job(root, 1)
    assert budget.execution_grant(root) == revised
    assert budget._events(root, c.settings(root))[0] == events


def test_unchanged_file_only_plugin_configuration_is_not_reopened_for_model_edit(tmp_path, monkeypatch):
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    from scidiscovery.research_loop import experiments
    from test_research_config import declaration
    calls = []

    def configure(config, source, *, binding):
        calls.append(source)
        return config

    def build(_):
        raise AssertionError('Configuration editing must not construct a runtime')

    declared = ExecutionInterfaceRegistration('third_party', '1', RuntimePluginFactory(
        build, configure=configure, requires_config_file=True))
    monkeypatch.setattr(experiments, 'registration', lambda executor: (declared, 'binding'))
    plugin = tmp_path / 'plugin.toml'
    plugin.write_text('setting = 9\n')
    config = declaration(tmp_path, '\n[executor.config]\nexecutor = "third_party:binding"\nconfig_file = "plugin.toml"\n')
    original = research_config.prepare(config)
    root = tmp_path / 'instance'
    plugin.write_text('invalid TOML = [')
    edit(root, model='changed')
    assert updates.current(root)['execution_config'] == original['execution_config']
    assert calls == [plugin]
    document = updates.status(root)['configuration']
    document['executor']['config'] = dict(executor='third_party:binding', config_file=str(plugin))
    with pytest.raises(ValueError):
        updates.submit(root, document)
