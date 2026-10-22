"""Early optional engineering feedback through real managed calls and isolated leaves.

Written for later supervised execution; no scientific models, private data or full
training. The fixture trains a constant predictor through the public runner shape.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import experiments as e, replay_check
from cov_execution import experiments as cov_experiments, lineage as cov_lineage
from scidiscovery.research_loop.protocol import CURRENT
from test_cov_receipt import CLI

pytestmark = pytest.mark.process_e2e

RUNNER = '''import importlib.util,json,os,sys
from pathlib import Path
import cloudpickle,pandas as pd
RUN_DIR=Path(os.environ['EXP_RUN_DIR']);RESULTS_LOG=RUN_DIR/'results.jsonl'
def split_X_y(frame,meta):
    return frame[['x']],frame['y'].values,['x']
def main():
    script=Path(sys.argv[1]).resolve();RUN_DIR.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('original_candidate',script)
    source=importlib.util.module_from_spec(spec);sys.modules[spec.name]=source;spec.loader.exec_module(source)
    train=pd.read_csv(os.environ['EXP_TRAIN']);val=pd.read_csv(os.environ['EXP_VAL'])
    model=source.train(train,val)
    rows=RESULTS_LOG.read_text().splitlines() if RESULTS_LOG.exists() else []
    n=len(rows)+1;path=RUN_DIR/f'model_run{n}.pkl'
    with path.open('wb') as stream:cloudpickle.dump(model,stream)
    rec=dict(run_id=n,script=str(script),ok=True,model_path=str(path),val_score=.5,val_acc=.5,wall_seconds=.01)
    with open(RESULTS_LOG, 'a') as f:
        f.write(json.dumps(rec) + '\\n')
    print(json.dumps(rec, indent=2))
    sys.exit(0 if rec.get('ok') else 1)
if __name__=='__main__':main()
'''
SOURCE = '''import numpy as np
from pathlib import Path
class Predictor:
    def predict(self,frame):return np.zeros(len(frame))
def train(train_df,val_df):
    if __file__.startswith('/inputs/'):
        assert not Path('/target').exists()
        assert not Path(CONTROL).exists()
        assert not Path(HOME).exists()
    REQUIREMENT
    print('ordinary training log')
    return Predictor()
'''


@pytest.fixture
def replay_scene(tmp_path, monkeypatch, execution_registry):
    if shutil.which('bwrap') is None:
        pytest.skip('Actual isolated replay requires qualified bwrap')
    harness = tmp_path / 'public-harness'
    harness.mkdir()
    (harness / 'exp_cli.py').write_text(CLI)
    (harness / 'runner.py').write_text(RUNNER)
    (harness / 'meta.json').write_text('{}')
    for name in ('train.csv', 'val.csv'):
        (harness / name).write_text('x,y\n' + ''.join(f'{n},0\n' for n in range(64)))
    # Present in the original task runner's import context, never a declared
    # replay input. A saved model itself does not require this train-only helper.
    (harness / 'fixture_helper.py').write_text('def train_dependency():return None\n')
    runs = tmp_path / 'runs'
    for key, value in dict(EXP_RUN_DIR=runs, EXP_RUNNER=harness / 'runner.py',
            EXP_TRAIN=harness / 'train.csv', EXP_VAL=harness / 'val.csv',
            EXP_META=harness / 'meta.json').items():
        monkeypatch.setenv(key, str(value))
    control = tmp_path / 'control-private'
    control.write_text('never mounted')
    home = Path.home() / '.codex'
    config_path = tmp_path / 'config.json'
    config = e.freeze(dict(contract_version=2, feedback_version=1, receipt_channel='public_runner_main',
        record_dir=str(tmp_path / 'work/lineage'), runs_root=str(runs), max_seconds=65,
        run_argv=[sys.executable, '-I', str(harness / 'exp_cli.py')], parents=[], evaluation_key='fixture-v1',
        admission=dict(version=1, mode='hard'), scope=dict(handoff_protocol=CURRENT, step=2, role='W',
            job_sha256='a' * 64, execution_key='b' * 64, mechanism_ref=None)),
        'cov_execution:experiment', path=config_path)
    config_path.write_text(json.dumps(config))

    def source(requirement='pass'):
        path = tmp_path / 'candidate.py'
        path.write_text(SOURCE.replace('CONTROL', repr(str(control))).replace('HOME', repr(str(home)))
                        .replace('REQUIREMENT', requirement))
        return path

    return config, config_path, source, harness


def test_train_only_dependency_is_reported_before_w_returns_and_can_be_repaired(replay_scene):
    config, _, source, _ = replay_scene
    config = e.freeze(dict(config, engineering_check_reserve_seconds=12), 'cov_execution:experiment')
    path = source('from fixture_helper import train_dependency; train_dependency()')
    original = e.dispatch(config, path, new=True)
    assert original['status'] == 'known_success'
    directory = e.Records(config).directory(original['call_id'])
    completed = (directory / 'completed.json').read_bytes()
    bound = e.Records(config).accepted(original['call_id'])['engineering_check']
    assert bound['time_allocation']['status'] == 'reserved'
    assert bound['time_allocation']['experiment_deadline_boottime'] == bound['deadline_boottime'] - 12
    assert bound['time_allocation']['check_deadline_boottime'] == bound['deadline_boottime'] - 2
    feedback = cov_lineage.public_feedback(config, original)['engineering_check']
    assert feedback['status'] == 'dependency_error'
    assert feedback['stage'] == 'sample_train' and 'fixture_helper' in feedback['diagnostic']
    assert feedback['samples']['train_rows'] == 32 and feedback['samples']['validation_rows'] == 8
    assert feedback['check_scope']['selection'] == 'first rows in frozen CSV order'
    assert 'no scientific qualification' in feedback['check_scope']['limitation']
    assert not (Path(config['runs_root']) / 'submitted.json').exists()
    assert (directory / 'engineering-replay/scratch/replay.stdout').is_file()
    repaired = e.dispatch(config, source(), parent=original['candidate_id'], change='Inline the train-only helper')
    assert repaired['status'] == 'known_success' and repaired['candidate_id'] != original['candidate_id']
    assert replay_check.public(config, repaired['call_id'])['status'] == 'sample_replay_ok'
    assert (directory / 'completed.json').read_bytes() == completed
    assert cov_lineage.public_feedback(config, original)['val_score'] == .5


def test_small_sample_failure_is_inconclusive_and_successful_execution_keeps_its_cli_code(replay_scene, capsys):
    config, config_path, source, _ = replay_scene
    path = source("assert len(train_df) >= 64, 'algorithm needs its full fixed sample count'")
    assert e.main(['--config', str(config_path), 'run', '--script', str(path), '--new-plan']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'known_success' and result['val_score'] == .5
    assert result['engineering_check']['status'] == 'inconclusive'
    assert 'full fixed sample count' in result['engineering_check']['diagnostic']
    assert result['engineering_check']['cleanup_confirmed'] is True


def test_early_check_reuses_accepted_bytes_and_never_repeats_uncertain_check(replay_scene, monkeypatch):
    config, _, source, harness = replay_scene
    path = source()
    frozen = path.read_bytes()
    original_check = cov_experiments.CovExecution.replay_check

    def check_accepted(self, call_id, **options):
        path.write_text('raise RuntimeError("changed current source")\n')
        (harness / 'val.csv').write_text('unrelated current data\n')
        return original_check(self, call_id, **options)

    monkeypatch.setattr(cov_experiments.CovExecution, 'replay_check', check_accepted)
    result = e.dispatch(config, path, new=True)
    directory = e.Records(config).directory(result['call_id'])
    assert replay_check.public(config, result['call_id'])['status'] == 'sample_replay_ok'
    assert (directory / 'source.py').read_bytes() == frozen
    assert (directory / 'inputs/replay/harness/val.csv').read_text().startswith('x,y\n')
    # Simulate a crash after accepting the diagnostic but before publishing its
    # outcome. The original experiment and check process receipts remain exact.
    (directory / replay_check.OUTCOME).unlink()
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    adapter = e.load(config)
    replay_check.complete(config, adapter, result['call_id'], json.loads(e.read(directory / 'execution-observation.json')))
    assert replay_check.public(config, result['call_id'])['status'] == 'unknown'
    assert before == {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def test_optional_check_exception_keeps_original_success_and_local_remains_unsupported(replay_scene, monkeypatch, capsys):
    config, config_path, source, _ = replay_scene

    def broken_check(*args, **kwargs):
        raise RuntimeError('isolated launcher diagnostic failure')

    monkeypatch.setattr(cov_experiments.CovExecution, 'replay_check', broken_check)
    assert e.main(['--config', str(config_path), 'run', '--script', str(source()), '--new-plan']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'known_success' and result['engineering_check']['status'] == 'unknown'
    assert result['engineering_check']['cleanup_confirmed'] is False
    # Original acceptance and the diagnostic share a deadline. Advancing that
    # clock past it must skip the optional method, even though the experiment
    # itself just completed successfully; no fresh 10s grant is created.
    stamp = replay_check.budget.clock_stamp()
    times = iter((stamp, dict(stamp, at_boottime=stamp['at_boottime'] + config['max_seconds'] + 1)))
    monkeypatch.setattr(replay_check.budget, 'clock_stamp', lambda: next(times))
    exhausted = e.dispatch(config, source(), new=True)
    assert exhausted['status'] == 'known_success'
    assert replay_check.public(config, exhausted['call_id'])['status'] == 'not_run'
    local = e.freeze(dict(record_dir=str(Path(config['record_dir']).parent / 'ordinary'),
        run_argv=[sys.executable], max_seconds=5, admission=config['admission'], scope=config['scope']),
        'local_execution:command')
    path = Path(config_path).parent / 'ordinary.py'
    path.write_text('print("ordinary local command")\n')
    original = e.dispatch(local, path)
    assert original['status'] == 'known_success'
    assert replay_check.public(local, original['call_id'])['status'] == 'unsupported'


def test_preparation_uses_the_original_deadline_and_never_starts_a_fresh_leaf(replay_scene, monkeypatch):
    config, _, source, _ = replay_scene
    actual_link = cov_experiments.os.link
    actual_clock = cov_experiments.time.clock_gettime
    preparation_expired = False

    def slow_link(*args, **kwargs):
        nonlocal preparation_expired
        actual_link(*args, **kwargs)
        preparation_expired = True

    def clock(which):
        return actual_clock(which) + (100 if preparation_expired else 0)

    def no_new_launch(*args, **kwargs):
        pytest.fail('Preparation consumed the original deadline; no fresh execution grant is allowed')

    from scidiscovery.research_loop import delivery
    monkeypatch.setattr(cov_experiments.os, 'link', slow_link)
    monkeypatch.setattr(cov_experiments.time, 'clock_gettime', clock)
    monkeypatch.setattr(delivery, '_call', no_new_launch)
    original = e.dispatch(config, source(), new=True)
    assert original['status'] == 'known_success'
    feedback = replay_check.public(config, original['call_id'])
    assert feedback['status'] == 'not_run'
    assert feedback['note'] == 'original_engineering_preparation_deadline_reached'


def test_reserved_window_expires_after_acceptance_without_launch_or_repeated_submit(replay_scene, monkeypatch):
    from scidiscovery.research_loop import local_experiments
    config, _, source, _ = replay_scene
    config = e.freeze(dict(config, engineering_check_reserve_seconds=12), 'cov_execution:experiment')
    original_deadline = cov_experiments.CovExecution._execution_deadline

    def expired_after_acceptance(self, accepted):
        stamp = replay_check.budget.clock_stamp()
        cutoff = accepted['engineering_check']['time_allocation']['experiment_deadline_boottime']
        monkeypatch.setattr(replay_check.budget, 'clock_stamp', lambda:dict(stamp, at_boottime=cutoff+1))
        return original_deadline(self, accepted)

    def no_launch(*args, **kwargs):
        pytest.fail('Expired accepted call must not start or repeat its original process')

    monkeypatch.setattr(cov_experiments.CovExecution, '_execution_deadline', expired_after_acceptance)
    monkeypatch.setattr(local_experiments.subprocess, 'Popen', no_launch)
    with pytest.raises(TimeoutError, match='no new time grant'):
        e.dispatch(config, source(), new=True)
    records = e.Records(config); directories = records.calls()
    assert len(directories) == 1 and not (directories[0]/'process.json').exists()
    assert not (directories[0]/'terminal.json').exists() and not (directories[0]/'completed.json').exists()
    call = directories[0].name; accepted = records.accepted(call)
    adapter = e.load(config)
    descriptor = e.LocalFileDescriptor.model_validate(accepted['prepared'])
    assert adapter.submit(descriptor) == (call, 'unknown')
    assert adapter.submit(descriptor) == (call, 'unknown')
    assert adapter.status_details(call)['state'] == 'unknown'
    assert replay_check.public(config, call)['status'] == 'not_run'
    assert not (directories[0]/replay_check.INTENT).exists()
