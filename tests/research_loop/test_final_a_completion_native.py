"""Real local final-A transport, with no provider or scientific execution."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, runtime
from test_final_a_completion_contract import accept, complete
from test_history_handoff import finish


FAKE_NATIVE = r'''import json,os,pathlib,sys,time
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
assert os.environ['THIN_HWA_ROLE']=='A'
config=json.loads(pathlib.Path(sys.argv[0]).with_suffix('.json').read_text())
with (work/'work/launches').open('a') as stream:stream.write('A\n')
if config['meter']=='mismatched':
 thread='12345678-1111-4111-8111-123456789abc'
 home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
 path=home/'sessions'/('rollout-'+thread+'.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n'+
  json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=
   dict(input_tokens=230,cached_input_tokens=100,output_tokens=10)))))+'\n')
 print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
payload=dict(summary='The original final report is retained.',
 scientific_result='Completed the bounded assessment; the scientific objective remains unresolved.',
 limitations=['A deterministic transport fixture supplies no scientific qualification.'],
 implementation_selection=None,candidate_recommendation=None,final_coverage=None,search_review=None,
 progress_assessment=None,decision_request=None,portfolio_review=None,
 next_action='finish',route_reason='Preserve this original report and its limitations.')
raw=json.dumps(payload)
print(json.dumps(dict(type='item.completed',item=dict(type='agent_message',phase='final_answer',text=raw))),flush=True)
if config['terminal']:
 event=dict(type='turn.completed')
 if config['meter'] is True or config['meter']=='mismatched':
  event['usage']=dict(input_tokens=210,cached_input_tokens=209,output_tokens=3)
 print(json.dumps(event),flush=True)
 (work/'work/terminal-emitted').touch()
# The native terminal already occurred. Delayed file publication is silent;
# emitting fixture activity here would contradict the terminal under test.
time.sleep(config['delay'])
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(raw)
(work/'work/final-written').touch()
raise SystemExit(config['returncode'])
'''


def instance(tmp_path, *, mode='soft', node_seconds=10, total_seconds=30,
             delay=.35, meter=True, terminal=None, returncode=0):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Complete the accepted final report without rerunning scientific work.')
    launcher = tmp_path / 'fake-native.py'
    launcher.write_text(FAKE_NATIVE)
    c.write(launcher.with_suffix('.json'), dict(delay=delay, meter=meter,
        terminal=bool(meter) if terminal is None else terminal, returncode=returncode))
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
        max_tokens=200, max_seconds=total_seconds, max_steps=2,
        model='deterministic-fixture-no-model', reasoning_effort='high',
        search_policy=None, node_budget_mode=mode,
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=node_seconds)
                            for role in 'HWAE'})
    finish(root, 1, 'H', [], action='analyze')
    budget.begin_closeout(root, 1, 2, 'Explicit final transport fixture', final_only=True)
    return root


@pytest.mark.parametrize('mode', ['soft'])
@pytest.mark.process_e2e
def test_last_original_a_collects_delayed_json_after_local_and_shared_token_overrun(tmp_path, mode):
    root = instance(tmp_path, mode=mode)
    original_settings = (root / 'settings.json').read_bytes()
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, ensure_ascii=False, indent=2)
    record = execution.origin(root, 2)
    work = c.workspace(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    intent = c.read(record / 'native-intent.json')
    assert (work / 'work/terminal-emitted').exists()
    assert (work / 'work/final-written').exists()
    assert (work / 'work/launches').read_text() == 'A\n'
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['metering']['coverage'] == 'terminal'
    assert c.validate(work)['scientific_result'].startswith('Completed the bounded assessment')
    assert c.read(record / 'usage.json') == dict(input_tokens=210, cached_input_tokens=209, output_tokens=3)
    assert native.usage(root)['observed_tokens'] == 223
    assert native.usage(root)['cached_input_tokens'] == 211
    expected = dict(original_shared_max_tokens=200, original_local_target_tokens=100,
        accounted_shared_tokens=223, observed_node_tokens=213,
        shared_tokens_over_ceiling=23, local_tokens_over_target=113)
    assert outcome['final_a_completion']['final_overrun'] == expected
    assert result['final_overrun'] == expected
    assert budget.final_a_return(root, c.read(root / 'jobs/002.json'), intent)['step'] == 2
    assert budget.snapshot(root)['max_tokens'] == 200
    assert (root / 'settings.json').read_bytes() == original_settings
    originals = {path: path.read_bytes() for path in (
        record / 'native-intent.json', record / 'native-outcome.json', record / 'usage.json',
        record / 'science-output.json', record / 'result-identity.json')}
    assert controller.run(root)['status'] == 'final_a_completed'
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (work / 'work/launches').read_text() == 'A\n'
    assert not (record / 'continuations').exists()
    assert not (root / 'jobs/003.json').exists()


@pytest.mark.process_e2e
def test_final_completion_still_stops_real_process_at_original_node_time_boundary(tmp_path):
    root = instance(tmp_path, node_seconds=.5, delay=4)
    expected = 'node_slice_time_limit'
    result = controller.run(root)
    assert result['status'] == 'closeout_native_unsealed', result
    record = execution.origin(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    assert budget.final_a_barrier(root)['step'] == 2
    assert outcome['stop_reason'] == expected
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert not (c.workspace(root, 2) / 'work/final-written').exists()
    assert not (record / 'result-identity.json').exists()
    assert not (record / 'continuations').exists()
    assert not (root / 'jobs/003.json').exists()
    assert (c.workspace(root, 2) / 'work/launches').read_text() == 'A\n'


@pytest.mark.process_e2e
def test_final_completion_survives_shared_wall_exhaustion_with_finite_node_time(tmp_path):
    root = instance(tmp_path, mode='soft', node_seconds=10, total_seconds=2, delay=4)
    assert controller.run(root)['status'] == 'final_a_completed'
    outcome = c.read(execution.origin(root, 2)/'native-outcome.json')
    assert outcome['stop_reason'] is None and outcome['returncode'] == 0
    assert budget.snapshot(root)['seconds_remaining'] == 0
    assert (c.workspace(root, 2)/'work/final-written').exists()


@pytest.mark.parametrize('fault', ['missing_terminal', 'nonzero_exit'])
@pytest.mark.process_e2e
def test_final_json_without_terminal_event_or_zero_exit_never_seals_or_relaunches(tmp_path, fault):
    root = instance(tmp_path, meter=fault != 'missing_terminal', returncode=7 if fault == 'nonzero_exit' else 0)
    result = controller.run(root)
    assert result['status'] == 'closeout_native_unsealed', result
    record = execution.origin(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    assert budget.final_a_barrier(root)['step'] == 2
    assert (c.workspace(root, 2) / 'work/final-written').exists()
    assert outcome['cleanup'] == 'observed_group_cleaned'
    if fault == 'missing_terminal':
        assert outcome['returncode'] == 0
        assert outcome['metering']['coverage'] == 'unknown'
        assert native.usage(root)['unknown_steps'] == [2]
        assert not (record / 'usage.json').exists()
    else:
        assert outcome['returncode'] == 7
        assert outcome['metering']['coverage'] == 'terminal'
        assert native.usage(root)['observed_tokens'] == 223
    originals = {path: path.read_bytes() for path in (
        record / 'native-intent.json', record / 'native-outcome.json')}
    assert controller.run(root)['status'] != 'final_a_completed'
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (c.workspace(root, 2) / 'work/launches').read_text() == 'A\n'
    assert not (record / 'result-identity.json').exists()
    assert not (record / 'continuations').exists()
    assert not (root / 'jobs/003.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('event,reason', [
    (dict(type='turn.failed',error=dict(message='Later failure')), 'native_failure_event'),
    (dict(type='turn.completed',usage=dict(input_tokens=210,cached_input_tokens=209,output_tokens=3)),
        'native_completion_duplicated'),
])
def test_final_completion_rejects_later_native_failure_or_duplicate_without_replay(tmp_path, monkeypatch, event, reason):
    monkeypatch.setitem(globals(), 'FAKE_NATIVE', FAKE_NATIVE.replace("raise SystemExit(config['returncode'])",
        'print('+repr(json.dumps(event))+",flush=True)\nraise SystemExit(config['returncode'])"))
    root = instance(tmp_path)
    result = controller.run(root)
    assert result['status'] == 'closeout_native_unsealed', result
    record = execution.origin(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] == reason
    assert outcome['metering']['coverage'] == 'unknown' and outcome['metering']['reason'] == reason
    assert c.read(record / 'usage.json') == dict(input_tokens=210,cached_input_tokens=209,output_tokens=3)
    assert native.usage(root)['unknown_steps'] == [2]
    assert (record / 'result.pending.json').is_file()
    assert not (record / 'science-output.json').exists() and not (record / 'result-identity.json').exists()
    originals = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    assert controller.run(root)['status'] != 'final_a_completed'
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (c.workspace(root, 2) / 'work/launches').read_text() == 'A\n'
    assert not (record / 'continuations').exists() and not (root / 'jobs/003.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('meter', ['missing'])
def test_final_native_terminal_report_seals_with_unknown_billing_without_relaunch(tmp_path, meter):
    root = instance(tmp_path, meter=meter, terminal=True)
    settings = (root / 'settings.json').read_bytes()
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', result
    record = execution.origin(root, 2)
    work = c.workspace(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['metering']['coverage'] == 'unknown'
    assert outcome['metering']['source'] == 'turn.completed'
    assert outcome['final_a_completion']['final_overrun']['usage_coverage'] == 'unknown'
    assert (work / 'work/terminal-emitted').exists() and (work / 'work/final-written').exists()
    assert any(json.loads(line).get('type') == 'turn.completed' for line in (record / 'native.jsonl').read_text().splitlines())
    author = c.validate(work)
    assert author['next_action'] == 'finish' and 'objective remains unresolved' in author['scientific_result']
    assert result['submission_result']['status'] == 'unsubmitted'
    usage = native.usage(root)
    assert usage['unknown_steps'] == [2]
    assert usage['accounted_tokens'] >= usage['observed_tokens']
    if meter == 'missing':
        assert not (record / 'usage.json').exists()
        assert usage['observed_tokens'] == 10  # Only the exact H meter is known.
        overrun = outcome['final_a_completion']['final_overrun']
        assert overrun['observed_node_tokens'] is None and overrun['local_tokens_over_target'] is None
    else:
        observed = c.read(record / 'usage.json')
        assert observed['input_tokens'] >= 230 and observed['output_tokens'] >= 10
        assert usage['observed_tokens'] >= 250
    originals = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    grant = budget.final_a_barrier(root)
    for command in (controller.run, controller.closeout):
        repeated = command(root)
        assert repeated['status'] == 'final_a_completed', repeated
        assert c.validate(work) == author and native.usage(root) == usage
        assert budget.final_a_barrier(root) == grant
        assert all(path.read_bytes() == raw for path, raw in originals.items())
        assert (work / 'work/launches').read_text() == 'A\n'
        assert not (record / 'continuations').exists() and not (root / 'jobs/003.json').exists()
        assert (root / 'settings.json').read_bytes() == settings


@pytest.mark.process_e2e
def test_final_native_terminal_with_unknown_cleanup_never_seals_or_relaunches(tmp_path, monkeypatch):
    root = instance(tmp_path, meter='missing', terminal=True)
    runners = []
    def start_with_unknown_cleanup(root, step, attempt=0):
        # The runner is a fresh interpreter. Inject the observation fault there
        # while retaining its real lease, watchdog, native process and protocol.
        bootstrap = ('import sys;from pathlib import Path;sys.path.insert(0,sys.argv.pop(1));'
            'from scidiscovery.research_loop import runtime;'
            'runtime.cleanup_owned_group=lambda identity:"cleanup_unknown";'
            'runtime._run(Path(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3]))')
        record = execution.record(root, step, attempt)
        with (record / 'runner.stderr').open('ab') as err:
            process = subprocess.Popen([sys.executable,'-c',bootstrap,
                str(Path(runtime.__file__).resolve().parents[2]),str(root),str(step),str(attempt)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=err,
                start_new_session=True,close_fds=True)
        runners.append(process)
        return process
    monkeypatch.setattr(runtime, 'start', start_with_unknown_cleanup)
    result = controller.run(root)
    assert result['status'] == 'closeout_native_unsealed', result
    assert len(runners) == 1
    runners[0].wait(timeout=10)
    record = execution.origin(root, 2)
    work = c.workspace(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    assert (work / 'work/terminal-emitted').exists() and (work / 'work/final-written').exists()
    assert outcome['returncode'] == 0 and outcome['cleanup'] == 'cleanup_unknown'
    assert outcome['stop_reason'] == 'cleanup_unknown'
    assert not (record / 'result-identity.json').exists()
    originals = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    repeated = controller.run(root)
    assert repeated['status'] != 'final_a_completed'
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (work / 'work/launches').read_text() == 'A\n'
    assert len(runners) == 1
    assert not (record / 'continuations').exists() and not (root / 'jobs/003.json').exists()


@pytest.mark.process_e2e
def test_final_native_unknown_billing_preserves_explicit_exact_submit_and_confirm(tmp_path, monkeypatch):
    import test_closeout_protocol as protocol
    old = "\nprint(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)\n"
    replacement = ("\nprint(json.dumps(dict(type='turn.completed')),flush=True)\n"
        "(work/'work/terminal-emitted').touch()\n")
    assert protocol.NATIVE.count(old) == 1
    # The scenario freezes this fake native process before preparation; the
    # existing real namespace adapter and its exact candidate remain in path.
    monkeypatch.setattr(protocol, 'NATIVE', protocol.NATIVE.replace(old, replacement))
    root = protocol.scene(tmp_path, [dict(action='deliver',candidate='run-1')], node_budget_mode='soft')
    settings = (root / 'settings.json').read_bytes()
    result = controller.closeout(root)
    assert result['status'] == 'delivery_confirmed' and result['goal_completion'] == 'not_asserted', result
    record = execution.origin(root, 3)
    work = c.workspace(root, 3)
    author = c.validate(work)
    assert author['next_action'] == 'deliver' and author['candidate_recommendation'] == 'run-1'
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['cleanup'] == 'observed_group_cleaned' and outcome['metering']['coverage'] == 'unknown'
    assert outcome['metering']['source'] == 'turn.completed'
    assert outcome['final_a_completion']['final_overrun']['usage_coverage'] == 'unknown'
    assert (work / 'work/terminal-emitted').exists() and not (record / 'usage.json').exists()
    overrun = outcome['final_a_completion']['final_overrun']
    assert overrun['observed_node_tokens'] is None and overrun['local_tokens_over_target'] is None
    usage = native.usage(root)
    assert usage['unknown_steps'] == [3] and usage['accounted_tokens'] >= usage['observed_tokens']
    grant = budget.final_a_barrier(root)
    assert grant['step'] == 3 and grant['basis'] == 'final_state'
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    marker = c.read(record / 'native-intent.json')['budget_slice']['final_return']
    assert all(row['intent']['final_return'] == marker for row in actions)
    assert actions[-1]['outcome']['result']['status'] == 'confirmed'
    artifact = actions[0]['intent']['binding']['source']['objects']['artifact']
    assert actions[-1]['outcome']['result']['artifact_ref'] == artifact
    assert c.read(root / 'runs/submitted.json')['run_id'] == 1
    assert (root / 'runs/submitted_model.pkl').read_bytes() == (root / 'submission/model.pkl').read_bytes() == Path(artifact['path']).read_bytes()
    originals = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    submitted = {path: path.read_bytes() for path in (root / 'runs/submitted.json',root / 'runs/submitted_model.pkl',root / 'submission/model.pkl')}
    for command in (controller.run, controller.closeout):
        repeated = command(root)
        assert repeated['status'] == 'delivery_confirmed' and repeated['last_confirmed'] == result['last_confirmed']
        assert budget.closeout_state(root)['actions'] == actions and budget.final_a_barrier(root) == grant
        assert native.usage(root) == usage and c.validate(work) == author
        assert all(path.read_bytes() == raw for path, raw in {**originals,**submitted}.items())
        assert (work / 'work/launches').read_text() == 'exec\n'
        assert not (record / 'continuations').exists() and not (root / 'jobs/004.json').exists()
        assert (root / 'settings.json').read_bytes() == settings


def test_wait_collects_same_original_after_partial_runner_receipt_is_completed(tmp_path, monkeypatch):
    root = instance(tmp_path)
    job, intent = accept(root)
    record = execution.origin(root, 2)
    receipt = record / 'runner-process.json'
    receipt.write_text('{')
    original_intent = (record / 'native-intent.json').read_bytes()
    original_grant = budget.final_a_barrier(root)
    now, states, pauses = [0.], [], []
    runner_alive, owner_probes = [False], []
    original_state = runtime.node_state

    def state(root, step):
        value = original_state(root, step)
        states.append(value)
        return value

    def process_matches(identity, binding):
        assert identity == c.read(receipt)
        assert identity['pid'] == os.getpid() and binding == execution.identity(root, 2)
        owner_probes.append(runner_alive[0])
        return runner_alive[0]

    def advance(seconds):
        now[0] += seconds
        pauses.append(seconds)
        if len(pauses) == 1:
            c.write(receipt, runtime.process_identity(os.getpid(), execution.identity(root, 2)))
            runner_alive[0] = True
        elif len(pauses) == 2:
            complete(root, job, intent)
            assert runner_alive[0] and not (record / 'result-identity.json').exists()
            # The fake owner exits after publishing its terminal originals;
            # pytest's own PID in the receipt is only an identity stand-in.
            runner_alive[0] = False
        else:
            pytest.fail('The same original runner should become terminal in two polling ticks')

    monkeypatch.setattr(native.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(native.time, 'sleep', advance)
    monkeypatch.setattr(runtime, 'node_state', state)
    monkeypatch.setattr(runtime, 'process_live', process_matches)
    monkeypatch.setattr(runtime, 'start', lambda *args, **kwargs: pytest.fail('Never relaunch an accepted original'))
    result = native.wait_native(root, 2)
    assert result['next_action'] == 'finish'
    assert states == ['unknown', 'running', 'terminal']
    assert owner_probes == [True, False] and len(pauses) == 2
    assert 0 < now[0] < 5
    assert (record / 'native-intent.json').read_bytes() == original_intent
    assert budget.final_a_barrier(root) == original_grant
    assert c.validate(c.workspace(root, 2))['next_action'] == 'finish'
    assert c.read(receipt)['pid'] == os.getpid()
    assert not (record / 'native-process.json').exists()
    assert not (record / 'continuations').exists()


def test_stale_unknown_probe_rechecks_published_original_before_collecting(tmp_path, monkeypatch):
    root = instance(tmp_path)
    job, intent = accept(root)
    record = execution.origin(root, 2)
    identity = runtime.process_identity(os.getpid(), execution.identity(root, 2))
    c.write(record / 'runner-process.json', identity)
    c.write(record / 'native-process.json', identity)
    originals = {path: path.read_bytes() for path in (
        record / 'native-intent.json', record / 'runner-process.json', record / 'native-process.json')}
    original_grant = budget.final_a_barrier(root)
    now, states, pauses = [0.], [], []
    runner_alive, owner_probes = [True], []
    original_state = runtime.node_state

    def state(root, step):
        value = original_state(root, step)
        if not states:
            assert value == 'running'
            # The first probe began before publication and returned stale
            # unknown; both complete receipts are visible by its return.
            value = 'unknown'
        states.append(value)
        return value

    def process_matches(receipt, binding):
        assert receipt == identity and binding == execution.identity(root, 2)
        owner_probes.append(runner_alive[0])
        return runner_alive[0]

    def advance(seconds):
        now[0] += seconds
        pauses.append(seconds)
        if len(pauses) == 2:
            complete(root, job, intent)
            outcome=c.read(record/'native-outcome.json')
            outcome['native_started']=True
            c.write(record/'native-outcome.json',outcome)
            assert runner_alive[0] and not (record / 'result-identity.json').exists()
            runner_alive[0] = False
        elif len(pauses) > 2:
            pytest.fail('Collect the same original without restarting its startup window')

    monkeypatch.setattr(native.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(native.time, 'sleep', advance)
    monkeypatch.setattr(runtime, 'node_state', state)
    monkeypatch.setattr(runtime, 'process_matches', process_matches)
    monkeypatch.setattr(runtime, 'process_live', process_matches)
    monkeypatch.setattr(runtime, 'start', lambda *args, **kwargs: pytest.fail('Never relaunch after a stale probe'))
    assert native.wait_native(root, 2)['next_action'] == 'finish'
    assert states == ['unknown', 'running', 'terminal']
    assert owner_probes[:2] == [True, True]
    assert owner_probes[2:] and not any(owner_probes[2:]) and len(pauses) == 2
    assert 0 < now[0] < 5
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert budget.final_a_barrier(root) == original_grant
    seal = record / 'result-identity.json'
    sealed_bytes = seal.read_bytes()
    assert native.wait_native(root, 2)['next_action'] == 'finish'
    assert seal.read_bytes() == sealed_bytes
    assert not (record / 'continuations').exists()
    assert not (root / 'jobs/003.json').exists()


def test_incomplete_runner_receipt_exhausts_only_original_startup_window(tmp_path, monkeypatch):
    root = instance(tmp_path)
    accept(root)
    record = execution.origin(root, 2)
    receipt = record / 'runner-process.json'
    receipt.write_text('{')
    original_intent = (record / 'native-intent.json').read_bytes()
    now, pauses = [0.], []

    def advance(seconds):
        now[0] += seconds
        pauses.append(seconds)
        assert len(pauses) < 110, 'The original five-second startup window must stay finite'

    monkeypatch.setattr(native.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(native.time, 'sleep', advance)
    monkeypatch.setattr(runtime, 'start', lambda *args, **kwargs: pytest.fail('Never replace an unknown original'))
    with pytest.raises(RuntimeError, match='Original native execution is unknown'):
        native.wait_native(root, 2)
    assert 5 <= now[0] < 5.1
    assert receipt.read_text() == '{'
    assert (record / 'native-intent.json').read_bytes() == original_intent
    assert not (record / 'native-outcome.json').exists()
    assert not (record / 'result-identity.json').exists()
    assert not (record / 'continuations').exists()


def test_complete_runner_receipt_with_wrong_identity_stays_unknown_after_startup_window(tmp_path, monkeypatch):
    root = instance(tmp_path)
    accept(root)
    record = execution.origin(root, 2)
    receipt = record / 'runner-process.json'
    c.write(receipt, runtime.process_identity(os.getpid(), 'different-original-task'))
    original = receipt.read_bytes()
    now, pauses = [0.], []

    def advance(seconds):
        now[0] += seconds
        pauses.append(seconds)
        assert len(pauses) < 110, 'A wrong identity cannot renew the original startup window'

    monkeypatch.setattr(native.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(native.time, 'sleep', advance)
    monkeypatch.setattr(runtime, 'start', lambda *args, **kwargs: pytest.fail('Never replace an unknown original'))
    with pytest.raises(RuntimeError, match='Original native execution is unknown'):
        native.wait_native(root, 2)
    assert 5 <= now[0] < 5.1
    assert receipt.read_bytes() == original
    assert not (record / 'native-outcome.json').exists()
    assert not (record / 'result-identity.json').exists()
    assert not (record / 'continuations').exists()
