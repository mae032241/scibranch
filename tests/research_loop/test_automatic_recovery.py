"""Automatic recovery is a bounded transport grant, never scientific success."""
import copy
import json
import math
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (backends, native_recovery as recovery, continuation,
    contracts as c, configuration_updates, execution, runtime, budget, correction,
    execution_recovery, history)


# Captured from installed Codex exec JSON against an unauthenticated loopback
# provider by test_installed_codex_http_failure_conformance on 2026-10-26.
# The server's structured code is intentionally absent in these actual events.
CODEX_HTTP_FAILURE_MESSAGES = {
    'rate_limit_exceeded': 'exceeded retry limit, last status: 429 Too Many Requests',
    'insufficient_quota': 'Quota exceeded. Check your plan and billing details.',
    'server_overloaded': 'unexpected status 503 Service Unavailable: Deterministic loopback native failure., url: http://127.0.0.1:46345/v1/responses',
}


def failure(code='rate_limit_exceeded', **extra):
    return recovery.classify(dict(type='turn.failed', error=dict(code=code, **extra)))


def outcome(code='rate_limit_exceeded', **extra):
    return dict(terminal_observed=False, native_failure=failure(code, **extra),
        native_activity_observed=False, thread_id='original',
        stop_reason='native_failure_event', cleanup='observed_group_cleaned')


def decide(value=None, *, used=None, available=True, policy=None):
    return recovery.decision(outcome() if value is None else value,
        recovery.policy({} if policy is None else policy),
        used or dict(continues=0, retries=0, wait_seconds=0.),
        session_available=available)


@pytest.mark.parametrize('code', [
    'invalid_api_key', 'authentication_error', 'permission_denied',
    'insufficient_quota', 'quota_exceeded', 'billing_hard_limit_reached',
    'invalid_request_error', 'model_not_found', 'invalid_configuration',
])
def test_permanent_structured_error_dominates_429(code):
    native = dict(type='turn.failed', error=dict(code=code, status=429))
    original = copy.deepcopy(native)
    projected = recovery.classify(native)
    assert projected['category'] == 'permanent'
    assert native == original
    assert decide(dict(outcome(), native_failure=projected))['action'] == 'stop'


def test_only_native_failure_events_can_supply_classification():
    for kind in ('item.completed', 'message', 'tool', 'turn.completed'):
        assert recovery.classify(dict(type=kind, error=dict(code='rate_limit_exceeded'))) is None
    assert recovery.classify(dict(type='error', message='429 rate limit temporary capacity'))['category'] == 'unknown'
    assert decide(dict(outcome(), native_failure=None))['action'] == 'stop'


@pytest.mark.parametrize('code,category', [('rate_limit_exceeded','temporary'),
    ('insufficient_quota','permanent'),('server_overloaded','temporary')])
def test_confirmed_exec_native_http_translations_have_narrow_message_patterns(code, category):
    message = CODEX_HTTP_FAILURE_MESSAGES[code]
    for event in (dict(type='error', message=message),dict(type='turn.failed',error=dict(message=message))):
        assert recovery.classify(event)['category'] == category
    for kind in ('message','item.completed','tool'):
        assert recovery.classify(dict(type=kind,message=message,error=dict(message=message))) is None
    for altered in ('Reported: '+message, message+' Extra ordinary text', message+'\n'):
        assert recovery.classify(dict(type='error',message=altered))['category'] == 'unknown'


def test_confirmed_quota_message_dominates_generic_http_429():
    event = dict(type='turn.failed',error=dict(status=429,message=CODEX_HTTP_FAILURE_MESSAGES['insufficient_quota']))
    assert recovery.classify(event)['category'] == 'permanent'


def json_error_message(code='invalid_json_schema', *, status=400, **fields):
    return json.dumps(dict(type='error', error=dict(code=code, **fields), status=status))


@pytest.mark.parametrize('kind', ['error', 'turn.failed'])
def test_json_native_schema_error_is_permanent_without_startup_replay(kind):
    message = json_error_message(type='invalid_request_error', param='text.format.schema',
        message="Invalid schema for response_format 'codex_output_schema': "
        "'schema.properties.portfolio_review.properties.discriminating_work.anyOf.1.properties.mechanism_refs.uniqueItems' "
        'is not permitted when strict is true.')
    event = dict(type=kind, **({'message':message} if kind == 'error' else {'error':dict(message=message)}))
    original = copy.deepcopy(event)
    projected = recovery.classify(event)
    assert projected['category'] == 'permanent'
    assert projected['codes'] == ['invalid_json_schema', 'invalid_request_error']
    assert projected['statuses'] == [400] and projected['not_accepted'] is False
    stopped = decide(dict(outcome(), native_failure=projected, thread_id=None), available=False)
    assert stopped['action'] == 'stop' and stopped['reason'] == 'automatic_recovery_permanent_failure'
    assert event == original


def test_json_native_temporary_error_cannot_supply_startup_acceptance_authority():
    message = json_error_message('rate_limit_exceeded', status=429,
        accepted=False, request_started=False)
    event = dict(type='turn.failed', error=dict(message=message))
    projected = recovery.classify(event)
    assert projected['category'] == 'temporary'
    assert projected['not_accepted'] is False
    value = dict(outcome(), native_failure=projected, thread_id=None)
    assert decide(value, available=False)['reason'] == 'automatic_recovery_session_unavailable'
    for flag in ('accepted', 'request_started'):
        projected = recovery.classify(dict(event, **{flag:False}))
        assert projected['not_accepted'] is True
        assert decide(dict(value, native_failure=projected), available=False)['startup_retry'] is True


def test_nested_json_permanent_error_dominates_temporary_code_and_http_429():
    message = json_error_message('rate_limit_exceeded', status=429,
        message=json_error_message(status=429))
    projected = recovery.classify(dict(type='turn.failed', error=dict(status=429, message=message)))
    assert projected['category'] == 'permanent'
    assert projected['codes'] == ['invalid_json_schema', 'rate_limit_exceeded']
    assert decide(dict(outcome(), native_failure=projected))['action'] == 'stop'


@pytest.mark.parametrize('kind', ['message', 'item.completed', 'tool', 'turn.completed'])
def test_json_provider_envelope_in_agent_or_tool_events_is_ignored(kind):
    message = json_error_message()
    assert recovery.classify(dict(type=kind, message=message, error=dict(message=message))) is None


@pytest.mark.parametrize('message', [
    '{"type":"error","error":',
    'Reported: ' + json_error_message(),
    json_error_message() + ' Extra ordinary text',
    json.dumps([dict(type='error', error=dict(code='invalid_json_schema'), status=400)]),
    json.dumps(dict(type='message', error=dict(code='invalid_json_schema'), status=400)),
    json.dumps(dict(type='error', code='invalid_json_schema', status=400)),
    json_error_message(message='x' * (16 * 1024)),
    '{"error":' * 1100 + '0' + '}' * 1100,
    json_error_message(None, status=None, message=json_error_message(None, status=None,
        message=json_error_message())),
], ids=['malformed', 'prefixed-prose', 'trailing-prose', 'array', 'wrong-type',
    'missing-error-object', 'oversize', 'excessive-json-depth', 'excessive-envelope-depth'])
