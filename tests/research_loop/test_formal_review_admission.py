"""Common admission boundaries, real local/cov CLI, no native models."""
import json
from pathlib import Path
import sys

import pytest

from execution_fixtures import execution_registry
from test_registered_experiments import managed
from test_soft_node_budget import scene, start_clock
from test_history_handoff import finish
from scidiscovery.research_loop import accepted_budget, budget, contracts as c, execution, experiments, native


def soft(managed, *, max_seconds=None):
    value=json.loads(managed.config.read_text());value.pop('execution_interface')
    if max_seconds is not None:value['max_seconds']=max_seconds
    directory=managed.root/'host-review';directory.mkdir()
    (directory/'accept.lock').touch(mode=0o444)
    value['admission']=dict(version=1,mode='soft',directory=str(directory),stage_anchor=value['scope']['job_ref'])
    value['native_attempt']=dict(number=0,execution_key=value['scope']['execution_key'])
    value=experiments.freeze(value,managed.executor,path=managed.config)
    managed.config.write_text(json.dumps(value))
    return value


def publish(config, *, pending=False, reason='reserve_reached', malformed=False):
    """Controlled host projection fixture; no budget or scientific authorization claim."""
    stamp=budget.clock_stamp()
    value=dict(version=1,scope=config['scope'],stage_anchor=config['admission']['stage_anchor'],
        native_attempt=config['native_attempt'],boot_id=stamp['boot_id'],
        deadline_boottime=stamp['at_boottime']+60,valid_until_boottime=stamp['at_boottime']+60,
        pending=pending,reason=reason if pending else None,inflight_usage_unknown=True)
    if malformed:value.pop('native_attempt')
    c.write(Path(config['admission']['directory'])/'review.json',value)


def assert_no_accept(managed):
    assert not list(managed.records.glob('calls/*/accepted.json'))
    assert not list(managed.records.glob('calls/*/process.json'))
    assert not list(managed.records.glob('.prepared-*'))


@pytest.mark.process_e2e
def test_pending_new_accept_has_no_formal_call_or_external_effect(managed):
    config=soft(managed)
    with experiments.Records(config).lease('accept.lock'):publish(config,pending=True)
    result=managed.cli('run','--script',managed.source(),expected=2)
    assert result['status']=='budget_exhausted' and result['accepted'] is False
    assert_no_accept(managed)


@pytest.mark.process_e2e
def test_soft_target_cannot_mask_expired_original_shared_window(managed):
    config=soft(managed);publish(config,pending=True,reason='target_tokens_reached')
    path=Path(config['admission']['directory'])/'review.json';value=c.read(path)
    value['deadline_boottime']=budget.clock_stamp()['at_boottime']-.001;c.write(path,value)
    result=managed.cli('run','--script',managed.source(),expected=2)
    assert result['status']=='budget_exhausted' and result['accepted'] is False
    assert experiments.review_status(config)['enforced'] is True
    assert_no_accept(managed)


@pytest.mark.process_e2e
@pytest.mark.parametrize('observation', ['missing', 'expired'])
def test_soft_budget_observation_delay_does_not_deny_new_managed_call(managed, observation):
    config=soft(managed);publish(config)
    path=Path(config['admission']['directory'])/'review.json'
    if observation=='missing':path.unlink()
    else:
        value=c.read(path);value['valid_until_boottime']=budget.clock_stamp()['at_boottime']-.001;c.write(path,value)
    review=experiments.review_status(config)
    assert review['requested'] is True and review['enforced'] is False
    result=managed.cli('run','--script',managed.source(),'--new-plan')
    assert result['status']=='known_success'


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['soft', 'hard'])
def test_managed_call_keeps_finite_execution_limit_after_soft_target(managed, mode):
    if mode=='soft':
        config=soft(managed,max_seconds=.1)
        publish(config,pending=True,reason='target_tokens_reached')
    else:
        config=json.loads(managed.config.read_text());config.pop('execution_interface')
        config['max_seconds']=.1
        managed.config.write_text(json.dumps(experiments.freeze(config,managed.executor,path=managed.config)))
    result=managed.cli('run','--script',managed.source(slow=True),'--new-plan',expected=2)
    assert result['status']=='unknown' and result['returncode']!=0
    terminal=c.read(managed.records/'calls'/result['call_id']/'terminal.json')
    assert terminal['cleanup_confirmed'] is True


