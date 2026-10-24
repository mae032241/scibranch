"""Selected fork settings reject before admission effects; no native launches."""
import copy
from contextlib import contextmanager
import sys

import pytest

from scidiscovery.research_loop import (
    budget, configuration_updates as updates, contracts as c, controller,
    execution_authority, fork_runtime, fork_service, fork_sources, formal_review,
)
from test_history_handoff import finish


ALLOWANCE = dict(max_tokens=600, max_seconds=40, max_steps=3)
REVISED_ALLOWANCE = dict(max_tokens=800, max_seconds=50, max_steps=4)
RESOURCES = dict(wall_time_seconds=100, max_storage_bytes=10000)


@pytest.fixture
def selected_fork(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Keep the selected scientific task and its declared limitations.')
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, ['fixture-never-launch'],
        max_tokens=1000, max_seconds=60, max_steps=4, model='original-model',
        reasoning_effort='high', search_policy=None, experiment_mode='local',
        execution_config=dict(executor='local_execution:command',
            configuration=dict(run_argv=[sys.executable], max_seconds=10)),
        source_restrictions=[dict(source=str(objective), restriction='clear')])
    write = c.write

    def synthetic_outcome(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', synthetic_outcome)
        finish(parent, 1, 'H', [], action='analyze')
        finish(parent, 2, 'A', [1], action='finish')
    with controller._writer(parent):
        pass
    child = tmp_path / 'child'
    preview = fork_service.preview(parent, 2, child, budget=ALLOWANCE)
    assert preview['status'] == 'fork_preview_ready'
    fork_service.create(parent, 2, child, budget=ALLOWANCE,
        expected_preview_id=preview['preview_id'], operation_id='selected-configuration')
    return child


def allowance_document(root):
    document = updates.status(root)['configuration']
    document['budget'].update(REVISED_ALLOWANCE)
    document['policies'].update(formal_review=True, execution_authority=True)
    document['executor']['resource_policy'] = dict(version=1, allowance=dict(RESOURCES))
    return document


def test_initial_selected_edit_rejects_before_contracts_and_accounting_then_can_be_corrected(selected_fork, monkeypatch):
    root = selected_fork
    document = allowance_document(root)
    original_model = document['agent']['model']
    document['agent']['model'] = 'illegal-selected-model'
    original_settings = (root / 'settings.json').read_bytes()
    original_events, _ = budget._events(root, c.settings(root))

    def forbidden_contract(*args, **kwargs):
        pytest.fail('Rejected selected settings reached contract freezing')

    with monkeypatch.context() as patch:
        patch.setattr(formal_review, 'freeze_contract', forbidden_contract)
        patch.setattr(execution_authority, 'freeze_configuration', forbidden_contract)
        with pytest.raises(ValueError, match='Fork selected task configuration changed: model'):
            updates.submit(root, document, expected_revision=0,
                authorization_note='Allow the declared additional comparison resources')

    assert updates.revisions(root) == []
    assert not (root / 'host-control/configuration/revisions/000001.json').exists()
    assert not (root / 'host-control/configuration/identities/000001.json').exists()
    assert not (root / 'host-control/configuration/contracts/000001').exists()
    assert not (root / 'host-control/configuration/bindings/000002.json').exists()
    assert not (root / 'jobs/002.json').exists()
    assert budget._events(root, c.settings(root))[0] == original_events
    assert budget.ceilings(root) == ALLOWANCE
    assert (root / 'settings.json').read_bytes() == original_settings

    document['agent']['model'] = original_model
    saved = updates.submit(root, document, expected_revision=0,
        authorization_note='Allow the declared additional comparison resources')
    assert saved['revision'] == 1 and saved['pending']
    assert budget._events(root, c.settings(root))[0] == original_events
    job = fork_runtime.build_selected_job(root, 2)
    fork_runtime.validate_selected_job(root, c.workspace(root, 2), job)
    assert job['step'] == 2 and job['role'] == 'A'
    assert updates.for_job(root, job)['model'] == original_model
    assert budget.ceilings(root) == REVISED_ALLOWANCE
    accounting = budget.execution_accounting(root)
    assert accounting['grant']['allowance'] == RESOURCES
    assert accounting['reservations'] == []
    events, _ = budget._events(root, c.settings(root))
    assert [event.get('operation_id') for event in events if event['kind'] == 'budget_amend'] == ['configuration-1']
    assert [event.get('operation_id') for event in events if event['kind'] == 'resource_grant'
            and event.get('operation_id') is not None] == ['configuration-1-resources']
    assert updates._reservation(root, 2)['configuration_revision'] == job['configuration_revision']


@pytest.mark.parametrize('entry', ['direct', 'controller'])
def test_selected_bind_rechecks_under_existing_locks_before_any_effect(selected_fork, monkeypatch, entry):
    root = selected_fork
    document = allowance_document(root)
    document['agent']['model'] = 'illegal-selected-model'
    # Stage a valid snapshot without submit's outer guard so this regression
    # exercises the independent admission check at each binding caller.
    with monkeypatch.context() as patch:
        patch.setattr(fork_runtime, 'preflight_selected_settings', lambda *args, **kwargs: None)
        updates.submit(root, document, expected_revision=0,
            authorization_note='Queued finite comparison allowance')
    original_revision = (root / 'host-control/configuration/revisions/000001.json').read_bytes()
    original_events, _ = budget._events(root, c.settings(root))
    if entry == 'controller':
        (root / 'pause.request').unlink()
    held = []
    observations = []
    operator_writer, configuration_writer = budget._operator_writer, updates._writer
    preflight = fork_runtime.preflight_selected_settings

    @contextmanager
    def observed_operator_writer(*args, **kwargs):
        with operator_writer(*args, **kwargs):
            held.append('budget')
            try:
                yield
            finally:
                held.pop()

    @contextmanager
    def observed_configuration_writer(*args, **kwargs):
        with configuration_writer(*args, **kwargs):
            held.append('configuration')
            try:
                yield
            finally:
                held.pop()

    def observed_preflight(*args, **kwargs):
        observations.append(tuple(held))
        return preflight(*args, **kwargs)

    def forbidden_amendment(*args, **kwargs):
        pytest.fail('Rejected selected settings reached a budget or resource amendment')

    monkeypatch.setattr(budget, '_operator_writer', observed_operator_writer)
    monkeypatch.setattr(updates, '_writer', observed_configuration_writer)
    monkeypatch.setattr(fork_runtime, 'preflight_selected_settings', observed_preflight)
    monkeypatch.setattr(budget, '_amend_locked', forbidden_amendment)
    monkeypatch.setattr(budget, 'amend_execution_grant', forbidden_amendment)
    with pytest.raises(ValueError, match='Fork selected task configuration changed: model'):
        if entry == 'direct':
            updates.bind_new_job(root, 2)
        else:
            next(controller._step_numbers(root, 2))

    assert observations == [('budget', 'configuration')]
    assert budget._events(root, c.settings(root))[0] == original_events
    assert budget.ceilings(root) == ALLOWANCE
    assert budget.execution_accounting(root)['grant'] is None
    assert not (root / 'host-control/configuration/bindings/000002.json').exists()
    assert not (root / 'jobs/002.json').exists()
    assert (root / 'host-control/configuration/revisions/000001.json').read_bytes() == original_revision


@pytest.mark.parametrize('built', [False, True])
def test_selected_reservation_keeps_original_settings_and_allows_successor_edits(selected_fork, monkeypatch, built):
    root = selected_fork
    reserved = updates.bind_new_job(root, 2)
    original = copy.deepcopy(updates.for_new_job(root, 2))
    if built:
        fork_runtime.build_selected_job(root, 2)
    state = updates.status(root)
    document = state['configuration']
    document['agent']['model'] = 'successor-model'

    def forbidden_selected_task(*args, **kwargs):
        pytest.fail('Bound selected task settings consulted frozen task history')

    with monkeypatch.context() as patch:
        patch.setattr(fork_sources, 'selected_task', forbidden_selected_task)
        saved = updates.submit(root, document, expected_revision=state['revision'])
    assert saved['pending']
    assert updates.bind_new_job(root, 2) == reserved
    assert updates.for_new_job(root, 2) == original
    job = fork_runtime.build_selected_job(root, 2)
    original_job = (root / 'jobs/002.json').read_bytes()
    fork_runtime.validate_selected_job(root, c.workspace(root, 2), job)
    assert updates.for_job(root, job) == original
    assert fork_runtime.build_selected_job(root, 2) == job
    assert (root / 'jobs/002.json').read_bytes() == original_job
    with monkeypatch.context() as patch:
        patch.setattr(fork_sources, 'selected_task', forbidden_selected_task)
        updates.bind_new_job(root, 3)
    assert updates.for_new_job(root, 3)['model'] == 'successor-model'
    fork_runtime.validate_selected_job(root, c.workspace(root, 2), job)
