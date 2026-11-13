"""Billing coverage alone grants no replay and removes no execution completion."""
import sys

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, contracts as c, continuation,
    controller, execution, execution_authority as authority, experiments, native, runtime)


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
    accepted_budget.accept_native(root,1,record,intent)
    binding=execution.identity(root,1)
    for name in ('runner-process.json','native-process.json'):
        c.write(record/name,dict(task_ref=binding,boot_id=budget.clock_stamp()['boot_id'],pid=1,start_ticks=1,pgid=1,sid=1,ppid=1))
    # Synthetic stopped transport facts bind the existing process receipts; no writer runs.
    outcome=dict(returncode=0,stop_reason=None,native_started=True,cleanup='observed_group_cleaned',
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
    inventory=c.accept_current_inventory(root,1)
    request=continuation.output_request(root,1,record,inventory=inventory,source_attempt=0,verify_task=False)
    assert request['code']=='missing_output'
    assert 'usage.json' not in execution.proof(record)
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
        inventory=c.accept_current_inventory(root,1)
        with pytest.raises(ValueError,match='known completed original'):
            continuation.output_request(root,1,record,inventory=inventory,source_attempt=0,verify_task=False)


def test_formal_dispatch_budget_gate_accepts_historical_billing_unknown(completed_without_meter,monkeypatch):
    root,job,record=completed_without_meter
    (record/'native-outcome.json').unlink()
    monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    monkeypatch.setattr(native,'usage',lambda root:dict(observed_tokens=17,accounted_tokens=17,unknown_steps=[9]))
    monkeypatch.setattr(experiments,'review_status',lambda config:dict(requested=False))
    stamp,seconds=authority._remaining(root,dict(scope=dict(step=1),native_attempt=dict(number=0)))
    assert seconds>0 and stamp['boot_id']==budget.clock_stamp()['boot_id']
