"""Resource-origin and same-ledger retry cases; no process or scientific grant."""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, atomic_files, budget, configuration_updates as updates
from scidiscovery.research_loop import contracts as c, qualification as q
from execution_fixtures import execution_registry
from test_configuration_updates import edit, resource_instance


def queue_resources(root, wall=120, *, tokens=None):
    document = updates.status(root)['configuration']
    document['executor']['resource_policy'] = dict(version=1,
        allowance=dict(wall_time_seconds=wall, max_storage_bytes=10000))
    if tokens is not None:
        document['budget']['max_tokens'] = tokens
    return updates.submit(root, document, authorization_note='Explicit queued cumulative allowance')


def hold_resources(root, grant):
    """Ledger arithmetic only; no execution eligibility or backend is asserted."""
    request = q._save(root, root/'host-runtime/origin-fixture/request.json', dict(fixture='request'))
    decision = q._save(root, root/'host-runtime/origin-fixture/decision.json', dict(fixture='decision'))
    return budget.reserve_execution(root,
        binding=dict(call_id='origin-fixture', scope=dict(fixture='same cumulative owner'),
            native_attempt=dict(number=0), interface=dict(executor=grant['owner']['executor']),
            authority=dict(fixture='not an execution authorization')),
        request_ref=request, decision_ref=decision,
        budget=dict(wall_time_seconds=6, max_storage_bytes=60), grant_revision=grant['ref'],
        effective_allowance=grant['allowance'])


def test_original_enabled_unseeded_pool_ignores_pending_origin_and_keeps_consumption(tmp_path):
    root = resource_instance(tmp_path, enabled=True)
    original_bytes = (root/'settings.json').read_bytes()
    queue_resources(root)
    assert accepted_budget.read(root)['resource'] is None
    assert accepted_budget.read(root)['applied_configuration'] == 0
    assert budget.ceilings(root)['max_tokens'] == 10000
    grant = budget.execution_grant(root)
    assert grant['allowance'] == dict(wall_time_seconds=100, max_storage_bytes=10000)
    assert accepted_budget.read(root)['resource']['settings_ref'] == dict(path='settings.json')
    hold_resources(root, grant)
    updates.bind_new_job(root, 1)
    revised = budget.execution_grant(root)
    assert revised['owner'] == grant['owner'] and revised['ref'] != grant['ref']
    assert revised['allowance']['wall_time_seconds'] == 120
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=114, max_storage_bytes=9940)
    later = budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=150, max_storage_bytes=10000),
        authorization_note='Independent later cumulative adjustment')
    edit(root, model='later-model')
    updates.bind_new_job(root, 2)
    assert budget.execution_grant(root) == later
    state = accepted_budget.read(root)['resource']
    assert state['settings_ref'] == dict(path='settings.json') and state['reservation_count'] == 1
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=144, max_storage_bytes=9940)
    grants = [event for event in budget.audit_events(root)[0] if event['kind'] == 'resource_grant']
    assert len([event for event in grants if event['previous_grant'] is None]) == 1
    assert (root/'settings.json').read_bytes() == original_bytes


