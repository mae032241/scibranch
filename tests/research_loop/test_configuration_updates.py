"""Operational edits bind new nodes without rewriting the research originals."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
import threading

import pytest

from scidiscovery.research_loop import atomic_files, budget, configuration_updates as updates, contracts as c, controller, history, research_config
from test_history_handoff import finish
from test_composed_execution_results import composed_registry
from execution_fixtures import execution_registry


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


def accounting_events(root):
    """Explicit audit of accounting originals, excluding new-node control facts."""
    events, _ = budget.audit_events(root)
    return [event for event in events if event['kind'] == 'budget_amend' or event['kind'].startswith('resource_')]


def test_pending_revision_binds_only_new_jobs_and_keeps_original_bytes(tmp_path, monkeypatch):
    root = instance(tmp_path)
    monkeypatch.setattr(history, 'source_sequence', lambda *args, **kwargs: [])
    base = (root / 'settings.json').read_bytes()
    first = c.build_job(root, 1, 'H', [])
    first_bytes = (root / 'jobs/001.json').read_bytes()
    saved = edit(root, model='new-model')
    assert saved['pending'] and saved['active_revision'] == 0
    assert updates.for_job(root, first)['model'] == 'original-model'
    finish(root, 1, 'H', [])
    second = c.build_job(root, 2, 'H', [])
    assert updates.for_job(root, second)['model'] == 'new-model'
    assert updates.status(root)['active_revision'] == 1
    assert c.build_job(root, 1, 'H', []) == first
    assert (root / 'jobs/001.json').read_bytes() == first_bytes
    assert (root / 'settings.json').read_bytes() == base
    finish(root, 2, 'H', [])
    third = c.build_job(root, 3, 'H', [])
    assert third['configuration_revision'] == second['configuration_revision']


def test_context_cadence_revision_projects_new_node_and_preserves_original_job_binding(tmp_path, monkeypatch, composed_registry):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Preserve the original cadence until an explicit new-node revision.')
    binary = tmp_path / 'codex'
    binary.write_text('#!/bin/sh\nexit 99\n')
    binary.chmod(0o755)
    root = tmp_path / 'research'
    controller.prepare(root, objective, public, [str(binary)], max_tokens=10000,
        max_seconds=120, max_steps=8, model='original-model', reasoning_effort='high', search_policy=None,
        execution_config=dict(executor='composed_execution:fixture',
            configuration=dict(synchronous=True,collect=True,terminal_status='known_success')),
        node_budget_policy={role: dict(max_observed_tokens=10000,max_seconds=120) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
            output_repair=True,output_only=True,native_home=str(tmp_path/'native-home')),
        worker_fragment_policy=dict(context='same_session'))
    monkeypatch.setattr(history, 'source_sequence', lambda *args, **kwargs: [])
    first = c.build_job(root, 1, 'H', [])
    original = (root/'settings.json').read_bytes()
    saved = edit(root, model='next-model')
    assert 'worker_context_windows' not in saved['configuration']['policies']
    assert 'max_context_windows' not in updates.for_new_job(root, 2)['worker_fragment_policy']
    state = updates.status(root)
    state['configuration']['policies']['worker_context_windows'] = 1
    updates.submit(root, state['configuration'], expected_revision=state['revision'])
    assert 'max_context_windows' not in updates.for_job(root, first)['worker_fragment_policy']
    assert updates.for_new_job(root, 2)['worker_fragment_policy']['max_context_windows'] == 1
    assert (root/'settings.json').read_bytes() == original


def test_automatic_recovery_edit_binds_new_nodes_and_preserves_original_policy_and_meters(tmp_path, monkeypatch):
    from scidiscovery.research_loop import native_recovery
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain original identity and consumed work during recovery.')
    binary = tmp_path / 'codex'
    binary.write_text('#!/bin/sh\nexit 99\n')
    binary.chmod(0o755)
    root = tmp_path / 'research'
    legacy = dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn',
        output_repair=True, output_only=True, native_home=str(tmp_path / 'native-home'))
    research_config.prepare(root=root, objective=objective, public_root=public,
        launcher=[str(binary)], max_tokens=10000, max_seconds=120, max_steps=8,
        resume_policy=legacy, search_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=10000, max_seconds=120) for role in 'HWAE'})
    monkeypatch.setattr(history, 'source_sequence', lambda *args, **kwargs: [])
    first = c.build_job(root, 1, 'H', [])
    originals = {path: path.read_bytes() for path in (root / 'settings.json', root / 'jobs/001.json')}
    prior_policy = copy.deepcopy(updates.for_job(root, first)['resume_policy'])
    assert 'automatic_recovery' not in prior_policy
    budget._amend_locked(root, max_tokens=12000, authorization_note='Existing allowance before recovery-policy edit')
    meter = root / budget.authorization_revision(root)['path']
    originals[meter] = meter.read_bytes()
    events = accounting_events(root)
    document = updates.status(root)['configuration']
    automatic = native_recovery.policy(dict(max_retries=1, max_wait_seconds=600))
    document['policies']['resume']['automatic_recovery'] = automatic
    saved = updates.submit(root, document, expected_revision=0)
    assert saved['pending']
    assert updates.for_job(root, first)['resume_policy'] == prior_policy
    assert updates.for_new_job(root, 2)['resume_policy']['automatic_recovery'] == automatic
    assert saved['configuration']['policies']['resume']['automatic_recovery'] == automatic
    finish(root, 1, 'H', [])
    second = c.build_job(root, 2, 'H', [])
    assert updates.for_job(root, second)['resume_policy']['automatic_recovery'] == automatic
    assert accounting_events(root) == events
    assert all(path.read_bytes() == data for path, data in originals.items())
    # Removing the independent grant is explicit and applies only to a new node.
    state = updates.status(root)
    state['configuration']['policies']['resume'].pop('automatic_recovery')
    updates.submit(root, state['configuration'], expected_revision=state['revision'])
    assert updates.for_job(root, second)['resume_policy']['automatic_recovery'] == automatic
    assert 'automatic_recovery' not in updates.for_new_job(root, 3)['resume_policy']


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
    with pytest.raises(ValueError, match='Configuration revision is missing or changed'):
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
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
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


@pytest.mark.parametrize('final_tokens', [10000, 12000, 14000])
def test_pending_budget_change_rejects_later_authorization_even_when_values_match(tmp_path, final_tokens):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    updates.submit(root, document, authorization_note='Queued comparison allowance')
    original = updates.revisions(root)[0]
    budget._amend_locked(root, max_tokens=14000, authorization_note='Independent newer allowance')
    if final_tokens != 14000:
        budget._amend_locked(root, max_tokens=final_tokens, authorization_note='Explicit later amount')
    events, _ = budget.audit_events(root)
    with pytest.raises(ValueError, match='budget conflicts.*reload'):
        updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == final_tokens
    assert budget.audit_events(root)[0] == events
    assert updates.revisions(root)[0] == original
    assert not (root / 'host-control/configuration/bindings/000001.json').exists()


def test_model_edit_preserves_pending_budget_predecessor_after_cli_amendment(tmp_path):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    updates.submit(root, document, authorization_note='Original pending budget statement')
    baseline = updates.revisions(root)[0]['budget_baseline']
    budget._amend_locked(root, max_tokens=14000, authorization_note='Later CLI allowance')
    edit(root, model='changed')
    latest = updates.revisions(root)[-1]
    assert latest['budget_baseline'] == baseline
    assert latest['authorization_note'] == 'Original pending budget statement'
    with pytest.raises(ValueError, match='budget conflicts'):
        updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 14000


@pytest.mark.parametrize('change_model', [False, True])
def test_explicit_new_statement_reauthorizes_same_pending_budget_after_conflict(tmp_path, change_model):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    updates.submit(root, document, authorization_note='Original queued allowance')
    budget._amend_locked(root, max_tokens=14000, authorization_note='Independent newer allowance')
    with pytest.raises(ValueError, match='budget conflicts'):
        updates.bind_new_job(root, 1)
    state = updates.status(root)
    assert updates.submit(root, state['configuration'])['status'] == 'configuration_unchanged'
    document = state['configuration']
    if change_model:
        document['agent']['model'] = 'explicitly-revised-model'
    saved = updates.submit(root, document, expected_revision=state['revision'],
        authorization_note='New decision: retain 12000 after reading the newer CLI allowance')
    assert saved['revision'] == 2
    assert updates.revisions(root)[-1]['budget_baseline'] == budget.authorization_revision(root)
    updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 12000


def test_explicit_uncontested_budget_decrease_is_applied(tmp_path):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 8000
    updates.submit(root, document, authorization_note='Explicitly reduce remaining research scope')
    updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 8000


def test_budget_bind_retry_recognizes_own_applied_operation_before_later_cli_change(tmp_path, monkeypatch):
    root = instance(tmp_path)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    updates.submit(root, document, authorization_note='Queued budget allowance')
    original_publish = atomic_files.publish_bytes

    def interrupted_binding(path, payload, **options):
        if path.parent.name == 'bindings':
            raise InterruptedError('Crash after amendment before binding')
        return original_publish(path, payload, **options)

    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'publish_bytes', interrupted_binding)
        with pytest.raises(InterruptedError):
            updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 12000
    budget._amend_locked(root, max_tokens=14000, authorization_note='Later CLI ceiling')
    events, _ = budget.audit_events(root)
    updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 14000
    assert budget.audit_events(root)[0] == events
    assert len([event for event in events if event.get('operation_id') == 'configuration-1']) == 1


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
    before = updates.status(root)
    path = root / budget.authorization_revision(root)['path']
    event = c.read(path)
    event['before']['max_tokens'] += 1
    c.write(path, event)
    # Ordinary status consumes accepted ceilings, not a replay of its prefix.
    assert updates.status(root) == before
    events, _ = budget.audit_events(root)
    with pytest.raises(ValueError, match='Budget amendment predecessor changed'):
        budget._ceiling_limits(c.settings(root), events)


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
    events = accounting_events(root)
    edit(root, model='new-model')
    updates.bind_new_job(root, 1)
    assert budget.execution_grant(root) == original
    assert accounting_events(root) == events


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
    events = accounting_events(root)
    edit(root, model='changed')
    updates.bind_new_job(root, 1)
    assert budget.execution_grant(root) == revised
    assert accounting_events(root) == events


@pytest.mark.parametrize('final_wall', [100, 120, 150])
def test_resource_conflict_is_detected_before_any_queued_budget_change(tmp_path, final_wall):
    root = resource_instance(tmp_path, enabled=True)
    budget.execution_grant(root)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    document['executor']['resource_policy']['allowance']['wall_time_seconds'] = 120
    updates.submit(root, document, authorization_note='Queued combined allowance adjustment')
    budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
        authorization_note='Later explicit resource allowance')
    if final_wall != 150:
        budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=final_wall, max_storage_bytes=10000),
            authorization_note='Explicit subsequent resource amount')
    events, _ = budget.audit_events(root)
    with pytest.raises(ValueError, match='execution resources conflict.*reload'):
        updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 10000
    assert budget.execution_grant(root)['allowance']['wall_time_seconds'] == final_wall
    assert budget.audit_events(root)[0] == events
    assert not (root / 'host-control/configuration/bindings/000001.json').exists()


def test_resource_bind_retry_uses_original_operation_after_later_grant_change(tmp_path, monkeypatch):
    root = resource_instance(tmp_path, enabled=True)
    budget.execution_grant(root)
    document = updates.status(root)['configuration']
    document['executor']['resource_policy']['allowance']['wall_time_seconds'] = 120
    updates.submit(root, document, authorization_note='Queued execution resource adjustment')
    original_publish = atomic_files.publish_bytes

    def interrupted_binding(path, payload, **options):
        if path.parent.name == 'bindings':
            raise InterruptedError('Crash after resource amendment before binding')
        return original_publish(path, payload, **options)

    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'publish_bytes', interrupted_binding)
        with pytest.raises(InterruptedError):
            updates.bind_new_job(root, 1)
    assert budget.execution_grant(root)['allowance']['wall_time_seconds'] == 120
    revised = budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
        authorization_note='Later independent resource adjustment')
    events, _ = budget.audit_events(root)
    original_event = next(event for event in events if event.get('operation_id') == 'configuration-1-resources')
    replay = budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=120, max_storage_bytes=10000),
        authorization_note='Queued execution resource adjustment', operation_id='configuration-1-resources')
    assert replay['ref'] == budget._resource_ref(root, original_event)
    with pytest.raises(ValueError, match='operation changed'):
        budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=121, max_storage_bytes=10000),
            authorization_note='Cannot mutate an original operation', operation_id='configuration-1-resources')
    updates.bind_new_job(root, 1)
    assert budget.execution_grant(root) == revised
    assert budget.audit_events(root)[0] == events
    assert len([event for event in events if event.get('operation_id') == 'configuration-1-resources']) == 1


def test_first_resource_seed_interruption_is_not_a_competing_authorization(tmp_path, monkeypatch):
    root = resource_instance(tmp_path, enabled=False)
    document = updates.status(root)['configuration']
    document['executor']['resource_policy'] = dict(version=1, allowance=dict(wall_time_seconds=100, max_storage_bytes=10000))
    updates.submit(root, document, authorization_note='Enable the original execution resource pool')
    original_append = budget._append

    def interrupted_amendment(*args, **fields):
        if fields.get('operation_id') == 'configuration-1-resources':
            raise InterruptedError('Crash after initial seed before operation receipt')
        return original_append(*args, **fields)

    with monkeypatch.context() as patch:
        patch.setattr(budget, '_append', interrupted_amendment)
        with pytest.raises(InterruptedError):
            updates.bind_new_job(root, 1)
    assert budget.execution_grant(root)['allowance'] == document['executor']['resource_policy']['allowance']
    updates.bind_new_job(root, 1)
    events, _ = budget.audit_events(root)
    grants = [event for event in events if event['kind'] == 'resource_grant']
    assert len(grants) == 2
    assert grants[0]['previous_grant'] is None
    assert grants[1]['operation_id'] == 'configuration-1-resources'


def test_resource_model_edit_does_not_rebase_pending_resource_authorization(tmp_path):
    root = resource_instance(tmp_path, enabled=True)
    document = updates.status(root)['configuration']
    document['executor']['resource_policy']['allowance']['wall_time_seconds'] = 120
    updates.submit(root, document, authorization_note='Original resource adjustment')
    baseline = updates.revisions(root)[0]['resource_baseline']
    budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
        authorization_note='Later independent resource adjustment')
    edit(root, model='changed')
    assert updates.revisions(root)[-1]['resource_baseline'] == baseline
    with pytest.raises(ValueError, match='execution resources conflict'):
        updates.bind_new_job(root, 1)
    assert budget.execution_grant(root)['allowance']['wall_time_seconds'] == 150


@pytest.mark.parametrize('change_model', [False, True])
def test_explicit_new_statement_reauthorizes_same_pending_resources_after_conflict(tmp_path, change_model):
    root = resource_instance(tmp_path, enabled=True)
    document = updates.status(root)['configuration']
    document['executor']['resource_policy']['allowance']['wall_time_seconds'] = 120
    updates.submit(root, document, authorization_note='Original queued resource allowance')
    budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
        authorization_note='Independent newer resource allowance')
    with pytest.raises(ValueError, match='execution resources conflict'):
        updates.bind_new_job(root, 1)
    state = updates.status(root)
    assert updates.submit(root, state['configuration'])['status'] == 'configuration_unchanged'
    document = state['configuration']
    if change_model:
        document['agent']['model'] = 'explicitly-revised-model'
    saved = updates.submit(root, document, expected_revision=state['revision'],
        authorization_note='New decision: retain 120 after reading the newer CLI allowance')
    assert saved['revision'] == 2
    updates.bind_new_job(root, 1)
    assert budget.execution_grant(root)['allowance']['wall_time_seconds'] == 120


def test_external_resource_amendment_cannot_cross_composite_configuration_binding(tmp_path, monkeypatch):
    root = resource_instance(tmp_path, enabled=True)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    document['executor']['resource_policy']['allowance']['wall_time_seconds'] = 120
    updates.submit(root, document, authorization_note='One combined queued declaration')
    ready, competed = threading.Event(), threading.Event()
    original_amend = budget._amend_execution_grant_locked

    def pause_resource_apply(*args, **kwargs):
        if kwargs.get('operation_id') == 'configuration-1-resources':
            ready.set()
            assert competed.wait(timeout=5), 'Resource competitor did not reach the controller boundary'
        return original_amend(*args, **kwargs)

    def competing_operator():
        assert ready.wait(timeout=5), 'Configuration did not reach its resource amendment'
        try:
            with pytest.raises(RuntimeError, match='running controller'):
                budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
                    authorization_note='Concurrent independent CLI-equivalent adjustment')
        finally:
            competed.set()

    monkeypatch.setattr(budget, '_amend_execution_grant_locked', pause_resource_apply)
    with ThreadPoolExecutor(max_workers=1) as executor:
        # A fresh context cannot borrow the binding thread's writer marker.
        competitor = executor.submit(Context().run, competing_operator)
        updates.bind_new_job(root, 1)
        competitor.result(timeout=10)
    assert budget.ceilings(root)['max_tokens'] == 12000
    assert budget.execution_grant(root)['allowance']['wall_time_seconds'] == 120
    budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
        authorization_note='Explicit later retry after configuration binding completed')
    assert budget.execution_grant(root)['allowance']['wall_time_seconds'] == 150


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


def test_cli_current_inspect_separates_base_active_pending_without_writes(tmp_path, capsys):
    import json
    from scidiscovery.research_loop import cli
    from test_history_handoff import finish
    root = instance(tmp_path)
    first, _, _ = finish(root, 1, 'H', [])
    edit(root, model='active-model')
    finish(root, 2, 'H', [])
    edit(root, model='pending-model')
    budget._amend_locked(root, max_tokens=14000, authorization_note='Extend actual shared ceiling')
    originals = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert cli.main(['config-inspect', '--root', str(root)]) == 0
    frozen = json.loads(capsys.readouterr().out)
    assert frozen['source'] == 'frozen_instance' and frozen['settings']['model'] == 'original-model'
    assert cli.main(['config-inspect', '--root', str(root), '--view', 'current']) == 0
    current = json.loads(capsys.readouterr().out)
    assert current['base_identity']['goal_id'] == frozen['settings']['goal_id']
    assert current['active_revision'] == 1 and current['pending_revision'] == 2
    assert current['active']['agent']['model'] == 'active-model'
    assert current['active']['budget']['max_tokens'] == 14000
    assert current['pending']['agent']['model'] == 'pending-model'
    assert current['pending']['budget']['max_tokens'] == 14000
    assert current['effective_from'] == 'next_new_node'
    assert updates.for_job(root, first)['model'] == 'original-model'
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()} == originals
    c.build_job(root, 3, 'H', [])
    assert cli.main(['config-inspect', '--root', str(root), '--view', 'current']) == 0
    bound = json.loads(capsys.readouterr().out)
    assert bound['active_revision'] == 2 and bound['pending_revision'] is None and bound['pending'] is None
    assert bound['active']['agent']['model'] == 'pending-model'


def test_current_inspect_requires_instance_and_keeps_declaration_read_only(tmp_path):
    with pytest.raises(ValueError, match='existing instance root'):
        research_config.inspect(config=tmp_path / 'missing.toml', view='current')
