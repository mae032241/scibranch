"""Failed control stops active advancement only when native never started."""
import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, runtime
from test_worker_resume import instance


def clock(monkeypatch):
    now = [100.]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(
        at_unix=1800000000. + now[0], at_boottime=now[0], boot_id='controller-test-boot'))
    return now



@pytest.mark.parametrize('original', ['accepted_unknown', 'live_child', 'cleanup_unknown',
    'native_activity', 'terminal_observed', 'native_receipt_symlink',
    'live_runner', 'live_watchdog', 'other_task_runner'])
def test_failed_controller_keeps_live_or_unknown_original_advancing(tmp_path, monkeypatch, original):
    root = instance(tmp_path, with_policy=False)
    now = clock(monkeypatch)
    starts = []

    def start(root, step, attempt=0):
        starts.append((step, attempt))
        record = execution.record(root, step)
        now[0] += 3.
        if original != 'accepted_unknown':
            c.write(record / 'native-outcome.json', dict(returncode=None,
                native_started=False, native_activity_observed=original == 'native_activity',
                terminal_observed=original == 'terminal_observed',
                stop_reason='cleanup_unknown' if original == 'cleanup_unknown' else 'native_error_unknown'))
        if original == 'live_child':
            c.write(record / 'native-outcome.json', dict(returncode=0, native_started=True,
                native_activity_observed=True, stop_reason=None))
            binding = execution.identity(root, step)
            c.write(record / 'native-process.json', dict(pid=123, task_ref=binding))
        if original == 'native_receipt_symlink':
            (record / 'native-process.json').symlink_to(record / 'missing-receipt.json')
        if original in {'live_runner', 'live_watchdog', 'other_task_runner'}:
            binding = execution.identity(root, step)
            name = 'watchdog-process.json' if original == 'live_watchdog' else 'runner-process.json'
            c.write(record / name, dict(pid=123, task_ref=binding if original != 'other_task_runner' else {},
                boot_id='controller-test-boot', start_ticks=1, pgid=123, sid=123, ppid=1))
        raise RuntimeError('Controller lost the accepted original call')

    monkeypatch.setattr(runtime, 'start', start)
    monkeypatch.setattr(runtime, 'process_matches', lambda identity, binding:
        identity.get('pid') == 123 and identity.get('task_ref') == binding)
    assert controller.run(root)['status'] == 'native_stopped'
    assert budget.execution_blocked(root)
    before = budget.snapshot(root)
    assert before['clock_phase'] == 'advance'
    assert before['consumed_seconds'] == 3.
    now[0] += 7.
    after = budget.snapshot(root)
    assert after['consumed_seconds'] == 10.
    assert before['seconds_remaining'] - after['seconds_remaining'] == 7.
    if original != 'live_runner':
        controller.run(root)
    assert starts == [(1, 0)] and len(execution.records(root, 1)) == 1
    assert budget.snapshot(root)['consumed_seconds'] == 10.
    assert native.usage(root)['unknown_steps'] == [1]
