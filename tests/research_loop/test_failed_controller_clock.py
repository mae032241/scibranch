"""Failed control stops active advancement only when native never started."""
import os

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, runtime
from test_worker_resume import instance


def clock(monkeypatch):
    now = [100.]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(
        at_unix=1800000000. + now[0], at_boottime=now[0], boot_id='controller-test-boot'))
    return now


def test_pre_popen_host_failure_stops_advancement_and_preserves_unknown_original(tmp_path, monkeypatch):
    from scidiscovery.research_loop import shared_input_views
    root = instance(tmp_path, with_policy=False)
    now = clock(monkeypatch)
    starts = []

    def before_popen(*args, **kwargs):
        now[0] += 3.
        raise RuntimeError('Injected host preparation failure before Popen')

    def start(root, step, attempt=0):
        starts.append((step, attempt))
        # Run the ordinary accepted native entry synchronously so the fault
        # can be injected before Popen, without a fake scientific response.
        return native.run_native(root, step, attempt)

    monkeypatch.setattr(shared_input_views, 'wrap', before_popen)
    monkeypatch.setattr(runtime, 'start', start)
    assert controller.run(root)['status'] == 'native_stopped'
    record = execution.record(root, 1)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is False and outcome['returncode'] is None
    assert outcome['stop_reason'] == 'native_error_unknown'
    assert outcome['native_activity_observed'] is False
    assert not (record / 'native-process.json').exists()
    assert not (record / 'science-output.json').exists()
    assert (record / 'native.jsonl').read_bytes() == b''
    assert not budget.execution_blocked(root)
    usage = native.usage(root)
    assert usage['unknown_steps'] == [1]
    assert budget.used_steps(root) == 1
    before = budget.snapshot(root)
    assert before['clock_phase'] == 'paused' and before['consumed_seconds'] == 3.
    originals = {path: path.read_bytes() for path in (
        root / 'settings.json', root / 'jobs/001.json', record / 'native-intent.json',
        record / 'native-outcome.json')}
    now[0] += 60.
    after = budget.snapshot(root)
    assert after['consumed_seconds'] == before['consumed_seconds']
    assert after['seconds_remaining'] == before['seconds_remaining']
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert starts == [(1, 0)] and execution.records(root, 1) == [record]
    assert native.usage(root) == usage and budget.used_steps(root) == 1
    assert all(path.read_bytes() == data for path, data in originals.items())

    # The existing runner-completion entry must also stop its own finished
    # prelaunch work when no controller remains. Its exact watchdog is still
    # waiting for this same runner to exit, rather than performing native work.
    binding = execution.identity(root, 1)
    for name, pid in (('runner-process.json', os.getpid()), ('watchdog-process.json', os.getpid() + 1)):
        c.write(record / name, dict(pid=pid, task_ref=binding, boot_id='controller-test-boot',
            start_ticks=1, pgid=pid, sid=pid, ppid=1))
    monkeypatch.setattr(runtime, 'process_matches', lambda identity, selected:
        selected == binding and identity.get('task_ref') == selected)
    with controller._writer(root):
        budget.transition(root, 'advance', 'original_runner_final_writes')
    now[0] += 2.
    runtime._complete_clock(root)
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    assert budget.snapshot(root)['consumed_seconds'] == 5.
    now[0] += 20.
    assert budget.snapshot(root)['consumed_seconds'] == 5.
    assert native.usage(root) == usage


@pytest.mark.parametrize('original', ['accepted_unknown', 'live_child', 'cleanup_unknown',
    'native_activity', 'terminal_observed', 'native_receipt_symlink', 'managed_waiting',
    'live_runner', 'live_watchdog', 'other_task_runner'])
def test_failed_controller_keeps_live_or_unknown_original_advancing(tmp_path, monkeypatch, original):
    from scidiscovery.research_loop import managed_wait
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
    monkeypatch.setattr(managed_wait, 'pending', lambda root: original == 'managed_waiting')
    assert controller.run(root)['status'] == 'native_stopped'
    assert budget.execution_blocked(root)
    before = budget.snapshot(root)
    assert before['clock_phase'] == ('waiting' if original == 'managed_waiting' else 'advance')
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