def test_disabled_pool_uses_enabling_revision_then_reenable_retains_origin_and_spend(tmp_path):
    root = resource_instance(tmp_path, enabled=False)
    queue_resources(root, wall=100)
    assert accepted_budget.read(root)['resource'] is None
    enabling = updates.bind_new_job(root, 1)
    origin = dict(path=enabling['path'])
    grant = budget.execution_grant(root)
    assert accepted_budget.read(root)['resource']['settings_ref'] == origin
    hold_resources(root, grant)
    document = updates.status(root)['configuration']
    document['executor'].pop('resource_policy')
    updates.submit(root, document, authorization_note='Disable future resource use')
    updates.bind_new_job(root, 2)
    assert budget.execution_grant(root) == grant
    queue_resources(root)
    updates.bind_new_job(root, 3)
    state = accepted_budget.read(root)['resource']
    assert state['settings_ref'] == origin and state['reservation_count'] == 1
    assert state['held'] == dict(wall_time_seconds=6, max_storage_bytes=60)
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=114, max_storage_bytes=9940)
    grants = [event for event in budget.audit_events(root)[0] if event['kind'] == 'resource_grant']
    assert len([event for event in grants if event['previous_grant'] is None]) == 1


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('changed', ['owner', 'allowance', 'predecessor', 'origin', 'missing_origin'])
def test_initial_grant_validator_consumes_facts_without_reopening_and_rejects_changes(tmp_path, monkeypatch, enabled, changed):
    root = resource_instance(tmp_path, enabled=enabled)
    queue_resources(root)
    head = accepted_budget.read(root)
    origin = budget._resource_origin(root, state=head)
    fields = dict(owner=budget._resource_owner(origin['settings']), previous_grant=None,
        allowance=copy.deepcopy(origin['settings']['execution_resource_policy']['allowance']),
        authorization_note='Original explicit allowance')

    def forbidden(*args, **kwargs):
        pytest.fail('Initial resource validation must consume supplied originals, not reopen them')

    monkeypatch.setattr(c, 'read', forbidden)
    updates_, rows = accepted_budget.resource_delta(head, None, 'resource_grant', fields, origin=origin)
    assert updates_['resource']['settings_ref'] == origin['ref'] and rows == {}
    changed_fields, changed_origin = copy.deepcopy(fields), copy.deepcopy(origin)
    if changed == 'owner':
        changed_fields['owner']['executor'] = 'another:executor'
    elif changed == 'allowance':
        changed_fields['allowance']['wall_time_seconds'] += 1
    elif changed == 'predecessor':
        changed_fields['previous_grant'] = dict(path='control-ledger/000001.json', size_bytes=1)
    elif changed == 'origin':
        changed_origin['ref'] = dict(path='host-control/configuration/revisions/999999.json')
    else:
        changed_origin = None
    with pytest.raises(ValueError, match='Initial resource grant'):
        accepted_budget.resource_delta(head, None, 'resource_grant', changed_fields, origin=changed_origin)
    assert head['resource'] is None


def test_combined_retry_after_token_apply_and_original_seed_preserves_later_ceilings_and_spend(tmp_path, monkeypatch):
    root = resource_instance(tmp_path, enabled=True)
    queue_resources(root, tokens=12000)
    original_append = budget._append

    def interrupted_resource(*args, **fields):
        if fields.get('operation_id') == 'configuration-1-resources':
            raise InterruptedError('After durable tokens and original resource seed, before resource amendment')
        return original_append(*args, **fields)

    with monkeypatch.context() as patch:
        patch.setattr(budget, '_append', interrupted_resource)
        with pytest.raises(InterruptedError):
            updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 12000
    grant = budget.execution_grant(root)
    assert grant['allowance']['wall_time_seconds'] == 100
    assert not (root/'host-control/configuration/bindings/000001.json').exists()
    hold_resources(root, grant)
    budget.amend(root, max_tokens=14000, authorization_note='Later independent token decision')
    updates.bind_new_job(root, 1)
    assert budget.ceilings(root)['max_tokens'] == 14000
    assert budget.execution_accounting(root)['remaining'] == dict(wall_time_seconds=114, max_storage_bytes=9940)
    state = accepted_budget.read(root)['resource']
    assert state['settings_ref'] == dict(path='settings.json') and state['reservation_count'] == 1
    events, _ = budget.audit_events(root)
    for operation in ('configuration-1', 'configuration-1-resources'):
        assert len([event for event in events if event.get('operation_id') == operation]) == 1
    assert len([event for event in events if event['kind'] == 'resource_grant' and event['previous_grant'] is None]) == 1
    updates.bind_new_job(root, 1)
    assert budget.audit_events(root)[0] == events


def test_combined_binding_redo_recovers_without_an_intervening_writer(tmp_path, monkeypatch):
    root = resource_instance(tmp_path, enabled=True)
    queue_resources(root, tokens=12000)
    original_publish = atomic_files.publish_bytes

    def interrupted_binding(path, payload, **options):
        if path.parent.name == 'bindings':
            raise InterruptedError('Binding event durable; its original not installed yet')
        return original_publish(path, payload, **options)

    with monkeypatch.context() as patch:
        patch.setattr(atomic_files, 'publish_bytes', interrupted_binding)
        with pytest.raises(InterruptedError):
            updates.bind_new_job(root, 1)
    assert not (root/'host-control/configuration/bindings/000001.json').exists()
    assert budget.ceilings(root)['max_tokens'] == 12000
    assert accepted_budget.read(root)['resource']['allowance']['wall_time_seconds'] == 120
    events, _ = budget.audit_events(root)
    revision = updates.bind_new_job(root, 1)
    assert revision['revision'] == 1
    assert (root/'host-control/configuration/bindings/000001.json').exists()
    assert budget.audit_events(root)[0] == events
    for operation in ('configuration-1', 'configuration-1-resources'):
        assert len([event for event in events if event.get('operation_id') == operation]) == 1
