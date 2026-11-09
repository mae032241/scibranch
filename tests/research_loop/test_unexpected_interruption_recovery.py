"""Unexpected interruption retains the accepted task, not a replacement study."""
import copy
from contextlib import contextmanager
import errno
import json
import os
import signal
import sys
import threading
import time

import pytest

from scidiscovery.research_loop import (accepted_budget, budget, continuation, contracts as c,
    controller, execution, instance_ui, native, runtime, ui_saved_status)
from test_immediate_interrupt import accepted_scene, resume_options
from test_native_transport_stall import _transport_guard
from test_reboot_native_acceptance import accepted_interrupted_native


def stopped_same_boot(tmp_path,monkeypatch,*,meter=False):
    root,record,target=accepted_scene(tmp_path,monkeypatch)
    stamp=budget.clock_stamp();binding=dict(path=str(root/'jobs/001.json'))
    for index,name in enumerate(('runner-process.json','native-process.json','watchdog-process.json')):
        pid=999990+index
        c.write(record/name,dict(pid=pid,boot_id=stamp['boot_id'],start_ticks=1,pgid=pid,sid=pid,ppid=1,task_ref=binding))
    (c.workspace(root,1)/'work/partial-output').write_bytes(b'original incomplete work\n')
    if meter:c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=1))
    return root,record,target


@contextmanager
def fixture_cleanup(record):
    """Only the two exact temporary attempt owners, including red controls."""
    root=record.parent.parent
    for_cleanup=(record,record/'continuations/001')
    try:yield
    finally:
        for directory in for_cleanup:
            binding=dict(path=str(root/'jobs'/f'{int(record.name):03d}.json')) if directory==record else dict(path=str(directory/'native-intent.json'))
            for name in ('native-process.json','bridge-process.json','runner-process.json','watchdog-process.json'):
                path=directory/name
                if not path.exists():continue
                receipt=c.read(path)
                if runtime.process_live(receipt,binding):runtime.stop_bound_group(receipt,binding)
                try:os.waitpid(receipt['pid'],os.WNOHANG)
                except (ChildProcessError,KeyError):pass


@pytest.mark.parametrize('meter',[False,True])
def test_same_boot_confirmed_stop_reenters_without_outcome_or_session(tmp_path,monkeypatch,meter):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch,meter=meter)
    originals={path:path.read_bytes() for path in (record/'native-intent.json',record/'runner-process.json',
        record/'native-process.json',c.workspace(root,1)/'work/partial-output')}
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    assert runtime.node_state(root,1)=='unknown'
    continuation.start(root,1,**resume_options(target))
    current=record/'continuations/001'
    intent=c.read(current/'native-intent.json')
    marker=c.read(record/'interruption.json')
    assert marker['stop_proof']['kind']=='same_boot_stopped'
    assert intent['continuation_kind']=='reentry' and intent['context_mode']=='new_context'
    assert intent['context_reason']=='native_resume_unsupported_backend'
    assert intent['budget_slice']['previous_tokens']==(8 if meter else 0)
    assert starts==[(root,1,1)]
    assert not (record/'native-outcome.json').exists()
    assert (record/'usage.json').exists() is meter
    assert native.usage(root)['unknown_steps']==[1]
    assert {path:path.read_bytes() for path in originals}==originals
    # The accepted typed witness is historical, not a fresh PID scan on reads.
    monkeypatch.setattr(runtime,'probe_interruption',lambda *args,**kwargs:pytest.fail('historical proof reprobed'))
    assert continuation.interruption(root,c.read(root/'jobs/001.json'),record)==marker
    continuation.start(root,1,**resume_options(target))
    assert starts==[(root,1,1)]


def test_old_boot_explicit_new_context_needs_no_session_but_preserves_accounting(tmp_path,monkeypatch):
    root,record,originals=accepted_interrupted_native(tmp_path,monkeypatch,meter=False,closed_clock=True)
    assert budget.snapshot(root)['consumed_seconds']==10.
    (record/'usage-observation.json').unlink()
    originals.pop(record/'usage-observation.json')
    target=dict(expected_attempt=0,native_intent_ref=dict(path=str(record/'native-intent.json')),after_interrupt=True,new_context=True)
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    monkeypatch.setattr(continuation,'session',lambda *args,**kwargs:pytest.fail('new context reopened old SDK session'))
    continuation.start(root,1,**target)
    intent=c.read(record/'continuations/001/native-intent.json')
    assert intent['context_mode']=='new_context' and 'session' not in intent
    assert c.read(record/'interruption.json')['stop_proof']['kind']=='boot_changed'
    assert starts==[(root,1,1)] and not (record/'native-outcome.json').exists()
    assert {path:path.read_bytes() for path in originals}==originals


