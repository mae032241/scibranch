"""Mechanical provider transport checks; no model, auth or native-home access."""
import io
import hashlib
import json
import time

import pytest

from scidiscovery.research_loop import backends
from scidiscovery.research_loop import native_app_server as rpc
from scidiscovery.research_loop.native_usage import context


def declaration(tmp_path, *, resumed=None):
    return dict(command=['codex','app-server','--stdio'], model='fixture', cwd=str(tmp_path),
        sandbox='workspace-write', pending=tmp_path/'result.json', schema=None, resumed=resumed,
        config=dict(model_reasoning_effort='high',service_tier='priority',
            sandbox_workspace_write=dict(network_access=False,writable_roots=[])))


def started(d, thread='thread-one'):
    return dict(id=2,result=dict(thread=dict(id=thread,turns=[]),model=d['model'],cwd=d['cwd'],
        approvalPolicy='never',sandbox=dict(type='workspaceWrite',writableRoots=[],networkAccess=False),
        reasoningEffort='high',serviceTier='priority'))


def ready(tmp_path, *, resumed=None):
    emitted, sent = [], []
    d=declaration(tmp_path,resumed=resumed)
    transport=rpc.Transport(d,emit=emitted.append,send=sent.append)
    transport.initialize()
    transport.receive(dict(id=1,result={}), 'Private prompt')
    transport.receive(started(d,thread=resumed or 'thread-one'),'Private prompt')
    transport.receive(dict(id=3,result=dict(turn=dict(id='turn-one',items=[],status='inProgress'))),'Private prompt')
    return transport,d,emitted,sent


def test_current_turn_advice_is_queued_once_without_a_new_turn(tmp_path):
    transport, declaration, events, sent = ready(tmp_path)
    request = dict(input_id='a' * 32, payload='Compare the alternate explanation.',
        payload_sha256=hashlib.sha256(b'Compare the alternate explanation.').hexdigest(),
        binding=dict(channel_id='b' * 32, thread='thread-one', turn='turn-one'))
    transport.queue_input(request)
    request_id = sent[-1]['id']
    assert sent[-1]['method'] == 'turn/steer'
    assert sent[-1]['params']['expectedTurnId'] == 'turn-one'
    raw = (json.dumps(dict(id=request_id, result=dict(turnId='turn-one',
        ignored=dict(tool_body='SECRET_OPAQUE')))) + '\n').encode()
    for selected in rpc.native_lines(io.BytesIO(raw)):
        transport.receive(selected, 'Private prompt')
    transport.queue_input(request)
    assert [event['status'] for event in events if event['type'] == 'turn.input'] == ['submitting', 'accepted']
    assert [item['method'] for item in sent].count('turn/steer') == 1
    assert [item['method'] for item in sent].count('turn/start') == 1
    assert transport.turn == 'turn-one' and not transport.finished
    assert not declaration['pending'].exists()
    wire = (json.dumps(events[-1]) + '\n').encode()
    selected = backends.CodexEvents().feed(wire)
    assert backends.Codex.event(None, selected[0]) == dict(turn_input=events[-1])


def test_real_typed_latest_context_reaches_neutral_meter_without_cumulative_ratio(tmp_path):
    transport,d,events,sent=ready(tmp_path)
    transport.receive(dict(method='thread/tokenUsage/updated',params=dict(threadId=transport.thread,
        turnId=transport.turn,tokenUsage=dict(last=dict(totalTokens=42),
            total=dict(totalTokens=900000),modelContextWindow=100))), 'Private prompt')
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    meter=adapter.meter(tmp_path,tmp_path,context_state=None)
    meter.bind_thread(transport.thread);meter.bind_turn(transport.turn)
    normalized=backends.Codex.event(None,events[-1])
    meter.observe_context(normalized['context'])
    observation=meter.snapshot()
    assert observation['context']['ratio']==.42
    assert observation['tokens']==dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
    assert observation['context']['current_window'] is True
    assert 'Private prompt' not in json.dumps(events)
    assert sent[0]['method']=='initialize'
    assert [item['method'] for item in sent].count('turn/start')==1


def test_optional_unknown_capacity_and_mismatched_identity_never_make_percentage(tmp_path):
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    meter=adapter.meter(tmp_path,tmp_path,context_state=None)
    meter.bind_thread('thread-one');meter.bind_turn('turn-one')
    for capacity,thread in [(None,'thread-one'),(100,'foreign-thread')]:
        meter.observe_context(dict(status='last_reported',total_tokens=90,model_context_window=capacity,
            thread_id=thread,turn_id='turn-one',current_window=True))
        assert meter.snapshot()['context']==dict(status='unavailable')


def test_compaction_clears_sample_and_foreign_turn_cannot_restore_it(tmp_path):
    meter=backends.EventUsage()
    meter.bind_thread('thread-one');meter.bind_turn('turn-one')
    sample=dict(status='last_reported',total_tokens=85,model_context_window=100,
        thread_id='thread-one',turn_id='turn-one',current_window=True)
    meter.observe_context(sample)
    assert context(meter.snapshot()['context'])['ratio']==.85
    meter.invalidate_context()
    assert meter.snapshot()['context']==dict(status='unavailable')
    meter.observe_context(dict(sample,turn_id='old-turn'))
    assert meter.snapshot()['context']==dict(status='unavailable')