def test_json_provider_envelope_requires_bounded_complete_error_object(message):
    projected = recovery.classify(dict(type='error', message=message))
    assert projected['category'] == 'unknown'
    assert projected['codes'] == [] and projected['statuses'] == []
    assert projected['not_accepted'] is False


def test_json_provider_projection_excludes_private_prose_headers_and_code_strings():
    secret = '/private/native/request?token=fixture-private-secret'
    message = json_error_message(secret, type='invalid_request_error',
        message=secret, headers=dict(Authorization='Bearer ' + secret), param=secret)
    event = dict(type='error', message=message)
    backend = backends.Codex(dict(native_launcher_argv=['codex']))
    projected = recovery.classify(event)
    assert projected['category'] == 'permanent'
    assert projected['codes'] == ['invalid_request_error']
    assert secret not in str(projected) and secret not in str(backend.event(event))
    assert backend.public_events(event) == []


def test_generic_terminal_wrapper_preserves_structured_permanent_failure():
    combined = recovery.combine([failure('insufficient_quota', status=429), recovery.classify(dict(type='error'))])
    assert combined['category'] == 'permanent'
    assert recovery.combine([failure(), recovery.classify(dict(type='turn.failed'))])['category'] == 'temporary'


def test_codex_adapter_keeps_failure_projection_without_public_error_prose():
    backend = backends.Codex(dict(native_launcher_argv=['codex']))
    event = dict(type='turn.failed', error=dict(code='service_unavailable', status=503, message='private request'))
    assert backend.event(event)['failure']['category'] == 'temporary'
    assert 'private request' not in str(backend.event(event))
    assert backend.public_events(event) == []


def test_retry_backoff_and_independent_continue_limit_are_finite():
    used = dict(continues=2, retries=0, wait_seconds=0.)
    for retries, seconds in enumerate((30, 60, 120)):
        used['retries'] = retries
        assert decide(used=used)['delay_seconds'] == seconds
    used['retries'] = 3
    assert decide(used=used)['reason'] == 'automatic_recovery_retry_limit'
    incomplete = dict(outcome(), native_failure=recovery.classify(dict(type='turn.incomplete')))
    assert decide(incomplete, used=used)['reason'] == 'automatic_recovery_continue_limit'


def test_retry_after_is_honored_or_stops_without_shortening():
    assert decide(outcome(retry_after_seconds=200))['delay_seconds'] == 200
    assert decide(outcome(retry_after_seconds=301))['reason'] == 'automatic_recovery_retry_after_limit'
    used = dict(continues=0, retries=1, wait_seconds=1790.)
    assert decide(outcome(retry_after_seconds=30), used=used)['reason'] == 'automatic_recovery_wait_limit'


def test_no_thread_is_not_positive_unaccepted_evidence():
    value = dict(outcome(), thread_id=None)
    assert decide(value, available=False)['reason'] == 'automatic_recovery_session_unavailable'
    value = dict(outcome(accepted=False), thread_id=None)
    assert decide(value, available=False)['startup_retry'] is True
    assert recovery.decision(value, recovery.policy({}), dict(continues=0,retries=0,wait_seconds=0.),
        session_available=False, activity_observed=True)['action'] == 'stop'
    assert decide(dict(value, thread_id='accepted-original'), available=False)['action'] == 'stop'


def test_completed_turn_and_unknown_failure_never_enter_continue_loop():
    assert decide(dict(outcome(), terminal_observed=True))['action'] == 'none'
    assert decide(outcome('unrecognized_error'))['action'] == 'stop'
    interrupted = dict(outcome(), native_failure=recovery.classify(dict(type='turn.interrupted')))
    assert decide(interrupted)['action'] == 'continue'
    assert decide(interrupted)['delay_seconds'] == 0
    assert decide(interrupted, available=False)['action'] == 'stop'


def test_attempt_fold_preserves_wait_and_both_error_categories():
    intents = [{}, {'continuation_kind':'fragment'},
        dict(automatic_recovery=dict(action='retry', wait_seconds=29.5)),
        dict(automatic_recovery=dict(action='continue', wait_seconds=0.)),
        dict(automatic_recovery=dict(action='retry', wait_seconds=61.))]
    assert recovery.counters(intents) == dict(continues=1, retries=2, wait_seconds=90.5)
    intents[-1]['automatic_recovery']['wait_seconds'] = math.nan
    with pytest.raises(ValueError, match='wait accounting'):recovery.counters(intents)


@pytest.mark.parametrize('changed', [dict(enabled=1), dict(max_retries=True), dict(max_continues=-1),
    dict(initial_backoff_seconds=301), dict(max_wait_seconds=math.inf), dict(jitter_fraction=2),
    dict(unknown_field=1)])
def test_policy_rejects_unbounded_or_ambiguous_configuration(changed):
    with pytest.raises(ValueError):recovery.policy(changed)


def test_default_policy_is_independent_and_retains_narrow_output_only_meaning():
    first = recovery.policy({});first['max_retries'] = 0
    assert recovery.policy({})['max_retries'] == 3
    assert recovery.policy({})['max_continues'] == 2
    assert decide(policy=dict(enabled=False))['action'] == 'none'


def test_missing_usage_eligibility_does_not_clear_unknown_or_weaken_cleanup():
    value = outcome()
    assert recovery.recoverable_source(value)
    for change in (dict(terminal_observed=True), dict(stop_reason='token_limit'),
            dict(cleanup='cleanup_unknown'), dict(native_failure=failure('insufficient_quota'))):
        assert not recovery.recoverable_source(dict(value, **change))


