"""Billing coverage alone grants no replay and removes no execution completion."""
import sys
from pathlib import Path

import pytest

from scidiscovery.research_loop import (budget, checkpoint, contracts as c, continuation,
    controller, execution, execution_authority as authority, experiments, managed_wait, native, runtime, search, worker_delivery)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_composed_execution_results import composed_registry, case


@pytest.fixture
def completed_without_meter(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Read retained evidence.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable],max_tokens=1000,max_seconds=120,max_steps=8,
        model='no-model',reasoning_effort='high',search_policy=None,node_budget_mode='soft',
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=30) for role in 'HWAE'},
        worker_fragment_policy=dict(context='new_context'),
        resume_policy=dict(max_attempts=1,protocol='codex_exec_resume',usage_scope='turn',output_repair=True))
    job=c.build_job(root,1,'W',[]);record=execution.origin(root,1)
    anchor=budget.accept_node(root,job)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor)
    c.write(record/'native-intent.json',intent)
    binding=execution.identity(root,1)
    for name in ('runner-process.json','native-process.json'):
        c.write(record/name,dict(task_ref=binding,boot_id=budget.clock_stamp()['boot_id'],pid=1,start_ticks=1,pgid=1,sid=1,ppid=1))
    outcome=dict(returncode=0,stop_reason=None,cleanup='observed_group_cleaned',
        metering=dict(source='turn.completed',coverage='unknown',reason='terminal_usage_unreported'),
        usage=dict(unknown_steps=[1],blocking_unknown_steps=[]),
        execution_processes={name:c.record_ref(record/name) for name in ('runner-process.json','native-process.json')},
        budget_slice=dict(role='W',advance_seconds=1.,input_tokens=0,cached_input_tokens=0,output_tokens=0,
            observed_tokens=0,dispatches=1,limits=anchor['limits'],usage_observed=False),output_status='missing',raw_ref=None)
    c.write(record/'native-outcome.json',outcome)
    return root,job,record


def test_missing_billing_preserves_absence_and_allows_original_output_repair(completed_without_meter,monkeypatch):
    root,job,record=completed_without_meter
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    continuation._eligible_original(root,1,[record],successful=True)
    assert budget._attempt_consumption(root,job,[record])==(0,1.)
    request=continuation.output_request(root,1,record,source_attempt=0,verify_task=False)
    assert request['code']=='missing_output'
    assert 'usage.json' not in execution.proof(record)
    saved=checkpoint.capture(root,job,record)
    assert 'usage.json' in saved['missing_native_originals']
    assert not (record/'usage.json').exists()


@pytest.mark.parametrize('fault',['cleanup','terminal'])
def test_missing_billing_never_replaces_execution_completion(completed_without_meter,monkeypatch,fault):
    root,job,record=completed_without_meter
    outcome=c.read(record/'native-outcome.json')
    if fault=='cleanup':outcome['cleanup']='cleanup_unknown'
    else:outcome['metering']['source']='native_session_token_count'
    c.write(record/'native-outcome.json',outcome)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    with pytest.raises(continuation.ResumeUnavailable):
        continuation._eligible_original(root,1,[record],successful=True)
    if fault=='terminal':
        with pytest.raises(ValueError,match='known completed original'):
            continuation.output_request(root,1,record,source_attempt=0,verify_task=False)


def test_formal_dispatch_budget_gate_accepts_historical_billing_unknown(completed_without_meter,monkeypatch):
    root,job,record=completed_without_meter
    (record/'native-outcome.json').unlink()
    monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    monkeypatch.setattr(native,'usage',lambda root:dict(observed_tokens=17,accounted_tokens=17,unknown_steps=[9]))
    monkeypatch.setattr(experiments,'review_status',lambda config:dict(requested=False))
    stamp,seconds=authority._remaining(root,dict(scope=dict(step=1),native_attempt=dict(number=0)))
    assert seconds>0 and stamp['boot_id']==budget.clock_stamp()['boot_id']


def test_fragment_validation_uses_completed_original_despite_billing_unknown(completed_without_meter,monkeypatch):
    root,job,record=completed_without_meter
    payload=dict(summary='Retained finding',scientific_result='More analysis is needed',limitations=[],
        implementation_selection=None,stage_intent='continue',mechanism_findings=None)
    c.write(record/'science-output.json',payload)
    successor=record/'continuations/001';successor.mkdir(parents=True)
    c.write(successor/'native-intent.json',dict(continuation_kind='fragment',context_mode='new_context',
        native_home=str(root/'native-homes/001/context-001')))
    monkeypatch.setattr(checkpoint,'validate',lambda *args:dict(science=dict(status='present')))
    continuation.validate_fragment_chain(root,1,[record,successor])


@pytest.mark.process_e2e
@pytest.mark.parametrize('state',['running','unknown'])
def test_managed_original_still_waits_or_retains_unknown_without_resubmission(tmp_path,composed_registry,monkeypatch,state):
    root,job,config,cli,call=case(tmp_path,dict(collect=False,query_state=state))
    record=execution.origin(root,2);raw=record/'science-output.json';raw.write_text('{}')
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,cleanup='observed_group_cleaned',
        usage=dict(unknown_steps=[1,2],blocking_unknown_steps=[]),raw_ref=c.file_ref(raw),
        metering=dict(source='turn.completed',coverage='unknown',reason='terminal_usage_unreported')))
    monkeypatch.setattr(worker_delivery,'writers_stopped',lambda *args:None)
    before=(experiments.Records(config).directory(call)/'accepted.json').read_bytes()
    if state=='running':
        with pytest.raises(managed_wait.ExecutionWaiting):managed_wait.before_seal(root,job,record)
        wait=c.read(record/managed_wait.NAME)
        assert all(Path(item['accepted']['path']).is_absolute() for item in wait['calls'])
        assert [c.read(Path(item['accepted']['path']))['call_id'] for item in wait['calls']]==[call]
    else:
        managed_wait.before_seal(root,job,record)
        assert not (record/managed_wait.NAME).exists()
    assert len(experiments.Records(config).calls())==1
    assert (experiments.Records(config).directory(call)/'accepted.json').read_bytes()==before
