"""Automatic recovery timing and UI projections; no native or solver execution."""
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import budget, contracts, controller, ui_saved_status, ui_view
from test_controller import scene


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    record = root / 'native-evidence/001'
    contracts.write(root / 'jobs/001.json', dict(step=1, role='H'))
    contracts.write(record / 'native-intent.json', dict(step=1))
    now = [1_800_000_000., 100.]
    sleeps, phases, launches = [], [], []
    policy = dict(enabled=True, max_continues=2, max_retries=3,
        initial_backoff_seconds=30., max_backoff_seconds=300., jitter_fraction=.1, max_wait_seconds=1800.)
    decision = dict(action='retry', reason='temporary_capacity', source_attempt=0,
        source_record=str(record), delay_seconds=3., retry_after_seconds=None,
        counters=dict(continues=0, retries=1, wait_seconds=7.))

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
        now[1] += seconds

    def start(actual_root, step, selected, *, recovery_wait):
        saved = contracts.read(record / 'automatic-recovery.json')
        assert saved['status'] == 'ready'
        assert selected == decision
        assert recovery_wait['waited_seconds'] == saved['local_wait_seconds']
        launches.append(recovery_wait)

    monkeypatch.setattr(controller, 'time', SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[1], sleep=sleep))
    monkeypatch.setattr(controller, 'require_writer', lambda root: None)
    monkeypatch.setattr(controller.execution, 'record', lambda root, step: record)
    monkeypatch.setattr(controller.configuration_updates, 'for_step',
        lambda root, step: dict(resume_policy=dict(automatic_recovery=policy)))
    monkeypatch.setattr(controller.budget, 'transition', lambda root, phase, reason: phases.append((phase, reason)))
    monkeypatch.setattr(controller.budget, 'snapshot', lambda root: dict(seconds_remaining=999.))
    monkeypatch.setattr(controller.continuation, 'automatic_decision', lambda root, step: dict(decision))
    monkeypatch.setattr(controller.continuation, 'automatic_start', start)
    return SimpleNamespace(root=root, record=record, now=now, sleeps=sleeps, phases=phases,
        launches=launches, decision=decision, policy=policy)


def test_backoff_counts_only_live_monotonic_wait_and_rearbitrates(recovery):
    scene = recovery
    controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    saved = contracts.read(scene.record / 'automatic-recovery.json')
    assert sum(scene.sleeps) == 3.
    assert saved['local_wait_seconds'] == 3. and saved['service_wait_seconds'] == 10.
    assert saved['retry_count'] == 1 and saved['continue_count'] == 0
    assert saved['status'] == 'started' and len(scene.launches) == 1
    assert [phase for phase, _ in scene.phases] == ['waiting', 'advance']


def test_restart_keeps_saved_wait_and_does_not_charge_offline_gap(recovery):
    scene = recovery
    path, saved = controller._recovery_state(scene.root, 1, scene.decision)
    saved.update(local_wait_seconds=1., service_wait_seconds=8.)
    contracts.write(path, saved)
    scene.now[0] += 3000.  # The controller was absent beyond the UTC deadline.
    scene.now[1] = 2.  # A new boot's monotonic origin is unrelated.
    controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    after = contracts.read(path)
    assert scene.sleeps == []
    assert after['local_wait_seconds'] == 1. and after['service_wait_seconds'] == 8.
    assert scene.launches[0]['waited_seconds'] == 1.


def test_pause_retains_schedule_and_prevents_recovery_launch(recovery):
    scene = recovery
    (scene.root / 'pause.request').touch()
    with pytest.raises(controller._AutomaticRecoveryUnavailable) as caught:
        controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    assert caught.value.status == 'paused'
    assert scene.launches == [] and scene.sleeps == []
    assert contracts.read(scene.record / 'automatic-recovery.json')['status'] == 'paused'
    assert scene.phases[-1][0] == 'paused'


def test_pause_after_partial_backoff_preserves_actual_wait(recovery, monkeypatch):
    scene = recovery
    sleep = controller.time.sleep

    def pause_after_wait(seconds):
        sleep(seconds)
        (scene.root / 'pause.request').touch()

    monkeypatch.setattr(controller.time, 'sleep', pause_after_wait)
    with pytest.raises(controller._AutomaticRecoveryUnavailable):
        controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    saved = contracts.read(scene.record / 'automatic-recovery.json')
    assert saved['status'] == 'paused' and saved['next_attempt_at'] is not None
    assert saved['local_wait_seconds'] == 1. and saved['service_wait_seconds'] == 8.
    assert scene.launches == []


