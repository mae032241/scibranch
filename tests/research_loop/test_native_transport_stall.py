"""Real native pipes under backpressure; deterministic transport only, no models."""
from contextlib import contextmanager
import json
import signal
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, budget,configuration_updates,contracts as c,controller,execution,native,profiles,runtime
from test_history_handoff import finish


PADDING = 'x' * (256 * 1024)
LAUNCHER = r'''import json,os,pathlib,sys,time
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
(work/'work/launched').write_text('one')
if MODE=='blocked':
 print(json.dumps(dict(type='usage',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=1))),flush=True)
 time.sleep(60)
elif MODE=='closed':
 os.close(0)
 print(json.dumps(dict(type='error',message='Fixture startup rejects request')),flush=True)
 raise SystemExit(7)
else:
 # Fill stdout before reading stdin: synchronous request delivery deadlocks.
 row=(json.dumps(dict(type='message',text='fixture startup '+('z'*4096)))+'\n').encode()
 for _ in range(64):sys.stdout.buffer.write(row)
 sys.stdout.buffer.flush()
 request=json.loads(sys.stdin.read())
 assert request['protocol']=='scidiscovery-agent-v1'
 assert request['prompt'].endswith('x'*(256*1024))
 (work/'work/request-received').write_text(str(len(request['prompt'])))
 print(json.dumps(dict(type='session',session_id='fixture-transport-session')),flush=True)
 # Accepted work keeps running across a next-node pause barrier.
 pathlib.Path(PAUSE).write_text('pause after this accepted node')
 payload=dict(summary='Transport fixture',scientific_result='Complete request received.',limitations=['No scientific qualification.'],
  implementation_selection=None,search_plan=None,next_action='wait',route_reason='Fixture complete')
 print(json.dumps(dict(type='result',result=payload)),flush=True)
 # Exercise the existing EOF handling for a final event without newline.
 sys.stdout.write(json.dumps(dict(type='completed',usage=dict(input_tokens=17,cached_input_tokens=3,output_tokens=5))))
 sys.stdout.flush()
'''


@contextmanager
def _transport_guard(seconds=15):
    """Fail a stuck pre-fix write, allowing run_native's own finally to clean up."""
    fired=[]
    def expired(signum,frame):
        fired.append(True)
        raise TimeoutError('Fixture transport guard: request delivery stalled')
    previous=signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try:
        yield
        assert not fired,'Native request delivery stalled before its existing control loop'
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)


def _scene(tmp_path,monkeypatch,mode='backpressure',*,final=False,interrupt_support=False):
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal';goal.write_text('Exercise deterministic native transport.')
    root=tmp_path/'instance'
    launcher=tmp_path/'launcher.py'
    launcher.write_text('MODE='+repr(mode)+'\nPAUSE='+repr(str(root/'pause.request'))+'\n'+LAUNCHER)
    controller.prepare(root,goal,public,[sys.executable,str(launcher)],native_backend='protocol',
        max_tokens=10000,max_seconds=120,max_steps=6,model='fixture-no-model',reasoning_effort='high',
        search_policy=None,node_budget_mode='soft',
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=3 if final and role=='A' else 20) for role in 'HWAE'})
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','fixture native transport')
    if final:
        finish(root,1,'H',[],action='experiment')
        finish(root,2,'W',[1])
        budget.begin_closeout(root,2,3,'fixture final transport',final_only=True)
        job=c.build_job(root,3,'A',[1,2],final_report=dict(source_cutoff=2,stopped_original=None))
    else:
        job=c.build_job(root,1,'H',[])
    step=job['step']
    anchor=budget.accept_node(root,job)
    record=execution.origin(root,step)
    intent=dict(step=step,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        **profiles.binding(configuration_updates.for_job(root,job),job),budget_slice=anchor)
    if interrupt_support:
        intent['operator_interrupt_version']=native.OPERATOR_INTERRUPT_VERSION
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,step,record,intent)
    render=native.render_scientific_request
    def padded(*args,**kwargs):
        prompt,instruction,schema=render(*args,**kwargs)
        return prompt+'\n'+PADDING,instruction,schema
    monkeypatch.setattr(native,'render_scientific_request',padded)
    return root,record


@pytest.mark.process_e2e
def test_native_request_and_startup_events_drain_together(tmp_path,monkeypatch):
    root,record=_scene(tmp_path,monkeypatch)
    with _transport_guard():native.run_native(root,1)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['cleanup']=='observed_group_cleaned'
    assert outcome['metering']['coverage']=='terminal'
    assert c.read(record/'usage.json')==dict(input_tokens=17,cached_input_tokens=3,output_tokens=5)
    assert c.read(record/'science-output.json')['scientific_result']=='Complete request received.'
    assert int((c.workspace(root,1)/'work/request-received').read_text())>len(PADDING)
    assert (root/'pause.request').exists()
    assert (record/'native.jsonl').stat().st_size>256*1024
    assert not runtime.process_matches(c.read(record/'native-process.json'),execution.identity(root,1))


@pytest.mark.process_e2e
def test_native_closed_request_pipe_retains_failure_and_cleanup(tmp_path,monkeypatch):
    root,record=_scene(tmp_path,monkeypatch,'closed')
    with _transport_guard(),pytest.raises(RuntimeError,match='not a verified terminal result'):
        native.run_native(root,1)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==7 and outcome['stop_reason']=='native_failure_event'
    assert outcome['terminal_observed'] is False
    assert outcome['cleanup']=='observed_group_cleaned'
    assert json.loads((record/'native.jsonl').read_text())['type']=='error'
    assert not (c.workspace(root,1)/'work/request-received').exists()
    assert not runtime.process_matches(c.read(record/'native-process.json'),execution.identity(root,1))


@pytest.mark.process_e2e
def test_native_blocked_request_keeps_original_final_a_hard_clock(tmp_path,monkeypatch):
    root,record=_scene(tmp_path,monkeypatch,'blocked',final=True)
    original_intent=(record/'native-intent.json').read_bytes()
    claim=budget.final_a_barrier(root)
    with _transport_guard(),pytest.raises(RuntimeError,match='not a verified terminal result'):
        native.run_native(root,3)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['stop_reason']=='node_slice_time_limit'
    assert outcome['native_started'] and outcome['returncode']!=0
    assert outcome['cleanup']=='observed_group_cleaned'
    assert outcome['metering']['coverage']=='unknown'
    assert c.read(record/'usage.json')==dict(input_tokens=7,cached_input_tokens=2,output_tokens=1)
    assert outcome['budget_slice']['advance_seconds']>=3
    assert (record/'native-intent.json').read_bytes()==original_intent
    assert budget.final_a_barrier(root)==claim
    assert not (c.workspace(root,3)/'work/request-received').exists()
    assert not (record/'continuations').exists()
    assert not runtime.process_matches(c.read(record/'native-process.json'),execution.identity(root,3))