def test_uncertain_turn_submission_is_not_repeated_and_no_terminal_is_invented(tmp_path):
    transport,d,events,sent=ready(tmp_path)
    with pytest.raises(ValueError,match='submission response'):
        transport.receive(dict(id=3,error=dict(code=-32600,message='Private body')),'Private prompt')
    assert [item['method'] for item in sent].count('turn/start')==1
    assert not transport.finished
    assert not any(item['type']=='turn.completed' for item in events)
    assert not d['pending'].exists()


@pytest.mark.parametrize('change',[
    dict(model='different'),dict(cwd='/different'),dict(approvalPolicy='on-request'),
    dict(reasoningEffort='low'),dict(serviceTier='default'),
    dict(sandbox=dict(type='workspaceWrite',writableRoots=['/private'],networkAccess=False)),
    dict(sandbox=dict(type='workspaceWrite',writableRoots=[],networkAccess=True))])
def test_native_permission_profile_mismatch_stops_before_turn_start(tmp_path,change):
    d=declaration(tmp_path);sent=[];events=[]
    transport=rpc.Transport(d,emit=events.append,send=sent.append)
    transport.receive(dict(id=1,result={}), 'Private prompt')
    message=started(d);message['result'].update(change)
    with pytest.raises(ValueError):transport.receive(message,'Private prompt')
    assert not any(item['method']=='turn/start' for item in sent)
    assert events[-1]['mismatch_fields']
    assert set(events[-1]['mismatch_fields'])<=set(['history','cwd','model','approval_policy','resume_thread',
        'reasoning_effort','service_tier','sandbox_shape','sandbox_type','sandbox_network','sandbox_roots',
        'sandbox_slash_tmp','sandbox_tmpdir'])


def test_explicit_default_tier_accepts_present_native_null_and_preserves_wire_fact(tmp_path):
    d=declaration(tmp_path);d['config']['service_tier']='default';sent=[];events=[]
    transport=rpc.Transport(d,emit=events.append,send=sent.append)
    transport.receive(dict(id=1,result={}), 'Private prompt')
    message=started(d);message['result']['serviceTier']=None
    transport.receive(message,'Private prompt')
    assert any(item['method']=='turn/start' for item in sent)
    event=events[-1]
    assert event['requested_profile']['service_tier']=='default'
    assert event['effective_profile']['service_tier']=='default'
    assert event['service_tier_wire_classification']=='null'
    outcome=dict(thread_id=transport.thread,native_identity=dict(participant_scope=rpc.SCOPE))
    intent=dict(native_profile=event['requested_profile'])
    assert backends.Codex.validate_participant_event(event,outcome,intent,tmp_path)==('codex_exec',transport.thread)
    with pytest.raises(ValueError):
        backends.Codex.validate_participant_event(dict(event,service_tier_wire_classification='priority'),outcome,intent,tmp_path)


@pytest.mark.parametrize('requested',['priority','flex','default'])
@pytest.mark.parametrize('wire',['null','missing','other'])
def test_tier_projection_never_treats_missing_or_nonstandard_tier_as_default(tmp_path,requested,wire):
    if requested=='default' and wire=='null':return
    d=declaration(tmp_path);d['config']['service_tier']=requested;sent=[];events=[]
    transport=rpc.Transport(d,emit=events.append,send=sent.append)
    transport.receive(dict(id=1,result={}), 'Private prompt')
    message=started(d)
    if wire=='missing':message['result'].pop('serviceTier')
    else:message['result']['serviceTier']=None if wire=='null' else 'other'
    with pytest.raises(ValueError):transport.receive(message,'Private prompt')
    assert not any(item['method']=='turn/start' for item in sent)
    assert 'service_tier' in events[-1]['mismatch_fields']


def test_inherited_tier_does_not_become_an_explicit_default(tmp_path):
    d=declaration(tmp_path);d['config'].pop('service_tier');sent=[];events=[]
    transport=rpc.Transport(d,emit=events.append,send=sent.append)
    transport.receive(dict(id=1,result={}), 'Private prompt')
    message=started(d);message['result']['serviceTier']=None
    transport.receive(message,'Private prompt')
    assert events[-1]['requested_profile']['service_tier'] is None
    assert events[-1]['effective_profile']['service_tier'] is None
    assert 'serviceTier' not in next(item for item in sent if item['method']=='turn/start')['params']


def test_exact_resume_has_no_history_and_cannot_change_native_identity(tmp_path):
    transport,d,events,sent=ready(tmp_path,resumed='thread-one')
    request=next(item for item in sent if item['method']=='thread/resume')
    assert request['params']['excludeTurns'] is True
    assert request['params']['threadId']=='thread-one'
    assert events[0]['requested_thread_id']=='thread-one'
    assert events[0]['transport']=='codex_app_server'
    assert events[0]['rpc_method']=='thread/resume'


