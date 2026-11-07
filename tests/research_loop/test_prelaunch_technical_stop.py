"""Explicit prelaunch repair preserves one task and every consumed budget."""
import sys

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, continuation,
    contracts as c, controller, execution, instance_control, native,
    native_recovery, profiles, runtime)
from scidiscovery.research_loop.native_usage import stream_binding


def stopped_outcome(**changes):
    value=dict(native_started=False,returncode=None,terminal_observed=False,
        native_activity_observed=False,thread_id=None,native_thread_event=None,
        native_failure=None,cleanup=None,stop_reason='native_metadata_unsupported',
        native_identity=dict(backend='protocol',home='/private/native',path=None,session=None),
        native_stream=dict(size_bytes=0),shared_inputs=dict(enforcement='not_started'),
        metering=dict(coverage='unknown',has_observation=False,
            tokens=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)))
    value.update(changes)
    return value


@pytest.mark.parametrize('changes',[
    dict(native_started=None),dict(native_started=True),dict(returncode=1),
    dict(thread_id='retained-thread'),dict(native_thread_event={'type':'thread.started'}),
    dict(native_activity_observed=True),dict(terminal_observed=True),
    dict(cleanup='cleanup_unknown'),dict(execution_processes={'native-process.json':{}}),
    dict(native_stream={'size_bytes':1}),dict(native_identity={'path':'/private/session'}),
    dict(stop_reason='native_error_unknown'),dict(stop_reason='wall_limit'),
    dict(metering={'has_observation':True}),dict(metering={'tokens':{'input_tokens':1}}),
])
def test_prelaunch_repair_requires_positive_non_submission(changes):
    assert native_recovery.never_started(stopped_outcome())
    assert not native_recovery.never_started(stopped_outcome(**changes))


def test_public_technical_stop_has_no_private_native_identity_or_error_prose():
    outcome=stopped_outcome(limitation='/private/credential native error secret')
    job=dict(step=4,role='W')
    projected=continuation.technical_stop_from_facts(job,outcome,
        recovery_available=True,attempt=2)
    assert projected['supported_actions']==['worker-resume']
    assert projected['expected_attempt']==2 and projected['pending_step']==4
    assert projected['reason']=='native_metadata_unsupported'
    assert '/private' not in repr(projected) and 'secret' not in repr(projected)
    assert continuation.technical_stop_from_facts(job,dict(outcome,native_started=True)) is None


def prelaunch_instance(tmp_path):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain the same immutable scientific assignment.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable,'-c','raise SystemExit(99)'],
        native_backend='protocol',experiment_mode='external',
        max_tokens=1000,max_seconds=120,max_steps=5,model='no-model',reasoning_effort='high',
        search_policy=None,exploration_order_version=None,
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=20)
            for role in 'HWAE'})
    job=c.build_job(root,1,'H',[])
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','fixture_prelaunch')
    settings=c.settings(root,job)
    intent=dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),
        **profiles.binding(settings,job),budget_slice=budget.accept_node(root,job))
    record=execution.origin(root,1)
    accepted_budget.accept_native(root,1,record,intent)
    retain_stop(root,record,intent)
    return root,job,record


def retain_stop(root,record,intent):
    record.mkdir(parents=True,exist_ok=True)
    (record/'native.jsonl').write_bytes(b'')
    binding=(c.record_ref(record/'native-intent.json') if intent.get('attempt') else
        c.record_ref(root/'jobs/001.json'))
    c.write(record/'runner-process.json',dict(task_ref=binding,
        boot_id=budget.clock_stamp()['boot_id'],pid=2_000_000_000,start_ticks=1,
        pgid=2_000_000_000,sid=2_000_000_000,ppid=1))
    job=c.read(root/'jobs/001.json')
    report=budget.node_report(root,job,intent,
        dict(input_tokens=0,cached_input_tokens=0,output_tokens=0))
    report['usage_observed']=False
    c.write(record/'native-outcome.json',stopped_outcome(
        native_stream=stream_binding(record/'native.jsonl'),budget_slice=report,
        usage=native.usage(root)))