def arbiter_instance(tmp_path, monkeypatch, *, role='W', output_only=True):
    root = tmp_path / 'instance';root.mkdir()
    record = root / 'native-evidence' / '001';record.mkdir(parents=True)
    (root / 'jobs').mkdir()
    c.write(root / 'jobs/001.json', dict(role=role, step=1))
    c.write(record / 'native-intent.json', {})
    binding = dict(path='accepted-original')
    for name in ('runner-process.json','native-process.json'):
        c.write(record / name, dict(task_ref=binding, boot_id='original-boot',
            pid=100, start_ticks=200, pgid=100, sid=100, ppid=99))
    value = dict(outcome(), execution_processes={name:c.record_ref(record / name)
        for name in ('runner-process.json','native-process.json')})
    c.write(record / 'native-outcome.json', value)
    monkeypatch.setattr(configuration_updates, 'for_step', lambda *_:dict(resume_policy=dict(
        output_only=output_only, automatic_recovery=recovery.policy({}))))
    monkeypatch.setattr(execution, 'records', lambda *_:[record])
    monkeypatch.setattr(execution, 'origin', lambda *_:record)
    monkeypatch.setattr(execution, 'identity', lambda *_:binding)
    monkeypatch.setattr(runtime, 'node_state', lambda *_:'terminal')
    monkeypatch.setattr(runtime, 'process_matches', lambda *_:False)
    monkeypatch.setattr(budget, 'final_a_barrier', lambda *_:None)
    monkeypatch.setattr(budget, 'closeout_state', lambda *_:dict(begin=None))
    monkeypatch.setattr(correction, 'gate', lambda *_:None)
    monkeypatch.setattr(backends, 'selected', lambda *_:SimpleNamespace(name='codex', standard=True))
    monkeypatch.setattr(continuation, 'automatic_source', lambda *_:c.read(record / 'native-outcome.json'))
    monkeypatch.setattr(continuation, '_automatic_session', lambda *_:dict(thread_id='original'))
    monkeypatch.setattr(execution_recovery, 'pending', lambda *_:False)
    monkeypatch.setattr(history, 'managed_calls', lambda *_:[])
    return root, record


def test_independent_automatic_grant_does_not_reinterpret_output_only(tmp_path, monkeypatch):
    root, record = arbiter_instance(tmp_path, monkeypatch)
    decision = continuation.automatic_decision(root, 1)
    assert decision['action'] == 'retry'
    assert decision['source_record'] == str(record) and decision['source_attempt'] == 0
    assert decision['counters'] == dict(continues=0,retries=0,wait_seconds=0.)


def test_living_original_and_unknown_managed_call_never_start_replacement(tmp_path, monkeypatch):
    root, _ = arbiter_instance(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, 'process_matches', lambda *_:True)
    assert continuation.automatic_decision(root, 1)['action'] == 'none'
    monkeypatch.setattr(runtime, 'process_matches', lambda *_:False)
    monkeypatch.setattr(history, 'managed_calls', lambda *_:[dict(status='unknown')])
    assert continuation.automatic_decision(root, 1)['reason'] == 'automatic_recovery_managed_execution_pending'


@pytest.mark.parametrize('boundary', ['pause','final_only','closeout','final_barrier'])
def test_pause_and_final_boundaries_do_not_add_normal_work(tmp_path, monkeypatch, boundary):
    root, _ = arbiter_instance(tmp_path, monkeypatch)
    if boundary == 'pause':c.write(root / 'pause.request', dict(reason='user pause'))
    elif boundary == 'final_barrier':monkeypatch.setattr(budget, 'final_a_barrier', lambda *_:dict(step=1))
    else:
        job = c.read(root / 'jobs/001.json')
        job.update(final_only=True) if boundary == 'final_only' else job.update(purpose='closeout')
        c.write(root / 'jobs/001.json', job)
    assert continuation.automatic_decision(root, 1)['action'] == 'stop'


def test_known_completed_fragment_or_missing_output_returns_to_existing_flow(tmp_path, monkeypatch):
    root, record = arbiter_instance(tmp_path, monkeypatch)
    value = c.read(record / 'native-outcome.json')
    value.update(terminal_observed=True, returncode=0, output_status='missing')
    c.write(record / 'native-outcome.json', value)
    assert continuation.automatic_decision(root, 1)['action'] == 'none'


@pytest.mark.parametrize('scope', ['author_closeout','execution_wake','output_repair','work_repair'])
def test_narrow_original_scope_does_not_acquire_full_task_recovery(tmp_path, monkeypatch, scope):
    root, record = arbiter_instance(tmp_path, monkeypatch)
    original = (dict(continuation_kind=scope) if scope in {'author_closeout','execution_wake'}
        else {scope:dict(code='retained_narrow_scope')})
    c.write(record / 'native-intent.json', original)
    assert continuation.automatic_decision(root, 1)['reason'] == 'automatic_recovery_special_scope_unavailable'


def test_raw_failure_projection_is_rechecked_without_reading_agent_text(tmp_path, monkeypatch):
    root = tmp_path;record = root / 'record';record.mkdir()
    backend = backends.Codex(dict(native_launcher_argv=['codex']))
    monkeypatch.setattr(configuration_updates, 'for_step', lambda *_:{})
    monkeypatch.setattr(backends, 'selected', lambda *_:backend)
    c.write(record / 'native-intent.json', {})
    event = dict(type='turn.failed', error=dict(code='rate_limit_exceeded', status=429))
    import json
    raw = (json.dumps(dict(type='item.completed', item=dict(type='agent_message', text='insufficient_quota')))
        + '\n' + json.dumps(event) + '\n').encode()
    (record / 'native.jsonl').write_bytes(raw)
    c.write(record / 'native-outcome.json', dict(native_failure=recovery.classify(event),
        terminal_observed=False,native_activity_observed=True,thread_id=None))
    assert continuation.automatic_source(root, 1, record)['native_failure']['category'] == 'temporary'
    assert (record / 'native.jsonl').read_bytes() == raw
    event['error']['code'] = 'insufficient_quota'
    (record / 'native.jsonl').write_text(json.dumps(event) + '\n')
    with pytest.raises(ValueError, match='failure evidence changed'):
        continuation.automatic_source(root, 1, record)


def test_retained_unknown_json_schema_failure_is_rejected_without_rewriting_originals(tmp_path, monkeypatch):
    record = tmp_path / 'record';record.mkdir()
    backend = backends.Codex(dict(native_launcher_argv=['codex']))
    monkeypatch.setattr(configuration_updates, 'for_step', lambda *_:{})
    monkeypatch.setattr(backends, 'selected', lambda *_:backend)
    c.write(record / 'native-intent.json', {})
    event = dict(type='turn.failed', error=dict(message=json_error_message()))
    (record / 'native.jsonl').write_text(json.dumps(event) + '\n')
    old_projection = recovery.classify(dict(type='turn.failed'))
    c.write(record / 'native-outcome.json', dict(native_failure=old_projection,
        terminal_observed=False, native_activity_observed=False, thread_id=None))
    retained = {name:(record / name).read_bytes() for name in ('native.jsonl', 'native-outcome.json')}
    with pytest.raises(ValueError, match='failure evidence changed'):
        continuation.automatic_source(tmp_path, 1, record)
    assert all((record / name).read_bytes() == raw for name, raw in retained.items())
    saved = c.read(record / 'native-outcome.json')
    assert decide(saved, available=False)['reason'] == 'automatic_recovery_unknown_failure'


def format_error(**changes):
    fields=dict(code='invalid_json_schema',type='invalid_request_error',param='text.format.schema',
        message='The provider rejected a constraint in its native response format.')
    fields.update(changes)
    return dict(type='error',message=json_error_message(**fields))