@pytest.mark.parametrize('first_binding',['response','notification'])
def test_resume_usage_before_current_turn_binding_stays_unqualified(tmp_path,first_binding):
    d=declaration(tmp_path,resumed='thread-one');events=[];sent=[]
    transport=rpc.Transport(d,emit=events.append,send=sent.append)
    transport.initialize()
    transport.receive(dict(id=1,result={}), 'Private prompt')
    transport.receive(started(d),'Private prompt')

    def receive_wire(message):
        wire=(json.dumps(message)+'\n').encode()
        for selected in rpc.native_lines(io.BytesIO(wire)):
            transport.receive(selected,'Private prompt')

    def usage(turn,used):
        return dict(method='thread/tokenUsage/updated',params=dict(threadId='thread-one',turnId=turn,
            tokenUsage=dict(last=dict(totalTokens=used),modelContextWindow=100)))

    # A resumed thread can report its previous usage while turn/start is
    # awaiting its response and turn/started notification. Neither that sample
    # nor an early sample naming the eventual new turn may bind the new turn.
    receive_wire(usage('previous-turn',90))
    receive_wire(usage('turn-one',80))
    assert transport.turn is None
    assert [event['type'] for event in events]==['thread.started']
    assert not transport.finished and not d['pending'].exists()

    response=dict(id=3,result=dict(turn=dict(id='turn-one',items=[],status='inProgress')))
    notification=dict(method='turn/started',params=dict(threadId='thread-one',
        turn=dict(id='turn-one',items=[],status='inProgress')))
    first,second=(response,notification) if first_binding=='response' else (notification,response)
    receive_wire(first)
    assert transport.turn=='turn-one'
    assert [event['type'] for event in events]==['thread.started','turn.started']
    receive_wire(usage('turn-one',42))
    assert events[-1]['context']['total_tokens']==42
    assert events[-1]['context']['turn_id']=='turn-one'
    assert events[-1]['context']['current_window'] is True
    receive_wire(second)
    assert sum(event['type']=='turn.started' for event in events)==1
    assert not any(event['type']=='turn.completed' for event in events)
    assert not d['pending'].exists()

    receive_wire(dict(method='item/completed',params=dict(threadId='thread-one',turnId='turn-one',
        item=dict(type='agentMessage',text='{"summary":"Fixture"}',phase='final_answer'))))
    receive_wire(dict(method='turn/completed',params=dict(threadId='thread-one',
        turn=dict(id='turn-one',items=[],status='completed'))))
    assert transport.finished and transport.completed
    assert json.loads(d['pending'].read_text())==dict(summary='Fixture')
    assert [item['method'] for item in sent].count('thread/resume')==1
    assert [item['method'] for item in sent].count('turn/start')==1


@pytest.mark.parametrize('bound,thread,turn,error',[
    (False,'foreign-thread','previous-turn','thread changed'),
    (True,'foreign-thread','turn-one','thread changed'),
    (True,'thread-one','foreign-turn','turn changed')])
def test_resume_context_identity_mismatch_still_stops(tmp_path,bound,thread,turn,error):
    d=declaration(tmp_path,resumed='thread-one');events=[];sent=[]
    transport=rpc.Transport(d,emit=events.append,send=sent.append)
    transport.receive(dict(id=1,result={}), 'Private prompt')
    transport.receive(started(d),'Private prompt')
    if bound:
        transport.receive(dict(id=3,result=dict(turn=dict(id='turn-one'))),'Private prompt')
    with pytest.raises(ValueError,match=error):
        transport.receive(dict(method='thread/tokenUsage/updated',params=dict(threadId=thread,turnId=turn,
            tokenUsage=dict(last=dict(totalTokens=42),modelContextWindow=100))),'Private prompt')
    assert not any(event['type'] in {'context.reported','turn.completed'} for event in events)
    assert not transport.finished and not d['pending'].exists()
    assert [item['method'] for item in sent].count('turn/start')==1


def test_new_transport_proof_binds_frozen_profile_and_keeps_same_thread_identity(tmp_path):
    transport,d,events,sent=ready(tmp_path)
    event=events[0]
    intent=dict(native_profile=dict(model='fixture',reasoning_effort='high',service_tier='priority'))
    outcome=dict(thread_id=transport.thread,native_identity=dict(participant_scope=rpc.SCOPE))
    verifier=backends.participant_provider(outcome['native_identity'])
    assert verifier.authorship_proof_kind(outcome['native_identity'])=='native_app_server_hooks_v1'
    assert verifier.validate_participant_event(event,outcome,intent,tmp_path)==('codex_exec',transport.thread)
    legacy=dict(outcome,native_identity=dict(participant_scope='codex_exec_single_agent_v1'))
    assert verifier.validate_participant_event(dict(type='thread.started',thread_id=transport.thread),legacy,{},tmp_path)==('codex_exec',transport.thread)
    with pytest.raises(ValueError):verifier.validate_participant_event(event,outcome,{},tmp_path)
    with pytest.raises(ValueError):verifier.validate_participant_event(dict(event,requested_thread_id='foreign'),outcome,intent,tmp_path)
    with pytest.raises(ValueError):verifier.validate_participant_event(dict(event,effective_profile=dict(model='other')),outcome,intent,tmp_path)