def test_uncomparable_utc_deadline_gets_one_bounded_wait(recovery):
    scene = recovery
    path, saved = controller._recovery_state(scene.root, 1, scene.decision)
    saved['next_attempt_at'] = 'not-a-time'
    contracts.write(path, saved)
    controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    assert sum(scene.sleeps) == scene.policy['initial_backoff_seconds']
    assert len(scene.launches) == 1
    assert 'not comparable' in contracts.read(path)['limitation']


def test_wait_hint_exceeding_remaining_wait_budget_stops_before_sleep(recovery):
    scene = recovery
    scene.policy['max_wait_seconds'] = 8.
    with pytest.raises(controller._AutomaticRecoveryUnavailable, match='automatic_recovery_wait_limit'):
        controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    assert scene.sleeps == [] and scene.launches == []
    assert contracts.read(scene.record / 'automatic-recovery.json')['service_wait_seconds'] == 7.


def test_deadline_expiry_rechecks_arbiter_and_never_grants_launch(recovery, monkeypatch):
    scene = recovery
    monkeypatch.setattr(controller.continuation, 'automatic_decision',
        lambda root, step: dict(scene.decision, action='stop', reason='managed_execution_unknown'))
    with pytest.raises(controller._AutomaticRecoveryUnavailable, match='managed_execution_unknown'):
        controller._wait_automatic_recovery(scene.root, 1, scene.decision)
    assert scene.launches == []
    assert contracts.read(scene.record / 'automatic-recovery.json')['limitation'] == 'managed_execution_unknown'


def test_one_controller_helper_consumes_native_recovery_exception(recovery, monkeypatch):
    scene = recovery
    calls = []

    def first(root, step):
        calls.append('execute')
        raise controller.continuation.AutomaticRecoveryRequired(scene.decision)

    def collect(root, step):
        calls.append('wait_original')
        return dict(sealed=True)

    monkeypatch.setattr(controller.native, 'execute_native', first)
    monkeypatch.setattr(controller.native, 'wait_native', collect)
    assert controller._native_call(scene.root, 1, launch=True) == dict(sealed=True)
    assert calls == ['execute', 'wait_original'] and len(scene.launches) == 1


def test_controller_route_consumes_recovery_then_continues_exact_task(scene, monkeypatch):
    """Exercise the routing consumer; native lifecycle qualification is separate."""
    scene.plan[:] = [('H', 'analyze'), ('A', 'finish')]
    execute = scene.native.execute_native
    launches, collected, waits = [], [], []
    decision = dict(action='continue', reason='native_incomplete', source_attempt=0)

    def submit(root, step):
        launches.append(step)
        if step == 1:
            record = root / 'native-evidence/001'
            scene.write(record / 'native-intent.json', dict(step=1,
                task_ref=scene.controller.contracts.record_ref(root / 'jobs/001.json')))
            raise scene.controller.continuation.AutomaticRecoveryRequired(decision)
        return execute(root, step)

    def collect(root, step):
        collected.append(step)
        return execute(root, step)

    monkeypatch.setattr(scene.native, 'execute_native', submit)
    monkeypatch.setattr(scene.native, 'wait_native', collect, raising=False)
    monkeypatch.setattr(scene.controller, '_wait_automatic_recovery',
        lambda root, step, selected: waits.append((step, selected)))
    result = scene.controller.run(scene.root)
    assert result['status'] == 'finish' and result['completed_steps'] == 2
    assert launches == [1, 2] and collected == [1]
    assert waits == [(1, decision)] and scene.calls == [1, 2]
    assert scene.read(scene.root / 'jobs/002.json')['direct_refs'][0]['path'] == 'inputs/parents/001/conclusion.json'


def test_explicit_resume_uses_same_recovery_consumer_without_repeating_start(recovery, monkeypatch):
    scene = recovery
    starts, collections = [], []

    def start(root, step):
        starts.append(step)
        raise controller.continuation.AutomaticRecoveryRequired(scene.decision)

    def collect(root, step):
        collections.append(step)
        return dict(sealed=True)

    monkeypatch.setattr(controller.continuation, 'start', start)
    monkeypatch.setattr(controller.native, 'wait_native', collect)
    monkeypatch.setattr(controller.budget, 'historical_step_limit', lambda root: 1)
    monkeypatch.setattr(controller, '_run', lambda root: dict(status='finish'))
    assert controller._worker_resume_locked(scene.root, 1) == dict(status='finish')
    assert starts == [1] and collections == [1]
    assert len(scene.launches) == 1