@pytest.mark.process_e2e
def test_unavailable_contract_never_grants_new_accept(managed):
    value=json.loads(managed.config.read_text());value.pop('execution_interface')
    value['admission']=dict(version=1,mode='unavailable')
    managed.config.write_text(json.dumps(experiments.freeze(value,managed.executor,path=managed.config)))
    result=managed.cli('run','--script',managed.source(),expected=2)
    assert result['status']=='unavailable'
    assert_no_accept(managed)


@pytest.mark.process_e2e
def test_bad_current_feedback_preserves_original_collect_and_rejects_new_accept(managed):
    config=soft(managed);publish(config)
    result=managed.cli('run','--script',managed.source())
    publish(config,malformed=True)
    collected=managed.cli('collect','--call-id',result['call_id'])
    assert collected['status']=='collected' and collected['outputs']
    assert collected['feedback']['review']['requested'] is None
    assert managed.cli('query','--call-id',result['call_id'])['state']=='succeeded'
    refused=managed.cli('run','--script',managed.source('new'),expected=3)
    assert refused['status']=='integrity_error'
    assert len(list(managed.records.glob('calls/*/accepted.json')))==1


def original_stage(tmp_path):
    root=scene(tmp_path,tokens=2,seconds=20,total_seconds=60,resume=True,search_policy=None)
    job=c.build_job(root,1,'H',[]);start_clock(root,'Original no-model stage')
    record=execution.record(root,1)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=budget.accept_node(root,job))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,1,record,intent)
    return root,job,record,intent


def test_restart_and_same_stage_attempt_reconstruct_cumulative_review_without_reset(tmp_path,execution_registry):
    root,job,first,intent=original_stage(tmp_path)
    config=root/'registered.json'
    initial=experiments.configure(root,1,'local_execution:command',dict(run_argv=[sys.executable]),config)
    pointer_bytes=config.read_bytes()
    with pytest.raises(experiments.IntegrityError,match='Frozen experiment configuration changed'):
        experiments.configure(root,1,'local_execution:command',dict(run_argv=[sys.executable],max_seconds=12),config)
    assert config.read_bytes()==pointer_bytes
    assert c.read(first/'experiment-config.json')==initial
    meter=dict(input_tokens=2,cached_input_tokens=0,output_tokens=0)
    c.write(first/'usage.json',meter)
    report=dict(budget.node_report(root,job,intent,meter),usage_observed=True)
    c.write(first/'native-outcome.json',dict(returncode=1,budget_slice=report))
    accepted_budget.accept_terminal(root,1,first,meter=meter,unknown=False)
    anchor=budget.accept_continuation(root,job,[first])
    next_record=first/'continuations/001'
    next_intent=dict(attempt=1,task_ref=intent['task_ref'],previous=execution.proof(first),
        root_intent_ref=c.record_ref(first/'native-intent.json'),budget_slice=anchor)
    c.write(next_record/'native-intent.json',next_intent)
    accepted_budget.accept_native(root,1,next_record,next_intent)
    current=experiments.configure(root,1,'local_execution:command',dict(run_argv=[sys.executable]),config)
    assert current['scope']==initial['scope'] and current['admission']==initial['admission']
    assert current['native_attempt']['number']==1
    assert c.read(first/'experiment-config.json')==initial
    projection=Path(current['admission']['directory'])/'review.json'
    assert c.read(projection)['observed_tokens_lower_bound']==2
    assert experiments.review_status(current)['reason']=='target_tokens_reached'
    projection.unlink()  # Replaceable cache loss is not a new allocation.
    budget.review_configuration(root,job)
    assert c.read(projection)['observed_tokens_lower_bound']==2
    assert experiments.review_status(current)['requested'] is True
    assert experiments.review_feedback(initial)['requested'] is None


def test_review_publication_observes_new_meter_without_resetting_original_facts(tmp_path):
    root=scene(tmp_path,tokens=2,seconds=20,total_seconds=60,search_policy=None)
    finish(root,1,'H',[],action='experiment')
    start_clock(root,'Current W with a sealed original H')
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    intent=dict(task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=budget.accept_node(root,job))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,2,record,intent)
    admission=budget.review_configuration(root,job)
    projection=Path(admission['directory'])/'review.json'
    value=c.read(projection)
    assert 2 in native.usage(root)['unknown_steps']  # The open W has no meter yet.
    assert value['reason'] is None and value['pending'] is False
    assert value['observed_tokens_lower_bound']==0 and value['inflight_usage_unknown'] is True
    c.write(record/'usage.json',dict(input_tokens=2,cached_input_tokens=0,output_tokens=0))
    budget.review_configuration(root,job)
    value=c.read(projection)
    assert value['reason'] is None and value['pending'] is False
    assert value['observed_tokens_lower_bound']==2
    assert value['inflight_usage_unknown'] is True