def test_explicit_prelaunch_replacement_is_same_job_and_cumulative_without_zero_meter(tmp_path,monkeypatch):
    root,job,first=prelaunch_instance(tmp_path)
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_terminal_ineligible'):
        continuation._eligible_original(root,1,[first])
    before={name:(first/name).read_bytes() for name in
        ('native-intent.json','native-outcome.json','native.jsonl','runner-process.json')}
    launches=[]
    def start(root,step,attempt=0):
        launches.append(attempt)
        record=execution.record(root,step)
        retain_stop(root,record,c.read(record/'native-intent.json'))
    monkeypatch.setattr(runtime,'start',start)
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:dict(collected=True))
    assert continuation.start(root,1,expected_attempt=0)==dict(collected=True)
    second=execution.record(root,1)
    saved=c.read(second/'native-intent.json')
    assert saved['continuation_kind']=='technical_restart' and 'session' not in saved
    assert saved['task_ref']==c.record_ref(root/'jobs/001.json')
    assert saved['technical_restart']['source_attempt']==0
    assert saved['budget_slice']['previous_seconds']>=c.read(first/'native-outcome.json')['budget_slice']['advance_seconds']
    assert saved['budget_slice']['previous_tokens']==0 and budget.used_steps(root)==2
    assert native.usage(root)['unknown_steps']==[1]
    assert not (first/'usage.json').exists() and not (second/'usage.json').exists()
    assert all((first/name).read_bytes()==data for name,data in before.items())
    # Repeating the exact source attempt only collects its accepted replacement.
    continuation.start(root,1,expected_attempt=0)
    assert launches==[1] and len(execution.records(root,1))==2
    # A distinct explicit repair can address another proven prelaunch failure.
    continuation.start(root,1,expected_attempt=1)
    assert launches==[1,2] and budget.used_steps(root)==3
    assert execution.validate_records(root,1)==execution.records(root,1)


@pytest.mark.parametrize('block',['running','native_process','unresolved','paused','steps','activity','accounting'])
def test_prelaunch_recovery_rechecks_original_liveness_and_current_gates(tmp_path,monkeypatch,block):
    root,job,first=prelaunch_instance(tmp_path)
    if block=='running':monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    elif block=='native_process':c.write(first/'native-process.json',{})
    elif block=='unresolved':monkeypatch.setattr(budget,'execution_blocked',lambda *args,**kwargs:True)
    elif block=='paused':c.write(root/'pause.request',dict(reason='operator pause'))
    elif block=='steps':monkeypatch.setattr(budget,'used_steps',lambda *args,**kwargs:5)
    elif block=='accounting':
        value=c.read(first/'native-outcome.json')
        value['budget_slice']['observed_tokens']=99
        c.write(first/'native-outcome.json',value)
    else:(first/'science-output.json').write_text('{}')
    launches=[];monkeypatch.setattr(runtime,'start',lambda *args:launches.append(args))
    with pytest.raises(ValueError):continuation.start(root,1,expected_attempt=0)
    assert not launches and len(execution.records(root,1))==1
    if block=='accounting':
        with pytest.raises(ValueError):continuation.technical_stop(root,job,first,c.read(first/'native-outcome.json'))
    else:
        stop=continuation.technical_stop(root,job,first,c.read(first/'native-outcome.json'))
        assert stop['recovery_available'] is False and stop['supported_actions']==[]


def test_status_projects_exact_prelaunch_action_without_accepting_inventory(tmp_path,monkeypatch):
    root,job,first=prelaunch_instance(tmp_path)
    monkeypatch.setattr(c,'accept_current_inventory',lambda *args:pytest.fail('status reaccepted science'))
    before=(first/'native-outcome.json').read_bytes()
    status=instance_control.status(root,live_feedback=False)
    assert status['technical_stop']['recovery_available'] is True
    assert status['technical_stop']['expected_attempt']==0
    assert status['pending_step']==1 and status['pending_role']=='H'
    assert (first/'native-outcome.json').read_bytes()==before