def test_new_authorship_proof_keeps_exact_intent_and_process_binding(tmp_path):
    from scidiscovery.research_loop import formal_review,qualification
    transport,d,events,sent=ready(tmp_path)
    event=events[0]
    session=dict(source='native_metadata_hooks',workspace=str(tmp_path),thread_id=transport.thread)
    outcome=dict(thread_id=transport.thread,native_identity=dict(participant_scope=rpc.SCOPE,session=session),
        native_thread_event=event,budget_notification=dict(status='available'))
    intent=dict(task_ref=dict(path='jobs/001.json',sha256='original-task'),
        native_profile=dict(model='fixture',reasoning_effort='high',service_tier='priority'))
    row=dict(attempt=0,intent=dict(path='native-intent.json'),outcome=dict(path='native-outcome.json'),
        proof_kind='native_app_server_hooks_v1',session=dict(session,thread_event=event))
    metadata=dict(version=1,kind=row['proof_kind'],intent=row['intent'],outcome=row['outcome'],session=row['session'])
    process=dict(task_ref=intent['task_ref'])
    assert formal_review._metadata_participant(row,metadata,outcome,intent,process,tmp_path,row['intent'])==('codex_exec',transport.thread)
    with pytest.raises(qualification.IntegrityError):
        formal_review._metadata_participant(row,metadata,outcome,intent,dict(task_ref={'path':'other'}),tmp_path,row['intent'])


def test_opaque_tool_and_input_events_never_enter_json_decoder(monkeypatch):
    monkeypatch.setattr(rpc.json,'loads',lambda value:pytest.fail('Opaque event decoded'))
    opaque=(b'{"method":"item/completed","params":{"threadId":"thread-one","turnId":"turn-one",'
        b'"item":{"type":"mcpToolCall","arguments":"SECRET","result":"SECRET"}}}\n')
    assert list(rpc.native_lines(io.BytesIO(opaque)))==[]
    assert list(rpc.native_lines(io.BytesIO(b'{"method":"item/commandExecution/outputDelta","params":{"delta":"SECRET"}}\n')))==[]


def test_metadata_projection_never_decodes_preview_or_opaque_error(monkeypatch):
    original=json.loads
    def guarded(value):
        assert b'SECRET' not in value if isinstance(value,bytes) else 'SECRET' not in value
        return original(value)
    monkeypatch.setattr(rpc.json,'loads',guarded)
    raw=b'{"id":2,"result":{"thread":{"id":"thread-one","turns":[],"preview":"SECRET"},"extra":{"body":"SECRET"}}}'
    assert rpc.metadata_json(raw)==dict(id=2,result=dict(thread=dict(id='thread-one',turns=[])))


def test_terminal_requires_genuine_completed_turn_and_public_final(tmp_path):
    transport,d,events,sent=ready(tmp_path)
    transport.receive(dict(method='item/completed',params=dict(threadId=transport.thread,turnId=transport.turn,
        item=dict(type='agentMessage',id='msg-one',text='{"summary":"Fixture"}',phase='final_answer'))),'Private prompt')
    assert not d['pending'].exists()
    transport.receive(dict(method='turn/completed',params=dict(threadId=transport.thread,
        turn=dict(id=transport.turn,items=[],status='completed'))),'Private prompt')
    assert transport.finished and json.loads(d['pending'].read_text())==dict(summary='Fixture')
    assert events[-1]==dict(type='turn.completed',native_terminal=True)


def test_terminal_hydration_skips_opaque_items_and_preserves_exact_metadata(monkeypatch):
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(rpc.json,'loads',guarded)
    raw=(b'{"method":"turn/completed","params":{"threadId":"thread-one","turn":{"id":"turn-one","items":[{"type":"mcpToolCall","arguments":"'
        +b'SECRET_BODY'*10000+b'"}],"status":"completed","error":{"message":"SECRET_ERROR"}}}}\n')
    assert rpc.selected_line(raw) is True
    assert list(rpc.native_lines(io.BytesIO(raw)))==[dict(method='turn/completed',params=dict(threadId='thread-one',
        turn=dict(id='turn-one',status='completed',error={}))) ]


def test_transport_diagnostics_have_only_fixed_stage_and_code(monkeypatch,capsys):
    def failed(*args,**kwargs):
        error=ValueError('SECRET_NATIVE_BODY /private/binding')
        error._scid_transport_stage='turn_completion'
        raise error
    monkeypatch.setattr(rpc,'run',failed)
    monkeypatch.setattr(rpc.sys,'stdin',io.StringIO('Private prompt'))
    assert rpc.main([])==1
    error=capsys.readouterr().err
    assert 'SECRET' not in error and '/private' not in error
    assert json.loads(error.split(': ',1)[1])==dict(error_stage='turn_completion',error_code='native_projection_invalid')


def test_new_backend_must_implement_full_explicit_contract():
    class Incomplete(backends.Backend):
        name='fixture'
    with pytest.raises(TypeError):Incomplete()
    assert issubclass(backends.Codex,backends.Backend)
    assert issubclass(backends.Protocol,backends.Backend)


def test_required_window_capability_is_refused_before_native_submission(tmp_path):
    from scidiscovery.research_loop.native_usage import MetadataUnavailable
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    assert adapter.observation_capability('context')['status']=='supported'
    assert adapter.observation_capability('compactions')['status']=='unsupported'
    meter=adapter.meter(tmp_path,tmp_path)
    with pytest.raises(MetadataUnavailable,match='compaction capability'):
        adapter.configure_observation(meter,tmp_path,0,None,adapter.initial_notice(None),required=True)
    assert meter.snapshot()['context']==dict(status='unavailable')