@pytest.mark.parametrize('change', [dict(param='model'),dict(code='insufficient_quota'),
    dict(type='authentication_error'),dict(param=None)])
def test_format_negotiation_requires_structured_format_parameter(change):
    assert recovery.format_rejection(format_error())
    assert not recovery.format_rejection(format_error(**change))
    assert not recovery.format_rejection(dict(type='error',message='400 invalid_json_schema text.format.schema'))
    assert not recovery.format_rejection(dict(type='error',message=json_error_message(status=429,
        code='invalid_json_schema',param='text.format.schema')))


@pytest.mark.parametrize('activity', [None,dict(type='item.started',item=dict(type='agent_message')),
    dict(type='item.completed',item=dict(type='mcp_tool_call')),dict(type='turn.completed'),
    dict(type='unknown.event'),'malformed'])
def test_positive_original_format_rejection_is_separate_from_immutable_old_diagnostic(tmp_path, monkeypatch, activity):
    record=tmp_path/'record';record.mkdir()
    backend=backends.Codex(dict(native_launcher_argv=['codex']))
    monkeypatch.setattr(configuration_updates,'for_step',lambda *_:{})
    monkeypatch.setattr(backends,'selected',lambda *_:backend)
    c.write(record/'native-intent.json',{})
    events=[dict(type='thread.started',thread_id='original'),dict(type='turn.started')]
    if activity is not None:events.append(activity)
    events.extend([format_error(),dict(type='turn.failed',error=dict(message=format_error()['message']))])
    (record/'native.jsonl').write_text('\n'.join(json.dumps(event) if event!='malformed' else '{'
        for event in events)+'\n')
    c.write(record/'native-outcome.json',dict(returncode=7,stop_reason='native_failure_event',
        cleanup='observed_group_cleaned',native_failure=recovery.classify(dict(type='error')),
        terminal_observed=False,native_activity_observed=True,thread_id='original'))
    originals={name:(record/name).read_bytes() for name in ('native.jsonl','native-outcome.json','native-intent.json')}
    value=continuation.format_source(tmp_path,1,record)
    assert (value is not None) is (activity is None)
    if value is not None:
        assert value['format_rejected'] is True and value['native_failure']['category']=='unknown'
    assert all((record/name).read_bytes()==raw for name,raw in originals.items())
    with pytest.raises(ValueError,match='failure evidence changed'):
        continuation.automatic_source(tmp_path,1,record)


def test_format_fallback_is_once_and_spends_existing_retry_budget():
    used=dict(continues=0,retries=0,wait_seconds=0.)
    limits=recovery.policy({})
    value=recovery.format_decision(limits,used,session_available=False)
    assert value['action']=='retry' and value['format_fallback'] is True and value['startup_retry'] is True
    assert value['delay_seconds']==0
    assert recovery.format_decision(limits,used,session_available=True,already_fallback=True)['action']=='stop'
    assert recovery.format_decision(dict(limits,max_retries=0),used,session_available=False)['reason']=='automatic_recovery_retry_limit'
    assert recovery.format_decision(dict(limits,enabled=False),used,session_available=False)['action']=='none'


AUTOMATIC_NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read();resume='resume' in args
assert '--output-schema' not in args
assert prompt.strip() and '"additionalProperties"' not in prompt
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['CODEX_HOME'])
role=os.environ['THIN_HWA_ROLE'];thread='11111111-2222-4333-8444-'+('%012d'%int(work.name))
configuration=json.loads(pathlib.Path(__file__).resolve().with_name('launcher-config.json').read_text())
with (work/'work/launches').open('a') as stream:stream.write('resume\n' if resume else 'exec\n')
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
if not resume:
 session=home/'sessions'/('rollout-'+thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
 session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
session=home/'sessions'/('rollout-'+thread+'.jsonl')
if not configuration['fault'].endswith('_unreported') or resume:
 with session.open('a') as stream:
  stream.write(json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(
   total_token_usage=dict(input_tokens=20 if resume else 10,cached_input_tokens=6 if resume else 3,output_tokens=8 if resume else 4)))))+'\n')
if role=='H' and not resume:
 (work/'work/retained-observation').write_text('one exact observation')
 if configuration['fault'].startswith('retry'):
  message=('unexpected status 503 Service Unavailable: Deterministic loopback native failure., url: http://127.0.0.1:46345/v1/responses'
   if configuration['fault']=='retry503' else 'exceeded retry limit, last status: 429 Too Many Requests')
  print(json.dumps(dict(type='turn.failed',error=dict(message=message))),flush=True)
 else:print(json.dumps(dict(type='turn.incomplete')),flush=True)
 raise SystemExit(7)
if resume:
 assert thread in args and prompt.strip()
 assert (work/'work/retained-observation').read_text()=='one exact observation'
task=json.loads((work/'science-task.json').read_text())
payload={name:None for name in task['final_response']}
payload.update(summary='Recovered bounded transport',scientific_result='Retained observations, no scientific qualification.',
 limitations=['Only native transport is simulated.'],
 next_action='analyze' if role=='H' else 'finish',route_reason='Review the retained bounded observation.')
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))),flush=True)
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault,action', [('retry','retry'),('retry503','retry'),('incomplete','continue'),('retry_unreported','retry')])
def test_default_controller_recovers_one_exact_session_and_keeps_attempt_chain(tmp_path, monkeypatch, fault, action):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller, native
    root, home, env = prepare_cli(tmp_path, fault=fault)
    program = tmp_path / 'native.py'
    program.write_text('#!' + sys.executable + '\n' + AUTOMATIC_NATIVE)
    # A persisted deadline can elapse while no controller is running. This
    # clock seam exercises due-time arbitration without a real 30-second wait.
    monkeypatch.setattr(controller, '_recovery_remaining', lambda *_:0.)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key, env[key])
    settings = c.settings(root)
    originals = {path:path.read_bytes() for path in (root / 'settings.json',home / 'config.toml',home / 'auth.json')}
    result = controller.run(root)
    assert result['status'] == 'finish', result
    first, recovered = execution.records(root, 1)
    request = c.read(recovered / 'native-intent.json')
    assert request['continuation_kind'] == 'automatic_recovery'
    assert request['automatic_recovery']['action'] == action
    assert request['automatic_recovery']['source_attempt'] == 0
    assert request['session']['thread_id'] == c.read(first / 'native-outcome.json')['thread_id']
    assert request['session']['home'] == str(home)
    assert request['previous'] == execution.proof(first)
    assert request['task_ref'] == c.read(first / 'native-intent.json')['task_ref']
    assert (c.workspace(root,1) / 'work/launches').read_text() == 'exec\nresume\n'
    assert c.read(first / 'automatic-recovery.json')['status'] == 'started'
    assert recovery.counters([request]) == dict(continues=int(action=='continue'),retries=int(action=='retry'),wait_seconds=0.)
    assert budget.used_steps(root) == 3
    reported = not fault.endswith('_unreported')
    assert request['budget_slice']['previous_tokens'] == (14 if reported else 0)
    assert c.read(recovered / 'native-outcome.json')['budget_slice']['observed_tokens'] == (28 if reported else 14)
    assert native.usage(root)['observed_tokens'] == (42 if reported else 28)
    assert (first / 'usage.json').exists() is reported
    assert native.usage(root)['unknown_steps'] == [1]  # Failed-turn coverage stays unknown.
    assert settings['resume_policy']['output_only'] is True
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    before = native.usage(root)
    assert controller.run(root)['status'] == 'finish'
    assert native.usage(root) == before and len(execution.records(root,1)) == 2
    assert (c.workspace(root,1) / 'work/launches').read_text() == 'exec\nresume\n'


