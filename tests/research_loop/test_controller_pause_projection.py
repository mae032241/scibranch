"""Run/status projections preserve an independently admitted recovery decision."""
from scidiscovery.research_loop import (
    accepted_budget, budget, continuation, contracts as c, controller,
    instance_control, native_recovery,
)
from test_adaptive_a_budget import _prepare_publication_instance


def stopped_attempt(tmp_path, *, call_state):
    root=_prepare_publication_instance(tmp_path,search_policy=None,supervision_policy=None,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30.) for role in 'HWAE'})
    job=c.build_job(root,1,'W',[])
    record=root/'native-evidence/001'
    intent=dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),
        budget_slice=budget.accept_node(root,job))
    accepted_budget.accept_native(root,1,record,intent)
    outcome=dict(stop_reason='native_metadata_unsupported',native_started=False,
        returncode=None,terminal_observed=False,native_activity_observed=False,
        thread_id=None,native_thread_event=None,native_failure=None,cleanup=None,
        native_identity=dict(path=None,session=None),native_stream=dict(size_bytes=0),
        shared_inputs=dict(enforcement='not_started'),metering=dict(has_observation=False,tokens={}),
        technical_diagnostic=dict(phase='initialization_submit',code='native_metadata_unsupported',
            original_call_state=call_state,thread_bound=False,turn_bound=False))
    c.write(record/'native-outcome.json',outcome)
    accepted_budget.accept_terminal(root,1,record,meter=None,unknown=True)
    return root,job,record,outcome


def test_run_preserves_status_recovery_admitted_from_positive_non_submission(tmp_path,monkeypatch):
    root,job,record,outcome=stopped_attempt(tmp_path,call_state='not_submitted')
    assert native_recovery.never_started(outcome)
    checked=[]
    # This is a projection regression. Admission itself has its own original-
    # receipt tests; substitute its passed decision without launching a provider.
    def admitted(root,job,paths,intents,outcomes,**facts):
        assert native_recovery.never_started(outcomes[-1])
        checked.append((paths[-1],intents[-1]))
        return []
    monkeypatch.setattr(continuation,'_technical_restart_gate',admitted)
    frozen={path:path.read_bytes() for path in (root/'jobs/001.json',
        record/'native-intent.json',record/'native-outcome.json')}
    before=accepted_budget.read(root)
    result=controller._technical_pause_result(root,c.settings(root),0,None,
        job=job,record=record,outcome=outcome)
    status=instance_control.status(root,live_feedback=False)
    assert len(checked)==2
    for key in ('supported_actions','stage','pending_step','pending_role','repair_scope'):
        assert result[key]==status[key]
    assert result['supported_actions']==['worker-resume']
    assert result['technical_stop']['recovery_available'] is True
    assert result['technical_stop']['expected_attempt']==0
    assert result['technical_stop']['recheck_required'] is True
    assert result['technical_stop']['technical_diagnostic']==result['technical_diagnostic']
    assert result['technical_diagnostic']['original_call_state']=='not_submitted'
    after=accepted_budget.read(root)
    for key in ('usage','prepared_count','dispatch_count','continuation_count'):
        assert after[key]==before[key]
    assert all(path.read_bytes()==original for path,original in frozen.items())
    assert not (root/'jobs/002.json').exists()


def test_unknown_original_has_no_restart_action_or_new_submission(tmp_path,monkeypatch):
    root,job,record,outcome=stopped_attempt(tmp_path,call_state='unknown')
    assert not native_recovery.never_started(outcome)
    def forbidden(*args,**kwargs):
        raise AssertionError('Unknown original entered a restart gate or native submission')
    monkeypatch.setattr(continuation,'_technical_restart_gate',forbidden)
    monkeypatch.setattr(controller,'_native_call',forbidden)
    before=accepted_budget.read(root)
    result=controller._technical_pause_result(root,c.settings(root),0,None,
        job=job,record=record,outcome=outcome)
    assert result['supported_actions']==[]
    assert result['technical_stop']['supported_actions']==[]
    assert result['technical_diagnostic']['next_action']=='reconcile_original_without_replay'
    assert 'Reconcile the exact original native call' in result['technical_stop']['prerequisite']
    after=accepted_budget.read(root)
    assert after['usage']==before['usage'] and after['dispatch_count']==before['dispatch_count']
    assert not (root/'jobs/002.json').exists()