def test_neutral_native_call_metadata_deduplicates_without_billing_again(tmp_path):
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    meter=adapter.meter(tmp_path,tmp_path)
    adapter.configure_observation(meter,tmp_path,0,None,adapter.initial_notice(None),required=False)
    event=adapter.event(dict(type='native_call',call_id='request-one',status='completed',
        input='Private body',output='Private body'))
    adapter.observe(meter,event);adapter.observe(meter,event)
    observation=adapter.snapshot_observation(meter)
    assert observation['calls']==dict(completed=1,failed=0,cancelled=0)
    assert observation['tokens']==dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
    assert 'Private body' not in json.dumps(event)


def test_exec_argv_translation_preserves_frozen_native_configs(tmp_path):
    settings=dict(native_launcher_argv=['codex'],native_options=dict(workspace='workspace-write',network=False,
        web='disabled',subagents=False))
    adapter=backends.Codex(settings)
    argv=adapter.launch(dict(model='fixture',reasoning_effort='high',service_tier='priority'),tmp_path,
        tmp_path/'result',tmp_path/'schema','Original developer instructions')
    d=rpc.invocation(argv)
    assert d['config']['developer_instructions']=='Original developer instructions'
    assert d['config']['sandbox_workspace_write']['network_access'] is False
    assert d['config']['features']['multi_agent'] is False
    assert d['ignore_user_config'] is True
    assert '--ignore-user-config' not in d['command']
    assert d['model']=='fixture'
    with pytest.raises(ValueError,match='Unsupported'):rpc.invocation([*argv[:-1],'--unknown',argv[-1]])


@pytest.mark.parametrize('resumed',[None,'original-thread'])
@pytest.mark.parametrize('qualified',[False,True])
def test_only_qualified_hook_bypass_is_forwarded_to_native_thread_config(tmp_path,resumed,qualified):
    d=declaration(tmp_path,resumed=resumed);d['trusted_hook_bypass']=qualified
    transport=rpc.Transport(d,emit=lambda value:None,send=lambda value:None)
    params=transport._thread_params()
    if qualified:assert params['config']==dict(bypass_hook_trust=True)
    else:assert 'config' not in params
    assert params.get('threadId')==resumed
    if resumed:assert params['excludeTurns'] is True


def terminal_receipt(text):
    data=text.encode()
    meter=dict(input_tokens=10,cached_input_tokens=0,output_tokens=2)
    facts=dict(version=1,source='native_owner_v1',
        failures=[dict(category='temporary',event_type='error',retry_after_seconds=None,not_accepted=False)],
        completed_before=1,terminal=meter,thread='thread-one',activity=True,ambiguous=False,
        final=dict(sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data)))
    outcome=dict(native_backend='codex',stop_reason='native_failure_event',returncode=0,native_started=True,
        cleanup='observed_group_cleaned',terminal_observed=True,thread_id='thread-one',native_activity_observed=True,
        metering=dict(tokens=meter,live_reason=None),terminal_evidence=facts)
    return outcome,meter


def test_terminal_reconciliation_uses_small_owner_receipt_and_exact_final_bytes(tmp_path):
    from scidiscovery.research_loop import contracts
    text='{"summary":"Original science"}'
    outcome,meter=terminal_receipt(text)
    (tmp_path/'native-outcome.json').write_text(json.dumps(outcome))
    (tmp_path/'usage.json').write_text(json.dumps(meter))
    output=tmp_path/'science-output.json';output.write_text(text)
    assert not (tmp_path/'native.jsonl').exists()
    assert contracts.native_outcome(tmp_path)['stop_reason'] is None
    output.write_text(text+'changed')
    assert contracts.native_outcome(tmp_path)['stop_reason']=='native_failure_event'
    assert json.loads((tmp_path/'native-outcome.json').read_text())==outcome


def test_terminal_reconciliation_consumer_never_opens_originals(tmp_path,monkeypatch):
    from scidiscovery.research_loop import contracts
    outcome,meter=terminal_receipt('Original science')
    final=outcome['terminal_evidence']['final']
    evidence=dict(facts=outcome['terminal_evidence'],outputs=[final])
    monkeypatch.setattr('builtins.open',lambda *args,**kwargs:pytest.fail('Pure reconciliation opened a file'))
    assert contracts._native_outcome(tmp_path,outcome,meter=meter,terminal_evidence=evidence)['stop_reason'] is None
    assert contracts._native_outcome(tmp_path,outcome,meter=meter)['stop_reason']=='native_failure_event'


