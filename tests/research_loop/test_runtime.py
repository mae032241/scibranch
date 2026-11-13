"""Local completion recovery with deterministic processes, never real models."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts, controller, runtime
from scidiscovery.research_loop.cli import status


def _failure_originals(root, record):
    """Retain only named fixture originals after an existing assertion fails."""
    destination = os.environ.get('SCID_TEST_LOG_DIR')
    if not destination:
        return '\nRuntime originals: SCID_TEST_LOG_DIR is unset.'
    parts = []
    for name in ('native-outcome.json', 'native.stderr', 'runner.stderr',
            'runner-cleanup.json', 'native.jsonl', 'usage-observation.json',
            'science-output.json', 'result.pending.json', 'runner-process.json',
            'native-process.json', 'watchdog-process.json'):
        path = record / name
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as stream:
            raw = stream.read(16384)
        parts.append(name + ':\n' + raw.decode('utf-8', errors='replace'))
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / ('runtime-' + root.parent.name + '.log')
    output.write_text('\n\n'.join(parts), encoding='utf-8')
    return '\nRuntime originals: ' + str(output)


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
request=json.load(sys.stdin)
count=work/'work/launches.txt'
with count.open('a') as f:f.write('launch\\n')
child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
(work/'work/child.json').write_text(json.dumps({'pid':child.pid}))
(work/'work/ready').touch()
while not (work/'work/release').exists():time.sleep(.02)
child.terminate()
try:child.wait(timeout=.1)
except subprocess.TimeoutExpired:child.kill();child.wait()
payload={'summary':'A finding','scientific_result':'Bounded evidence','limitations':['Fixture only'],
'next_action':'experiment','route_reason':'No external event protocol','search_plan':None}
if 'implementation_selection' in request['response_schema']['properties']:
 payload['implementation_selection']=None
print(json.dumps({'type':'session','session_id':'exact-local-thread'}),flush=True)
print(json.dumps({'type':'result','result':payload}),flush=True)
print(json.dumps({'type':'completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':2}}),flush=True)
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
        max_tokens=1000, max_seconds=60, max_steps=3, model='fake-model', reasoning_effort='high',
        native_backend='protocol', search_policy=None)
    work = contracts.workspace(root, 1)
    record = contracts.host_record(work)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))

    def launch(command='run'):
        if command=='run' and not (root/'jobs/001.json').exists():
            # Explicit supported component task; controller defaults stay W-led.
            contracts.build_job(root,1,'H',[],instruction='Observe this one mechanical transport fixture; no scientific qualification.')
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
        until(lambda: not runtime.process_matches(owner, owner['task_ref']))


@pytest.mark.process_e2e
def test_runner_loss_cleans_observed_group_and_never_replays(local):
    root, work, record, launch, until = local
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    runner = contracts.read(record / 'runner-process.json')
    leader = contracts.read(record / 'native-process.json')
    child = _owned_child(record)
    originals = {record/name: (record/name).read_bytes() for name in
        ('native-intent.json', 'runner-process.json', 'native-process.json', 'watchdog-process.json')}
    os.kill(runner['pid'], signal.SIGKILL)
    until(lambda: (record / 'runner-cleanup.json').exists())
    until(lambda: not runtime.process_matches(child, child['task_ref']))
    cleanup = contracts.read(record / 'runner-cleanup.json')
    assert cleanup['status'] in {'observed_group_signalled', 'cleanup_unknown'}, _failure_originals(root, record)
    if cleanup['status'] == 'cleanup_unknown':
        assert not runtime.process_matches(leader, leader['task_ref']), _failure_originals(root, record)
    assert cleanup['limitation'] == 'Only exactly observed original process-group members; detached descendants are not covered.'
    assert runtime.node_state(root, 1) == 'unknown'
    assert not (record / 'native-outcome.json').exists()
    front.communicate(timeout=5)
    consumed = budget.snapshot(root)['advance_seconds']
    first = controller.run(root)
    second = controller.run(root)
    assert first['status'] == second['status'] == 'native_outcome_unsealed'
    assert first['usage']['unknown_steps'] == second['usage']['unknown_steps'] == [1]
    assert budget.snapshot(root)['advance_seconds'] >= consumed
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


def test_intent_before_runner_start_is_unknown_and_never_replayed(local):
    root, work, record, launch, until = local
    contracts.build_job(root, 1, 'H', [])
    assert runtime.node_state(root, 1) == 'unstarted'
    accepted_budget.accept_native(root, 1, record, dict(step=1,
        task_ref=contracts.record_ref(root / 'jobs/001.json')))
    assert runtime.node_state(root, 1) == 'unknown'
    assert controller.run(root)['status'] == 'native_outcome_unsealed'
    assert not (work / 'work/launches.txt').exists()


@pytest.mark.process_e2e
def test_stale_identity_never_signals_an_unrelated_process(local):
    root, work, record, launch, until = local
    contracts.build_job(root, 1, 'H', [])
    binding = contracts.record_ref(root / 'jobs/001.json')
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
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    contracts.write(root / 'pause.request', dict(reason='operator pause'))
    front.kill()
    front.communicate(timeout=3)
    resumed = launch()
    (work / 'work/release').touch()
    out, err = resumed.communicate(timeout=8)
    assert resumed.returncode == 0, out + err + _failure_originals(root, record)
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
    contracts.write(root / 'pause.request', dict(reason='operator pause'))
    (work / 'work/release').touch()
    out, err = front.communicate(timeout=8)
    assert front.returncode == 0, out + err + _failure_originals(root, record)
    assert json.loads(out)['status'] == 'paused'
    assert json.loads(out)['completed_steps'] == 1
    assert (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()
    owner = contracts.read(record / 'runner-process.json')
    until(lambda: not runtime.process_matches(owner, owner['task_ref']))
    with pytest.raises(RuntimeError, match='refusing duplicate'):
        runtime._run(root, 1)
    assert (work / 'work/launches.txt').read_text() == 'launch\n'


@pytest.mark.parametrize('budget,reason', [('wall', 'wall_limit'), ('tokens', 'token_limit')])
def test_actual_native_boundary_rechecks_budget_without_submitting(local, monkeypatch, budget, reason):
    from scidiscovery.research_loop import budget as control_budget, native, profiles
    from test_history_handoff import finish
    from test_soft_node_budget import start_clock
    root, work, record, launch, until = local
    settings = contracts.settings(root)
    settings.pop('time_budget_basis')  # This fixture checks the legacy absolute deadline.
    contracts.write(root / 'settings.json', settings)
    contracts.write(root / 'settings-identity.json', dict(path='settings.json'))
    start_clock(root, 'source boundary fixture starts accepted clock')
    # Publish historical accounting through the same fixture writer and seal
    # owner as other process-free science fixtures, before accepting this call.
    prior = record
    write = contracts.write
    def fixture_write(path, value):
        if Path(path) == prior / 'usage.json':
            if budget == 'tokens':
                value = dict(input_tokens=1000, cached_input_tokens=0, output_tokens=0)
        return write(path, value)
    with monkeypatch.context() as fixture:
        fixture.setattr(contracts, 'write', fixture_write)
        finish(root, 1, 'H', [], action='analyze')
    job = contracts.build_job(root, 2, 'A', [1])
    work = contracts.workspace(root, 2)
    record = contracts.host_record(work)
    accepted_budget.accept_native(root, 2, record,
        dict(step=2, task_ref=contracts.record_ref(root / 'jobs/002.json'),
            **profiles.binding(settings, job)))
    if budget == 'wall':
        stamp = control_budget.clock_stamp()
        stamp['at_unix'] += settings['max_seconds'] + 1
        stamp['at_boottime'] += settings['max_seconds'] + 1
        monkeypatch.setattr(control_budget, 'clock_stamp', lambda: stamp)
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('budget must prevent submission'))
    native.run_native(root, 2)
    outcome = contracts.read(record / 'native-outcome.json')
    assert outcome['stop_reason'] == reason
    assert outcome['native_started'] is False
    assert status(root)['nodes'][-1]['state'] == 'native_not_submitted'
    assert contracts.read(record / 'usage.json') == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    assert not (record / 'native-process.json').exists()
    assert not (record / 'science-output.json').exists()


def test_historical_billing_unknown_preserves_missing_meter_and_allows_ordinary_admission(local, monkeypatch):
    from scidiscovery.research_loop import budget as control_budget, native
    from test_history_handoff import finish
    from test_soft_node_budget import start_clock
    root, work, prior, launch, until = local
    monkeypatch.setattr(native.subprocess, 'Popen',
        lambda *a, **kw: pytest.fail('source admission fixture must not submit native work'))
    start_clock(root, 'billing observation fixture starts accepted clock')
    write = contracts.write
    def fixture_write(path, value):
        if Path(path) == prior / 'usage.json':
            return  # Publish no historical billing observation.
        return write(path, value)
    with monkeypatch.context() as fixture:
        fixture.setattr(contracts, 'write', fixture_write)
        finish(root, 1, 'H', [], action='analyze')
    job = contracts.build_job(root, 2, 'A', [1])
    usage = native.usage(root)
    assert usage['unknown_steps'] == [1]
    assert usage['blocking_unknown_count'] == 0
    assert not (prior / 'usage.json').exists()
    assert control_budget.final_a_usage_unknown(None, usage, ignore_step=2) is False
    assert control_budget.node_admission(root, job, ignore_step=2) is None
    record = contracts.host_record(contracts.workspace(root, 2))
    assert not (record / 'native-intent.json').exists()
    assert not (record / 'native-process.json').exists()


def _leave_child_alive(root):
    launcher = Path(contracts.settings(root)['native_launcher_argv'][1])
    script = launcher.read_text()
    script = script.replace('child.terminate()\ntry:child.wait(timeout=.1)\nexcept subprocess.TimeoutExpired:child.kill();child.wait()\n', '')
    script = script.replace("time.sleep(60)'])", "time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
    launcher.write_text(script)


def _owned_child(record):
    leader = contracts.read(record/'native-process.json')
    members = runtime._observed_group_members(leader)
    parents = {row['ppid'] for row in members}
    leaves = [row for row in members if row['pid'] not in parents and row['pid']!=leader['pid']]
    assert len(leaves)==1, members
    return leaves[0]


def _assert_child_dead(child, binding):
    assert not runtime.process_matches(child, binding)


@pytest.mark.process_e2e
def test_successful_leader_exit_cleans_surviving_standard_child(local):
    root, work, record, launch, until = local
    _leave_child_alive(root)
    front = launch()
    until(lambda: (work / 'work/ready').exists())
    child = _owned_child(record)
    contracts.write(root / 'pause.request', dict(reason='operator pause'))
    (work / 'work/release').touch()
    out, err = front.communicate(timeout=8)
    assert front.returncode == 0, out + err + _failure_originals(root, record)
    assert json.loads(out)['status'] == 'paused'
    assert json.loads(out)['completed_steps'] == 1
    assert (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()
    assert contracts.read(record / 'native-outcome.json')['cleanup'] == 'observed_group_cleaned'
    _assert_child_dead(child, contracts.record_ref(root / 'jobs/001.json'))


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
    contracts.write(root / 'settings-identity.json', dict(path='settings.json'))
    front = launch('closeout')
    until(lambda: (work / 'work/ready').exists())
    binding = contracts.record_ref(root / 'jobs/001.json')
    child = _owned_child(record)
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
    _assert_child_dead(child, binding)
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
    contracts.write(root / 'pause.request', dict(reason='operator pause'))
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
    original = (record / 'native-outcome.json').read_bytes()
    result = controller.run(root)
    assert result['status'] == 'paused', _failure_originals(root, record)
    assert result['completed_steps'] == 1
    assert (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()
    assert (record / 'native-outcome.json').read_bytes() == original
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