@pytest.mark.process_e2e
@pytest.mark.parametrize('status,code', [(429,'rate_limit_exceeded'),(429,'insufficient_quota'),(503,'server_overloaded')])
def test_installed_codex_http_failure_conformance(tmp_path, status, code):
    """Actual CLI JSON and exact-session failure resume, isolated from real API/auth."""
    import json
    import os
    import shutil
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    program = shutil.which('codex')
    if program is None:pytest.skip('Installed Codex CLI is unavailable')
    requests = []
    current_launch = 'original'
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append(dict(launch=current_launch,method='POST',path=self.path,
                has_body=bool(body),has_authorization=self.headers.get('Authorization') is not None))
            encoded = json.dumps(dict(error=dict(code=code, type=code, message='Deterministic loopback native failure.'))).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(encoded)))
            self.send_header('Connection','close')
            self.end_headers();self.wfile.write(encoded)
        def log_message(self, *_):pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    home = tmp_path / 'isolated-codex-home';home.mkdir()
    workspace = tmp_path / 'empty-workspace';workspace.mkdir()
    endpoint = 'http://127.0.0.1:' + str(server.server_port) + '/v1'
    configuration = dict(model_provider='loopback_fixture', project_doc_max_bytes=0,
        **{'model_providers.loopback_fixture.name':'Local HTTP conformance fixture',
           'model_providers.loopback_fixture.base_url':endpoint,
           'model_providers.loopback_fixture.wire_api':'responses',
           'model_providers.loopback_fixture.requires_openai_auth':False,
           'model_providers.loopback_fixture.request_max_retries':0,
           'model_providers.loopback_fixture.stream_max_retries':0,
           'features.multi_agent':False,'features.fast_mode':False,'web_search':'disabled'})
    args = [program,'exec','--json','--ignore-user-config','--skip-git-repo-check',
        '--sandbox','read-only','-C',str(workspace),'--model','fixture-model']
    for key,value in configuration.items():args.extend(['-c',key+'='+json.dumps(value)])
    args.append('-')
    env = {key:value for key,value in os.environ.items()
        if not key.startswith('OPENAI_') and key != 'CODEX_API_KEY'}
    env['CODEX_HOME'] = str(home)
    launches = []
    def invoke(command, operation, requested_thread=None):
        nonlocal current_launch
        current_launch = operation
        completed = subprocess.run(command,input='Return a bounded fixture answer.\n',env=env,
            cwd=workspace,capture_output=True,text=True,timeout=25)
        # Keep every native JSON event, including thread.started and turn.started.
        # Earlier samples filtered these out and cannot establish session absence.
        events, non_json_stdout = [], []
        for line in completed.stdout.splitlines():
            try:value = json.loads(line)
            except ValueError:
                non_json_stdout.append(line)
                continue
            events.append(value)
        failures = [projected for event in events
            if (projected := recovery.classify(event)) is not None]
        failure = recovery.combine(failures)
        thread_ids = [event['thread_id'] for event in events if isinstance(event,dict)
            and event.get('type') == 'thread.started' and isinstance(event.get('thread_id'),str)]
        terminal = any(isinstance(event,dict) and event.get('type') == 'turn.completed' for event in events)
        activity = any(isinstance(event,dict) and backends.Codex(dict(native_launcher_argv=[program])).event(event).get('activity')
            for event in events)
        unique_threads = list(dict.fromkeys(thread_ids))
        observed_thread = unique_threads[0] if len(unique_threads) == 1 else None
        decision = recovery.decision(dict(terminal_observed=terminal,thread_id=observed_thread,native_failure=failure),
            recovery.policy({}),dict(continues=0,retries=0,wait_seconds=0.),
            session_available=observed_thread is not None,activity_observed=activity)
        row = dict(operation=operation,requested_thread_id=requested_thread,returncode=completed.returncode,
            events=events,non_json_stdout=non_json_stdout,stderr=completed.stderr,thread_ids=thread_ids,
            observed_thread_id=observed_thread,native_failure=failure,native_failure_decision=decision,
            requests=[row for row in requests if row['launch'] == operation])
        launches.append(row)
        return row
    try:
        original = invoke(args,'original')
        original_thread = original['observed_thread_id']
        if code != 'insufficient_quota' and original_thread is not None:
            # Let the actual CLI resolve exactly its own ID in this same empty
            # home. Do not enumerate session files or synthesize session proof.
            schema = tmp_path / 'resume-schema.json'
            schema.write_text(json.dumps(dict(type='object',properties=dict(answer=dict(type='string')),
                required=['answer'],additionalProperties=False)))
            backend = backends.Codex(dict(native_launcher_argv=[program],native_options=dict(workspace='read-only')))
            resume_args, _ = backend.resume(args,dict(model='fixture-model'),workspace,
                tmp_path / 'resume-output.json',schema,dict(thread_id=original_thread))
            invoke(resume_args,'resume',original_thread)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    sample = dict(status=status,code=code,returncode=original['returncode'],events=original['events'],
        launches=launches,requests=requests,session_observation=(
            'exact_thread_resume_attempted' if len(launches) == 2 else
            'permanent_failure_not_resumed' if code == 'insufficient_quota' else 'thread_not_observed'),
        limitation='Synthetic loopback failures only; no real provider recovery, model work, or usage qualification.')
    encoded_sample = json.dumps(sample,ensure_ascii=False,indent=2)+'\n'
    if os.environ.get('SCID_TEST_LOG_DIR'):
        from pathlib import Path
        destination = Path(os.environ['SCID_TEST_LOG_DIR'])
        destination.mkdir(parents=True,exist_ok=True)
        (destination / f'codex-http-{status}-{code}.json').write_text(encoded_sample)
    print('CODEX_HTTP_CONFORMANCE ' + json.dumps(sample,ensure_ascii=False))
    assert requests
    assert all(row['has_body'] and row['path'] == '/v1/responses' for row in requests)
    assert all(not row['has_authorization'] for row in requests)
    expected = 'permanent' if code == 'insufficient_quota' else 'temporary'
    for launch in launches:
        assert launch['returncode'] != 0 and launch['native_failure'], launch
        assert launch['native_failure']['category'] == expected, launch
        assert launch['requests'], launch
        assert not launch['non_json_stdout'], launch
    if code == 'insufficient_quota':
        assert len(launches) == 1
        assert original['native_failure_decision']['reason'] == 'automatic_recovery_permanent_failure'
    elif original['observed_thread_id'] is not None:
        resumed = launches[1]
        assert resumed['requested_thread_id'] == original['observed_thread_id']
        assert resumed['observed_thread_id'] == original['observed_thread_id'], resumed
        assert original['native_failure_decision']['action'] == resumed['native_failure_decision']['action'] == 'retry'
        assert not original['native_failure_decision']['startup_retry']
        assert not resumed['native_failure_decision']['startup_retry']
    else:
        assert len(launches) == 1
        assert original['native_failure']['not_accepted'] is False
        assert original['native_failure_decision']['reason'] == 'automatic_recovery_session_unavailable'