def test_legacy_terminal_projection_never_decodes_opaque_tool_body(tmp_path,monkeypatch):
    outcome,meter=terminal_receipt('Original science')
    outcome.pop('terminal_evidence')
    rows=[dict(type='thread.started',thread_id='thread-one'),
        dict(type='error',error=dict(code='rate_limit_exceeded',status=429)),
        dict(type='item.completed',item=dict(type='mcp_tool_call',arguments='SECRET_BODY',result='SECRET_BODY')),
        dict(type='item.completed',item=dict(type='agent_message',text='Original science')),
        dict(type='turn.completed',usage=meter)]
    (tmp_path/'native.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (tmp_path/'science-output.json').write_text('Original science')
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_BODY' not in value if isinstance(value,bytes) else 'SECRET_BODY' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    evidence=backends.Codex.acquire_terminal_evidence(tmp_path,outcome)
    assert evidence is not None
    assert evidence['facts']['terminal']==meter
    assert evidence['facts']['final']==evidence['outputs'][0]


def test_large_genuine_final_is_streamed_exactly_and_never_reuses_earlier_text(tmp_path):
    transport,d,events,sent=ready(tmp_path)
    transport.receive(dict(method='item/completed',params=dict(threadId=transport.thread,turnId=transport.turn,
        item=dict(type='agentMessage',id='earlier',text='Earlier final',phase='final_answer'))),'Private prompt')
    text=json.dumps(dict(summary=('Large α U0001f642 \\"\n'*20000),limitations=[]),ensure_ascii=False)
    row=dict(method='item/completed',params=dict(threadId=transport.thread,turnId=transport.turn,
        item=dict(type='agentMessage',id='final-large',text=text,phase='final_answer')))
    terminal=dict(method='turn/completed',params=dict(threadId=transport.thread,
        turn=dict(id=transport.turn,items=[],status='completed')))
    original=(json.dumps(row,ensure_ascii=True)+'\n'+json.dumps(terminal)+'\n').encode()
    try:
        for message in rpc.native_lines(io.BytesIO(original)):transport.receive(message,'Private prompt')
        assert d['pending'].read_bytes()==text.encode('utf-8')
        final_event=next(event for event in reversed(events) if event.get('item',{}).get('native_final'))
        assert final_event['item']['text_truncated'] is True
        assert backends.Codex.terminal_message(None,final_event) is None
        binding=backends.Codex.terminal_binding(None,final_event)
        assert binding==dict(sha256=hashlib.sha256(text.encode()).hexdigest(),size_bytes=len(text.encode()))
        assert max(len(json.dumps(event)) for event in events)<rpc.MAX_EVENT
    finally:transport.close()


def test_large_opaque_native_event_is_discarded_without_json_decode_and_keeps_terminal(monkeypatch):
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_BODY' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_BODY' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(rpc.json,'loads',guarded)
    opaque=(b'{"method":"item/completed","params":{"item":{"type":"mcpToolCall","arguments":"'
        +b'SECRET_BODY'*500000+b'"}}}\n')
    terminal=b'{"method":"turn/completed","params":{"threadId":"thread-one","turn":{"id":"turn-one","items":[],"status":"completed"}}}\n'
    messages=list(rpc.native_lines(io.BytesIO(opaque+terminal)))
    assert len(messages)==1 and messages[0]['method']=='turn/completed'


def test_nested_agent_tag_in_tool_arguments_cannot_select_the_tool_body(monkeypatch):
    monkeypatch.setattr(rpc,'assistant_message',lambda *args:pytest.fail('Tool body entered assistant parser'))
    monkeypatch.setattr(rpc.json,'loads',lambda *args:pytest.fail('Tool body entered JSON decoder'))
    row=b'{"method":"item/completed","params":{"item":{"type":"mcpToolCall","arguments":{"item":{"type":"agentMessage","text":"SECRET_BODY"}}}}}\n'
    assert list(rpc.native_lines(io.BytesIO(row)))==[]


def test_entire_large_final_is_hashed_without_original_file_quota(tmp_path):
    from scidiscovery.research_loop import contracts
    text='Complete original science\n'*20000
    outcome,meter=terminal_receipt(text)
    (tmp_path/'native-outcome.json').write_text(json.dumps(outcome))
    (tmp_path/'usage.json').write_text(json.dumps(meter))
    output=tmp_path/'science-output.json';output.write_text(text)
    assert contracts.native_outcome(tmp_path)['stop_reason'] is None
    with output.open('ab') as stream:stream.write(b'Tampered after the first 64 KiB')
    assert contracts.native_outcome(tmp_path)['stop_reason']=='native_failure_event'


def test_legacy_stream_has_no_total_size_quota_and_skips_large_opaque_body(tmp_path,monkeypatch):
    outcome,meter=terminal_receipt('Original science');outcome.pop('terminal_evidence')
    rows=[dict(type='thread.started',thread_id='thread-one'),
        dict(type='error',error=dict(code='rate_limit_exceeded',status=429))]
    with (tmp_path/'native.jsonl').open('wb') as stream:
        for row in rows:stream.write((json.dumps(row)+'\n').encode())
        stream.write(b'{"type":"item.completed","item":{"type":"mcp_tool_call","arguments":"')
        for _ in range(80):stream.write(b'SECRET_BODY'*8192)
        stream.write(b'"}}\n')
        for row in [dict(type='item.completed',item=dict(type='agent_message',text='Original science')),
                    dict(type='turn.completed',usage=meter)]:stream.write((json.dumps(row)+'\n').encode())
    (tmp_path/'science-output.json').write_text('Original science')
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_BODY' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_BODY' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    evidence=backends.Codex.acquire_terminal_evidence(tmp_path,outcome)
    assert evidence['facts']['terminal']==meter
    assert backends.Codex.reconcile_terminal(outcome,meter=meter,evidence=evidence,original_record=tmp_path)['stop_reason'] is None


def live_context_meter(tmp_path):
    from scidiscovery.research_loop import budget_notice
    from scidiscovery.research_loop.native_usage import SessionUsage
    (tmp_path/'work').mkdir()
    path=budget_notice.start_metadata(tmp_path,0)
    value=json.loads(path.read_text())
    value.update(session_id='thread-one',acknowledged=1,calls=dict(completed=1,failed=0,cancelled=0),
        tokens=dict(input_tokens=10,cached_input_tokens=0,output_tokens=2),reported_calls=1)
    path.write_text(json.dumps(value))
    meter=SessionUsage(tmp_path/'home',tmp_path)
    meter.configure(path,0,available=True);meter.bind_thread('thread-one');meter.bind_turn('turn-one');meter.poll()
    sample=dict(status='last_reported',total_tokens=90,model_context_window=100,observed_at=time.time(),
        thread_id='thread-one',turn_id='turn-one',current_window=True)
    meter.observe_context(sample)
    assert meter.snapshot()['context']['current_window'] is True
    return meter,path,value,sample


def test_pending_metadata_invalidates_context_until_a_new_qualified_sample(tmp_path):
    meter,path,value,sample=live_context_meter(tmp_path)
    value.update(pending=True,pending_started_at=time.monotonic());path.write_text(json.dumps(value))
    assert meter.poll()['context']==dict(status='unavailable')
    meter.observe_context(sample)
    assert meter.snapshot()['context']==dict(status='unavailable')
    value.update(pending=False,pending_started_at=None,acknowledged=2);path.write_text(json.dumps(value))
    assert meter.poll()['context']==dict(status='unavailable')
    meter.observe_context(sample)
    assert meter.snapshot()['context']==dict(status='unavailable')
    meter.observe_context(dict(sample,observed_at=time.time(),total_tokens=40))
    assert meter.snapshot()['context']['total_tokens']==40


@pytest.mark.parametrize('fault',['bad_projection','missing_file','binding_changed'])
def test_failed_metadata_cannot_keep_or_restore_stale_current_window(tmp_path,fault):
    meter,path,value,sample=live_context_meter(tmp_path)
    if fault=='missing_file':path.unlink()
    elif fault=='binding_changed':
        value['session_id']='foreign-thread';path.write_text(json.dumps(value))
    else:
        value['fault']='native_metadata_event_invalid';path.write_text(json.dumps(value))
    assert meter.poll()['context']==dict(status='unavailable')
    meter.observe_context(sample)
    assert meter.snapshot()['context']==dict(status='unavailable')


def test_fast_completed_compaction_receipt_rejects_queued_precompaction_sample(tmp_path):
    meter,path,value,sample=live_context_meter(tmp_path)
    value.update(compactions=1,acknowledged=2);path.write_text(json.dumps(value))
    adapter=backends.Codex(dict(native_launcher_argv=['codex'],native_options={}))
    # No pending receipt was ever polled. The context event was queued before
    # the independently observed completed compaction receipt.
    adapter.observe(meter,dict(context=sample))
    assert meter.snapshot()['context']==dict(status='unavailable')
    meter.observe_context(dict(sample,total_tokens=30,observed_at=time.time()))
    assert meter.snapshot()['context']['total_tokens']==30


def test_explicit_compaction_event_rejects_same_thread_turn_queued_old_sample(tmp_path):
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    meter=adapter.meter(tmp_path,tmp_path);meter.bind_thread('thread-one');meter.bind_turn('turn-one')
    sample=dict(status='last_reported',total_tokens=90,model_context_window=100,observed_at=time.time(),
        thread_id='thread-one',turn_id='turn-one',current_window=True)
    adapter.observe(meter,dict(context=sample));adapter.observe(meter,dict(compaction=True))
    adapter.observe(meter,dict(context=sample))
    assert meter.snapshot()['context']==dict(status='unavailable')


def test_canonical_observer_skips_huge_tools_but_keeps_full_result_usage_and_late_activity(tmp_path,monkeypatch):
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    source=tmp_path/'native.jsonl'
    decoder=adapter.event_selector(source=source);original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_OPAQUE' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_OPAQUE' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    meter=dict(input_tokens=17,cached_input_tokens=5,output_tokens=3)
    result=dict(summary='Complete scientific payload '*10000,limitations=['Mechanical fixture'])
    tool=b'{"type":"tool","tool":"fixture","status":"completed","input":"'+b'SECRET_OPAQUE'*100000+b'","output":"SECRET_OPAQUE"}\n'
    payload=(tool+(json.dumps(dict(type='usage',usage=meter))+'\n').encode()
        +(json.dumps(dict(type='result',result=result))+'\n').encode()
        +(json.dumps(dict(type='completed',usage=meter))+'\n').encode()+tool)
    source.write_bytes(payload)
    events=[]
    for offset in range(0,len(payload),65536):events.extend(decoder.feed(payload[offset:offset+65536]))
    events.extend(decoder.feed(b'',final=True))
    assert [event['type'] for event in events]==['tool','usage','result','completed','tool']
    assert events[0]==dict(type='tool') and events[-1]==dict(type='tool')
    assert events[2]['result']==result
    assert adapter.event(events[1])['live_usage']==meter
    assert adapter.event(events[3])==dict(completed=True,usage=meter)
    # The completion guard keeps activity after completion visible; the
    # separate tool fact survives without decoding the opaque body.
    assert adapter.event(events[-1])==dict(activity=True,tool_action=True)
    observation=adapter.meter(tmp_path,tmp_path)
    adapter.observe(observation,adapter.event(events[1]))
    assert adapter.snapshot_observation(observation)['tokens']==meter


def test_canonical_tiny_metadata_skips_undeclared_bodies_before_decoding(tmp_path,monkeypatch):
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_OPAQUE' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_OPAQUE' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    line=b'{"type":"usage","input":{"body":"SECRET_OPAQUE"},"usage":{"input_tokens":17,"cached_input_tokens":5,"output_tokens":3},"output":"SECRET_OPAQUE"}\n'
    source=tmp_path/'native.jsonl';source.write_bytes(line)
    assert backends.CanonicalEvents(source).feed(line)==[dict(type='usage',usage=dict(input_tokens=17,cached_input_tokens=5,output_tokens=3))]


def test_canonical_type_last_uses_exact_published_source_without_buffering_opaque_prefix(tmp_path,monkeypatch):
    adapter=backends.Protocol(dict(native_launcher_argv=['fixture']))
    source=tmp_path/'native.jsonl';decoder=adapter.event_selector(source=source)
    science=dict(summary='Full {scientific} "result" α '*20000,limitations=[])
    meter=dict(input_tokens=17,cached_input_tokens=5,output_tokens=3)
    tool=(json.dumps(dict(input='SECRET_OPAQUE'*100000,output='SECRET_OPAQUE',type='tool'))+'\n').encode()
    rows=[dict(result=science,type='result'),dict(usage=meter,type='usage'),dict(usage=meter,type='completed')]
    payload=(tool+b''.join((json.dumps(row)+'\n').encode() for row in rows)+tool
        +b'{"payload":"SECRET_OPAQUE","type":"unknown.event"}\n'
        +b'{"type":"usage","usage": BAD}\n  \n')
    source.write_bytes(payload)
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_OPAQUE' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_OPAQUE' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    events=[]
    for offset in range(0,len(payload),4096):events.extend(decoder.feed(payload[offset:offset+4096]))
    events.extend(decoder.feed(b'',final=True))
    assert [event['type'] for event in events]==['tool','result','usage','completed','tool','opaque.activity','opaque.activity']
    assert events[1]['result']==science
    assert adapter.event(events[2])['live_usage']==meter
    assert adapter.event(events[3])['usage']==meter
    assert adapter.event(events[4])==dict(activity=True,tool_action=True)
    assert all(adapter.event(event)==dict(activity=True) for event in events[5:])


def test_type_last_tool_never_reopens_its_body_from_published_source(tmp_path,monkeypatch):
    source=tmp_path/'native.jsonl'
    payload=(json.dumps(dict(input='SECRET_OPAQUE'*100000,type='tool'))+'\n').encode()
    source.write_bytes(payload);decoder=backends.CanonicalEvents(source)
    monkeypatch.setattr(decoder,'_original_line',lambda *args:pytest.fail('Opaque tool original was loaded'))
    events=[]
    for offset in range(0,len(payload),4096):events.extend(decoder.feed(payload[offset:offset+4096]))
    assert events==[dict(type='tool')]


@pytest.mark.parametrize('later_type',['completed','tool'])
def test_scientific_result_discriminator_cannot_be_replaced_by_duplicate_root_type(tmp_path,monkeypatch,later_type):
    source=tmp_path/'native.jsonl'
    payload=(b'{"type":"result","result":{"summary":"Candidate"},"input":"'
        +b'SECRET_OPAQUE'*100000+b'","type":'+json.dumps(later_type).encode()+b'}\n')
    source.write_bytes(payload);decoder=backends.CanonicalEvents(source)
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_OPAQUE' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_OPAQUE' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    monkeypatch.setattr(decoder,'_original_line',lambda *args:pytest.fail('Ambiguous result original was decoded'))
    events=[]
    for offset in range(0,len(payload),4096):events.extend(decoder.feed(payload[offset:offset+4096]))
    assert events==[dict(type='opaque.activity')]


@pytest.mark.parametrize('payload',[
    b'{"type":"unknown.event","input":"SECRET_OPAQUE"}\n',
    b'not JSON SECRET_OPAQUE\n',
    b'{"type":"turn.failed","error": BAD}\n'])
def test_legacy_unknown_or_malformed_rows_emit_only_opaque_activity(payload,monkeypatch):
    original=json.loads
    def guarded(value,*args,**kwargs):
        assert b'SECRET_OPAQUE' not in value if isinstance(value,(bytes,bytearray)) else 'SECRET_OPAQUE' not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(backends.json,'loads',guarded)
    events=backends.CodexEvents().feed(payload)
    assert events==[dict(type='opaque.activity')]
    assert backends.Codex.event(None,events[0])==dict(activity=True)
    assert backends.Codex.format_observation(None,events[0])=='activity'
