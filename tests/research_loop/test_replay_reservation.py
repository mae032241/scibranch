"""Small clock/contracts checks; no native process, replay leaf or solver."""
import json
from pathlib import Path
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.operations.execution import AdapterCapability
from scidiscovery.research_loop import experiments as e, replay_check
from cov_execution import experiments as cov_experiments
from scidiscovery.research_loop.protocol import CURRENT


def configuration(tmp_path, reserve=12):
    return dict(record_dir=str(tmp_path / 'lineage'), run_argv=[sys.executable], max_seconds=65,
        engineering_check_reserve_seconds=reserve, receipt_channel='public_runner_main',
        admission=dict(version=1, mode='hard'),
        scope=dict(handoff_protocol=CURRENT, step=2, role='W', job_ref=dict(path=str(tmp_path/'jobs/002.json')),
                   execution_key=dict(path=str(tmp_path/'jobs/002.json')), mechanism_ref=None))


def test_operator_reserve_is_frozen_and_long_experiment_keeps_same_check_deadline(tmp_path, monkeypatch, execution_registry):
    clock = dict(boot_id='fixture-boot', at_boottime=100.)
    monkeypatch.setattr(replay_check.budget, 'clock_stamp', lambda:dict(clock))
    monkeypatch.setattr(cov_experiments.time, 'monotonic', lambda:500.)
    config = e.freeze(configuration(tmp_path), 'cov_execution:experiment')
    adapter = e.load(config)
    bound = replay_check.acceptance(config, adapter)
    assert config['engineering_check_reserve_seconds'] == 12
    assert bound['deadline_boottime'] == 165
    assert bound['time_allocation'] == dict(requested_seconds=12, status='reserved',
        note='experiment_and_check_share_original_deadline', experiment_deadline_boottime=153,
        check_deadline_boottime=163, check_seconds=10)
    accepted = dict(engineering_check=bound)
    assert adapter._execution_deadline(accepted) == 553
    clock['at_boottime'] = 150  # The original experiment used most of its allocated window.
    assert adapter._execution_deadline(accepted) == 503
    clock['at_boottime'] = 153
    assert replay_check._deadline(config, bound) == 163
    with pytest.raises(TimeoutError, match='no new time grant'):
        adapter._execution_deadline(accepted)
    clock['at_boottime'] = 166
    assert replay_check._deadline(config, bound) is None
    with pytest.raises(e.IntegrityError, match='configuration changed'):
        e.load(dict(config, engineering_check_reserve_seconds=15))


def test_zero_or_unavailable_reserve_does_not_invent_a_training_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_check.budget, 'clock_stamp', lambda:dict(boot_id='boot', at_boottime=100.))
    config = configuration(tmp_path, 0); adapter = cov_experiments.CovExecution(config)
    original = replay_check.acceptance(config, adapter)
    assert 'time_allocation' not in original
    assert adapter._execution_deadline(dict(engineering_check=original)) is None
    # Reopening an accepted pre-reservation call never shortens its original limit.
    selected = cov_experiments.CovExecution(dict(config, engineering_check_reserve_seconds=12))
    assert selected._execution_deadline(dict(engineering_check=original)) is None
    for reserve in (65, 2):
        current = dict(config, engineering_check_reserve_seconds=reserve)
        bound = replay_check.acceptance(current, cov_experiments.CovExecution(current))
        assert bound['time_allocation']['status'] == 'unavailable'
        assert 'experiment_deadline_boottime' not in bound['time_allocation']
        assert replay_check._deadline(current, bound) is None
        assert bound['deadline_boottime'] == 165


