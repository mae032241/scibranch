"""Local completion recovery with deterministic processes, never real models."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import budget, contracts, controller, runtime
from scidiscovery.research_loop.cli import status


@pytest.fixture
def local(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Resolve the bounded mechanism.')
    launcher = tmp_path / 'native.py'
    launcher.write_text('''import json,os,pathlib,subprocess,sys,time
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
args=sys.argv[1:]
sys.stdin.read()
count=work/'work/launches.txt'
with count.open('a') as f:f.write('launch\\n')
child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
(work/'work/child.json').write_text(json.dumps({'pid':child.pid}))
(work/'work/ready').touch()
while not (work/'work/release').exists():time.sleep(.02)
child.terminate()
try:child.wait(timeout=.1)
except subprocess.TimeoutExpired:child.kill();child.wait()
output=pathlib.Path(args[args.index('--output-last-message')+1])
payload={'summary':'A finding','scientific_result':'Bounded evidence','limitations':['Fixture only'],
'next_action':'wait','route_reason':'No external event protocol','search_plan':None}
if 'implementation_selection' in json.loads((work/'science-task.json').read_text())['final_response']:
 payload['implementation_selection']=None
output.write_text(json.dumps(payload))
print(json.dumps({'type':'thread.started','thread_id':'exact-local-thread'}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}}),flush=True)
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
        max_tokens=1000, max_seconds=60, max_steps=3, model='fake-model', reasoning_effort='high')
    work = contracts.workspace(root, 1)
    record = contracts.host_record(work)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))

    def launch(command='run'):
        return subprocess.Popen([sys.executable, '-m', 'scidiscovery.research_loop.cli',
            command, '--root', str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def until(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        pytest.fail('Deterministic local event did not arrive')

    yield root, work, record, launch, until
    # A failing assertion must release accepted fixture work, not leave it alive.
    if (work / 'work').exists():
        (work / 'work/release').touch()
    if (record / 'runner-process.json').exists():
        owner = contracts.read(record / 'runner-process.json')
        until(lambda: not runtime.process_matches(owner, owner['task_sha256']))


@pytest.mark.process_e2e
def test_controller_kill_rejoins_same_native_execution(local):
    root, work, record, launch, until = local
    first = launch()
    until(lambda: (work / 'work/ready').exists())
    owner = contracts.read(record / 'runner-process.json')
    assert status(root)['nodes'][0]['state'] == 'running'
    first.kill()
    first.communicate(timeout=3)
    assert runtime.process_matches(owner, owner['task_sha256'])
    before_wait = budget.snapshot(root)['advance_seconds']
    assert budget.snapshot(root)['clock_phase'] == 'advance'
    second = launch()
    time.sleep(.15)
    assert second.poll() is None  # Completion waiting launches no new role.
    assert budget.snapshot(root)['advance_seconds'] > before_wait
    (work / 'work/release').touch()
    out, err = second.communicate(timeout=8)
    assert second.returncode == 0, out + err
    result = json.loads(out)
    assert result['status'] == 'wait'
    assert result['completed_steps'] == 1
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    assert (work / 'work/launches.txt').read_text() == 'launch\n'
    assert controller.run(root) == result
    assert contracts.read(record / 'native-outcome.json')['thread_id'] == 'exact-local-thread'


@pytest.mark.process_e2e
def test_runner_loss_cleans_observed_group_and_never_replays(local):
    root, work, record, launch, until = local
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    runner = contracts.read(record / 'runner-process.json')
    child_pid = contracts.read(work / 'work/child.json')['pid']
    child = runtime.process_identity(child_pid, runner['task_sha256'])
    os.kill(runner['pid'], signal.SIGKILL)
    until(lambda: (record / 'runner-cleanup.json').exists())
    until(lambda: not runtime.process_matches(child, child['task_sha256']))
    assert contracts.read(record / 'runner-cleanup.json')['status'] == 'observed_group_signalled'
    assert runtime.node_state(root, 1) == 'unknown'
    assert not (record / 'native-outcome.json').exists()
    front.communicate(timeout=5)
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


@pytest.mark.process_e2e
def test_identity_mismatch_is_unknown_without_killing_original(local):
    root, work, record, launch, until = local
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    original = contracts.read(record / 'runner-process.json')
    contracts.write(record / 'runner-process.json', dict(original, start_ticks=original['start_ticks'] + 1))
    assert runtime.node_state(root, 1) == 'unknown'
    assert runtime.process_matches(original, original['task_sha256'])
    until(lambda: front.poll() is not None)
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    (work / 'work/release').touch()
    until(lambda: (record / 'native-outcome.json').exists())
    front.communicate(timeout=5)
    # Terminal output precedes the detached runner's final clock append under
    # the host writer lock. Wait for its real identity, not the tampered file.
    until(lambda: not runtime.process_matches(original, original['task_sha256']))
    assert controller.run(root)['status'] == 'wait'
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


def test_intent_before_runner_start_is_unknown_and_never_replayed(local):
    root, work, record, launch, until = local
    contracts.build_job(root, 1, 'H', [])
    assert runtime.node_state(root, 1) == 'unstarted'
    contracts.write(record / 'native-intent.json', dict(step=1,
        task_sha256=contracts.digest(root / 'jobs/001.json')))
    assert runtime.node_state(root, 1) == 'unknown'
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert not (work / 'work/launches.txt').exists()


@pytest.mark.process_e2e
def test_stale_identity_never_signals_an_unrelated_process(local):
    root, work, record, launch, until = local
    contracts.build_job(root, 1, 'H', [])
    binding = contracts.digest(root / 'jobs/001.json')
    outsider = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'], start_new_session=True)
    try:
        identity = runtime.process_identity(outsider.pid, binding)
        stale = dict(identity, start_ticks=identity['start_ticks'] + 1)
        contracts.write(record / 'runner-process.json', stale)
        contracts.write(record / 'native-process.json', stale)
        runtime._watch(root, 1)
        assert outsider.poll() is None
        assert contracts.read(record / 'runner-cleanup.json')['status'] == 'cleanup_unknown'
    finally:
        outsider.terminate()
        outsider.wait(timeout=3)


@pytest.mark.process_e2e
def test_pause_recovery_collects_accepted_node_before_stopping(local):
    root, work, record, launch, until = local
    settings = contracts.settings(root)
    launcher = Path(settings['native_launcher_argv'][1])
    launcher.write_text(launcher.read_text().replace("'next_action':'wait'", "'next_action':'experiment'"))
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    contracts.write(root / 'pause.request', dict(reason='operator pause'))
    front.kill()
    front.communicate(timeout=3)
    resumed = launch()
    (work / 'work/release').touch()
    out, err = resumed.communicate(timeout=8)
    assert resumed.returncode == 0, out + err
    assert json.loads(out)['status'] == 'paused'
    assert json.loads(out)['completed_steps'] == 1
    assert (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


@pytest.mark.process_e2e
def test_direct_runner_entry_rejects_duplicate_and_retained_execution(local):
    root, work, record, launch, until = local
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    with pytest.raises(BlockingIOError):
        runtime._run(root, 1)
    (work / 'work/release').touch()
    front.communicate(timeout=8)
    owner = contracts.read(record / 'runner-process.json')
    until(lambda: not runtime.process_matches(owner, owner['task_sha256']))
    with pytest.raises(RuntimeError, match='refusing duplicate'):
        runtime._run(root, 1)
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


@pytest.mark.parametrize('budget,reason', [('wall', 'wall_limit'), ('tokens', 'token_limit'), ('unknown', 'usage_unknown')])
def test_actual_native_boundary_rechecks_budget_without_submitting(local, monkeypatch, budget, reason):
    from scidiscovery.research_loop import native
    root, work, record, launch, until = local
    settings = contracts.settings(root)
    settings.pop('time_budget_basis')  # This fixture checks the legacy absolute deadline.
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    contracts.build_job(root, 1, 'H', [])
    contracts.write(record / 'native-intent.json', dict(step=1, task_sha256=contracts.digest(root / 'jobs/001.json')))
    contracts.write(root / 'host-started.json', dict(at_unix=0 if budget == 'wall' else time.time()))
    if budget != 'wall':
        value = dict(input_tokens=1000 if budget == 'tokens' else 0, cached_input_tokens=0,
            output_tokens=0, observed_tokens=1000 if budget == 'tokens' else 0,
            unknown_steps=[1] if budget == 'tokens' else [1, 2])
        monkeypatch.setattr(native, 'usage', lambda _: value)
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('budget must prevent submission'))
    native.run_native(root, 1)
    outcome = contracts.read(record / 'native-outcome.json')
    assert outcome['stop_reason'] == reason
    assert outcome['native_started'] is False
    assert status(root)['nodes'][0]['state'] == 'native_not_submitted'
    assert not (record / 'native-process.json').exists()
    assert not (record / 'science-output.json').exists()


def _leave_child_alive(root):
    launcher = Path(contracts.settings(root)['native_launcher_argv'][1])
    script = launcher.read_text()
    script = script.replace('child.terminate()\ntry:child.wait(timeout=.1)\nexcept subprocess.TimeoutExpired:child.kill();child.wait()\n', '')
    script = script.replace("time.sleep(60)'])", "time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
    launcher.write_text(script)


def _assert_child_dead(work, binding):
    pid = contracts.read(work / 'work/child.json')['pid']
    try:
        child = runtime.process_identity(pid, binding)
    except FileNotFoundError:
        return
    assert not runtime.process_matches(child, binding)


@pytest.mark.process_e2e
def test_successful_leader_exit_cleans_surviving_standard_child(local):
    root, work, record, launch, until = local
    _leave_child_alive(root)
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    (work / 'work/release').touch()
    out, err = front.communicate(timeout=8)
    assert front.returncode == 0, out + err
    assert json.loads(out)['status'] == 'wait'
    assert contracts.read(record / 'native-outcome.json')['cleanup'] == 'observed_group_cleaned'
    _assert_child_dead(work, contracts.digest(root / 'jobs/001.json'))


@pytest.mark.process_e2e
def test_budget_term_leader_exit_cleans_stubborn_standard_child(local):
    root, work, record, launch, until = local
    _leave_child_alive(root)
    # Ordinary soft targets do not kill accepted work. The unique FINAL call
    # retains an enforced finite time slice, independent of the shared target.
    settings = contracts.settings(root)
    settings['node_budget_policy'] = {
        role: dict(max_observed_tokens=1000, max_seconds=1 if role == 'A' else 60)
        for role in ('H', 'W', 'A', 'E')}
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(sha256=contracts.digest(root / 'settings.json')))
    front = launch('closeout')
    until(lambda: (work / 'work/ready').exists())
    binding = contracts.digest(root / 'jobs/001.json')
    child = runtime.process_identity(contracts.read(work / 'work/child.json')['pid'], binding)
    leader = contracts.read(record / 'native-process.json')
    assert (child['pgid'], child['sid']) == (leader['pgid'], leader['sid'])
    out, err = front.communicate(timeout=8)
    assert front.returncode == 2, out + err
    assert json.loads(out)['status'] == 'closeout_native_unsealed', out + err
    outcome = contracts.read(record / 'native-outcome.json')
    assert outcome['returncode'] == -signal.SIGTERM
    assert outcome['stop_reason'] == 'node_slice_time_limit'
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert not (record / 'result-identity.json').exists()
    assert not runtime.process_matches(child, binding)
    _assert_child_dead(work, binding)
    final = contracts.read(root / 'jobs/001.json')
    assert final['role'] == 'A' and final['final_only']
    marker = budget.final_a_barrier(root)
    assert marker['step'] == 1
    assert marker['anchor']['limits']['max_seconds'] == 1
    originals = {name: (record / name).read_bytes() for name in (
        'native-intent.json', 'native-outcome.json', 'runner-process.json', 'native-process.json')}
    resumed = launch('closeout')
    out, err = resumed.communicate(timeout=8)
    assert resumed.returncode == 2, out + err
    assert json.loads(out)['status'] == 'closeout_native_unsealed', out + err
    assert budget.final_a_barrier(root) == marker
    assert all((record / name).read_bytes() == raw for name, raw in originals.items())
    assert not (root / 'jobs/002.json').exists()
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


@pytest.mark.process_e2e
def test_runner_completion_stops_clock_after_controller_disconnect(local):
    root, work, record, launch, until = local
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    front.kill()
    front.communicate(timeout=3)
    assert budget.snapshot(root)['clock_phase'] == 'advance'
    (work / 'work/release').touch()
    until(lambda: (record / 'native-outcome.json').exists())
    until(lambda: budget.snapshot(root)['clock_phase'] == 'paused')
    stopped = budget.snapshot(root)
    time.sleep(.08)
    later = budget.snapshot(root)
    assert later['advance_seconds'] == stopped['advance_seconds']
    assert later['paused_seconds'] > stopped['paused_seconds']
    assert not (record / 'result-identity.json').exists()
    assert controller.run(root)['status'] == 'wait'
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


@pytest.mark.process_e2e
def test_budget_amend_refuses_live_runner_even_after_controller_disconnect(local):
    root, work, record, launch, until = local
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    front.kill()
    front.communicate(timeout=3)
    with pytest.raises(RuntimeError, match='running or unknown'):
        budget.amend(root, max_tokens=2000, authorization_note='A declaration cannot bypass activity')
    assert all(contracts.read(path)['kind'] != 'budget_amend'
               for path in (root / 'control-ledger').glob('*.json'))
    (work / 'work/release').touch()
    until(lambda: (record / 'native-outcome.json').exists())