@pytest.mark.parametrize('wrong',['task_ref','reused_pid'])
def test_explicit_group_cleanup_never_signals_a_wrong_or_reused_owner(monkeypatch,wrong):
    owner=dict(task_ref='exact-operation',boot_id='original-boot',pid=1234567,
        start_ticks=20,pgid=1234567,sid=1234567,ppid=1,state='R')
    members=[dict(owner,start_ticks=99)]
    monkeypatch.setattr(runtime,'_observed_group_members',lambda identity:members)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    monkeypatch.setattr(runtime.os,'kill',lambda *args:pytest.fail('signalled an unbound/reused PID'))
    key='different-operation' if wrong=='task_ref' else 'exact-operation'
    assert runtime.stop_bound_group(owner,key)=='cleanup_unknown'


def test_explicit_group_cleanup_retains_unconfirmed_original_cleanup(monkeypatch):
    owner=dict(task_ref='exact-operation',boot_id='original-boot',pid=1234567,
        start_ticks=20,pgid=1234567,sid=1234567,ppid=1,state='R')
    live=[True];signals=[]
    monkeypatch.setattr(runtime,'_observed_group_members',lambda identity:[owner])
    monkeypatch.setattr(runtime,'process_matches',lambda *args:live[0])
    def signal(pid,number):
        signals.append((pid,number));live[0]=False
    monkeypatch.setattr(runtime.os,'kill',signal)
    monkeypatch.setattr(runtime,'cleanup_owned_group',lambda identity:'cleanup_unknown')
    assert runtime.stop_bound_group(owner,'exact-operation')=='cleanup_unknown'
    assert signals==[(owner['pid'],runtime.signal.SIGTERM)]