@pytest.mark.process_e2e
def test_second_controller_never_replaces_still_living_default_native(tmp_path):
    import subprocess
    import sys
    import time
    from test_default_cli_output_repair import prepare_cli, cli
    root, _, env = prepare_cli(tmp_path, fault='retry')
    # Keep the exact first native process alive before it reports its bounded
    # failure. A second controller must collect/decline the original owner.
    script = AUTOMATIC_NATIVE.replace("if role=='H' and not resume:",
        "if role=='H' and not resume:\n"
        " import time\n"
        " while not (work/'work/release-original').exists():time.sleep(.05)\n"
        " (work/'work/released-original').write_text('same process')\n"
        "if role=='H' and not resume:", 1)
    (tmp_path / 'native.py').write_text('#!' + sys.executable + '\n' + script)
    command = [sys.executable,'-m','scidiscovery.research_loop.cli','run','--root',str(root)]
    process = subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    work = c.workspace(root,1)
    try:
        until = time.monotonic() + 8
        while not (work / 'work/launches').exists():
            assert process.poll() is None
            assert time.monotonic() < until
            time.sleep(.05)
        assert (work / 'work/launches').read_text() == 'exec\n'
        second = cli(root,'run',env=env)
        assert second['status'] != 'finish'
        assert (work / 'work/launches').read_text() == 'exec\n'
        assert len(execution.records(root,1)) == 1
        c.write(root / 'pause.request', dict(reason='Stop before accepting a replacement'))
        (work / 'work/release-original').write_text('release the same original')
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 0, (stdout,stderr)
        assert (work / 'work/released-original').read_text() == 'same process'
        assert (work / 'work/launches').read_text() == 'exec\n'
        assert len(execution.records(root,1)) == 1
    finally:
        if process.poll() is None:
            (work / 'work/release-original').write_text('release during cleanup')
            try:process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate();process.communicate(timeout=3)


STARTUP_NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read();resume='resume' in args
assert '--output-schema' not in args
assert prompt.strip() and '"additionalProperties"' not in prompt
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['CODEX_HOME'])
role=os.environ['THIN_HWA_ROLE']
configuration=json.loads(pathlib.Path(__file__).resolve().with_name('launcher-config.json').read_text())
launches=work/'work/launches'
with launches.open('a') as stream:stream.write('resume\n' if resume else 'exec\n')
assert not resume,'An unaccepted request has no session to resume'
if role=='H' and launches.read_text()=='exec\n':
 error=dict(code='rate_limit_exceeded',status=429)
 if configuration['fault']=='startup_rejected':error['accepted']=False
 print(json.dumps(dict(type='error',error=error)),flush=True)
 raise SystemExit(7)
if role=='H':
 assert launches.read_text()=='exec\nexec\n'
 assert prompt.strip()