def test_explicit_resume_preserves_original_integrity_error_despite_recovery_stop(recovery, monkeypatch):
    scene = recovery
    original = ValueError('Original process receipt binding changed')

    def tampered(root, step):
        raise original

    monkeypatch.setattr(controller.continuation, 'start', tampered)
    monkeypatch.setattr(controller.budget, 'historical_step_limit', lambda root: 1)
    monkeypatch.setattr(controller.continuation, 'automatic_decision', lambda root, step:
        dict(scene.decision, action='stop', reason='automatic_recovery_cleanup_unknown'))
    monkeypatch.setattr(controller, '_run', lambda root: pytest.fail('Integrity failure cannot enter FINAL routing'))
    with pytest.raises(ValueError) as caught:
        controller._worker_resume_locked(scene.root, 1)
    assert caught.value is original
    assert scene.launches == [] and scene.sleeps == []
    assert contracts.read(scene.record / 'automatic-recovery.json')['status'] == 'stopped'


def test_saved_node_projection_omits_private_recovery_bindings(recovery):
    scene = recovery
    path, saved = controller._recovery_state(scene.root, 1, scene.decision)
    contracts.write(path, dict(saved, status='waiting'))
    contracts.write(scene.record / 'native-outcome.json', dict(returncode=1, stop_reason='native_failure_event'))
    shown = ui_saved_status._node(scene.root, 1, dict(role='H'))
    assert shown['state'] == 'automatic_recovery_waiting'
    assert shown['automatic_recovery']['retry_count'] == 1
    assert 'source_intent_ref' not in shown['automatic_recovery']
    assert 'task_ref' not in shown['automatic_recovery']


def test_saved_active_attempt_shows_accepted_recovery_counts(recovery, monkeypatch):
    scene = recovery
    attempt = scene.record / 'continuations/001'
    contracts.write(attempt / 'native-intent.json', dict(automatic_recovery=dict(action='continue',
        reason='native_incomplete', wait_seconds=3., counters=dict(retries=2, continues=1, wait_seconds=7.))))
    contracts.write(attempt / 'runner-process.json', dict(task_ref='fixture'))
    monkeypatch.setattr(ui_saved_status.runtime, 'process_matches', lambda *args: True)
    shown = ui_saved_status._node(scene.root, 1, dict(role='H'))
    assert shown['state'] == 'running'
    assert shown['automatic_recovery']['retry_count'] == 2
    assert shown['automatic_recovery']['continue_count'] == 2
    assert shown['automatic_recovery']['service_wait_seconds'] == 10.


@pytest.mark.parametrize('automatic, category, allowed', [(False, 'temporary', False),
    (True, 'temporary', True), (True, 'incomplete', True), (True, 'permanent', False),
    (True, 'unknown', False)])
def test_missing_usage_is_only_a_lower_bound_for_verified_recoverable_source(tmp_path, monkeypatch, automatic, category, allowed):
    root = tmp_path / 'instance'
    record = root / 'native-evidence/001'
    job = dict(step=1, role='H')
    policy = dict(max_observed_tokens=100, max_seconds=30.)
    contracts.write(root / 'jobs/001.json', job)
    contracts.write(record / 'native-intent.json', dict(step=1,
        task_ref=contracts.record_ref(root / 'jobs/001.json'), budget_slice=dict(role='H', limits=policy)))
    outcome = dict(terminal_observed=False, stop_reason='native_failure_event',
        cleanup='observed_group_cleaned', native_failure=dict(category=category),
        budget_slice=dict(role='H', limits=policy, usage_observed=False, dispatches=1,
            input_tokens=0, cached_input_tokens=0, output_tokens=0, observed_tokens=0, advance_seconds=2.))
    contracts.write(record / 'native-outcome.json', outcome)
    monkeypatch.setattr(budget, 'node_limits', lambda root, job: policy)
    monkeypatch.setattr(budget.contracts, 'settings', lambda root, job=None: {})
    monkeypatch.setattr(budget, 'native_completion_known', lambda outcome, settings: False)
    monkeypatch.setattr(controller.continuation, 'automatic_source', lambda root, step, source: outcome)
    keyword = dict(automatic_recovery=dict(action='retry')) if automatic else {}
    if allowed:
        assert budget._attempt_consumption(root, job, [record], **keyword) == (0, 2.)
    else:
        with pytest.raises(ValueError, match='Missing billing'):
            budget._attempt_consumption(root, job, [record], **keyword)
    assert not (record / 'usage.json').exists()


