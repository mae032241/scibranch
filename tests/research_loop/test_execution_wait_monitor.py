import json
import os
import subprocess
import time

import pytest

from scidiscovery.research_loop import contracts as c, execution_wait_monitor as monitor, runtime, ui_view


def waiting_call(tmp_path, *, live=True):
    root = tmp_path / 'instance'
    record = root / 'native-evidence/002'
    call = root / 'host-runtime/experiment-control/002/records/calls/' / ('c' * 32)
    record.mkdir(parents=True)
    call.mkdir(parents=True)
    c.write(call / 'accepted.json', dict(call_id=call.name))
    c.write(record / 'execution-wait.json', dict(calls=[dict(accepted=dict(path=str(call / 'accepted.json')))]))
    process = None
    if live:
        process = subprocess.Popen(['sleep', '30'], start_new_session=True)
        identity = runtime.process_identity(process.pid, dict(path=str(call / 'accepted.json')))
        (call / 'process.json').write_text(json.dumps(identity))
    return root, record, call, process


@pytest.mark.process_e2e
def test_observe_reports_live_process_log_tail_and_poll_age_without_adapter(tmp_path):
    root, record, call, process = waiting_call(tmp_path)
    try:
        lines = ''.join(f'line {n}\n' for n in range(100))
        (call / 'stdout.raw').write_text(lines + '\x1b[32mepoch 1\x1b[0m 10%\r epoch 1 90%\n' + root.as_posix() + '/secret\n')
        (call / 'stderr.raw').write_bytes(b'')
        monitor.record_poll(root, 2, {call.name: 'running'})
        value = monitor.observe(root, record, 2, include_logs=True,
                                clean=lambda text, limit: text.replace(root.as_posix(), '<instance>'))
        row = value['calls'][0]
        assert row['process'] == dict(pid=process.pid, alive=True)
        assert row['queried_state'] == 'running' and value['heartbeat_age'] < 5
        assert row['last_output_age'] < 5 and not row['result_ready']
        assert [log['name'] for log in row['logs']] == ['stdout']  # Empty stderr is omitted.
        text = row['logs'][0]['text'].splitlines()
        assert len(text) == monitor.LOG_TAIL_LINES and text[-2] == ' epoch 1 90%'
        assert text[-1] == '<instance>/secret' and '\x1b' not in row['logs'][0]['text']
        assert monitor.observe(root, record, 2)['calls'][0]['logs'] == []  # Read-only viewers get no logs.
    finally:
        process.kill(); process.wait()
    row = monitor.observe(root, record, 2)['calls'][0]
    assert row['process']['alive'] is None  # Absence in this PID namespace is not a positive stopped identity.


def test_record_poll_throttles_unchanged_states_but_writes_changes(tmp_path):
    root = tmp_path / 'instance'
    monitor.record_poll(root, 3, {'a': 'queued'})
    path = monitor.heartbeat_path(root, 3)
    first = json.loads(path.read_text())
    monitor.record_poll(root, 3, {'a': 'queued'})
    assert json.loads(path.read_text()) == first
    monitor.record_poll(root, 3, {'a': 'running'})
    assert json.loads(path.read_text())['states'] == {'a': 'running'}


def test_observe_is_absent_without_wait_and_tolerates_missing_local_files(tmp_path):
    root, record, call, _ = waiting_call(tmp_path, live=False)
    assert monitor.observe(root, tmp_path / 'missing', 2) is None
    row = monitor.observe(root, record, 2)['calls'][0]
    assert row['process'] is None and row['last_output_age'] is None and row['queried_state'] is None


