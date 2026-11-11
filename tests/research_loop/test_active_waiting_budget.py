"""Frozen wait accounting with real control originals, without native execution."""
import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, configuration_updates, continuation, contracts as c,
    controller, research_config, ui_saved_status,
)


@pytest.fixture
def clocked(tmp_path, monkeypatch, request):
    policy = getattr(request, 'param', 'new')
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain original wait accounting and finite research ceilings.')
    root = tmp_path / 'instance'
    if policy != 'new':
        initialize = accepted_budget.initialize
        def original_birth(selected, settings, **facts):
            # Freeze an absent historical marker before the first accepted head.
            # Never mutate an accepted instance to simulate legacy consumption.
            assert not (selected / accepted_budget.HEAD).exists()
            settings.pop('active_waiting_policy')
            if policy == 'elapsed':
                settings.pop('time_budget_basis')
            c.write(selected / 'settings.json', settings)
            return initialize(selected, settings, **facts)
        monkeypatch.setattr(accepted_budget, 'initialize', original_birth)
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=100, max_steps=4, model='fixture', reasoning_effort='high')
    stamp = dict(at_unix=1000., at_boottime=100., boot_id='first-boot')
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    c.write(root / 'host-started.json', stamp)
    return root, stamp


def transition(root, phase):
    with controller._writer(root):
        budget.transition(root, phase, 'Original accounting fixture')


@pytest.mark.parametrize('clocked,expected', [('new', 2), ('legacy', 32), ('elapsed', 32)], indirect=['clocked'])
def test_frozen_wait_policy_controls_open_closed_and_saved_balances(clocked, expected):
    root, stamp = clocked
    settings = c.settings(root)
    settings_bytes = (root / 'settings.json').read_bytes()
    transition(root, 'advance')
    stamp.update(at_unix=1002., at_boottime=102.)
    transition(root, 'waiting')
    stamp.update(at_unix=1032., at_boottime=132.)
    opened = budget.snapshot(root)
    assert opened['advance_seconds'] == 2 and opened['waiting_seconds'] == 30
    assert opened['consumed_seconds'] == expected
    assert opened['seconds_remaining'] == 100 - expected
    assert opened['waiting_consumes_budget'] == (expected != 2)
    transition(root, 'paused')
    originals = {root / 'control-ledger' / f'{seq:06d}.json':
        (root / 'control-ledger' / f'{seq:06d}.json').read_bytes() for seq in (1, 2, 3)}
    events, _ = budget.audit_events(root)
    saved = ui_saved_status._budget(root, settings, events)
    assert saved['consumed_seconds'] == budget.status_snapshot(root)['consumed_seconds'] == expected
    assert saved['waiting_consumes_budget'] == opened['waiting_consumes_budget']
    if settings.get('time_budget_basis') == 'active':
        stamp.update(at_unix=9000., at_boottime=3., boot_id='second-boot')
        assert budget.snapshot(root)['consumed_seconds'] == expected
        assert ui_saved_status._budget(root, settings, events)['consumed_seconds'] == expected
    assert (root / 'settings.json').read_bytes() == settings_bytes
    assert all(path.read_bytes() == original for path, original in originals.items())


@pytest.mark.parametrize('clocked', ['new', 'legacy', 'elapsed'], indirect=True)
def test_operational_edit_preserves_original_wait_marker_and_basis(clocked):
    root, _ = clocked
    settings = c.settings(root)
    original = (root / 'settings.json').read_bytes()
    document = research_config.to_document(root, settings)
    document['agent']['model'] = 'changed-fixture-model'
    edited = research_config.validate_update(root, document)
    for key in ('active_waiting_policy', 'time_budget_basis'):
        assert (key in edited) == (key in settings)
        assert edited.get(key) == settings.get(key)
    assert (root / 'settings.json').read_bytes() == original


@pytest.mark.parametrize('policy', [None, True, 1, 'charged', {}, []])
def test_unknown_explicit_wait_policy_is_rejected(policy):
    with pytest.raises(ValueError, match='frozen active waiting policy'):
        budget.waiting_is_free(dict(time_budget_basis='active', active_waiting_policy=policy))


def test_unknown_wait_duration_is_not_replaced_with_zero():
    durations = dict(advance=3., waiting=None)
    assert budget.consumed_seconds(dict(time_budget_basis='active'), durations, 50.) is None
    assert budget.consumed_seconds(dict(time_budget_basis='active', active_waiting_policy='excluded'), durations, 50.) == 3.
    assert budget.consumed_seconds(dict(active_waiting_policy='excluded'), durations, 50.) == 50.


