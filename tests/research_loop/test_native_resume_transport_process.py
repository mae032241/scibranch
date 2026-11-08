"""Actual adapter entry point and stdio RPC; no provider, auth or native home."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

from scidiscovery.research_loop import native_app_server as rpc
from test_native_app_server import declaration, started


# The fixture speaks only the real app-server controls consumed by the adapter.
# It records requests once and waits at an explicit final/terminal barrier.
APP_SERVER = r'''
import json,pathlib,sys,time
requests=pathlib.Path(REQUESTS)
def send(value):
 print(json.dumps(value,separators=(',',':')),flush=True)
def receive():
 value=json.loads(sys.stdin.readline())
 with requests.open('a') as stream:
  stream.write(json.dumps(dict(id=value.get('id'),method=value['method'],params=value.get('params')))+'\n')
 return value
assert receive()['method']=='initialize'
send(dict(id=1,result={}))
assert receive()['method']=='initialized'
resume=receive()
assert resume['method']=='thread/resume'
assert resume['params']['threadId']=='thread-one'
assert resume['params']['excludeTurns'] is True
send(STARTED)
assert receive()['method']=='turn/start'
def usage(thread,turn,used):
 send(dict(method='thread/tokenUsage/updated',params=dict(threadId=thread,turnId=turn,
  tokenUsage=dict(last=dict(totalTokens=used),modelContextWindow=100))))
usage('foreign-thread' if MODE=='foreign-thread' else 'thread-one','previous-turn',90)
if MODE=='foreign-thread':
 time.sleep(.2)
 raise SystemExit(0)
usage('thread-one','turn-one',80)
response=dict(id=3,result=dict(turn=dict(id='turn-one',items=[],status='inProgress')))
notice=dict(method='turn/started',params=dict(threadId='thread-one',
 turn=dict(id='turn-one',items=[],status='inProgress')))
send(response if FIRST=='response' else notice)
usage('thread-one','foreign-turn' if MODE=='foreign-turn' else 'turn-one',42)
if MODE=='foreign-turn':
 time.sleep(.2)
 raise SystemExit(0)
send(notice if FIRST=='response' else response)
send(dict(method='item/completed',params=dict(threadId='thread-one',turnId='turn-one',
 item=dict(type='agentMessage',text='{"summary":"Fixture original"}',phase='final_answer'))))
if MODE=='no-terminal':raise SystemExit(0)
until=time.monotonic()+5
while not pathlib.Path(GATE).exists():
 if time.monotonic()>until:raise SystemExit(8)
 time.sleep(.01)
send(dict(method='turn/completed',params=dict(threadId='thread-one',
 turn=dict(id='turn-one',items=[],status='completed'))))
'''


@contextmanager
def adapter(tmp_path, *, first='response', mode='complete'):
    d=declaration(tmp_path,resumed='thread-one')
    launcher=tmp_path/'codex'
    requests=tmp_path/'requests.jsonl';gate=tmp_path/'terminal-gate'
    launcher.write_text('#!'+sys.executable+'\n'+
        '\n'.join(name+'='+repr(value) for name,value in dict(
            REQUESTS=str(requests),GATE=str(gate),MODE=mode,FIRST=first,STARTED=started(d)).items())+
        '\n'+APP_SERVER)
    launcher.chmod(0o700)
    argv=[sys.executable,rpc.__file__,'--',str(launcher),'exec','--model','fixture','-C',str(tmp_path),
        '--sandbox','workspace-write','--output-last-message',str(d['pending']),
        '-c','model_reasoning_effort="high"','-c','service_tier="priority"','resume','thread-one','-']
    process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,
        start_new_session=True)
    process.stdin.write(b'Private deterministic request');process.stdin.close()
    events=[]
    def event():
        ready,_,_=select.select([process.stdout],[],[],5)
        assert ready,'Adapter did not publish its bounded transport event'
        line=process.stdout.readline()
        assert line,'Adapter closed before the expected event'
        value=json.loads(line);events.append(value);return value
    try:
        yield process,event,events,d['pending'],requests,gate
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
        process.wait(timeout=3)
        process.stdout.close();process.stderr.close()


def request_methods(path):
    return [json.loads(line)['method'] for line in path.read_text().splitlines()]


@pytest.mark.process_e2e
@pytest.mark.parametrize('first',['response','notification'])
def test_same_thread_resume_context_before_binding_requires_original_terminal(tmp_path,first):
    with adapter(tmp_path,first=first) as (process,event,events,pending,requests,gate):
        while event()['type']!='item.completed':pass
        assert not pending.exists() and process.poll() is None
        assert [row['type'] for row in events]==[
            'thread.started','turn.started','context.reported','item.completed']
        thread=events[0]
        assert thread['thread_id']==thread['requested_thread_id']=='thread-one'
        assert thread['rpc_method']=='thread/resume'
        assert events[2]['context']['total_tokens']==42
        assert events[2]['context']['turn_id']=='turn-one'
        gate.touch()
        assert event()==dict(type='turn.completed',native_terminal=True)
        assert process.wait(timeout=3)==0
        assert json.loads(pending.read_text())==dict(summary='Fixture original')
        methods=request_methods(requests)
        assert methods==['initialize','initialized','thread/resume','turn/start']
        assert 'Private deterministic request' not in json.dumps(events)


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode,phase,code,bound',[
    ('foreign-thread','context_report','thread_identity_changed',False),
    ('foreign-turn','context_report','turn_identity_changed',True),
    ('no-terminal','event_stream','native_terminal_unreported',True)])
def test_resume_process_rejection_or_missing_terminal_never_replays(tmp_path,mode,phase,code,bound):
    with adapter(tmp_path,mode=mode) as (process,event,events,pending,requests,gate):
        while (value:=event())['type']!='transport.diagnostic':pass
        assert value['diagnostic']==dict(phase=phase,code=code,
            original_call_state='unknown',thread_bound=True,turn_bound=bound)
        assert process.wait(timeout=3)==1
        assert not pending.exists()
        assert not any(row['type']=='turn.completed' for row in events)
        assert request_methods(requests).count('thread/resume')==1
        assert request_methods(requests).count('turn/start')==1