thread='11111111-2222-4333-8444-'+('%012d'%int(work.name))
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
session=home/'sessions'/('rollout-'+thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
task=json.loads((work/'science-task.json').read_text())
payload={name:None for name in task['final_response']}
payload.update(summary='Bounded request startup',scientific_result='No scientific qualification is asserted.',
 limitations=['Only native transport is simulated.'],
 next_action='analyze' if role=='H' else 'finish',route_reason='Review the retained bounded observation.')
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))),flush=True)
'''


@pytest.mark.process_e2e
def test_explicitly_unaccepted_startup_retries_fresh_request_and_retains_unknown_usage(tmp_path, monkeypatch):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller, native
    root, home, env = prepare_cli(tmp_path, fault='startup_rejected')
    (tmp_path / 'native.py').write_text('#!' + sys.executable + '\n' + STARTUP_NATIVE)
    monkeypatch.setattr(controller, '_recovery_remaining', lambda *_:0.)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key, env[key])
    settings_bytes = (root / 'settings.json').read_bytes()
    result = controller.run(root)
    assert result['status'] == 'finish', result
    first, recovered = execution.records(root,1)
    original = c.read(first / 'native-outcome.json')
    assert original['thread_id'] is None and original['native_activity_observed'] is False
    assert original['native_failure']['not_accepted'] is True
    assert not (first / 'usage.json').exists()
    intent = c.read(recovered / 'native-intent.json')
    assert intent['continuation_kind'] == 'automatic_recovery'
    assert intent['automatic_recovery']['startup_retry'] is True
    assert intent['automatic_recovery']['action'] == 'retry'
    assert 'session' not in intent
    assert intent['previous'] == execution.proof(first)
    assert intent['task_ref'] == c.read(first / 'native-intent.json')['task_ref']
    assert execution.home(root,1,0) == execution.home(root,1,1) == home
    assert intent['budget_slice']['previous_tokens'] == 0
    assert recovery.counters([intent]) == dict(continues=0,retries=1,wait_seconds=0.)
    assert (c.workspace(root,1) / 'work/launches').read_text() == 'exec\nexec\n'
    assert budget.used_steps(root) == 3
    assert native.usage(root)['observed_tokens'] == 28
    assert native.usage(root)['unknown_steps'] == [1]
    assert (root / 'settings.json').read_bytes() == settings_bytes
    before = native.usage(root)
    assert controller.run(root)['status'] == 'finish'
    assert native.usage(root) == before and len(execution.records(root,1)) == 2
    assert (c.workspace(root,1) / 'work/launches').read_text() == 'exec\nexec\n'


@pytest.mark.process_e2e
def test_missing_thread_without_positive_rejection_never_starts_another_request(tmp_path, monkeypatch):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller, native
    root, _, env = prepare_cli(tmp_path, fault='startup_indeterminate')
    (tmp_path / 'native.py').write_text('#!' + sys.executable + '\n' + STARTUP_NATIVE)
    monkeypatch.setattr(controller, '_recovery_remaining', lambda *_:0.)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key, env[key])
    assert controller.run(root)['status'] != 'finish'
    first, = execution.records(root,1)
    outcome = c.read(first / 'native-outcome.json')
    assert outcome['thread_id'] is None and outcome['native_activity_observed'] is False
    assert outcome['native_failure']['not_accepted'] is False
    assert continuation.automatic_decision(root,1)['reason'] == 'automatic_recovery_session_unavailable'
    assert not (first / 'usage.json').exists()
    assert budget.used_steps(root) == 1
    assert native.usage(root)['observed_tokens'] == 0
    assert native.usage(root)['unknown_steps'] == [1]
    retained = {name:(first / name).read_bytes() for name in ('native-intent.json','native-outcome.json','native.jsonl')}
    assert controller.run(root)['status'] != 'finish'
    assert len(execution.records(root,1)) == 1 and not (root / 'jobs/002.json').exists()
    assert (c.workspace(root,1) / 'work/launches').read_text() == 'exec\n'
    assert all((first / name).read_bytes() == data for name,data in retained.items())


FORMAT_NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read();resume='resume' in args
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['CODEX_HOME'])
role=os.environ['THIN_HWA_ROLE']
config=json.loads(pathlib.Path(__file__).resolve().with_name('launcher-config.json').read_text())
assert '--output-schema' not in args
assert prompt.strip() and '"additionalProperties"' not in prompt
task=json.loads((work/'science-task.json').read_text())
launches=work/'work/format-launches.jsonl'
prior=launches.read_text().splitlines() if launches.exists() else []
number=len(prior)+1
with launches.open('a') as stream:stream.write(json.dumps(dict(native=False,resume=resume,role=role))+'\n')
thread='11111111-2222-4333-8444-'+('%012d'%int(work.name))
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
session=home/'sessions'/('rollout-'+thread+'.jsonl')
if not resume:
 session.parent.mkdir(parents=True,exist_ok=True)
 session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
else:
 assert thread in args
 assert (work/'work/retained-format-observation').read_text()=='one exact observation'
if role==config['format_role']:
 if number==1:(work/'work/retained-format-observation').write_text('one exact observation')
 fault=config['fault']
 temporary=fault=='format-always-transient' or fault in {'format-transient','format-repeat'} and number==1
 if temporary:
  print(json.dumps(dict(type='turn.failed',error=dict(code='rate_limit_exceeded',status=429))),flush=True)
  raise SystemExit(7)
 permanent=fault in {'format-other400','format-activity','format-prose'} or fault=='format-repeat' and number==2
 if permanent:
  print(json.dumps(dict(type='turn.started')),flush=True)
  if fault=='format-activity':print(json.dumps(dict(type='item.started',item=dict(type='agent_message',text='started science'))),flush=True)
  error=dict(type='error',error=dict(code='invalid_json_schema',type='invalid_request_error',param='text.format.schema',message='A provider request is invalid.'),status=400)
  if fault=='format-other400':error['error']['param']='model'
  message=json.dumps(error)
  if fault=='format-prose':message='Provider says '+message
  print(json.dumps(dict(type='error',message=message)),flush=True)
  print(json.dumps(dict(type='turn.failed',error=dict(message=message))),flush=True)
  raise SystemExit(7)
payload={name:None for name in task['final_response']}
payload.update(summary='Bounded ordinary prompt transport',scientific_result='Only native transport is simulated.',
 limitations=['No scientific qualification is asserted.'],
 next_action='analyze' if role=='H' else 'finish',route_reason='Retain the exact bounded transport evidence.')
if role==config['format_role'] and config['fault']=='format-repair' and number==1:payload.pop('summary')
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))),flush=True)
'''


def retained_strict_contract(monkeypatch):
    """Keep an old strict schema original while ordinary launches use prompt."""
    build=c.build_job;retained={}
    def prepare(*args,**kwargs):
        job=build(*args,**kwargs)
        record=execution.origin(args[0],job['step'])
        if not (record/'native-intent.json').exists():
            path=record/'science-result.schema.json'
            schema=c.read(path);schema['additionalProperties']=False;c.write(path,schema)
            retained.setdefault(path,path.read_bytes())
        return job
    monkeypatch.setattr(c,'build_job',prepare)
    return retained


@pytest.mark.process_e2e
@pytest.mark.parametrize('role,fault,final',[('H','format',False),('A','format',False),
    ('A','format-repair',False),('A','format-transient',False),('A','format',True),('A','format-repair',True)])