@pytest.mark.parametrize('bad',['none','job','cleanup'])
def test_held_repair_cleanup_is_an_explicit_exact_owner_mutation(tmp_path,monkeypatch,bad):
    from scidiscovery.research_loop import control_operations
    root,job,first=prelaunch_instance(tmp_path)
    operation_id='1'*32
    operation=dict(operation_id=operation_id,kind='controller',sequence=1,status='running',
        action=dict(kind='worker_resume',step=1,expected_attempt=0,
            job_ref=c.record_ref(root/'jobs/001.json')),
        process=dict(task_ref=operation_id,boot_id='same-boot',pid=1234567,
            start_ticks=1,pgid=1234567,sid=1234567,ppid=1))
    if bad=='job':operation['action']['job_ref']=dict(path='/different/jobs/001.json')
    c.write(root/'host-control/operations'/f'{operation_id}.json',operation)
    c.write(first/'watchdog-process.json',c.read(first/'runner-process.json'))
    before=(first/'native-outcome.json').read_bytes()
    pauses=[];stops=[]
    monkeypatch.setattr(instance_control,'request_pause',lambda selected:pauses.append(selected))
    def stop(identity,binding):
        stops.append(binding)
        if binding != operation_id:
            probe=control_operations.os.open(root/'host-run.lock',control_operations.os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    control_operations.fcntl.flock(probe,
                        control_operations.fcntl.LOCK_EX|control_operations.fcntl.LOCK_NB)
            finally:control_operations.os.close(probe)
        return 'cleanup_unknown' if bad=='cleanup' else 'observed_group_cleaned'
    monkeypatch.setattr(runtime,'stop_bound_group',stop)
    result=control_operations.stop_held_recovery(root,held_operation_id=operation_id,
        active_operation_id=operation_id)
    assert result['cleanup_confirmed'] is (bad=='none')
    assert (first/'native-outcome.json').read_bytes()==before
    if bad=='job':assert pauses==stops==[]
    else:
        assert pauses==[root] and stops[0]==operation_id
        assert result['status']==('held_recovery_stopped' if bad=='none' else 'cleanup_unknown')


def test_held_cleanup_does_not_read_or_signal_a_new_controller_node(tmp_path,monkeypatch):
    from scidiscovery.research_loop import control_operations
    root,job,first=prelaunch_instance(tmp_path)
    operation_id='1'*32
    c.write(root/'host-control/operations'/f'{operation_id}.json',dict(
        operation_id=operation_id,kind='controller',sequence=1,status='running',
        action=dict(kind='worker_resume',step=1,expected_attempt=0,
            job_ref=c.record_ref(root/'jobs/001.json')),
        process=dict(task_ref=operation_id,boot_id='same-boot',pid=1234567,
            start_ticks=1,pgid=1234567,sid=1234567,ppid=1)))
    monkeypatch.setattr(instance_control,'request_pause',lambda *args:None)
    monkeypatch.setattr(accepted_budget,'read',lambda *args:pytest.fail('read a later owner node without its writer fence'))
    replacement=[];stops=[]
    def old_controller_stopped(identity,binding):
        stops.append(binding)
        # A different explicit controller wins the released writer before old
        # cleanup can inspect current native state. No real process is killed.
        descriptor=control_operations.os.open(root/'host-run.lock',
            control_operations.os.O_CREAT|control_operations.os.O_RDWR,0o600)
        control_operations.fcntl.flock(descriptor,control_operations.fcntl.LOCK_EX|control_operations.fcntl.LOCK_NB)
        replacement.append(descriptor)
        return 'observed_group_cleaned'
    monkeypatch.setattr(runtime,'stop_bound_group',old_controller_stopped)
    try:
        result=control_operations.stop_held_recovery(root,held_operation_id=operation_id,
            active_operation_id=operation_id)
    finally:
        for descriptor in replacement:control_operations.os.close(descriptor)
    assert stops==[operation_id]
    assert result['status']=='cleanup_unknown' and result['cleanup_confirmed'] is False


@pytest.mark.parametrize('situation',['none','accepted_race','unbound_owner','busy'])
def test_no_replacement_confirmation_requires_fenced_originals(tmp_path,monkeypatch,situation):
    from scidiscovery.research_loop import control_operations
    root,job,first=prelaunch_instance(tmp_path)
    c.write(first/'watchdog-process.json',c.read(first/'runner-process.json'))
    descriptor=control_operations.os.open(root/'host-run.lock',
        control_operations.os.O_CREAT|control_operations.os.O_RDWR,0o600)
    if situation=='busy':
        control_operations.fcntl.flock(descriptor,
            control_operations.fcntl.LOCK_EX|control_operations.fcntl.LOCK_NB)
    if situation in {'accepted_race','unbound_owner'}:
        operation_id='1'*32
        action=(dict(kind='worker_resume',step=1,expected_attempt=0,
            job_ref=c.record_ref(root/'jobs/001.json')) if situation=='accepted_race' else dict(kind='continue'))
        c.write(root/'host-control/operations'/f'{operation_id}.json',dict(
            operation_id=operation_id,kind='controller',sequence=1,status='accepted',action=action))
    pauses=[]
    monkeypatch.setattr(instance_control,'request_pause',lambda selected:pauses.append(selected))
    monkeypatch.setattr(runtime,'stop_bound_group',lambda *args:pytest.fail('confirmation signalled a process'))
    def stopped(identity,binding):
        probe=control_operations.os.open(root/'host-run.lock',control_operations.os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                control_operations.fcntl.flock(probe,
                    control_operations.fcntl.LOCK_EX|control_operations.fcntl.LOCK_NB)
        finally:control_operations.os.close(probe)
        return True
    monkeypatch.setattr(runtime,'bound_group_stopped',stopped)
    try:
        result=control_operations.confirm_no_held_recovery(root,
            goal_id=c.settings(root)['goal_id'],step=1,expected_attempt=0)
    finally:control_operations.os.close(descriptor)
    assert result['cleanup_confirmed'] is (situation=='none')
    assert result['no_replacement'] is (situation=='none')
    assert pauses==([] if situation=='busy' else [root])
