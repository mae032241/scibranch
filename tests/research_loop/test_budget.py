"""Budget control facts, with simulated time and no model execution."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts, controller, native, runtime
from scidiscovery.research_loop.cli import main, status


@pytest.fixture
def clocked(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Resolve a bounded mechanism.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=100,
        max_seconds=20, max_steps=3, model='fixture', reasoning_effort='high')
    now = [100.]
    monkeypatch.setattr(budget.time, 'time', lambda: now[0])
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: now[0])
    contracts.write(root / 'host-started.json', budget.clock_stamp())
    return root, now


def transition(root, phase):
    with controller._writer(root):
        budget.transition(root, phase, 'fixture transition')


def original_intent(root):
    contracts.build_job(root, 1, 'H', [])
    record = contracts.host_record(contracts.workspace(root, 1))
    contracts.write(record / 'native-intent.json', dict(step=1,
        task_sha256=contracts.digest(root / 'jobs/001.json')))
    return record


def test_active_clock_counts_overlapping_control_and_native_once(clocked):
    root, now = clocked
    transition(root, 'advance')
    now[0] = 105.
    transition(root, 'advance')  # Joining the same execution is no new interval.
    assert budget.snapshot(root)['advance_seconds'] == 5
    assert budget.snapshot(root)['ledger_events'] == 1
    transition(root, 'paused')
    now[0] = 130.
    value = budget.snapshot(root)
    assert value['advance_seconds'] == 5
    assert value['paused_seconds'] == 25
    assert value['elapsed_seconds'] == 30
    assert value['seconds_remaining'] == 15
    transition(root, 'advance')
    now[0] = 134.
    assert budget.snapshot(root)['advance_seconds'] == 9
    assert budget.snapshot(root)['seconds_remaining'] == 11


@pytest.mark.parametrize('closeout', [False, True])
def test_status_reads_closed_active_intervals_without_sampling_current_clock(clocked, monkeypatch, closeout):
    root, now = clocked
    transition(root, 'advance')
    now[0] = 105.
    if closeout:
        budget.begin_closeout(root, 0, 1, 'Retain an exact completed export', export_only=True)
    transition(root, 'paused')
    now[0] = 110.
    budget.amend(root, max_tokens=200, max_seconds=40, authorization_note='Retain the audited ceiling')
    originals = {path: path.read_bytes() for path in (
        root / 'settings.json', root / 'settings-identity.json', root / 'host-started.json',
        *(root / 'control-ledger').glob('*.json'))}
    monkeypatch.setattr(budget, 'clock_stamp', lambda: pytest.fail('Closed active status must use only recorded interval endpoints'))
    value = status(root)
    shown = value['budget']
    assert shown['consumed_seconds'] == 5 and shown['seconds_remaining'] == 35
    assert shown['max_tokens'] == 200 and shown['max_seconds'] == 40
    assert shown['paused_seconds'] is None and shown['elapsed_seconds'] is None
    if closeout:
        assert value['closeout']['status'] == 'exported_unsubmitted'
        assert value['closeout']['budget'] == shown
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_unstarted_status_does_not_sample_current_clock(clocked, monkeypatch):
    root, _ = clocked
    (root / 'host-started.json').unlink()
    monkeypatch.setattr(budget, 'clock_stamp', lambda: pytest.fail('An unstarted budget has no clock interval'))
    value = status(root)['budget']
    assert value['consumed_seconds'] == 0 and value['seconds_remaining'] == 20


@pytest.mark.parametrize('query, final_only', [('pending_status', False), ('pending_status', True), ('repair_funds', False)])
def test_closed_closeout_funds_use_recorded_limits_only_for_queries(clocked, monkeypatch, query, final_only):
    from scidiscovery.research_loop import delivery
    root, now = clocked
    settings = contracts.settings(root)
    settings['delivery_policy'] = delivery.policy(dict(closeout_a=dict(max_observed_tokens=10, max_seconds=2),
        inflight_tokens=0, cleanup_seconds=0, submit_seconds=0, confirm_seconds=0, adapter=None))
    settings['node_budget_policy'] = budget.node_policy({
        role: dict(max_observed_tokens=10, max_seconds=2) for role in 'HWAE'})
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    transition(root, 'advance')
    budget.begin_closeout(root, 0, 1, 'A retained pending closeout', final_only=final_only)
    now[0] = 105.
    transition(root, 'paused')
    originals = {path: path.read_bytes() for path in (
        root / 'settings.json', root / 'settings-identity.json', root / 'host-started.json',
        *(root / 'control-ledger').glob('*.json'))}
    stamp = budget.clock_stamp(); stamp.update(boot_id='next-boot', at_boottime=1.)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: pytest.fail('Query-only funds use the already recorded limits'))
    if query == 'pending_status':
        value = status(root)
        assert value['closeout']['status'] == 'closeout_pending'
        assert value['closeout']['budget']['consumed_seconds'] == 5
    else:
        limits = budget.status_snapshot(root)
        assert controller._repair_funds(root, limits=limits)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    for funds in (controller._closeout_funds, controller._repair_funds):
        with pytest.raises(ValueError, match='Control clock boot changed'):
            funds(root)
    assert all(path.read_bytes() == original for path, original in originals.items())


@pytest.mark.parametrize('basis, phase', [('active', 'advance'), ('active', 'waiting'), ('elapsed', 'paused')])
def test_status_keeps_unclosed_and_legacy_clock_reads_strict(clocked, monkeypatch, basis, phase):
    root, _ = clocked
    settings = contracts.settings(root)
    settings['time_budget_basis'] = basis
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    transition(root, phase)
    stamp = budget.clock_stamp(); stamp.update(boot_id='next-boot', at_boottime=1.)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    with pytest.raises(ValueError, match='Control clock boot changed'):
        status(root)


@pytest.mark.parametrize('action', ['snapshot', 'transition', 'amend', 'bind_new_job'])
def test_closed_status_does_not_authorize_cross_boot_budget_effects(clocked, monkeypatch, action):
    from scidiscovery.research_loop import configuration_updates
    root, now = clocked
    transition(root, 'advance')
    now[0] = 105.
    transition(root, 'paused')
    document = configuration_updates.status(root)['configuration']
    document['agent']['model'] = 'queued-model'
    configuration_updates.submit(root, document, expected_revision=0)
    originals = {path: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')}
    stamp = budget.clock_stamp(); stamp.update(boot_id='next-boot', at_boottime=1.)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    assert status(root)['budget']['consumed_seconds'] == 5
    with pytest.raises(ValueError, match='Control clock boot changed'):
        if action == 'transition':
            transition(root, 'advance')
        elif action == 'amend':
            budget.amend(root, max_seconds=40, authorization_note='A new boot is not a clock continuation')
        elif action == 'bind_new_job':
            configuration_updates.bind_new_job(root, 1)
        else:
            budget.snapshot(root)
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert not (root / 'host-control/configuration/bindings/000001.json').exists()


def test_pause_request_and_restart_do_not_make_unknown_time_free(clocked):
    root, now = clocked
    transition(root, 'advance')
    record = original_intent(root)
    now[0] = 110.
    assert main(['pause', '--root', str(root)]) == 0
    assert budget.snapshot(root)['clock_phase'] == 'advance'
    now[0] = 120.
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert budget.snapshot(root)['consumed_seconds'] == 20
    now[0] = 160.
    # A later observation may prove the old execution ended; never backdate
    # the closed clock to its claimed old timestamp and refund unknown time.
    contracts.write(record / 'native-outcome.json', dict(returncode=1, stop_reason=None,
        finished_at_unix=105., cleanup='observed_group_cleaned', usage=dict(unknown_steps=[])))
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    assert budget.snapshot(root)['advance_seconds'] == 60
    now[0] = 200.
    assert budget.snapshot(root)['advance_seconds'] == 60
    assert budget.snapshot(root)['paused_seconds'] == 40


def test_explicit_pause_stays_paused_across_run_restarts(clocked):
    root, now = clocked
    transition(root, 'advance')
    now[0] = 105.
    assert main(['pause', '--root', str(root)]) == 0
    now[0] = 200.
    assert controller.run(root)['status'] == 'paused'
    assert controller.run(root)['status'] == 'paused'
    assert budget.snapshot(root)['advance_seconds'] == 5
    assert budget.snapshot(root)['paused_seconds'] == 95
    assert not (root / 'jobs').exists()


def test_budget_amend_is_audited_and_preserves_consumption_and_settings(clocked, capsys):
    root, now = clocked
    frozen = (root / 'settings.json').read_bytes()
    transition(root, 'advance')
    now[0] = 108.
    transition(root, 'paused')
    now[0] = 150.
    assert main(['budget-amend', '--root', str(root), '--max-tokens', '200', '--max-seconds', '30',
                 '--authorization-note', 'Operator authorizes this explicit ceiling.']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'applied'
    assert result['kind'] == 'budget_amend'
    assert result == contracts.read(root / 'host-control/operations' / f'{result["operation_id"]}.json')
    amendment = result['result']
    assert amendment['status'] == 'budget_amended'
    assert amendment['authorization'] == 'operator_declaration_not_authentication'
    assert amendment['consumption'] == 'retained'
    value = budget.snapshot(root)
    assert value['advance_seconds'] == 8
    assert value['max_tokens'] == 200
    assert value['seconds_remaining'] == 22
    assert (root / 'settings.json').read_bytes() == frozen
    events, _ = budget._events(root, contracts.settings(root))
    assert [item['seq'] for item in events] == [1, 2, 3]
    assert amendment['amendment_seq'] == events[2]['seq']
    assert events[2]['operation_id'] == result['operation_id']
    assert events[2]['parent_sha256'] == contracts.digest(root / 'control-ledger/000002.json')
    assert events[2]['before'] == dict(max_tokens=100, max_seconds=20)
    assert events[2]['after'] == dict(max_tokens=200, max_seconds=30)
    assert events[2]['at_unix'] == 150


@pytest.mark.parametrize('note,changes', [('', dict(max_tokens=200)), ('Decision', {}),
    ('Decision', dict(max_tokens=True)), ('Decision', dict(max_seconds=float('nan')))])
def test_amend_rejects_missing_decision_or_invalid_ceiling(clocked, note, changes):
    root, now = clocked
    with pytest.raises(ValueError):
        budget.amend(root, authorization_note=note, **changes)
    assert not (root / 'control-ledger').exists()


def test_amend_rejects_writer_or_unknown_execution(clocked):
    root, now = clocked
    with controller._writer(root):
        with pytest.raises(RuntimeError, match='running controller'):
            budget.amend(root, max_tokens=200, authorization_note='Explicit operator decision')
    original_intent(root)
    with pytest.raises(RuntimeError, match='running or unknown'):
        budget.amend(root, max_tokens=200, authorization_note='Explicit operator decision')
    assert not (root / 'control-ledger').exists()


def test_runner_inactive_append_holds_host_writer_until_finished(clocked, monkeypatch):
    root, now = clocked
    transition(root, 'advance')
    now[0] = 103.
    original = budget.inactive
    def check_lock(root, reason):
        with pytest.raises(RuntimeError, match='running controller'):
            with controller._writer(root):
                pytest.fail('A second writer entered during runner stop append')
        original(root, reason)
    monkeypatch.setattr(budget, 'inactive', check_lock)
    runtime._complete_clock(root)
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    transition(root, 'advance')
    with controller._writer(root):
        runtime._complete_clock(root)  # A live controller cannot be stopped.
    assert budget.snapshot(root)['clock_phase'] == 'advance'


@pytest.mark.parametrize('entry', ['run', 'continue'])
def test_first_job_input_failure_preserves_error_and_stops_idle_clock(clocked, capsys, entry):
    root, now = clocked
    contracts.build_job(root, 1, 'H', [])
    copied = contracts.workspace(root, 1) / 'inputs/global/objective.md'
    copied.chmod(0o600)
    copied.write_text('Changed bound input copy')
    if entry == 'continue':
        assert main(['continue', '--root', str(root)]) == 1
        assert json.loads(capsys.readouterr().out)['error'] == 'Bound input copy changed'
    else:
        with pytest.raises(ValueError, match='Bound input copy changed'):
            controller.run(root)
    stopped = budget.snapshot(root)
    assert stopped['clock_phase'] == 'paused'
    now[0] = 150.
    assert budget.snapshot(root)['advance_seconds'] == stopped['advance_seconds']
    assert not list(root.glob('native-evidence/*/native-intent.json'))


@pytest.mark.parametrize('entry', ['run', 'closeout', 'continue', 'worker_resume'])
@pytest.mark.parametrize('original', ['absent', 'unknown', 'running'])
@pytest.mark.parametrize('failure_type', [OSError, KeyboardInterrupt])
def test_controller_exception_reconciles_clock_under_writer(clocked, monkeypatch, capsys, entry, original, failure_type):
    root, now = clocked
    frozen = (root / 'settings.json').read_bytes()
    if original != 'absent':
        record = original_intent(root)
        if original == 'running':
            contracts.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None))
            contracts.write(record / 'native-process.json', dict(fixture='live original'))
            monkeypatch.setattr(runtime, 'process_matches', lambda *args: True)
    transition(root, 'advance')
    now[0] = 104.
    failure = failure_type('job construction failed before submission')
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(controller, '_start_closeout' if entry == 'closeout' else '_run', fail)
    if entry == 'worker_resume':
        monkeypatch.setattr(controller.continuation, 'start', lambda *args: None)
    inactive = budget.inactive
    reconciled = []
    def check_lock(root, reason):
        with pytest.raises(RuntimeError, match='running controller'):
            with controller._writer(root):
                pytest.fail('Exception reconciliation released the writer')
        reconciled.append(reason)
        inactive(root, reason)
    monkeypatch.setattr(budget, 'inactive', check_lock)
    def invoke():
        if entry == 'continue':
            return main(['continue', '--root', str(root)])
        if entry == 'worker_resume':
            return controller.worker_resume(root, 1)
        return getattr(controller, entry)(root)
    if entry == 'continue' and failure_type is OSError:
        assert invoke() == 1
        assert json.loads(capsys.readouterr().out)['error'] == str(failure)
    else:
        with pytest.raises(failure_type) as raised:
            invoke()
        assert raised.value is failure
    assert reconciled == ['worker_resume_failed' if entry == 'worker_resume' else 'controller_failed']
    stopped = budget.snapshot(root)
    assert stopped['advance_seconds'] == 4
    assert stopped['clock_phase'] == ('paused' if original == 'absent' else 'advance')
    now[0] = 110.
    assert budget.snapshot(root)['advance_seconds'] == (4 if original == 'absent' else 10)
    assert (root / 'settings.json').read_bytes() == frozen
    assert len(list(root.glob('native-evidence/*/native-intent.json'))) == int(original != 'absent')


@pytest.mark.parametrize('notes', ['supported', 'absent', 'broken'])
def test_failed_budget_reconciliation_preserves_original_error(clocked, monkeypatch, notes):
    root, _ = clocked
    class CustomError(ValueError):
        def add_note(self, note):
            raise RuntimeError('Optional note failed')
    if notes == 'absent':
        CustomError.add_note = None
    failure = (ValueError if notes == 'supported' else CustomError)('Original invalid input')
    def fail(*args, **kwargs):
        raise failure
    def fail_clock(*args, **kwargs):
        raise OSError('Clock ledger unavailable')
    monkeypatch.setattr(controller, '_run', fail)
    monkeypatch.setattr(budget, 'inactive', fail_clock)
    with pytest.raises(ValueError) as raised:
        controller.run(root)
    assert raised.value is failure
    if notes == 'supported' and callable(getattr(failure, 'add_note', None)):
        assert failure.__notes__ == ['Budget reconciliation failed (OSError): Clock ledger unavailable']
    else:
        assert not hasattr(failure, '__notes__')


def test_continue_refusal_keeps_pause_and_stops_only_idle_clock(clocked, capsys):
    root, now = clocked
    contracts.write(root / 'pause.request', dict(reason='Original pause'))
    frozen = (root / 'pause.request').read_bytes()
    transition(root, 'advance')
    now[0] = 104.
    assert main(['continue', '--root', str(root), '--authorization-note', 'Missing exact trigger']) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'An authorization note requires an exact correction trigger'
    assert (root / 'pause.request').read_bytes() == frozen
    now[0] = 110.
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    assert budget.snapshot(root)['advance_seconds'] == 4
    assert not (root / 'jobs').exists()


@pytest.mark.parametrize('entry', ['run', 'continue'])
def test_interrupt_during_real_job_input_copy_stops_idle_clock(clocked, monkeypatch, entry):
    root, now = clocked
    interrupted = KeyboardInterrupt('Interrupted while preparing bound input')
    original = contracts._copy_bound
    def copy_then_interrupt(*args):
        original(*args)
        now[0] = 104.
        raise interrupted
    monkeypatch.setattr(contracts, '_copy_bound', copy_then_interrupt)
    with pytest.raises(KeyboardInterrupt) as raised:
        if entry == 'continue':
            main(['continue', '--root', str(root)])
        else:
            controller.run(root)
    assert raised.value is interrupted
    assert (contracts.workspace(root, 1) / 'inputs/global/objective.md').is_file()
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    now[0] = 110.
    assert budget.snapshot(root)['advance_seconds'] == 4
    assert not list(root.glob('native-evidence/*/native-intent.json'))


@pytest.mark.parametrize('action', ['snapshot', 'transition', 'amend'])
def test_boot_clock_rollback_fails_closed_without_refunding_or_appending(clocked, monkeypatch, action):
    root, now = clocked
    transition(root, 'advance')
    now[0] = 110.
    transition(root, 'paused')
    before = {path.name: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')}
    now[0] = 112.  # Advancing UTC cannot hide a negative boot-clock interval.
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: 109.)
    with pytest.raises(ValueError, match='backwards'):
        if action == 'snapshot':
            budget.snapshot(root)
        elif action == 'transition':
            transition(root, 'advance')
        else:
            budget.amend(root, max_seconds=50, authorization_note='Explicit operator decision')
    assert {path.name: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')} == before


@pytest.mark.parametrize('action', ['snapshot', 'transition', 'amend'])
def test_open_interval_utc_rollback_cannot_refund_observed_consumption(clocked, monkeypatch, action):
    root, now = clocked
    ticks = [100.]
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: ticks[0])
    transition(root, 'advance')
    now[0] = ticks[0] = 120.
    assert budget.snapshot(root)['consumed_seconds'] == 20
    now[0] = 110.  # UTC reversed; the actual boot-clock consumption did not.
    if action == 'snapshot':
        assert budget.snapshot(root)['consumed_seconds'] == 20
    elif action == 'transition':
        transition(root, 'paused')
    else:
        budget.amend(root, max_seconds=50, authorization_note='Explicit operator decision')
    assert budget.snapshot(root)['consumed_seconds'] == 20
    now[0] = ticks[0] = 130.
    assert budget.snapshot(root)['consumed_seconds'] == (20 if action == 'transition' else 30)


def test_case02_clock_slew_uses_boot_consumption_without_false_stop(clocked, monkeypatch):
    root, now = clocked
    settings = contracts.settings(root)
    settings['max_seconds'] = 3600
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    # Replay the observed same-boot case02 anchors, without touching that instance.
    now[0] = 1791694089.621297
    ticks = [2899.955554225]
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: ticks[0])
    contracts.write(root / 'host-started.json', budget.clock_stamp())
    transition(root, 'advance')
    now[0], ticks[0] = 1791694169.1294773, 2979.883511218
    observed = budget.snapshot(root)
    assert observed['consumed_seconds'] == pytest.approx(79.927956993)
    assert observed['seconds_remaining'] == pytest.approx(3520.072043007)
    assert controller._launch_limit(root, settings) is None
    transition(root, 'paused')
    before = observed['consumed_seconds']
    # UTC may even step below an earlier display timestamp. Ledger ordering
    # and consumed time remain governed by BOOTTIME, not UTC.
    now[0], ticks[0] = 1791694080., ticks[0] + 5
    transition(root, 'advance')
    assert budget.snapshot(root)['consumed_seconds'] == before
    now[0], ticks[0] = now[0] + 1, ticks[0] + 1
    assert budget.snapshot(root)['consumed_seconds'] == before + 1


def test_changed_boot_refuses_unverifiable_open_interval(clocked, monkeypatch):
    root, now = clocked
    transition(root, 'advance')
    original = budget.clock_stamp
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(original(), boot_id='different-boot'))
    with pytest.raises(ValueError, match='boot changed'):
        budget.snapshot(root)
    with pytest.raises(ValueError, match='boot changed'):
        transition(root, 'paused')
    assert len(list((root / 'control-ledger').glob('*.json'))) == 1


def test_legacy_elapsed_instance_retains_its_original_deadline(clocked):
    root, now = clocked
    settings = contracts.settings(root)
    settings.pop('time_budget_basis')
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    now[0] = 105.
    transition(root, 'paused')
    now[0] = 130.
    value = budget.snapshot(root)
    assert value['time_budget_basis'] == 'elapsed'
    assert value['consumed_seconds'] == 30
    assert value['seconds_remaining'] == 0
    assert value['unclassified_seconds'] == 5
    assert value['paused_seconds'] == 25
    # Elapsed clock accounting does not remove this fresh fixture's protected
    # final A contract. Inspect admission without dispatching that final task.
    assert controller._launch_limit(root, settings) == 'wall_limit'
    assert not (root / 'jobs').exists()


@pytest.mark.parametrize('action', ['snapshot', 'transition', 'amend'])
def test_legacy_elapsed_ledger_rejects_open_utc_rollback_without_refund(clocked, monkeypatch, action):
    root, now = clocked
    settings = contracts.settings(root)
    settings.pop('time_budget_basis')
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    ticks = [100.]
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: ticks[0])
    transition(root, 'advance')
    now[0] = ticks[0] = 120.
    assert budget.snapshot(root)['consumed_seconds'] == 20
    before = {path.name: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')}
    now[0] = 110.
    with pytest.raises(ValueError, match='backwards'):
        if action == 'snapshot':
            budget.snapshot(root)
        elif action == 'transition':
            transition(root, 'paused')
        else:
            budget.amend(root, max_seconds=50, authorization_note='Explicit operator decision')
    assert {path.name: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')} == before
    now[0] = ticks[0] = 130.
    assert budget.snapshot(root)['consumed_seconds'] == 30


def test_overlay_limits_reach_controller_and_actual_native_launch_boundary(clocked, monkeypatch):
    root, now = clocked
    settings = contracts.settings(root)
    monkeypatch.setattr(native, 'usage', lambda _: dict(input_tokens=120, cached_input_tokens=0,
        output_tokens=0, observed_tokens=120, unknown_steps=[]))
    assert controller._launch_limit(root, settings) == 'token_limit'
    budget.amend(root, max_tokens=200, max_seconds=5, authorization_note='Explicit operator decision')
    transition(root, 'advance')
    assert controller._launch_limit(root, settings) is None
    original_intent(root)
    now[0] = 106.
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('active ceiling must prevent submission'))
    native.run_native(root, 1)
    outcome = contracts.read(contracts.host_record(contracts.workspace(root, 1)) / 'native-outcome.json')
    assert outcome['native_started'] is False
    assert outcome['stop_reason'] == 'wall_limit'
    assert settings['max_seconds'] == 20
    assert settings['max_tokens'] == 100


def test_ledger_tamper_is_rejected_and_status_is_read_only(clocked):
    root, now = clocked
    transition(root, 'advance')
    now[0] = 104.
    transition(root, 'paused')
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert status(root)['budget']['consumed_seconds'] == 4
    assert {path: path.read_bytes() for path in root.rglob('*') if path.is_file()} == before
    first = root / 'control-ledger/000001.json'
    event = contracts.read(first)
    contracts.write(first, dict(event, reason='Changed audit reason'))
    with pytest.raises(ValueError, match='identity'):
        budget.snapshot(root)