def test_wait_facts_distinguish_fresh_stale_and_unmonitored_watchers():
    wait = dict(calls=[dict(call_id='c1', external_run_id='job-7')], since='2026-11-11T00:00:00+00:00')
    live = dict(waited_seconds=3725., since='2026-11-11T08:00:00+08:00', heartbeat_age=3., stale_after=30.,
                calls=[dict(call_id='c1', result_ready=False, finished=False, queried_state='running',
                            process=dict(pid=42, alive=True), last_output_age=4., logs=[])])
    facts = {row['label']: row['value'] for row in ui_view.execution_wait_facts(wait, monitoring=True, live=live)}
    assert facts['监视状态'].startswith('正在监视：3 秒前核对过外部实验')
    assert facts['已等待'].startswith('1 小时 2 分')
    assert facts['原调用 c1'] == '实验进程 PID 42 正在运行；最近查询状态：运行中；最后输出在 4 秒前；外部 ID job-7'
    stale = ui_view.execution_wait_facts(wait, monitoring=True, live=dict(live, heartbeat_age=95.))
    assert '可能卡住' in stale[0]['value']
    dead = dict(live, calls=[dict(live['calls'][0], process=dict(pid=42, alive=False))])
    idle = {row['label']: row['value'] for row in ui_view.execution_wait_facts(wait, monitoring=False, live=dead)}
    assert idle['监视状态'].endswith('（最后一次核对在 3 秒前）')
    assert idle['原调用 c1'].startswith('实验进程 PID 42 已不在运行，结果尚未记录')
    assert '已等待' in ui_view.waited_text(wait)


def test_controller_observation_ignores_saved_running_and_does_not_create_lock(tmp_path):
    operation = dict(kind='controller', status='running', operation_id='a' * 32)
    assert monitor.controller_observation(tmp_path, [operation])['monitoring'] is None
    assert not (tmp_path / 'host-run.lock').exists()
    (tmp_path / 'host-run.lock').touch()
    observed = monitor.controller_observation(tmp_path, [operation], dict(heartbeat_age=1., stale_after=30.))
    assert observed == dict(monitoring=False, host_locked=False, exact_owner=False)


def test_foreground_controller_fresh_heartbeat_needs_no_detached_operation(tmp_path):
    import fcntl
    with (tmp_path / 'host-run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed = monitor.controller_observation(tmp_path, [], dict(heartbeat_age=1., stale_after=30., owner_alive=True))
        assert observed['monitoring'] is True and observed['host_locked'] is True
        unknown = monitor.controller_observation(tmp_path, [], dict(heartbeat_age=90., stale_after=30., owner_alive=None))
        assert unknown['monitoring'] is None
        missing_pid = monitor.controller_observation(tmp_path, [], dict(heartbeat_age=1., stale_after=30., owner_alive=None))
        assert missing_pid['monitoring'] is None


def test_process_observation_preserves_unknown_and_detects_exact_dead_identity(monkeypatch):
    binding = dict(path='accepted.json')
    identity = dict(pid=42, boot_id='boot', start_ticks=3, pgid=42, sid=42, task_ref=binding)
    def missing(*args):
        raise FileNotFoundError('unavailable PID namespace')
    monkeypatch.setattr(runtime, 'process_identity', missing)
    assert monitor.process_observation(identity, binding) is None
    monkeypatch.setattr(runtime, 'process_identity', lambda *args: dict(identity, state='Z'))
    assert monitor.process_observation(identity, binding) is False
    monkeypatch.setattr(runtime, 'process_identity', lambda *args: dict(identity, state='S', start_ticks=4))
    assert monitor.process_observation(identity, binding) is False


def test_acquired_wait_is_reused_without_reopening_record(tmp_path, monkeypatch):
    root, record, call, _ = waiting_call(tmp_path, live=False)
    wait, source = monitor.read_wait(record)
    monkeypatch.setattr(monitor, 'read_wait', lambda *args: pytest.fail('consumer reopened wait'))
    observed = monitor.observe_acquired(root, 2, source)
    assert observed['calls'][0]['call_id'] == wait['calls'][0]['call_id']
    assert 'directories' not in wait


@pytest.mark.parametrize('consumes, phrase', [(True, '计入研究时间预算'), (False, '不消耗研究时间预算')])
def test_wait_cost_label_uses_projected_frozen_policy(consumes, phrase):
    facts = {row['label']: row['value'] for row in ui_view.execution_wait_facts({}, monitoring=None,
        waiting_consumes_budget=consumes)}
    assert phrase in facts['等待计时']
    assert '状态未知' in facts['监视状态']