def test_nonpartitioning_optional_adapter_reports_unsupported_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_check.budget, 'clock_stamp', lambda:dict(boot_id='boot', at_boottime=100.))
    config = configuration(tmp_path)
    adapter = cov_experiments.CovExecution(config)
    original = next(item for item in adapter.capabilities() if item.key == replay_check.KEY)
    policy = json.loads(original.content); del policy['supports_time_reservation']
    legacy = AdapterCapability(original.key, original.kind, original.schema_id,
        original.payload_schema_version, original.media_type, e.encoded(policy), original.public_summary)
    monkeypatch.setattr(adapter, 'capabilities', lambda:(legacy,))
    bound = replay_check.acceptance(config, adapter)
    assert bound['time_allocation']['status'] == 'unsupported'
    assert adapter._execution_deadline(dict(engineering_check=bound)) is None
    assert replay_check._deadline(config, bound) == 110  # Explicit best effort, no added time.


def test_operator_reserve_rejects_boolean_negative_and_nonfinite_values(tmp_path):
    for reserve in (True, -1, float('inf'), float('nan')):
        with pytest.raises(ValueError, match='finite nonnegative'):
            cov_experiments.CovExecution(configuration(tmp_path, reserve))


@pytest.mark.parametrize('enforced', [False, True])
def test_soft_target_advice_keeps_original_replay_window(tmp_path, monkeypatch, enforced):
    monkeypatch.setattr(replay_check.budget, 'clock_stamp', lambda:dict(boot_id='boot', at_boottime=100.))
    config=configuration(tmp_path)
    review=tmp_path/'review';review.mkdir()
    (review/'review.json').write_text(json.dumps(dict(deadline_boottime=150.)))
    config['admission']=dict(version=1,mode='soft',directory=str(review))
    monkeypatch.setattr(e,'review_status',lambda config:dict(requested=True,enforced=enforced))
    bound=replay_check.acceptance(config,cov_experiments.CovExecution(config))
    if enforced:
        assert bound['time_allocation']['status']=='unavailable'
        assert replay_check._deadline(config,bound) is None
    else:
        assert bound['time_allocation']['status']=='reserved'
        assert bound['deadline_boottime']==165.
        assert bound['time_allocation']['experiment_deadline_boottime']==153.


@pytest.mark.parametrize('expired',[False,True])
def test_optional_check_upgrade_keeps_original_deadline_and_single_call(tmp_path,monkeypatch,execution_registry,expired):
    config=e.freeze(configuration(tmp_path),'cov_execution:experiment')
    adapter=e.load(config)
    bound=replay_check.acceptance(config,adapter)
    if expired:bound['deadline_boottime']=1.
    calls=[]
    def upgraded_check(self,call_id,*,deadline_boottime,on_process):
        calls.append((call_id,deadline_boottime))
        return dict(status='sample_replay_ok',cleanup_confirmed=True,note='Synthetic optional check only.')
    monkeypatch.setattr(cov_experiments.CovExecution,'replay_check',upgraded_check)
    adapter=e.load(config)
    assert e._public_capabilities(adapter)==config['execution_interface']['public_capabilities']
    directory=Path(config['record_dir'])/'calls/original';directory.mkdir(parents=True)
    accepted=dict(call_id='original',scope=config['scope'],parent=dict(candidate_id=None),engineering_check=bound)
    e.write(directory/'accepted.json',accepted);e.write(directory/'completed.json',dict(status='known_success'))
    observation=dict(status='known_success',accepted=e.proof(directory/'accepted.json'),completed=e.proof(directory/'completed.json'))
    originals={path:path.read_bytes() for path in (directory/'accepted.json',directory/'completed.json')}
    replay_check.complete(config,adapter,'original',observation)
    feedback=replay_check.public(config,'original')
    assert feedback['status']==('not_run' if expired else 'sample_replay_ok')
    intent=json.loads((directory/replay_check.INTENT).read_text())
    assert intent['check']['capability']['policy']['max_seconds']==10
    if not expired:
        assert len(calls)==1 and calls[0][0]=='original'
        assert calls[0][1]<=bound['time_allocation']['check_deadline_boottime']
        assert calls[0][1]-replay_check.time.clock_gettime(replay_check.time.CLOCK_BOOTTIME)<=10
    else:assert calls==[]
    replay_check.complete(config,adapter,'original',observation)
    assert len(calls)==(0 if expired else 1)
    assert all(path.read_bytes()==raw for path,raw in originals.items())
