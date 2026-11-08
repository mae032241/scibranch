"""Accepted consultation plans cross real Backend/stdio boundaries using fake RPC.

The executable below is an engineering fixture: it never contacts a provider,
loads authentication, runs scientific work, or requires installed Codex/Pi.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from scidiscovery.research_loop import backends, budget_notice, continuation, contracts as c
from test_consultation_continuity import PROFILE, original
from test_native_app_server import started


FAKE_NATIVE = r'''
import json,os,pathlib,sys
fixture=json.loads(os.environ['SCID_CONSULTATION_FIXTURE'])
requests=pathlib.Path(fixture['requests'])
pathlib.Path(fixture['launch']).write_text(json.dumps(dict(
    argv=sys.argv[1:],cwd=os.getcwd(),home=os.environ.get('CODEX_HOME'))))
def receive():
    line=sys.stdin.readline()
    if not line:return None
    value=json.loads(line)
    with requests.open('a') as stream:stream.write(json.dumps(value)+'\n')
    return value
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
assert receive()['method']=='initialize'
send(dict(id=1,result={}))
assert receive()['method']=='initialized'
thread=receive()
assert thread['method'] in ('thread/start','thread/resume')
send(fixture['started'])
if fixture['reject']:
    while receive() is not None:pass
else:
    assert receive()['method']=='turn/start'
    metadata=pathlib.Path(fixture['metadata'])
    receipt=json.loads(metadata.read_text())
    receipt.update(session_id=fixture['thread'],transcript_path=fixture['transcript'],
        acknowledged=1,compactions=fixture['compactions'],reported_calls=1,
        calls=dict(completed=1,failed=0,cancelled=0),tokens=fixture['tokens'])
    metadata.write_text(json.dumps(receipt))
    turn=dict(id=fixture['turn'],items=[],status='inProgress')
    send(dict(id=3,result=dict(turn=turn)))
    send(dict(method='item/completed',params=dict(threadId=fixture['thread'],turnId=fixture['turn'],
        item=dict(type='agentMessage',phase='final_answer',text='{"summary":"Synthetic consultation fixture"}'))))
    send(dict(method='turn/completed',params=dict(threadId=fixture['thread'],
        turn=dict(id=fixture['turn'],items=[],status='completed'))))
    while receive() is not None:pass
'''


def invoke(backend, profile, work, home, *, thread, turn, tokens, compactions,
           session=None, context_state=None, reported_cwd=None):
    """Submit once through the actual adapter, and consume its public events."""
    (work/'work').mkdir(parents=True)
    schema=work/'schema.json';schema.write_text('{}')
    pending=work/'pending.json'
    meter=backend.meter(home,work,session=session,context_state=context_state)
    backend.configure_observation(meter,work,0,thread,dict(status='available'),required=True)
    args=backend.launch(profile,work,pending,schema,'Current consultation role instructions')
    if session is not None:
        args,_=backend.resume(args,profile,work,pending,schema,session)
    # Reuse the app-server fixture's effective launch receipt. The parent later
    # checks the real wire request independently of these supplied responses.
    response=started(dict(model=profile['model'],cwd=str(reported_cwd or work)),thread=thread)
    transcript=Path(session['path']) if session is not None else home/'sessions/advisor.jsonl'
    if session is None:transcript.write_bytes(b'opaque independent advice transcript\n')
    requests=work/'requests.jsonl';launch=work/'launch.json'
    fixture=dict(requests=str(requests),launch=str(launch),started=response,thread=thread,turn=turn,
        metadata=str(budget_notice.metadata_path(work,0)),transcript=str(transcript),
        tokens=tokens,compactions=compactions,reject=reported_cwd is not None)
    environment=dict(PATH='/usr/bin:/bin',HOME=str(home),LANG='C.UTF-8',
        SCID_CONSULTATION_FIXTURE=json.dumps(fixture))
    backend.apply_home(environment,home,isolated=True)
    process=subprocess.Popen(backend.transport_args(args),cwd=work,env=environment,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        output,errors=process.communicate(backend.request('Current task and independent advice',
            response_schema={}),timeout=8)
    finally:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        process.wait(timeout=3)
    events=[json.loads(line) for line in output.splitlines()]
    for value in events:backend.observe(meter,backend.event(value))
    backend.poll_observation(meter)
    observation=backend.finish_observation(meter,required=True)
    return dict(code=process.returncode,errors=errors,events=events,meter=meter,observation=observation,
        requests=[json.loads(line) for line in requests.read_text().splitlines()],
        launch=json.loads(launch.read_text()),pending=pending)


@pytest.mark.process_e2e
@pytest.mark.parametrize('reply',['success','source-workspace'])
def test_consultation_successor_resumes_only_requester_with_current_workspace(tmp_path,reply):
    root,job,settings,session,state=original(tmp_path)
    launcher=tmp_path/'codex'
    launcher.write_text('#!'+sys.executable+'\n'+FAKE_NATIVE);launcher.chmod(0o700)
    settings=dict(settings,native_launcher_argv=[str(launcher)])
    backend=backends.selected(settings)
    source=c.workspace(root,1);work=c.workspace(root,3);home=Path(session['home'])
    originals=[Path(session['path']),root/'native-evidence/001/native-intent.json',
        root/'native-evidence/001/native-outcome.json',root/'native-evidence/001/result-identity.json',
        root/'native-evidence/002/result-identity.json']
    retained={path:path.read_bytes() for path in originals}

    # An independent A uses a new thread even though the native home is shared.
    advice_tokens=dict(input_tokens=100,cached_input_tokens=20,output_tokens=30)
    advice=invoke(backend,dict(PROFILE,model='explicit-advisor'),c.workspace(root,2),home,
        thread='independent-advisor',turn='advice-turn',tokens=advice_tokens,compactions=2)
    assert advice['code']==0,advice['errors']
    assert [row['method'] for row in advice['requests']]==[
        'initialize','initialized','thread/start','turn/start']
    assert advice['observation']['thread_id']=='independent-advisor'
    assert advice['observation']['baseline_tokens'] is None
    assert advice['observation']['tokens']==advice_tokens

    plan=continuation.consultation_launch(root,job,settings=settings,backend=backend,profile=PROFILE)
    consumed=continuation.consultation_intent(job,plan,settings=settings,backend=backend)
    assert consumed['session']==session and consumed['worker_context_state']==state
    assert consumed['worker_continuation']['mode']=='resume_requested'
    delta=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    result=invoke(backend,PROFILE,work,home,thread=session['thread_id'],turn='successor-turn',
        tokens=delta,compactions=1,session=consumed['session'],context_state=consumed['worker_context_state'],
        reported_cwd=source if reply=='source-workspace' else None)
    methods=[row['method'] for row in result['requests']]
    assert methods[:3]==['initialize','initialized','thread/resume']
    request=result['requests'][2]['params']
    assert request['threadId']=='original-worker' and request['excludeTurns'] is True
    assert request['cwd']==str(work) and request['model']==PROFILE['model']
    assert request['sandbox']=='workspace-write' and request['approvalPolicy']=='never'
    assert request['serviceTier']==PROFILE['service_tier']
    assert request['developerInstructions']=='Current consultation role instructions'
    assert result['launch']['cwd']==str(work) and result['launch']['home']==str(home)
    assert result['launch']['argv'][0:2]==['-C',str(work)]
    assert 'sandbox_workspace_write={"writable_roots"=[],"exclude_tmpdir_env_var"=false,"exclude_slash_tmp"=false,"network_access"=false}' in result['launch']['argv']
    assert 'features.multi_agent=false' in result['launch']['argv']
    assert 'web_search="disabled"' in result['launch']['argv']

    if reply=='source-workspace':
        assert result['code']==1
        assert methods==['initialize','initialized','thread/resume']
        observed=next(row for row in result['events'] if row['type']=='thread.observed')
        assert observed['thread_id']=='original-worker' and observed['mismatch_fields']==['cwd']
        diagnostic=next(backend.event(row)['transport_diagnostic'] for row in result['events']
            if row['type']=='transport.diagnostic')
        assert diagnostic['code']=='launch_binding_mismatch'
        assert diagnostic['original_call_state']=='not_submitted'
        assert not result['pending'].exists()
        assert not any(row['type']=='turn.started' for row in result['events'])
    else:
        assert result['code']==0,result['errors']
        assert methods==['initialize','initialized','thread/resume','turn/start']
        turn_request=result['requests'][3]['params']
        assert turn_request['threadId']=='original-worker'
        assert turn_request['effort']==PROFILE['reasoning_effort']
        assert turn_request['model']==PROFILE['model'] and turn_request['serviceTier']==PROFILE['service_tier']
        thread_event=result['events'][0]
        assert thread_event['rpc_method']=='thread/resume' and thread_event['workspace']==str(work)
        assert thread_event['effective_profile']==PROFILE
        assert [row['turn_id'] for row in result['events'] if row['type']=='turn.started']==['successor-turn']
        observation=result['observation']
        assert observation['tokens']==delta and observation['baseline_tokens']==state['cursor']['terminal_tokens']
        assert observation['metadata_delivery']==dict(acknowledged=1,pending=False,fault=None)
        assert observation['context_windows']['windows_started']==4
        assert observation['context_windows']['cursor']['compactions']==1
        assert observation['session_binding']==dict(session,workspace=str(work))
        terminal=backend.event(result['events'][-1])
        assert terminal['native_terminal'] is True
        assert backend.terminal_usage(terminal,result['meter'])==delta
        # Polling the same acknowledged receipt cannot re-add historical W/A
        # costs or the already consumed context windows.
        assert backend.poll_observation(result['meter'])['tokens']==delta
        assert backend.poll_observation(result['meter'])['context_windows']['windows_started']==4
        assert json.loads(result['pending'].read_text())==dict(summary='Synthetic consultation fixture')
    assert {path:path.read_bytes() for path in originals}==retained
