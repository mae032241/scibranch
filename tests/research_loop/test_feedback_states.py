"""Read-only producer/consumer contracts; injected packets grant no execution."""
import copy
import sys
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, contracts as c,
    controller, execution, experiments, feedback, history)
from cov_execution import lineage as cov_lineage
from execution_fixtures import execution_registry


LEGACY = {'accepted', 'running', 'known_success', 'known_failed', 'unknown'}
REGISTERED = LEGACY | {'submitted', 'queued', 'cancelling', 'not_submitted'}


def row(status='known_success', **extra):
    if status == 'not_submitted':
        return dict(call_id='original-call', status=status, candidate_id=None, **extra)
    return dict(call_id='original-call', status=status, candidate_id=None, evaluation_key='fixture-public',
        metric=.5, direction='max', wall_seconds=1., parent_candidate=None, **extra)


def packet(rows, *, registered=True):
    counts = dict.fromkeys(REGISTERED if registered else LEGACY, 0)
    for item in rows:
        counts[item['status']] += 1
    return dict(version=1, calls=rows, counts=counts, omitted_count=0,
        coverage='partial' if counts['unknown'] else 'complete', observed_at=123.)


@pytest.fixture
def consumer(tmp_path, monkeypatch, execution_registry):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'goal';objective.write_text('Observe progress without granting scientific qualification.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=1000, max_seconds=60,
        max_steps=4, model='fixture', reasoning_effort='high', search_policy=None, node_budget_mode='soft',
        history_observer=dict(adapter=cov_lineage.__file__, contract_version=2,
            config=dict(runs='runs', calls='project/nodes', evaluation_key='fixture-public')))
    job = c.build_job(root, 1, 'H', [])
    record = execution.record(root, 1)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'))
    anchor=budget.accept_node(root,job)
    if anchor is not None:intent['budget_slice']=anchor
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,1,record,intent)
    experiments.configure(root, 1, 'local_execution:command', dict(run_argv=[sys.executable]), tmp_path / 'config.json')

    def observe(value, *, registered=True):
        # The legacy branch is a read-only consumer projection: no frozen job is
        # rewritten, no old write protocol is revived and no role is dispatched.
        observer_job = dict(job)
        if not registered:
            observer_job.pop('experiment_interface_version')
        original = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
        monkeypatch.setattr(experiments, 'progress', lambda config: copy.deepcopy(value))
        monkeypatch.setattr(history, 'read_adapter', lambda config, request: dict(copy.deepcopy(value), scope=request['scope']))
        result = feedback.live(root, observer_job)
        assert original == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
        return result

    return SimpleNamespace(observe=observe, root=root, job=job, record=record)


@pytest.mark.parametrize('state', sorted(REGISTERED))
def test_registered_progress_preserves_every_producer_state(consumer, state):
    value = packet([row(state)])
    actual = consumer.observe(value)
    assert actual['status'] == 'observed' and actual['calls'] == value['calls']
    assert actual['counts'] == value['counts'] and actual['coverage'] == value['coverage']
    assert actual['budget']['inflight_usage_unknown'] is True


@pytest.mark.parametrize('state', sorted(LEGACY))
def test_legacy_observer_keeps_its_exact_five_state_contract(consumer, state):
    value = packet([row(state)], registered=False)
    actual = consumer.observe(value, registered=False)
    assert actual['status'] == 'observed' and actual['calls'] == value['calls']
    assert actual['counts'] == value['counts']


@pytest.mark.parametrize('registered', [False, True])
def test_counts_from_the_other_contract_are_unavailable_not_zero_progress(consumer, registered):
    value = packet([], registered=not registered)
    actual = consumer.observe(value, registered=registered)
    assert actual['status'] == 'unavailable' and actual['counts'] is None
    assert actual['calls'] == [] and actual['coverage'] == 'unavailable'


@pytest.mark.parametrize('with_result', [False, True])
def test_registered_cancelled_failure_keeps_native_state_and_result(consumer, with_result):
    extra = dict(native_state='cancelled')
    if with_result:
        extra.update(returncode=None, result=dict(status='unavailable'))
    value = packet([row('known_failed', **extra)])
    actual = consumer.observe(value)
    assert actual['status'] == 'observed' and actual['calls'] == value['calls']
    assert actual['counts']['known_failed'] == 1 and actual['counts']['known_success'] == 0


@pytest.mark.parametrize('fault', ['extra_field', 'missing_metric', 'candidate_too_long', 'nonfinite_metric',
    'bad_direction', 'unpaired_result', 'boolean_returncode', 'invalid_result', 'unknown_native_state',
    'cancelled_success', 'retirement_candidate', 'expanded_retirement', 'duplicate_id', 'empty_id',
    'bad_count_sum', 'boolean_count', 'too_many_calls', 'oversized_result'])
def test_registered_progress_retains_strict_row_and_bound_checks(consumer, fault):
    value = packet([row()]);item = value['calls'][0]
    if fault == 'extra_field':item['private'] = 'not a producer field'
    elif fault == 'missing_metric':item.pop('metric')
    elif fault == 'candidate_too_long':item['candidate_id'] = 'x' * 81
    elif fault == 'nonfinite_metric':item['metric'] = float('nan')
    elif fault == 'bad_direction':item['direction'] = 'sideways'
    elif fault == 'unpaired_result':item['result'] = {}
    elif fault == 'boolean_returncode':item.update(returncode=True, result={})
    elif fault == 'invalid_result':item.update(returncode=0, result=[])
    elif fault == 'unknown_native_state':item.update(native_state='terminated')
    elif fault == 'cancelled_success':item.update(native_state='cancelled')
    elif fault == 'retirement_candidate':
        value = packet([row('not_submitted')]);value['calls'][0]['candidate_id'] = 'invented'
    elif fault == 'expanded_retirement':
        value = packet([row('not_submitted', metric=None)])
    elif fault == 'duplicate_id':
        value['calls'].append(dict(item));value['counts']['known_success'] = 2
    elif fault == 'empty_id':item['call_id'] = ''
    elif fault == 'bad_count_sum':value['counts']['known_success'] = 2
    elif fault == 'boolean_count':value['counts']['known_success'] = True
    elif fault == 'too_many_calls':
        value = packet([dict(row(), call_id=f'call-{i}') for i in range(feedback.MAX_CALLS + 1)])
    elif fault == 'oversized_result':item.update(returncode=0, result=dict(message='x' * (feedback.MAX_BYTES + 1)))
    actual = consumer.observe(value)
    assert actual['status'] == 'unavailable' and actual['counts'] is None
    assert actual['budget']['inflight_usage_unknown'] is True


@pytest.mark.parametrize('extra', [dict(native_state='cancelled'), dict(returncode=0, result={})])
def test_legacy_observer_cannot_borrow_registered_row_extensions(consumer, extra):
    value = packet([row('known_failed', **extra)], registered=False)
    assert consumer.observe(value, registered=False)['status'] == 'unavailable'


def test_registered_scope_mismatch_remains_typed_integrity_failure(consumer, monkeypatch):
    path = consumer.record / 'experiment-config.json'
    value = c.read(path);value['scope']['job_ref'] = '0' * 64
    c.write(path, value)
    def forbidden(config):
        pytest.fail('A changed bound scope cannot reach the producer')
    monkeypatch.setattr(experiments, 'progress', forbidden)
    with pytest.raises(history.ObserverIntegrityError, match='escaped its frozen task'):
        feedback.live(consumer.root, consumer.job)