@pytest.mark.parametrize('clocked,expected', [('new', 2), ('legacy', 132)], indirect=['clocked'])
def test_wait_past_shared_ceiling_retains_existing_consumption(clocked, expected):
    root, stamp = clocked
    transition(root, 'advance')
    stamp.update(at_unix=1002., at_boottime=102.)
    transition(root, 'waiting')
    stamp.update(at_unix=1132., at_boottime=232.)
    value = budget.snapshot(root)
    assert value['consumed_seconds'] == expected
    assert value['seconds_remaining'] == max(0, 100 - expected)
    assert value['waiting_seconds'] == 130


@pytest.mark.parametrize('clocked', ['new', 'legacy', 'elapsed'], indirect=True)
def test_service_backoff_uses_frozen_shared_deadline_and_retains_wait_counters(clocked, monkeypatch):
    root, stamp = clocked
    budget.amend(root, max_seconds=2, authorization_note='Bound this control-clock fixture')
    saved = dict(local_wait_seconds=1., service_wait_seconds=1., next_attempt_at='original schedule')
    path = root / 'host-runtime/service-wait-fixture.json'
    decision = dict(action='retry', reason='native_temporary', delay_seconds=3.)
    policy = dict(max_backoff_seconds=3., max_wait_seconds=5.)
    monotonic = [0.]
    starts = []
    def sleep(seconds):
        assert 0 < seconds <= 1
        monotonic[0] += seconds
        stamp['at_unix'] += seconds
        stamp['at_boottime'] += seconds
    monkeypatch.setattr(controller, '_recovery_state', lambda *_:(path, saved))
    monkeypatch.setattr(controller, '_recovery_remaining', lambda *_:3.)
    monkeypatch.setattr(configuration_updates, 'for_step',
        lambda *_:dict(resume_policy=dict(automatic_recovery=policy)))
    monkeypatch.setattr(controller.time, 'monotonic', lambda:monotonic[0])
    monkeypatch.setattr(controller.time, 'clock_gettime', lambda _:stamp['at_boottime'])
    monkeypatch.setattr(controller.time, 'time', lambda:stamp['at_unix'])
    monkeypatch.setattr(controller.time, 'sleep', sleep)
    monkeypatch.setattr(continuation, 'automatic_decision', lambda *_, **__:dict(decision))
    monkeypatch.setattr(continuation, 'automatic_start', lambda *_, **kwargs:starts.append(kwargs))
    free = budget.waiting_is_free(c.settings(root))
    original = (root / 'settings.json').read_bytes()
    with controller._writer(root):
        if free:
            controller._wait_automatic_recovery(root, 1, decision, inventory={})
        else:
            with pytest.raises(budget.NodeBudgetUnavailable, match='wall_limit'):
                controller._wait_automatic_recovery(root, 1, decision, inventory={})
    assert len(starts) == int(free)
    assert saved['local_wait_seconds'] == saved['service_wait_seconds'] == (4. if free else 3.)
    assert saved['status'] == ('started' if free else 'stopped')
    assert budget.snapshot(root)['consumed_seconds'] == (0. if free else 2.)
    assert (root / 'settings.json').read_bytes() == original


def test_free_waiting_does_not_remove_finite_service_backoff_limit(clocked, monkeypatch):
    root, _ = clocked
    saved = dict(local_wait_seconds=1., service_wait_seconds=1., next_attempt_at='original schedule')
    path = root / 'host-runtime/service-wait-fixture.json'
    monkeypatch.setattr(controller, '_recovery_state', lambda *_:(path, saved))
    monkeypatch.setattr(controller, '_recovery_remaining', lambda *_:3.)
    monkeypatch.setattr(configuration_updates, 'for_step',
        lambda *_:dict(resume_policy=dict(automatic_recovery=dict(max_wait_seconds=3.))))
    monkeypatch.setattr(continuation, 'automatic_start',
        lambda *_, **__:pytest.fail('Consumed service wait cannot authorize another attempt'))
    with controller._writer(root), pytest.raises(controller._AutomaticRecoveryUnavailable, match='automatic_recovery_wait_limit'):
        controller._wait_automatic_recovery(root, 1, dict(action='retry'), inventory={})
    assert saved['service_wait_seconds'] == saved['local_wait_seconds'] == 1.
    assert saved['status'] == 'stopped' and saved['next_attempt_at'] is None
