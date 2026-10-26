"""Automatic recovery is a bounded transport grant, never scientific success."""
import copy
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


@pytest.mark.parametrize('scope', ['author_closeout','startup_successor','execution_wake','output_repair','work_repair'])
def test_narrow_original_scope_does_not_acquire_full_task_recovery(tmp_path, monkeypatch, scope):
    root, record = arbiter_instance(tmp_path, monkeypatch)
    original = (dict(continuation_kind=scope) if scope in {'author_closeout','startup_successor','execution_wake'}
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


AUTOMATIC_NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read();resume='resume' in args
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
 assert thread in args and 'Continue completing the current assigned task' in prompt
 assert 'Repair only your final response' not in prompt
 assert (work/'work/retained-observation').read_text()=='one exact observation'
schema=json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
payload={name:None for name in schema['properties']}
payload.update(summary='Recovered bounded transport',scientific_result='Retained observations, no scientific qualification.',
 limitations=['Only native transport is simulated.'],implementation_selection=None,
 next_action='analyze' if role=='H' else 'finish',route_reason='Review the retained bounded observation.')
if role=='A':
 payload['selection_explanation']=dict(criteria='Retain the available bounded observation.',
  criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
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
    """Actual installed CLI, isolated loopback provider, no authentication/API."""
    import json
    import os
    import shutil
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    program = shutil.which('codex')
    if program is None:pytest.skip('Installed Codex CLI is unavailable')
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append(dict(method='POST',path=self.path,has_authorization=self.headers.get('Authorization') is not None))
            assert body and self.path == '/v1/responses'
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
        if key not in {'OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID'}}
    env['CODEX_HOME'] = str(home)
    try:
        completed = subprocess.run(args,input='Return a bounded fixture answer.\n',env=env,
            cwd=workspace,capture_output=True,text=True,timeout=25)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    events = []
    for line in completed.stdout.splitlines():
        try:value = json.loads(line)
        except ValueError:continue
        if isinstance(value,dict) and value.get('type') in {'error','turn.failed'}:events.append(value)
    sample = dict(status=status,code=code,returncode=completed.returncode,events=events,requests=requests)
    encoded_sample = json.dumps(sample,ensure_ascii=False,indent=2)+'\n'
    (tmp_path / 'native-failure-sample.json').write_text(encoded_sample)
    if os.environ.get('SCID_TEST_LOG_DIR'):
        from pathlib import Path
        destination = Path(os.environ['SCID_TEST_LOG_DIR'])
        destination.mkdir(parents=True,exist_ok=True)
        (destination / f'codex-http-{status}-{code}.json').write_text(encoded_sample)
    print('CODEX_HTTP_CONFORMANCE ' + json.dumps(sample,ensure_ascii=False))
    assert requests, (completed.returncode,completed.stdout,completed.stderr)
    assert all(not row['has_authorization'] for row in requests)
    assert completed.returncode != 0 and events, (completed.stdout,completed.stderr)
    expected = 'permanent' if code == 'insufficient_quota' else 'temporary'
    assert recovery.combine([recovery.classify(event) for event in events])['category'] == expected


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
 assert 'Continue completing the current assigned task' in prompt
thread='11111111-2222-4333-8444-'+('%012d'%int(work.name))
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
session=home/'sessions'/('rollout-'+thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
schema=json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
payload={name:None for name in schema['properties']}
payload.update(summary='Bounded request startup',scientific_result='No scientific qualification is asserted.',
 limitations=['Only native transport is simulated.'],implementation_selection=None,
 next_action='analyze' if role=='H' else 'finish',route_reason='Review the retained bounded observation.')
if role=='A':
 payload['selection_explanation']=dict(criteria='Retain the available bounded observation.',
  criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
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