@pytest.fixture
def accepted_without_source_usage(tmp_path, monkeypatch):
    """Accepted source and successor facts for the runner's budget recheck."""
    root = tmp_path / 'instance'
    source = root / 'native-evidence/001'
    current = source / 'continuations/001'
    job = dict(step=1, role='H')
    policy = dict(max_observed_tokens=100, max_seconds=30.)
    limits = dict(max_tokens=1000, max_seconds=100., advance_seconds=11.)
    contracts.write(root / 'jobs/001.json', job)
    task_ref = contracts.record_ref(root / 'jobs/001.json')
    contracts.write(source / 'native-intent.json', dict(step=1, task_ref=task_ref,
        budget_slice=dict(role='H', limits=policy)))
    outcome = dict(terminal_observed=False, stop_reason='native_failure_event',
        cleanup='observed_group_cleaned', native_failure=dict(category='temporary'),
        budget_slice=dict(role='H', limits=policy, usage_observed=False, dispatches=1,
            input_tokens=0, cached_input_tokens=0, output_tokens=0, observed_tokens=0, advance_seconds=2.))
    contracts.write(source / 'native-outcome.json', outcome)
    intent = dict(step=1, attempt=1, task_ref=task_ref, continuation_kind='automatic_recovery',
        budget_slice=dict(task_ref=task_ref, role='H', limits=policy,
            shared_limits=dict(max_tokens=1000, max_seconds=100.), advance_seconds=10.,
            previous_tokens=0, previous_seconds=2.))
    contracts.write(current / 'native-intent.json', intent)
    monkeypatch.setattr(budget, 'node_limits', lambda root, job: policy)
    monkeypatch.setattr(budget.contracts, 'settings', lambda root, job=None: {})
    monkeypatch.setattr(budget, 'native_completion_known', lambda outcome, settings: False)
    monkeypatch.setattr(controller.continuation, 'automatic_source', lambda root, step, record: outcome)
    monkeypatch.setattr(budget, 'node_mode', lambda root: 'soft')
    # Lifecycle/source validation owns the records reader. This fixture tests
    # the budget's exact accepted-intent and prior-slice consumption boundary.
    monkeypatch.setattr(controller.execution, 'records', lambda root, step: [source, current])
    return SimpleNamespace(root=root, source=source, current=current, job=job,
        intent=intent, outcome=outcome, limits=limits)


@pytest.mark.parametrize('kind, category, allowed', [
    ('automatic_recovery', 'temporary', True), ('automatic_recovery', 'incomplete', True),
    ('repair', 'temporary', False), ('reentry', 'temporary', False),
    ('automatic_recovery', 'permanent', False), ('automatic_recovery', 'unknown', False)])
def test_runner_budget_recheck_retains_exact_automatic_successor_for_missing_source_usage(
        accepted_without_source_usage, kind, category, allowed):
    scene = accepted_without_source_usage
    scene.intent['continuation_kind'] = kind
    scene.outcome['native_failure']['category'] = category
    contracts.write(scene.current / 'native-intent.json', scene.intent)
    contracts.write(scene.source / 'native-outcome.json', scene.outcome)
    if allowed:
        assert budget._node_duration(scene.root, scene.job, scene.intent, scene.limits) == 3.
        assert budget.stop_node(scene.root, scene.job, scene.intent, scene.limits, {}) is None
    else:
        with pytest.raises(ValueError, match='Missing billing'):
            budget._node_duration(scene.root, scene.job, scene.intent, scene.limits)
    assert not (scene.source / 'usage.json').exists()


def test_runner_budget_recheck_cannot_refund_unmetered_source_duration(accepted_without_source_usage):
    scene = accepted_without_source_usage
    scene.intent['budget_slice']['previous_seconds'] = 0.
    contracts.write(scene.current / 'native-intent.json', scene.intent)
    with pytest.raises(ValueError, match='cannot refund'):
        budget._node_duration(scene.root, scene.job, scene.intent, scene.limits)
    assert not (scene.source / 'usage.json').exists()


