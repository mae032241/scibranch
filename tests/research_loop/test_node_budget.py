"""All-role resource guards through real local transport, never real models."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, native, profiles, runtime, search
from scidiscovery.research_loop.cli import status
from scidiscovery.research_loop.protocol import CURRENT
from test_history_handoff import finish


ROLES = ('H', 'W', 'A', 'E')


def policy(tokens=1000, seconds=30):
    return {role: dict(max_observed_tokens=tokens, max_seconds=seconds) for role in ROLES}


FAKE_NATIVE = '''import json,os,pathlib,sys,time
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE']
task=json.loads((work/'science-task.json').read_text())
config=json.loads(pathlib.Path(sys.argv[0]).with_suffix('.json').read_text())
with (work/'work/launches.txt').open('a') as stream:stream.write('launch\\n')
payload={'summary':'A bounded finding','scientific_result':'Original fixture observation','limitations':['No scientific effectiveness claim']}
payload['implementation_selection']=None
if role=='H':payload.update(next_action='wait',route_reason='No further work required',search_plan=None)
if role=='W':
 job=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
 assigned=(job.get('search') or {}).get('coverage_obligation',[])
 payload.update(stage_intent='decision',consultation_question=None,mechanism_findings=[dict(mechanism=row['mechanism_ref'],basis='blocked',
  finding='This transport fixture performs no scientific experiment or managed observation.')
  for row in assigned] if assigned else None,
  candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
   reason='No scientific candidate was produced by this transport fixture.')
   for row in job['candidate_nomination_mechanisms'] if row['status']!='waived'])
   if job.get('candidate_nomination_mechanisms') else None)
if role=='A':payload.update(next_action='finish',route_reason='A bounded conclusion',candidate_recommendation=None,
 search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
mode=config['mode']
if mode!='no_usage':
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':config['input'],'cached_input_tokens':config['cached'],'output_tokens':config['output']}}),flush=True)
(work/'work/ready').touch()
if mode in ('time','tokens','no_usage'):time.sleep(20)
if mode=='hold':
 while not (work/'work/release').exists():time.sleep(.02)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
if role=='W':pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).parents[1].joinpath('pause.request').touch()
'''


def instance(tmp_path, *, limits=None, mode='normal', max_tokens=100_000,
             max_seconds=120, search_policy=None, observed=(7, 2, 3)):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Resolve the original bounded scientific question.')
    launcher = tmp_path / 'native.py'
    launcher.write_text(FAKE_NATIVE)
    c.write(launcher.with_suffix('.json'), dict(mode=mode, input=observed[0],
                                              cached=observed[1], output=observed[2]))
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
                       max_tokens=max_tokens, max_seconds=max_seconds, max_steps=6,
                       model='fixture-no-model', reasoning_effort='high', node_budget_mode='soft',
                       search_policy=search_policy, node_budget_policy=limits)
    return root


def target(root, role, *, with_search=False):
    if role == 'H':
        return 1
    action = {'W': 'experiment', 'A': 'analyze', 'E': 'extract'}[role]
    plan = (dict(scope='Discriminating mechanism', commitments=[dict(label='Mechanism',
                minimal_test='Bounded screen', existing_ref=None)],
                focus=dict(index=1, action='screen')) if with_search else None)
    finish(root, 1, 'H', [], action=action, search_choice=plan)
    return 2


@pytest.mark.parametrize('mutation', [
    'missing_role', 'extra_role', 'missing_limit', 'extra_limit', 'zero_tokens',
    'boolean_tokens', 'float_tokens', 'negative_seconds', 'boolean_seconds',
    'infinite_seconds', 'nan_seconds',
])
def test_strict_node_policy_has_no_silent_defaults(mutation):
    value = policy()
    if mutation == 'missing_role':
        value.pop('E')
    elif mutation == 'extra_role':
        value['Root'] = value['H'].copy()
    elif mutation == 'missing_limit':
        value['H'].pop('max_seconds')
    elif mutation == 'extra_limit':
        value['H']['retries'] = 3
    else:
        key, invalid = {
            'zero_tokens': ('max_observed_tokens', 0),
            'boolean_tokens': ('max_observed_tokens', True),
            'float_tokens': ('max_observed_tokens', 1.5),
            'negative_seconds': ('max_seconds', -1),
            'boolean_seconds': ('max_seconds', True),
            'infinite_seconds': ('max_seconds', float('inf')),
            'nan_seconds': ('max_seconds', float('nan')),
        }[mutation]
        value['H'][key] = invalid
    with pytest.raises(ValueError):
        budget.node_policy(value)


def test_valid_policy_is_explicit_and_does_not_mutate_caller():
    value = policy(tokens=123, seconds=2.5)
    before = copy.deepcopy(value)
    assert budget.node_policy(value) == before
    assert value == before


@pytest.mark.process_e2e
def test_cached_input_is_not_a_second_charge_in_actual_native_guard(tmp_path):
    root = instance(tmp_path, limits=policy(tokens=110), observed=(100, 99, 1))
    result = controller.run(root)
    assert result['status'] == 'paused' and result['completed_steps'] == 1, result
    record = c.host_record(c.workspace(root, 1))
    report = c.read(record / 'native-outcome.json')['budget_slice']
    assert report['observed_tokens'] == 101
    assert report['cached_input_tokens'] == 99
    assert report['usage_observed'] is True
    assert native.usage(root)['observed_tokens'] == 101
    assert c.validate(c.workspace(root, 1))['role'] == 'W'


def test_original_slice_excludes_explicit_pause_but_does_not_reset(tmp_path, monkeypatch):
    root = instance(tmp_path, limits=policy(seconds=10))
    now = [100.]
    monkeypatch.setattr(budget.time, 'time', lambda: now[0])
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: now[0])
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'first acceptance')
    job = c.build_job(root, 1, 'H', [])
    intent = dict(budget_slice=budget.accept_node(root, job))
    original = copy.deepcopy(intent)
    meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
    now[0] += 3
    budget.inactive(root, 'no activity during explicit pause')
    now[0] += 5000
    budget.transition(root, 'advance', 'resume original identity')
    now[0] += 2
    assert budget.node_report(root, job, intent, meter)['advance_seconds'] == 5
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None
    now[0] += 5
    assert budget.stop_node(root, job, intent, budget.snapshot(root), meter) is None
    assert budget.node_report(root, job, intent, meter)['advance_seconds'] == 10
    assert intent == original


@pytest.mark.parametrize('field', ['role', 'task_ref', 'limits'])
def test_changed_accepted_identity_cannot_refund_or_launch(tmp_path, field):
    root = instance(tmp_path, limits=policy())
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'original acceptance')
    job = c.build_job(root, 1, 'H', [])
    anchor = budget.accept_node(root, job)
    anchor[field] = dict(max_observed_tokens=9999, max_seconds=300) if field == 'limits' else 'forged'
    with pytest.raises(ValueError):
        budget.stop_node(root, job, dict(budget_slice=anchor), budget.snapshot(root),
                         dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))


@pytest.mark.parametrize('dimension', ['tokens', 'seconds'])
@pytest.mark.process_e2e
def test_controller_uses_actual_tokens_and_retains_finite_time_window(tmp_path, dimension):
    root = instance(tmp_path, limits=policy(tokens=100, seconds=10),
                    max_tokens=99 if dimension == 'tokens' else 1000,
                    max_seconds=9 if dimension == 'seconds' else 120)
    result = controller.run(root)
    record = c.host_record(c.workspace(root, 1))
    if dimension=='tokens':
        assert result['status']=='paused',result
        assert c.read(root/'jobs/001.json')['role']=='W'
        assert (record/'native-intent.json').exists()
        assert native.usage(root)['observed_tokens']==10
    else:
        assert result['status']=='paused',result
        assert c.read(root/'jobs/001.json')['role']=='W'
        assert not c.read(root/'jobs/001.json').get('final_only')
        assert native.usage(root)['observed_tokens']==10


def test_amend_rejects_original_n6_intent_without_terminal_outcome(tmp_path):
    root = instance(tmp_path, limits=policy())
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'original accepted node')
    job = c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'), budget_slice=budget.accept_node(root, job)))
    paths = [root / 'settings.json', record / 'native-intent.json',
             *sorted((root / 'control-ledger').glob('*.json'))]
    preserved = {path: path.read_bytes() for path in paths}
    with pytest.raises(RuntimeError, match='running or unknown'):
        budget.amend(root, max_tokens=200_000, authorization_note='External operator request')
    assert all(path.read_bytes() == raw for path, raw in preserved.items())
    assert len(list((root / 'control-ledger').glob('*.json'))) == len(paths) - 2
    assert not (record / 'native-outcome.json').exists()


@pytest.mark.process_e2e
def test_amend_after_success_preserves_old_slice_report_and_consumption(tmp_path):
    root = instance(tmp_path, limits=policy())
    assert controller.run(root)['status'] == 'paused'
    record = c.host_record(c.workspace(root, 1))
    paths = [root / 'settings.json', record / 'native-intent.json',
             record / 'native-outcome.json', record / 'usage.json', record / 'result-identity.json']
    preserved = {path: path.read_bytes() for path in paths}
    before = status(root)
    result = budget.amend(root, max_tokens=200_000, max_seconds=240,
                          authorization_note='External operator changes total, not consumed work')
    assert result['status'] == 'budget_amended'
    assert all(path.read_bytes() == raw for path, raw in preserved.items())
    after = status(root)
    assert after['nodes'][0]['budget_slice'] == before['nodes'][0]['budget_slice']
    assert after['nodes'][0]['budget_report'] == before['nodes'][0]['budget_report']
    assert after['usage'] == before['usage']
    assert after['usage']['observed_tokens'] == 10
    assert after['budget']['advance_seconds'] == before['budget']['advance_seconds']
    assert after['budget']['max_tokens'] == 200_000
    assert after['budget']['max_seconds'] == 240


def test_actual_popen_rechecks_late_budget_before_native_submission(tmp_path, monkeypatch):
    root = instance(tmp_path, limits=policy(tokens=100, seconds=10), max_seconds=60)
    now = [100.]
    monkeypatch.setattr(budget.time, 'time', lambda: now[0])
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: now[0])
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'accepted original node')
    job = c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    accepted_budget.accept_native(root, 1, record,
        dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'), budget_slice=budget.accept_node(root, job),
            **profiles.binding(c.settings(root, job), job)))
    now[0] = 160.  # The original intent is retained; no shared active time remains.
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Late budget must block native Popen'))
    native.run_native(root, 1)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is False
    assert budget.delivery_reserve(c.settings(root))==dict(tokens=0,seconds=0.,steps=0)
    assert outcome['stop_reason'] == 'wall_limit'
    assert not (record / 'native-process.json').exists()
    assert not (record / 'science-output.json').exists()


def _until(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail('Deterministic local process event did not arrive')


@pytest.mark.process_e2e
def test_controller_restart_rejoins_same_slice_and_counts_one_dispatch(tmp_path):
    root = instance(tmp_path, limits=policy(), mode='hold')
    work = c.workspace(root, 1)
    record = c.host_record(work)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    command = [sys.executable, '-m', 'scidiscovery.research_loop.cli', 'run', '--root', str(root)]
    fronts = []
    try:
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        fronts.append(first)
        _until(lambda: (work / 'work/ready').exists())
        before = (record / 'native-intent.json').read_bytes()
        owner = c.read(record / 'runner-process.json')
        first.kill()
        first.communicate(timeout=3)
        assert runtime.process_matches(owner, owner['task_ref'])
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        fronts.append(second)
        (work / 'work/release').touch()
        out, err = second.communicate(timeout=10)
        assert second.returncode == 0, out + err
        assert json.loads(out)['status'] == 'paused'
        assert (record / 'native-intent.json').read_bytes() == before
        assert (work / 'work/launches.txt').read_text() == 'launch\n'
        report = budget.node_report(root, c.read(root / 'jobs/001.json'), c.read(record / 'native-intent.json'),
                                    c.read(record / 'usage.json'))
        assert report['dispatches'] == 1 and report['observed_tokens'] == 10
        assert controller.run(root)['status'] == 'paused'
    finally:
        if (work / 'work').exists():
            (work / 'work/release').touch()
        for front in fronts:
            if front.poll() is None:
                front.communicate(timeout=12)


def test_budget_race_preserves_unaccepted_job_and_enters_durable_final(tmp_path, monkeypatch):
    root = instance(tmp_path, limits=policy(tokens=100, seconds=10), max_seconds=60)
    now = [100.]
    monkeypatch.setattr(budget.time, 'time', lambda: now[0])
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: now[0])
    original = c.build_job
    built = []
    def build(*args, **kwargs):
        job = original(*args, **kwargs)
        built.append(job)
        now[0] += 60  # Initial admission passed; no shared active time remains.
        return job
    monkeypatch.setattr(c, 'build_job', build)
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('No node was accepted'))
    final = []
    monkeypatch.setattr(controller, '_run_final',
        lambda root, begin, **facts: final.append(begin) or dict(status='final_report'))
    result = controller.run(root)
    assert result['status'] == 'final_report'
    assert len(built) == 1
    begin = budget.closeout_state(root)['begin']
    assert final == [begin] and begin['mode'] == 'final'
    assert begin['source_cutoff'] == 0 and begin['stop_reason'] == 'wall_limit'
    record = c.host_record(c.workspace(root, 1))
    assert not (record / 'native-intent.json').exists()
    assert not (record / 'native-process.json').exists()
    assert not (record / 'science-output.json').exists()
    prepared = (root / 'jobs/001.json').read_bytes()
    assert budget.protected_final_report(c.settings(root))
    assert c.read(root / 'jobs/001.json')['role'] == 'W'
    assert controller.run(root)['status'] == 'final_report'
    assert final == [begin, begin]
    assert len(built) == 1 and (root / 'jobs/001.json').read_bytes() == prepared
    assert not (record / 'native-intent.json').exists()
    assert not (record / 'native-process.json').exists()
    assert not (record / 'science-output.json').exists()
    # FINAL routing is durable, but the stub has accepted no native A return.
    assert budget.closeout_state(root)['begin'] == begin
    assert budget.final_a_barrier(root) is None


def test_admission_reuses_one_local_observation_and_next_boundary_reads_mutations(tmp_path, monkeypatch):
    root = instance(tmp_path, limits=policy(tokens=1000), max_tokens=100)
    job = c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    accepted_budget.accept_native(root, 1, record, dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    reads = []
    original = budget._events
    def observed(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(budget, '_events', observed)
    # Missing billing coverage leaves an observed lower bound; it is not an
    # execution-completion fact and cannot turn into a guessed token charge.
    assert budget.node_admission(root, job) is None
    assert len(reads) == 1
    c.write(record / 'usage.json', dict(input_tokens=90, cached_input_tokens=80, output_tokens=10))
    reads.clear()
    assert budget.node_admission(root, job) == 'node_budget_unavailable_tokens'
    assert len(reads) == 1
    # Accounting availability does not reconcile the original execution.
    assert budget.execution_blocked(root)
    with pytest.raises(RuntimeError, match='no running or unknown original execution'):
        budget._amend_locked(root, max_tokens=101, authorization_note='Unreconciled original must block')
    # A completed transport may still have incomplete billing coverage. This
    # original has no process/managed call; its terminal fact releases only the
    # execution barrier, while the 100 observed tokens remain consumed.
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_backend='codex', cleanup='observed_group_cleaned',
        metering=dict(source='turn.completed', coverage='unknown', reason='billing_unreported')))
    outcome = c.read(record / 'native-outcome.json')
    assert budget.native_completion_known(outcome, c.settings(root))
    accepted_budget.accept_terminal(root, 1, record,
        meter=c.read(record / 'usage.json'), unknown=True, metering=outcome['metering'])
    assert not budget.execution_blocked(root)
    assert native.usage(root)['unknown_steps'] == [1]
    budget._amend_locked(root, max_tokens=101, authorization_note='One more reported token')
    reads.clear()
    assert budget.node_admission(root, job) is None
    assert len(reads) == 1
    original_meter = (record / 'usage.json').read_bytes()
    with pytest.raises(ValueError, match='Closed native accounting changed'):
        accepted_budget.accept_terminal(root, 1, record,
            meter=dict(input_tokens=91, cached_input_tokens=80, output_tokens=10), unknown=True)
    assert (record / 'usage.json').read_bytes() == original_meter
    budget._amend_locked(root, max_tokens=100, authorization_note='Restore the explicit consumed token ceiling')
    assert budget.node_admission(root, job) == 'node_budget_unavailable_tokens'