def test_reentry_acquires_session_once_and_retained_validation_has_no_reads(tmp_path,monkeypatch):
    root,record,originals=accepted_interrupted_native(tmp_path,monkeypatch,meter=False)
    selected=continuation.session;calls=[]
    def acquired(*args,**kwargs):
        calls.append((args,kwargs));return selected(*args,**kwargs)
    monkeypatch.setattr(continuation,'session',acquired)
    monkeypatch.setattr(runtime,'start',lambda *args:None)
    intent=continuation.reenter(root,1)
    assert len(calls)==1
    saved=continuation.interruption(root,c.read(root/'jobs/001.json'),record)
    original=c.read(record/'native-intent.json');settings=c.settings(root)
    job=c.read(root/'jobs/001.json')
    execution.validate_records(root,1)
    assert len(calls)==1  # Immutable full-chain acceptance does not reopen SDK.
    with monkeypatch.context() as pure:
        pure.setattr(c,'read',lambda *args:pytest.fail('pure reentry validator read an original'))
        continuation.validate_reentry(root,job,intent,record,saved=saved,original=original,
            settings=settings,source_session=saved['source_session'],accepted_anchor=intent['budget_slice'])


@pytest.mark.parametrize('closed',[False,True])
def test_failed_outcome_missing_bill_uses_exact_reentry_proof_without_rewriting_source(tmp_path,monkeypatch,closed):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    original=c.read(record/'native-intent.json')
    report=dict(role='H',limits=original['budget_slice']['limits'],advance_seconds=0.,observed_tokens=0,
        dispatches=1,usage_observed=False,input_tokens=0,cached_input_tokens=0,output_tokens=0)
    outcome=dict(returncode=-15,native_started=True,terminal_observed=False,stop_reason='native_error_unknown',
        cleanup='observed_group_cleaned',budget_slice=report,metering=dict(coverage='unknown'))
    c.write(record/'native-outcome.json',outcome)
    if closed:accepted_budget.accept_terminal(root,1,record,meter=None,unknown=True,metering=outcome['metering'])
    originals={path:path.read_bytes() for path in (record/'native-intent.json',record/'native-outcome.json')}
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    continuation.start(root,1,**resume_options(target,new_context=True))
    successor=record/'continuations/001';intent=c.read(successor/'native-intent.json')
    assert intent['continuation_kind']=='reentry' and c.read(record/'interruption.json')['stop_proof']['kind']=='owner_cleanup'
    assert starts==[(root,1,1)] and not (record/'usage.json').exists()
    job=c.read(root/'jobs/001.json')
    with pytest.raises(ValueError,match='Missing billing'):
        budget._attempt_consumption(root,job,[record])
    assert budget._attempt_consumption(root,job,[record],reentry_successor=intent)==(0,0.)
    assert native.usage(root)['unknown_steps']==[1]
    assert {path:path.read_bytes() for path in originals}==originals


@pytest.mark.parametrize('failure',['permission','bad_stat'])
def test_failed_outcome_positive_cleanup_does_not_hide_existing_process_probe_errors(tmp_path,monkeypatch,failure):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    c.write(record/'native-outcome.json',dict(returncode=-15,native_started=True,terminal_observed=False,
        stop_reason='native_error_unknown',cleanup='observed_group_cleaned'))
    def unreadable(*args,**kwargs):
        if failure=='permission':raise PermissionError(errno.EACCES,'fixture process access denied')
        raise ValueError('fixture malformed process stat')
    monkeypatch.setattr(runtime,'process_identity',unreadable)
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('uncertain process was replaced'))
    with pytest.raises(continuation.ResumeUnavailable,match='original_process_probe_'):
        continuation.start(root,1,**resume_options(target,new_context=True))
    assert not (record/'interruption.json').exists() and not (record/'continuations').exists()


def test_current_amended_shared_budget_is_used_without_resetting_original_slice(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch,meter=True)
    original=c.read(record/'native-intent.json')
    stamp=budget.clock_stamp()
    elapsed=dict(stamp,at_unix=stamp['at_unix']+200,at_boottime=stamp['at_boottime']+200)
    monkeypatch.setattr(budget,'clock_stamp',lambda:dict(elapsed))
    monkeypatch.setattr(runtime,'start',lambda *args:None)
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    with pytest.raises(budget.NodeBudgetUnavailable,match='worker_resume_budget_unavailable'):
        continuation.start(root,1,**resume_options(target,new_context=True))
    # The positive stopped-source witness settles the original first; a
    # refused new turn must not strand it behind an "unknown running" gate.
    assert accepted_budget.read(root)['current_native'] is None
    assert not (record/'continuations').exists()
    budget.amend(root,max_tokens=20000,max_seconds=400,authorization_note='Explicit mechanical ceiling amendment')
    continuation.start(root,1,**resume_options(target,new_context=True))
    intent=c.read(record/'continuations/001/native-intent.json')
    assert intent['budget_slice']['shared_limits']==dict(max_tokens=20000,max_seconds=400)
    assert intent['budget_slice']['limits']==original['budget_slice']['limits']
    assert intent['budget_slice']['previous_tokens']==8
    execution.validate_records(root,1)


