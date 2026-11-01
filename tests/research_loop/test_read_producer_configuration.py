"""Configuration producers pass immutable facts; writers still observe fresh heads."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, budget, contracts as c
from scidiscovery.research_loop import configuration_updates as updates, execution, instance_owner, originals, protocol
from test_configuration_updates import edit, instance


def _forbidden(*args, **kwargs):
    pytest.fail('An internal consumer reopened an already produced control fact')


def test_head_loader_consumes_base_without_reopening_settings(tmp_path, monkeypatch):
    root = instance(tmp_path)
    base = c.settings(root)
    read = c.read
    paths = []

    def observed(path):
        path = Path(path)
        assert path not in {root / 'settings.json', root / 'settings-identity.json'}
        paths.append(path)
        return read(path)

    monkeypatch.setattr(c, 'settings', _forbidden)
    monkeypatch.setattr(c, 'read', observed)
    state = accepted.load(root, base=base)
    assert state.base is base
    assert paths.count(root / accepted.HEAD) == 1
    assert 'base' not in state
    with pytest.raises(ValueError, match='original research identity'):
        accepted.load(root, base=dict(base, goal_id='another-instance'))


@pytest.mark.parametrize('revision', [False, True])
def test_protocol_entry_produces_base_job_and_selected_policy_once(tmp_path, monkeypatch, revision):
    root = instance(tmp_path)
    if revision:
        edit(root, model='selected-model')
    job = c.build_job(root, 1, 'H', [])
    settings = c.settings
    read = c.read
    settings_calls = []
    paths = []

    def observed_settings(current, retained=None):
        settings_calls.append(retained)
        return settings(current, retained)

    def observed_read(path):
        paths.append(Path(path))
        return read(path)

    monkeypatch.setattr(c, 'settings', observed_settings)
    monkeypatch.setattr(c, 'read', observed_read)
    selected = protocol.require_current(root, job)
    assert settings_calls == [None]
    assert paths.count(root / 'settings-identity.json') == 1
    assert paths.count(root / 'jobs/001.json') == 1
    assert paths.count(root / accepted.HEAD) == int(revision)
    if revision:
        assert selected['model'] == 'selected-model'
        assert paths.count(root / job['configuration_revision']['path']) == 1
    changed = dict(job, configuration_revision=dict(revision=999, path='wrong.json'))
    with pytest.raises(ValueError, match='differs from its frozen original'):
        protocol.require_current(root, changed)


def test_base_policy_and_budget_consumers_use_explicit_facts(tmp_path, monkeypatch):
    root = instance(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    base = c.settings(root)
    state = accepted.load(root, base=base)
    events, parent = budget._events_from_state(state)
    monkeypatch.setattr(c, 'settings', _forbidden)
    monkeypatch.setattr(c, 'read', _forbidden)
    monkeypatch.setattr(accepted, 'read', _forbidden)
    monkeypatch.setattr(accepted, 'load', _forbidden)
    assert protocol.require_current_job(root, base=base, job=job, retained=job, policy=base) is base
    assert updates.policy_for_job(root, job, base=base, head=state) is base
    assert updates.current_policy(root, base=base, head=state) is base
    assert c.settings_for_job(root, job, base=base, head=state) is base
    assert c._global_materials(root, base=base) is base['global_materials']
    assert budget._events_from_state(state)[1] == parent
    assert budget._snapshot(root, base, events)['max_tokens'] == base['max_tokens']
    assert budget.final_a_barrier(root, events=events) is None
    assert budget.normal_resume(root, events=events) is None
    assert budget.closeout_state(root, events=events)['begin'] is None


def test_missing_head_cannot_make_a_revision_consumer_reload(tmp_path, monkeypatch):
    root = instance(tmp_path)
    base = c.settings(root)
    monkeypatch.setattr(accepted, 'read', _forbidden)
    monkeypatch.setattr(accepted, 'load', _forbidden)
    with pytest.raises(ValueError, match='requires its accepted head'):
        updates.policy_for_job(root, dict(configuration_revision=dict(revision=1,
            path='host-control/configuration/revisions/000001.json')), base=base, head=None)


def test_explicit_base_does_not_bypass_the_live_owner_gate(tmp_path, monkeypatch):
    root = instance(tmp_path)
    base = c.settings(root)

    def revoked(current):
        assert current == root
        raise ValueError('fixture owner generation revoked')

    monkeypatch.setattr(c, 'settings', _forbidden)
    monkeypatch.setattr(instance_owner, 'require_owner', revoked)
    with pytest.raises(ValueError, match='owner generation revoked'):
        protocol.require_current_base(root, base=base)
    with pytest.raises(ValueError, match='owner generation revoked'):
        with budget._ledger_writer_from_base(root, base=base):
            pytest.fail('Revoked owner entered the writer body')


def test_binding_returns_the_selected_policy_and_published_head(tmp_path, monkeypatch):
    root = instance(tmp_path)
    edit(root, model='selected-model')
    base = c.settings(root)
    revision_path = root / 'host-control/configuration/revisions/000001.json'
    read = c.read
    paths = []

    def observed(path):
        paths.append(Path(path))
        return read(path)

    # Enter the existing controller writer before measuring the binding owner.
    with budget._operator_writer(root):
        monkeypatch.setattr(c, 'settings', _forbidden)
        monkeypatch.setattr(c, 'read', observed)
        revision, policy, state = updates.bind_new_job_facts(root, 1, base=base)
    assert revision == dict(revision=1, path=revision_path.relative_to(root).as_posix())
    assert policy['model'] == 'selected-model'
    assert paths.count(revision_path) == 1
    # Selection and publication are separate writer boundaries, each fresh.
    assert paths.count(root / accepted.HEAD) == 2
    assert state.base is base
    assert state['applied_configuration'] == 1
    assert state.row('configuration-binding/000001')['configuration_revision'] == revision
    assert read(root / accepted.HEAD)['seq'] == state['seq']


def test_reservation_keeps_its_policy_after_a_later_save(tmp_path, monkeypatch):
    root = instance(tmp_path)
    edit(root, model='reserved-model')
    base = c.settings(root)
    first, _, before = updates.bind_new_job_facts(root, 1, base=base)
    edit(root, model='later-model')
    read = c.read
    paths = []

    def observed(path):
        paths.append(Path(path))
        return read(path)

    with budget._operator_writer(root):
        monkeypatch.setattr(c, 'settings', _forbidden)
        monkeypatch.setattr(c, 'read', observed)
        revision, policy, state = updates.bind_new_job_facts(root, 1, base=base)
    assert revision == first and policy['model'] == 'reserved-model'
    assert state['seq'] > before['seq'] and state['configuration']['revision'] == 2
    assert paths.count(root / first['path']) == 1
    assert root / 'host-control/configuration/revisions/000002.json' not in paths


def test_binding_returns_new_budget_state_and_next_writer_sees_later_amendment(tmp_path):
    root = instance(tmp_path)
    base = c.settings(root)
    document = updates.status(root)['configuration']
    document['budget']['max_tokens'] = 12000
    updates.submit(root, document, authorization_note='Explicit finite fixture allowance')
    before = accepted.load(root, base=base)
    _, policy, bound = updates.bind_new_job_facts(root, 1, base=base)
    assert before['ceilings']['max_tokens'] == 10000
    assert bound['seq'] > before['seq'] and bound['ceilings']['max_tokens'] == 12000
    assert policy['max_tokens'] == 12000
    budget.amend(root, max_tokens=14000, authorization_note='Later independent fixture allowance')
    _, _, later = updates.bind_new_job_facts(root, 2, base=base)
    assert later['seq'] > bound['seq'] and later['ceilings']['max_tokens'] == 14000
    assert bound['ceilings']['max_tokens'] == 12000


def test_build_consumes_binding_result_without_selecting_again(tmp_path, monkeypatch):
    root = instance(tmp_path)
    edit(root, model='selected-model')
    bind = updates.bind_new_job_facts
    produced = []

    def observed(current, step, *, base):
        result = bind(current, step, base=base)
        produced.append(result)
        return result

    monkeypatch.setattr(updates, 'bind_new_job_facts', observed)
    monkeypatch.setattr(updates, 'bind_new_job', _forbidden)
    monkeypatch.setattr(updates, 'for_new_job', _forbidden)
    job = c.build_job(root, 1, 'H', [])
    assert len(produced) == 1
    revision, policy, state = produced[0]
    assert job['configuration_revision'] == revision
    assert policy['model'] == 'selected-model'
    assert state['applied_configuration'] == revision['revision']


def test_inherited_policy_uses_its_original_source(tmp_path, monkeypatch):
    root = instance(tmp_path)
    base = c.settings(root)
    source = object()
    policy = dict(base, model='original-source-model')
    monkeypatch.setattr(originals, 'inherited', lambda current, step: True)
    monkeypatch.setattr(originals, 'node', lambda current, step: source)
    monkeypatch.setattr(originals, 'source_settings', lambda current: policy if current is source else _forbidden())
    monkeypatch.setattr(updates, 'policy_for_job', _forbidden)
    assert c.settings_for_job(root, dict(step=1), base=base, head=None) is policy


def test_material_consumer_requires_the_owner_policy(tmp_path, monkeypatch):
    root = instance(tmp_path)
    job = c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    inventory = c.CurrentInventory(root, job, record, 0, [], {})
    monkeypatch.setattr(execution, 'record', lambda *args: record)
    monkeypatch.setattr(c, 'settings', _forbidden)
    with pytest.raises(ValueError, match='requires its produced policy'):
        c.material_inventory(root, 1, inventory=inventory)