def test_runner_budget_recheck_rejects_changed_automatic_successor(accepted_without_source_usage):
    scene = accepted_without_source_usage
    supplied = dict(scene.intent, automatic_recovery=dict(action='retry', source_attempt=9))
    with pytest.raises(ValueError, match='exact accepted intent'):
        budget._node_duration(scene.root, scene.job, supplied, scene.limits)


@pytest.mark.parametrize('unknown, allowed', [([1], True), ([1, 2], False)])
def test_automatic_admission_never_ignores_other_tasks_unknown_usage(tmp_path, monkeypatch, unknown, allowed):
    from scidiscovery.research_loop import protocol
    root = tmp_path / 'instance'
    job = dict(step=1, role='H')
    contracts.write(root / 'jobs/001.json', job)
    decision = dict(action='retry', source_attempt=0)
    policy = dict(max_observed_tokens=100, max_seconds=30.)
    limits = dict(max_tokens=1000, max_seconds=100., seconds_remaining=100., advance_seconds=2.)
    monkeypatch.setattr(protocol, 'require_current', lambda root, job: None)
    monkeypatch.setattr(budget, 'final_a_barrier', lambda root: None)
    monkeypatch.setattr(controller.continuation, 'automatic_decision', lambda root, step: decision)
    monkeypatch.setattr(budget, '_attempt_consumption', lambda root, job, paths, **options: (10, 2.))
    monkeypatch.setattr(budget, 'node_limits', lambda root, job: policy)
    monkeypatch.setattr(budget, '_observe_node_clock', lambda root: (limits, dict(boot_id='fixture')))
    monkeypatch.setattr(controller.native, 'usage', lambda root: dict(unknown_steps=unknown,
        blocking_unknown_steps=unknown, observed_tokens=10, accounted_tokens=10))
    monkeypatch.setattr(budget.contracts, 'settings', lambda root, job=None: {})
    monkeypatch.setattr(budget, 'node_mode', lambda root: 'soft')
    monkeypatch.setattr(budget, 'delivery_reserve', lambda settings: dict(tokens=0, seconds=0., steps=0))
    monkeypatch.setattr(budget, 'step_limit', lambda root: 5)
    monkeypatch.setattr(budget, 'used_steps', lambda root: 1)
    if allowed:
        anchor = budget.accept_continuation(root, job, [], automatic_recovery=decision)
        assert anchor['previous_tokens'] == 10 and anchor['previous_seconds'] == 2.
    else:
        with pytest.raises(budget.NodeBudgetUnavailable, match='worker_resume_budget_unavailable'):
            budget.accept_continuation(root, job, [], automatic_recovery=decision)


def test_waiting_recovery_remains_visible_in_existing_role_facts():
    saved = dict(status='waiting', reason='temporary_capacity',
        next_attempt_at='2026-10-26T00:01:00+00:00', retry_count=2,
        continue_count=1, service_wait_seconds=30., limitation='Unknown calls are never resubmitted')
    node = dict(step=3, role='W', state='automatic_recovery_waiting', automatic_recovery=saved)
    line = ui_view.role_line(None, node, lambda text, limit=0: text)
    shown = ' '.join(item['value'] for item in line['facts'])
    assert 'temporary_capacity' in shown and saved['next_attempt_at'] in shown
    assert 'retry 2' in shown and 'continue 1' in shown
    assert 'Unknown calls are never resubmitted' in shown
    assert '30 秒' in shown


def test_service_recovery_headline_does_not_claim_scientific_failure():
    node = dict(step=1, role='H', state='automatic_recovery_waiting',
        automatic_recovery=dict(status='waiting', reason='temporary_service', next_attempt_at='2026-10-26T00:01:00Z'))
    presentation = dict(current=node, completed=False, waiting=False, active=False, state=node['state'])
    value = ui_view.status_summary(presentation, {}, clean=lambda text, limit: text)
    assert value['state'] == 'service_wait'
    assert 'temporary_service' in value['headline'] and '下次仲裁' in value['headline']
    assert '失败' not in value['headline']
    assert ui_view.status_summary(presentation, dict(paused=True))['state'] == 'paused'