def test_live_original_is_only_collected_even_when_new_context_was_selected(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    monkeypatch.setattr(runtime,'node_state',lambda *args:'running')
    starts=[];collected=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:collected.append(args) or {})
    continuation.start(root,1,**resume_options(target,new_context=True))
    assert starts==[] and collected==[(root,1)]
    assert not (record/'interruption.json').exists() and not (record/'continuations').exists()


def test_unknown_cleanup_does_not_start_a_replacement_or_invent_stop(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    monkeypatch.setattr(runtime,'probe_interruption',lambda *args,**kwargs:
        dict(status='unknown',reason='original_group_probe_unavailable'))
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('unknown original replaced'))
    with pytest.raises(continuation.ResumeUnavailable,match='original_group_probe_unavailable'):
        continuation.start(root,1,**resume_options(target,new_context=True))
    assert not (record/'interruption.json').exists() and not (record/'native-outcome.json').exists()


def test_stopped_local_like_bridge_without_old_cleanup_receipt_uses_new_typed_proof(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    original=c.read(record/'native-process.json')
    bridge=dict(original,pid=999980,pgid=999980,sid=999980)
    c.write(record/'bridge-process.json',bridge)
    before=(record/'bridge-process.json').read_bytes()
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    continuation.start(root,1,**resume_options(target,new_context=True))
    marker=c.read(record/'interruption.json')
    assert marker['stop_proof']['processes']['bridge-process.json']==bridge
    assert marker['stop_proof']['groups_absent'] is True and starts==[(root,1,1)]
    assert (record/'bridge-process.json').read_bytes()==before
    assert not (record/'bridge-outcome.json').exists() and not (record/'native-outcome.json').exists()


def test_known_complete_original_is_only_collected_without_a_new_model_turn(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    c.write(record/'native-outcome.json',dict(returncode=0,terminal_observed=True,stop_reason=None,
        cleanup='observed_group_cleaned'))
    starts=[];collected=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:collected.append(args) or {})
    continuation.start(root,1,**resume_options(target,new_context=True))
    assert not starts and collected==[(root,1)] and not (record/'continuations').exists()


def test_ui_exposes_direct_exact_new_context_recovery_and_readonly_has_no_control(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    current=ui_saved_status._node(root,1,c.read(root/'jobs/001.json'))
    assert current['unexpected_recovery_target']==dict(step=1,attempt=0,native_intent_ref=target['native_intent_ref'])
    host=instance_ui.InstanceUI.__new__(instance_ui.InstanceUI)
    host._technical_stop=lambda *args:None
    host._form=lambda token,action,fields,label:f'<form>{label}{fields}</form>'
    snapshot=dict(view=dict(nodes=[current],paused=False,correction={}))
    status=dict(state='unknown',display=dict(current=current))
    markup=str(host._primary('manager',True,snapshot,status))
    assert '用新上下文恢复该原任务' in markup and '<details>' not in markup
    assert 'name="expected_attempt" value="0"' in markup
    assert '<form>' not in str(host._primary('reader',False,snapshot,status))


def test_repeated_recovery_after_accept_before_spawn_collects_or_starts_only_the_accepted_successor(tmp_path,monkeypatch):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    def failed_start(*args):raise OSError('fixture interrupted after accept before spawn')
    monkeypatch.setattr(runtime,'start',failed_start)
    with pytest.raises(OSError,match='after accept'):
        continuation.start(root,1,**resume_options(target))
    head=accepted_budget.read(root)
    assert head['dispatch_count']==2
    successor=record/'continuations/001'
    assert c.read(successor/'native-intent.json')['continuation_kind']=='reentry'
    waits=[]
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:waits.append((args,kwargs)) or {})
    continuation.start(root,1,**resume_options(target))
    assert waits==[((root,1),dict(_reentry_started=False))]
    assert accepted_budget.read(root)['dispatch_count']==head['dispatch_count']
    assert execution.records(root,1)==[record,successor]


@pytest.mark.parametrize('state',['active','pause_requested'])
def test_running_primary_keeps_pause_and_immediate_interrupt_controls(tmp_path,monkeypatch,state):
    root,record,target=stopped_same_boot(tmp_path,monkeypatch)
    monkeypatch.setattr(runtime,'process_live',lambda *args:True)
    current=ui_saved_status._node(root,1,c.read(root/'jobs/001.json'))
    assert current['state']=='running' and 'unexpected_recovery_target' not in current
    host=instance_ui.InstanceUI.__new__(instance_ui.InstanceUI)
    host._technical_stop=lambda *args:None
    host._form=lambda token,action,fields,label:f'<form action="/control/{action}">{label}</form>'
    snapshot=dict(view=dict(nodes=[current],paused=state=='pause_requested',correction={}))
    status=dict(state=state,display=dict(current=current))
    markup=str(host._primary('manager',True,snapshot,status))
    assert '/control/interrupt' in markup and '恢复该原任务' not in markup
    if state=='active':assert '/control/pause' in markup


@pytest.mark.process_e2e
@pytest.mark.parametrize('spawn_gap',[False,True])
def test_same_boot_runner_loss_without_outcome_or_billing_recovers_and_seals(tmp_path,monkeypatch,spawn_gap):
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal';goal.write_text('Mechanical crash recovery only; no scientific qualification.')
    launcher=tmp_path/'native.py'
    launcher.write_text(r'''import json,os,pathlib,sys,time
request=json.loads(sys.stdin.read());work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
marker=work/'work/original-checkpoint'
with (work/'work/launches').open('a') as stream:stream.write('launch\n')
print(json.dumps(dict(type='session',session_id='crash-original')),flush=True)
if not marker.exists():
 marker.write_text('partial original W work')
 time.sleep(60)
else:
 assert marker.read_text()=='partial original W work'
 assert 'Explicit recovery of this same interrupted task' in request['prompt']
 payload=dict(summary='Recovered same W',scientific_result='Retained original partial work; transport fixture only.',
  limitations=['No actual experiment or scientific qualification.'],implementation_selection=None,
  mechanism_findings=None,stage_intent='review',consultation_question='Which scientific evidence remains missing beyond the retained transport checkpoint?')
 print(json.dumps(dict(type='result',result=payload)),flush=True)
 print(json.dumps(dict(type='completed',usage=dict(input_tokens=11,cached_input_tokens=2,output_tokens=3))),flush=True)
''')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,[sys.executable,str(launcher)],native_backend='protocol',experiment_mode='external',
        max_tokens=10000,max_seconds=120,max_steps=6,model='fixture-no-model',reasoning_effort='high',search_policy=None,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=20) for role in 'HWAE'})
    c.build_job(root,1,'W',[])
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','unexpected recovery fixture')
    record=execution.origin(root,1);binding=dict(path=str(root/'jobs/001.json'))
    errors=[]
    def crash_runner():
        try:
            until=time.monotonic()+8
            while not (c.workspace(root,1)/'work/original-checkpoint').exists():
                if time.monotonic()>=until:raise TimeoutError('native did not reach original checkpoint')
                time.sleep(.02)
            identity=c.read(record/'runner-process.json')
            assert runtime.process_matches(identity,binding)
            os.kill(identity['pid'],signal.SIGKILL)
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=crash_runner,daemon=True)
    with fixture_cleanup(record),controller._writer(root),_transport_guard(25):
        thread.start()
        with pytest.raises((RuntimeError,ValueError)):native.execute_native(root,1)
        thread.join(3);assert not thread.is_alive() and not errors
        until=time.monotonic()+5
        while runtime.probe_interruption(record,binding,current_boot=budget.clock_stamp()['boot_id'])['status']!='stopped':
            assert time.monotonic()<until
            time.sleep(.05)
        assert not (record/'native-outcome.json').exists() and not (record/'usage.json').exists()
        originals={path:path.read_bytes() for path in (record/'native-intent.json',record/'runner-process.json',
            record/'native-process.json',c.workspace(root,1)/'work/original-checkpoint')}
        options=dict(after_interrupt=True,expected_attempt=0,native_intent_ref=dict(path=str(record/'native-intent.json')),new_context=True)
        if spawn_gap:
            with monkeypatch.context() as failed_spawn:
                def fail(*args):raise OSError('fixture accept-before-spawn crash')
                failed_spawn.setattr(runtime,'start',fail)
                with pytest.raises(OSError,match='accept-before-spawn'):continuation.start(root,1,**options)
            assert accepted_budget.read(root)['dispatch_count']==2
        result=continuation.start(root,1,**options)
    assert result['role']=='W' and (record/'result-identity.json').exists()
    successor=record/'continuations/001'
    assert c.read(successor/'native-intent.json')['context_mode']=='new_context'
    assert not (record/'native-outcome.json').exists() and not (record/'usage.json').exists()
    assert native.usage(root)['observed_tokens']==14 and 1 in native.usage(root)['unknown_steps']
    assert (c.workspace(root,1)/'work/launches').read_text()=='launch\nlaunch\n'
    assert {path:path.read_bytes() for path in originals}==originals