def test_production_ordinary_prompt_preserves_strict_original_budget_home_and_sticky_mode(tmp_path,monkeypatch,role,fault,final):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller,native
    schemas=retained_strict_contract(monkeypatch)
    root,home,env=prepare_cli(tmp_path,fault=fault,final=final)
    (tmp_path/'native.py').write_text('#!'+sys.executable+'\n'+FORMAT_NATIVE)
    config=c.read(tmp_path/'launcher-config.json');config['format_role']=role;c.write(tmp_path/'launcher-config.json',config)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key,env[key])
    monkeypatch.setattr(controller,'_recovery_remaining',lambda *_:0.)
    originals={path:path.read_bytes() for path in (root/'settings.json',home/'config.toml',home/'auth.json')}
    result=controller.closeout(root) if final else controller.run(root)
    assert result['status']==('final_a_completed' if final else 'finish'),result
    step=1 if final or role=='H' else 2
    paths=execution.records(root,step);first=paths[0]
    original_intent=c.read(first/'native-intent.json')
    launches=[json.loads(line) for line in (c.workspace(root,step)/'work/format-launches.jsonl').read_text().splitlines()]
    expected_attempts=2 if fault in {'format-repair','format-transient'} else 1
    assert len(paths)==len(launches)==expected_attempts
    assert launches==[dict(native=False,resume=number>0,role=role) for number in range(expected_attempts)]
    assert all(execution.home(root,step,number)==home for number in range(len(paths)))
    assert all(c.read(path/'native-outcome.json')['native_schema_transport']=='prompt' for path in paths)
    assert schemas and all(c.read(path)['additionalProperties'] is False for path in schemas)
    assert all(path.read_bytes()==raw for path,raw in schemas.items())
    if expected_attempts==2:
        recovered=paths[1];intent=c.read(recovered/'native-intent.json');old=c.read(first/'native-outcome.json')
        assert intent['previous']==execution.proof(first) and intent['task_ref']==original_intent['task_ref']
        assert intent.get('native_profile')==original_intent.get('native_profile')
        assert intent['session']['home']==str(home) and intent['session']['thread_id']==old['thread_id']
        assert (c.workspace(root,step)/'work/retained-format-observation').read_text()=='one exact observation'
        if fault=='format-repair':
            assert intent['continuation_kind']=='repair' and intent['output_repair']['code']=='invalid_fields'
            assert intent.get('automatic_recovery') is None and intent.get('work_repair') is None
            assert 'summary' not in c.read(first/'science-output.json')
            assert intent['budget_slice']['previous_tokens']==14
            assert c.read(recovered/'native-outcome.json')['budget_slice']['observed_tokens']==28
            assert step not in native.usage(root)['unknown_steps']
        else:
            assert intent['continuation_kind']=='automatic_recovery'
            assert intent['automatic_recovery']['action']=='retry' and intent['automatic_recovery']['reason']=='native_temporary'
            assert not intent['automatic_recovery'].get('format_fallback',False)
            assert not intent['automatic_recovery']['startup_retry']
            assert recovery.counters([intent])==dict(continues=0,retries=1,wait_seconds=0.)
            assert old['native_failure']['category']=='temporary' and not (first/'usage.json').exists()
            assert intent['budget_slice']['previous_tokens']==0
            assert native.usage(root)['unknown_steps']==[step]
        if final:assert intent['budget_slice']['final_return']==original_intent['budget_slice']['final_return']
    else:
        assert native.usage(root)['unknown_steps']==[]
        assert not (first/'continuations').exists()
    assert native.usage(root)['observed_tokens']==(14*expected_attempts if final else 14*(expected_attempts+1) if fault!='format-transient' else 28)
    if final:assert not (root/'jobs/002.json').exists()
    elif role=='A':assert len(execution.records(root,1))==1 and c.read(execution.origin(root,1)/'result-identity.json')
    retained={path:path.read_bytes() for path in (first/'native-outcome.json',first/'native-intent.json',first/'native.jsonl')}
    assert (controller.closeout(root) if final else controller.run(root))['status']==result['status']
    assert len(execution.records(root,step))==len(paths)
    assert all(path.read_bytes()==raw for path,raw in {**originals,**schemas,**retained}.items())


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault',['format-other400','format-activity','format-prose'])
def test_production_prompt_permanent_failure_does_not_start_schema_negotiation(tmp_path,monkeypatch,fault):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller
    schemas=retained_strict_contract(monkeypatch)
    root,_,env=prepare_cli(tmp_path,fault=fault)
    (tmp_path/'native.py').write_text('#!'+sys.executable+'\n'+FORMAT_NATIVE)
    config=c.read(tmp_path/'launcher-config.json');config['format_role']='H';c.write(tmp_path/'launcher-config.json',config)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key,env[key])
    assert controller.run(root)['status']!='finish'
    first,=execution.records(root,1)
    assert not (root/'jobs/002.json').exists()
    launches=[json.loads(line) for line in (c.workspace(root,1)/'work/format-launches.jsonl').read_text().splitlines()]
    assert launches==[dict(native=False,resume=False,role='H')]
    assert c.read(first/'native-outcome.json')['native_schema_transport']=='prompt'
    assert all(path.read_bytes()==raw for path,raw in schemas.items())
    retained={path:path.read_bytes() for path in (first/'native-intent.json',first/'native-outcome.json',first/'native.jsonl')}
    assert controller.run(root)['status']!='finish'
    assert execution.records(root,1)==[first]
    assert all(path.read_bytes()==raw for path,raw in retained.items())


@pytest.mark.process_e2e
@pytest.mark.parametrize('cap,fault,attempts,success',[(0,'format',1,True),(0,'format-transient',1,False),
    (1,'format-always-transient',2,False),(3,'format-repeat',2,False)])
def test_production_ordinary_prompt_obeys_retry_cap_without_schema_requests(tmp_path,monkeypatch,cap,fault,attempts,success):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller,native
    schemas=retained_strict_contract(monkeypatch)
    root,_,env=prepare_cli(tmp_path,fault=fault,automatic=dict(max_retries=cap))
    (tmp_path/'native.py').write_text('#!'+sys.executable+'\n'+FORMAT_NATIVE)
    config=c.read(tmp_path/'launcher-config.json');config['format_role']='H';c.write(tmp_path/'launcher-config.json',config)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key,env[key])
    monkeypatch.setattr(controller,'_recovery_remaining',lambda *_:0.)
    result=controller.run(root)
    assert (result['status']=='finish') is success,result
    paths=execution.records(root,1)
    assert len(paths)==attempts
    if not success:assert not (root/'jobs/002.json').exists() and native.usage(root)['unknown_steps']==[1]
    launches=[json.loads(line) for line in (c.workspace(root,1)/'work/format-launches.jsonl').read_text().splitlines()]
    assert launches==[dict(native=False,resume=number>0,role='H') for number in range(attempts)]
    if attempts>1:
        intent=c.read(paths[1]/'native-intent.json')
        assert intent['session']['thread_id']==c.read(paths[0]/'native-outcome.json')['thread_id']
        assert intent['automatic_recovery']['action']=='retry' and not intent['automatic_recovery'].get('format_fallback',False)
        assert recovery.counters([intent])['retries']==1
    assert all(path.read_bytes()==raw for path,raw in schemas.items())


@pytest.mark.process_e2e
def test_final_ordinary_prompt_failure_preserves_protected_scope_and_exact_original(tmp_path,monkeypatch):
    import sys
    from test_default_cli_output_repair import prepare_cli
    from scidiscovery.research_loop import controller,native
    schemas=retained_strict_contract(monkeypatch)
    root,home,env=prepare_cli(tmp_path,fault='format-transient',final=True)
    (tmp_path/'native.py').write_text('#!'+sys.executable+'\n'+FORMAT_NATIVE)
    config=c.read(tmp_path/'launcher-config.json');config['format_role']='A';c.write(tmp_path/'launcher-config.json',config)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key,env[key])
    result=controller.closeout(root)
    assert result['status']!='final_a_completed',result
    first,=execution.records(root,1)
    intent=c.read(first/'native-intent.json');outcome=c.read(first/'native-outcome.json')
    assert intent['budget_slice']['final_return'] and execution.home(root,1,0)==home
    assert outcome['native_schema_transport']=='prompt' and outcome['native_failure']['category']=='temporary'
    assert not (first/'usage.json').exists() and native.usage(root)['unknown_steps']==[1]
    assert not (root/'jobs/002.json').exists()
    assert [json.loads(line) for line in (c.workspace(root,1)/'work/format-launches.jsonl').read_text().splitlines()]==[dict(native=False,resume=False,role='A')]
    retained={path:path.read_bytes() for path in (root/'settings.json',home/'config.toml',home/'auth.json',
        first/'native-intent.json',first/'native-outcome.json',first/'native.jsonl')}
    assert controller.closeout(root)['status']!='final_a_completed'
    assert execution.records(root,1)==[first] and not (root/'jobs/002.json').exists()
    assert all(path.read_bytes()==raw for path,raw in {**schemas,**retained}.items())
