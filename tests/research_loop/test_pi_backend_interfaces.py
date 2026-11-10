"""Pi interface completion; deterministic peers are not scientific evidence.

Process nodes run the production durable runner outside pytest, never cleanup a
native group in the pytest process. No provider, installation or live model.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (accepted_prefix, backends, budget, continuation,
    contracts as c, delivery_review, execution, experiment_client, native, native_recovery, profiles,
    research_config, shared_input_views as views)
from scidiscovery.research_loop import pi_backend as pi, pi_namespace, pi_sessions, pi_transport
from test_pi_backend_v2 import (AUX, FAKE_RPC, FINAL, OPTIONS, PROFILE, STAMP, TOKENS,
    USAGE, adapter, collect_separately, configure_installed_pi_fixture, installed_pi_fixture,
    loopback, message, options, require_namespace, settings)

RESUME = dict(protocol='pi_rpc_v1', usage_scope='turn', max_attempts=1,
    output_repair=True, output_only=True, execution_wake=False)


def original(tmp_path, rows=None, *, profile=PROFILE):
    home, work = tmp_path/'home', tmp_path/'workspace'
    (home/'sessions').mkdir(parents=True); work.mkdir()
    path = home/'sessions/pi-session.jsonl'
    header = dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work))
    rows = rows if rows is not None else [
        dict(type='message', id='user', parentId=None, timestamp=STAMP,
            message=dict(role='user', content='Original task', timestamp=0)),
        dict(type='message', id='msg', parentId='user', timestamp=STAMP,
            message=dict(message(), provider=profile['model'].split('/', 1)[0],
                model=profile['model'].split('/', 1)[1], thinkingLevel=profile['reasoning_effort']))]
    data = ''.join(json.dumps(row)+'\n' for row in [header, *rows]).encode()
    path.write_bytes(data)
    info = path.stat()
    session = dict(backend='pi', home=str(home), workspace=str(work), path=str(path),
        thread_id='exact-session', profile=dict(profile), header=json.dumps(header)+'\n',
        identity=[info.st_dev, info.st_ino], size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return home, work, path, session


@pytest.mark.parametrize('role', list('HWAE'))
@pytest.mark.parametrize('formal', [False, True])
def test_ordinary_roles_and_owner_response_schemas_use_one_pi_request(tmp_path, role, formal):
    backend = adapter()
    backend.project_skills(tmp_path, settings(), dict(role=role, formal_review=formal))
    home, work = tmp_path/'home', tmp_path/'workspace'
    backend.bind_attempt(home, work)
    backend.launch(PROFILE, work, None, None, 'Original role contract')
    schema = dict(type='object', properties={'owner_field': {'const': role}}, required=['owner_field'])
    prompt = 'Original role contract\nOriginal task, scope, permissions and budget'
    request = backend.request(prompt, profile=PROFILE, work=work, schema=None,
        formal_review=formal, response_schema=schema).decode()
    assert request.count(prompt) == 1
    assert request.count(json.dumps(schema, separators=(',', ':'))) == 1
    assert backend.complete_authorship is True and backend.authorship_scope({}) is False
    with pytest.raises(ValueError):
        backend.request(prompt, profile=dict(PROFILE, model='fixture/other'), work=work,
            schema=None, response_schema=schema)


@pytest.mark.parametrize('kind,used,role,field,choices', [
    ('delivery_review', 0, 'A', 'next_action', ['deliver', 'finish', 'continue_w']),
    ('delivery_review', 1, 'A', 'next_action', ['deliver', 'finish']),
    ('delivery_correction', 1, 'W', 'stage_intent', ['continue', 'handoff', 'decision']),
])
def test_v3_pi_request_keeps_current_delivery_schema_and_remaining_shared_budget(
        tmp_path, monkeypatch, kind, used, role, field, choices):
    # Frozen phase/balance rendering only, not a review verdict or execution
    # allocation. The shared owner, not Pi, narrows correction and final routes.
    job = dict(step=3, role=role, research_strategy_version=3,
        evidence_return=delivery_review.make(kind, 2, used))
    status = dict(shared_seconds_remaining=11, shared_observed_tokens_remaining=19)
    remaining = dict(remaining_observed_tokens=7, remaining_active_seconds=5,
        previous_consumed_observed_tokens=44, mode='soft_targets')
    originals = copy.deepcopy((job, status, remaining))
    prompt, instruction, schema = native.render_scientific_request(job, {}, {},
        backend_name='pi', instruction_transport='prompt', budget_status=status, remaining=remaining)
    assert schema['properties'][field]['enum'] == choices
    if kind == 'delivery_correction':
        assert schema['properties']['search_plan'] == dict(type='null')
        assert 'consultation_question' not in c.response_instructions(job)['stage_response']
    backend = adapter(); work = tmp_path/'workspace'
    backend.bind_attempt(tmp_path/'home', work)
    backend.launch(PROFILE, work, None, None, instruction)
    def forbidden(*a, **kw): pytest.fail('Request consumer reacquired control or launched a process')
    with monkeypatch.context() as guarded:
        for name in ('stat', 'open', 'resolve'): guarded.setattr(Path, name, forbidden)
        guarded.setattr(c, 'read', forbidden)
        guarded.setattr(subprocess, 'Popen', forbidden)
        request = backend.request(prompt, profile=PROFILE, work=work, schema=None,
            response_schema=schema).decode()
    assert request.count(prompt) == 1
    for value in (schema, status, remaining):
        assert request.count(json.dumps(value, ensure_ascii=False, separators=(',', ':'))) == 1
    assert (job, status, remaining) == originals
    assert backend.complete_authorship is True


@pytest.mark.parametrize('question', [None, '', ' \t', 'What limitation remains in the exact retained observation?'])
def test_v3_pi_review_question_uses_current_acceptance_without_rewriting_original(tmp_path, question):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    job = dict(role='W', research_strategy_version=3)
    payload = dict(summary='Mechanical consultation', scientific_result='No scientific qualification.',
        limitations=['Source contract fixture only.'], stage_intent='review')
    if question is not None: payload['consultation_question'] = question
    raw = (' \n' + json.dumps(payload) + '\n\t').encode()
    path = tmp_path/'science-output.json'; path.write_bytes(raw)
    if question is None or not question.strip():
        with pytest.raises(OutputRepairRequired, match='consultation_question'):
            c.science_payload(path, 'W', **c.response_options(job))
    else:
        assert c.science_payload(path, 'W', **c.response_options(job)) == payload
    assert path.read_bytes() == raw


@pytest.mark.parametrize('workspace', ['read-only', 'workspace-write'])
def test_explicit_pi_configuration_accepts_roles_resume_and_retains_current_defaults(tmp_path, workspace):
    value = options(tmp_path, native_options=dict(OPTIONS, workspace=workspace), resume_policy=dict(RESUME),
        role_profiles={r: {'reasoning_effort': 'low'} for r in 'HWAE'},
        worker_profiles={'unused': {'model': 'fixture/another-exact-model'}},
        worker_fragment_policy={'context': 'same_session'})
    before = copy.deepcopy(value)
    normalized = research_config.normalize(value)
    assert value == before
    assert normalized['native_options']['workspace'] == workspace
    assert normalized['resume_policy'] == RESUME
    assert normalized['worker_fragment_policy'] == {'context': 'same_session', 'max_context_windows': 1}
    assert normalized['experiment_mode'] == 'external'  # Explicit fixture scope, not provider cadence.
    assert adapter().default_experiment_mode() == 'local'
    assert pi.DEFAULT_RESUME_POLICY['protocol'] == 'pi_rpc_v1'
    assert pi.DEFAULT_RESUME_POLICY['output_repair'] is True
    assert before['search_policy'] is None
    assert 'search_policy' in value and 'search_policy' in normalized
    assert normalized['search_policy'] == value['search_policy'] == before['search_policy']


@pytest.mark.parametrize('bad', [dict(protocol='other'), dict(usage_scope='session'),
    dict(max_attempts=0), dict(max_attempts=True), dict(max_attempts=3),
    dict(native_home='/auth-redirect'), dict(output_only='true'), dict(unknown=True)])
def test_invalid_resume_protocol_and_auth_redirect_refuse_without_effects(monkeypatch, bad):
    def forbidden(*a, **kw):pytest.fail('Invalid declarations acquired mutable state')
    monkeypatch.setattr(Path, 'open', forbidden); monkeypatch.setattr(Path, 'stat', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden); monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError):pi.Pi.validate_declaration(settings(resume_policy={**RESUME, **bad}))


def test_isolated_session_home_never_changes_inherited_home_or_pi_auth():
    env = dict(HOME='/original/home', PI_CODING_AGENT_DIR='/original/agent', OTHER='retained')
    before = dict(env); backend = adapter()
    backend.apply_home(env, Path('/private/framework/session-home'), isolated=True)
    assert env == before
    assert backend.actual_home(env, Path('/workspace'), Path('/private/framework/session-home'), isolated=True) == Path('/private/framework/session-home')


def test_new_pi_default_resume_grants_shared_finite_recovery_without_changing_frozen_policy(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda executable: '/explicit/native/pi')
    selected = adapter().default_resume_policy()
    assert selected['launcher'] == ['/explicit/native/pi']
    expected = dict(pi.DEFAULT_RESUME_POLICY, automatic_recovery=native_recovery.policy({}))
    assert selected['policy'] == expected
    assert selected['policy']['automatic_recovery']['max_retries'] == 8
    assert selected['policy']['automatic_recovery']['max_attempts'] == 8
    pi.validate_resume_policy(selected['policy'])
    assert 'native_home' not in selected['policy']
    frozen = dict(RESUME)
    pi.validate_resume_policy(frozen)
    assert frozen == RESUME and 'automatic_recovery' not in frozen


def test_observed_session_identity_and_session_event_are_exact_pure_projections(tmp_path, monkeypatch):
    backend = adapter(); home, work = tmp_path/'home', tmp_path/'workspace'
    backend.bind_attempt(home, work); backend.launch(PROFILE, work, None, None, 'scope')
    meter = backend.meter(home, work)
    assert backend.identity(meter, None)['path'] is None
    assert backend.identity(meter, None)['session'] is None
    path = home/'sessions/pi-session.jsonl'
    header = dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work))
    path.write_text(json.dumps(header)+'\n'+json.dumps(dict(type='message', id='msg',
        parentId=None, timestamp=STAMP, message=message()))+'\n')
    backend.poll_observation(meter)
    assert backend.identity(meter, 'exact-session')['session'] is None
    backend.finish_observation(meter, required=False)
    identity = backend.identity(meter, 'exact-session')
    binding = identity['session']
    assert binding['path'] == str(path) and binding['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    event = dict(type='pi.session_bound', thread_id='exact-session', path=str(path),
        workspace=str(work), profile=dict(PROFILE))
    backend.validate_resume_session(binding, work, home)
    def forbidden(*a, **kw):pytest.fail('Pure identity/session validator reopened original facts')
    monkeypatch.setattr(Path, 'open', forbidden); monkeypatch.setattr(Path, 'stat', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden); monkeypatch.setattr(os, 'open', forbidden)
    assert backend.identity(meter, 'exact-session') == identity
    backend.validate_session_event(event, 'exact-session')
    for key, value in [('path', '/different'), ('workspace', '/different'), ('thread_id', 'different'),
            ('profile', dict(PROFILE, model='fixture/different'))]:
        with pytest.raises(ValueError):backend.validate_session_event({**event, key: value}, 'exact-session')


def test_prelaunch_identity_is_not_a_planned_session_original(tmp_path):
    backend = adapter(); home, work = tmp_path/'home', tmp_path/'workspace'
    backend.bind_attempt(home, work); backend.launch(PROFILE, work, None, None, 'scope')
    meter = backend.meter(home, work)
    outcome = dict(stop_reason='shared_input_view_unsupported', native_started=False,
        returncode=None, terminal_observed=False, native_activity_observed=False, thread_id=None,
        native_thread_event=None, native_failure=None, cleanup=None, native_identity=backend.identity(meter, None),
        native_stream=dict(size_bytes=0), metering=meter.snapshot(), shared_inputs=dict(enforcement='not_started'))
    assert native_recovery.never_started(outcome)
    assert not native_recovery.never_started(dict(outcome, native_started=True))
    assert not native_recovery.never_started(dict(outcome, native_identity={'path': str(meter.path)}))
    for kind in ('resume', 'automatic_recovery', 'technical_restart'):
        assert backend.recovery_capability(kind)['status'] == 'supported'
    for kind in ('format_fallback', 'arbitrary_replay'):
        assert backend.recovery_capability(kind)['status'] == 'unsupported'


def failed_controlled_exit(tmp_path, *, diagnostic='fetch failed'):
    """One settled failed native turn and its exact pure lifecycle receipt."""
    from scidiscovery.research_loop import pi_observation
    backend = adapter(); home, work = tmp_path/'home', tmp_path/'workspace'
    backend.bind_attempt(home, work)
    args = backend.launch(PROFILE, work, None, None, 'Original scope')
    meter = backend.meter(home, work)
    meter.observation.prompt_hash = 'b' * 64
    proof = backend._observation_proof
    policy = pi_transport.resource_policy()
    argv = [*args[4:], '--extension', proof['extension']]
    backend._launch_evidence = dict(kind='pi_cli_closed_resources_v1', launcher=argv[0],
        resource_policy=policy, resources=[], observer=dict(proof), source_readonly=True,
        observer_source=dict(version=1, path=proof['extension'], sha256='a'*64, size_bytes=1))
    backend.event(dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work)))
    failure = backend.event(dict(type='message_end', message=dict(message(reason='error'),
        errorMessage=diagnostic)))['failure']
    assert backend.event(dict(type='agent_settled', aborted=False)) == dict(failed=True)
    start = dict(type=pi_observation.BOUND, nonce=proof['nonce'], path=proof['path'],
        workspace=proof['workspace'], session_id='exact-session')
    receipt = dict(type=pi_transport.CONTROLLED_EXIT, version=1, argv=argv,
        session_id='exact-session', path=proof['path'], workspace=str(work), profile=dict(PROFILE),
        resource_policy=policy, resources=[], observer_start=start,
        observer_command=pi_observation.command_proof(proof['extension']),
        prompt_sha256=meter.observation.prompt_hash, disposition='started',
        settled=dict(type='agent_settled', aborted=False), idle=True, stdin_closed=True,
        stdout_eof=True, drained=True, pending_commands=0, returncode=0)
    return backend, meter, receipt, failure


@pytest.mark.parametrize('diagnostic,category', [('fetch failed', 'temporary'),
    ('401: {"type":"invalid_api_key"}', 'permanent'), ('unqualified native diagnostic', 'unknown')])
def test_settled_failed_controlled_exit_keeps_failure_without_completion_or_authorship(
        tmp_path, monkeypatch, diagnostic, category):
    backend, meter, receipt, failure = failed_controlled_exit(tmp_path, diagnostic=diagnostic)
    original = copy.deepcopy(receipt)
    def forbidden(*args, **kwargs): pytest.fail('Lifecycle consumer reacquired originals or launched work')
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'stat', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert backend.event(receipt) == {}
    assert backend._controlled_exit == original and receipt == original
    assert meter.observation.fault is None
    assert backend.engine.final is None and backend.engine.failures == [failure]
    assert backend.combine_failures([failure])['category'] == category
    assert backend.identity(meter, 'exact-session')['participant_scope'] == 'unsupported'


@pytest.mark.parametrize('change', [dict(returncode=1), dict(prompt_sha256='c'*64),
    dict(session_id='different'), dict(profile=dict(PROFILE, model='fixture/different')),
    dict(drained=False), dict(settled=dict(type='agent_settled', aborted=True))])
def test_failed_controlled_exit_still_rejects_changed_identity_or_unverified_lifecycle(tmp_path, change):
    backend, meter, receipt, _ = failed_controlled_exit(tmp_path)
    event = backend.event(dict(receipt, **change))
    assert event['failed'] and event['failure']['category'] == 'unknown'
    assert meter.observation.fault == 'pi_authorship_exit_invalid'
    assert backend._controlled_exit is None and 'completed' not in event


def test_failed_controlled_exit_cannot_be_accepted_twice(tmp_path):
    backend, meter, receipt, _ = failed_controlled_exit(tmp_path)
    assert backend.event(receipt) == {}
    assert backend.event(receipt)['failure']['category'] == 'unknown'
    assert meter.observation.fault == 'pi_authorship_exit_invalid'


@pytest.mark.parametrize('content', ['Original task', [dict(type='text', text='Original task')]])
def test_resume_user_message_accepts_text_and_text_blocks_without_summary(content):
    pi_sessions.validate_message(dict(role='user', content=content, timestamp=0), PROFILE)


@pytest.mark.parametrize('role', ['compactionSummary', 'branchSummary'])
def test_resume_summary_message_accepts_string_summary(role):
    pi_sessions.validate_message(dict(role=role, summary='Original summary', timestamp=0), PROFILE)


@pytest.mark.parametrize('role', ['compactionSummary', 'branchSummary'])
@pytest.mark.parametrize('fields', [{}, dict(summary=None), dict(summary=1)])
def test_resume_summary_message_rejects_missing_or_non_string_summary(role, fields):
    with pytest.raises(ValueError, match='Pi resume summary invalid'):
        pi_sessions.validate_message(dict(role=role, timestamp=0, **fields), PROFILE)


@pytest.mark.parametrize('fields', [dict(role='user'), dict(role='user', content=None),
    dict(role='user', content=[dict(type='text', text=1)]), dict(role='unknown', content='Original task')])
def test_resume_user_content_and_unknown_role_still_reject_with_summary(fields):
    with pytest.raises(ValueError):
        pi_sessions.validate_message(dict(timestamp=0, summary='Not a content substitute', **fields), PROFILE)


def test_resume_uses_owner_original_once_and_bills_only_appended_tail_and_shutdown(tmp_path, monkeypatch):
    home, work, path, session = original(tmp_path)
    historical = path.read_bytes()
    backend = adapter(resume_policy=dict(RESUME))
    backend.validate_resume_session(session, work, home)
    monkeypatch.setattr(pi_sessions, 'load_resume_original', lambda *a, **k: pytest.fail('Consumer reacquired owner original'))
    backend.bind_attempt(home, work, session)
    args = backend.launch(PROFILE, work, None, None, 'same scope')
    args, position = backend.resume(args, PROFILE, work, None, None, session)
    assert position is None and json.loads(args[4]) == session
    assert args[args.index('--session')+1] == str(path)
    meter = backend.meter(home, work, session, context_state=dict(coverage='unknown', windows_started=1))
    assert meter.snapshot()['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    msg = message(stamp=2)
    with path.open('ab') as stream:
        for row in [dict(type='message', id='msg2', parentId='msg', timestamp=STAMP, message=msg),
                dict(type='usage', id='shutdown', parentId='msg2', timestamp=STAMP, kind='future-operation', usage=AUX)]:
            stream.write((json.dumps(row)+'\n').encode())
    backend.engine.usage.add(USAGE, identity=pi_sessions.message_identity(msg))
    observed = backend.finish_observation(meter, required=False)
    assert observed['coverage'] == 'terminal' and observed['tokens'] == TOKENS
    assert observed['baseline_offset'] == len(historical)
    assert observed['context_windows']['windows_started'] == 1
    assert path.read_bytes().startswith(historical)


@pytest.mark.parametrize('model_id', ['nested/model:exacto', 'model:high', 'model[1m]'])
def test_literal_model_launch_resume_and_session_receipt_preserve_full_identity(tmp_path, monkeypatch, model_id):
    profile = dict(PROFILE, model='fixture/' + model_id)
    home, work, path, session = original(tmp_path, profile=profile)
    backend = adapter(model=profile['model'], resume_policy=dict(RESUME))
    backend.validate_resume_session(session, work, home)
    backend.bind_attempt(home, work, session)
    args = backend.launch(profile, work, None, None, 'same scope')
    args, position = backend.resume(args, profile, work, None, None, session)
    assert position is None and json.loads(args[4]) == session
    assert args[args.index('--model') + 1] == model_id
    assert args[args.index('--thinking') + 1] == 'high'
    child, provider, actual_id, thinking, actual_path, skills = pi_transport._arguments(args[5:])
    assert (provider, actual_id, thinking, actual_path, skills) == ('fixture', model_id, 'high', str(path), ())
    event = dict(type='pi.session_bound', thread_id=session['thread_id'], path=str(path),
        workspace=str(work), profile=dict(profile))
    def forbidden(*a, **kw): pytest.fail('Identity consumer reacquired original or native state')
    for name in ('open', 'stat', 'resolve'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    backend.validate_session_event(event, session['thread_id'])
    substituted = dict(profile, model='fixture/model')
    with pytest.raises(ValueError):
        backend.resume(args, substituted, work, None, None, session)
    with pytest.raises(ValueError):
        backend.validate_session_event(dict(event, profile=substituted), session['thread_id'])


@pytest.mark.parametrize('fault', ['malformed', 'partial', 'version2', 'wrong_cwd', 'wrong_model',
    'duplicate_id', 'missing_content', 'deferred', 'unsettled_tool', 'symlink', 'hardlink', 'replaced'])
def test_resume_rejects_unsafe_native_migration_corruption_and_wrong_binding(tmp_path, fault):
    home, work, path, session = original(tmp_path)
    rows = [json.loads(line) for line in path.read_bytes().split(b'\n') if line]
    if fault == 'malformed':path.write_bytes(path.read_bytes()+b'{bad}\n')
    elif fault == 'partial':path.write_bytes(path.read_bytes()+b'{partial')
    elif fault == 'symlink':
        other = home/'other'; path.rename(other); path.symlink_to(other)
    elif fault == 'hardlink':os.link(path, home/'outside-link')
    elif fault == 'replaced':
        other = home/'replacement'; other.write_bytes(path.read_bytes()); other.replace(path)
    else:
        if fault == 'version2':rows[0]['version'] = 2
        elif fault == 'wrong_cwd':rows[0]['cwd'] = str(tmp_path/'other-work')
        elif fault == 'wrong_model':rows[-1]['message']['model'] = 'different'
        elif fault == 'duplicate_id':rows[-1]['id'] = rows[-2]['id']
        elif fault == 'missing_content':del rows[-1]['message']['content']
        elif fault == 'deferred':rows[-1]['message']['stopReason'] = 'deferred'
        elif fault == 'unsettled_tool':
            rows[-1]['message']['stopReason'] = 'toolUse'
            rows[-1]['message']['content'] = [dict(type='toolCall', id='pending', name='bash', arguments={})]
        data = ''.join(json.dumps(row)+'\n' for row in rows).encode(); path.write_bytes(data)
        # Even a self-consistent byte proof cannot authorize permissive native
        # migration, coercion, another working directory or model, or tool replay.
        session.update(header=json.dumps(rows[0])+'\n', size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    before = path.read_bytes()
    with pytest.raises(ValueError):adapter().validate_resume_session(session, work, home)
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind', ['usage', 'model_change', 'thinking_level_change'])
def test_resume_refuses_valid_unaccounted_append_without_changing_retained_debit(tmp_path, kind):
    home, work, path, session = original(tmp_path)
    retained = copy.deepcopy(session)
    prior = pi_sessions.SessionUsage(home, work)
    prior.profile = dict(PROFILE)
    accounted = prior.terminal()
    assert accounted['coverage'] == 'terminal'
    row = dict(type=kind, id='late', parentId='msg', timestamp=STAMP)
    row.update(dict(usage=AUX) if kind == 'usage' else
        dict(provider='fixture', modelId='exact-model') if kind == 'model_change' else
        dict(thinkingLevel='high'))
    with path.open('ab') as stream:stream.write((json.dumps(row)+'\n').encode())
    backend = adapter(resume_policy=dict(RESUME))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='unaccounted append'):
        backend.validate_resume_session(session, work, home)
    assert backend._resume_original is None
    assert session == retained and path.read_bytes() == before
    assert accounted['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert prior.snapshot()['tokens'] == accounted['tokens']


def test_live_poll_cannot_reuse_previous_terminal_coverage_or_proof(tmp_path, monkeypatch):
    home, work, path, session = original(tmp_path)
    meter = pi_sessions.SessionUsage(home, work); meter.profile = dict(PROFILE)
    terminal = meter.terminal()
    assert terminal['coverage'] == 'terminal' and terminal['session_binding'] == session
    def forbidden():pytest.fail('Live poll performed a stopped-writer rehash')
    monkeypatch.setattr(meter, '_verify_stopped_original', forbidden)
    live = meter.poll()
    assert live['coverage'] == 'live' and live['session_binding'] is None
    assert not meter.finished and live['tokens'] == terminal['tokens']


@pytest.mark.parametrize('resumed', [False, True])
def test_terminal_rehash_rejects_early_rewrite_with_valid_append_and_retains_debit(tmp_path, resumed):
    rows = [dict(type='message', id='user', parentId=None, timestamp=STAMP,
        message=dict(role='user', content='Original task'+'x'*8192, timestamp=0)),
        dict(type='message', id='msg', parentId='user', timestamp=STAMP,
            message=message('padding outside the tail anchor'+'y'*8192))]
    home, work, path, session = original(tmp_path, rows)
    loaded = pi_sessions.load_resume_original(session, work, home) if resumed else None
    meter = pi_sessions.SessionUsage(home, work, session if resumed else None,
        resume_original=loaded)
    meter.profile = dict(PROFILE)
    meter.poll()
    assert meter.session_binding() is None  # Live anchors are not reusable proof.
    previous = path.read_bytes()
    changed = previous.replace(b'Original task', b'Changed! task')
    assert len(previous) == len(changed) and previous[-4096:] == changed[-4096:]
    path.write_bytes(changed+(json.dumps(dict(type='usage', id='late', parentId='msg',
        timestamp=STAMP, usage=AUX))+'\n').encode())
    # The inexpensive live check intentionally cannot see this early rewrite.
    live = meter.poll()
    expected = pi_sessions.usage_tokens(AUX) if resumed else TOKENS
    assert live['tokens'] == expected and meter.fault is None
    observed = meter.terminal()
    assert observed['coverage'] == 'unknown'
    assert observed['reason'] == 'session_terminal_original_changed_or_unverified'
    assert observed['tokens'] == expected and observed['has_observation']
    assert observed['session_binding'] is None and meter.session_binding() is None
    assert adapter().terminal_usage(dict(completed=True, post_exit=True), meter) is None


@pytest.mark.parametrize('change', ['append', 'replace', 'rewrite'])
def test_terminal_rehash_checks_snapshot_before_and_after_read(tmp_path, monkeypatch, change):
    home, work, path, session = original(tmp_path)
    meter = pi_sessions.SessionUsage(home, work); meter.profile = dict(PROFILE)
    meter.poll()
    original_open = meter._open
    class ChangingReader:
        def __enter__(self):
            self.stream = original_open(); return self
        def __exit__(self, *args):self.stream.close()
        def fileno(self):return self.stream.fileno()
        def read(self, count):
            data = self.stream.read(count)
            if change == 'replace':
                replacement = home/'replacement'; replacement.write_bytes(path.read_bytes()); replacement.replace(path)
            elif change == 'rewrite':
                path.write_bytes(path.read_bytes().replace(b'Original task', b'Changed! task'))
            else:
                with path.open('ab') as stream:stream.write(b'\n')
            return data
    monkeypatch.setattr(meter, '_open', ChangingReader)
    # Exercise the stopped-writer verifier directly; live polls do not rehash.
    meter._verify_stopped_original()
    assert meter.fault == 'session_terminal_original_changed_or_unverified'
    assert meter.session_binding() is None
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)


@pytest.mark.parametrize('change', ['rewrite', 'unaccounted_append'])
def test_resume_effect_boundary_rechecks_early_prefix_not_just_tail_anchor(tmp_path, change):
    home, work, path, session = original(tmp_path)
    loaded = pi_sessions.load_resume_original(session, work, home)
    data = path.read_bytes()
    if change == 'rewrite':data = data.replace(b'Original task', b'Changed! task')
    else:
        data += (json.dumps(dict(type='usage', id='late', parentId='msg',
            timestamp=STAMP, usage=AUX))+'\n').encode()
    path.write_bytes(data)
    with pytest.raises(ValueError):pi_sessions.recheck_prefix(loaded)
    assert adapter().complete_authorship is True


@pytest.mark.parametrize('setup', [('model_change',), ('thinking_level_change',),
    ('model_change', 'thinking_level_change')])
def test_resume_post_start_setup_preserves_proof_and_meter_baseline_and_scans_entire_tail(tmp_path, setup):
    home, work, path, session = original(tmp_path)
    historical = path.read_bytes()
    loaded = pi_sessions.load_resume_original(session, work, home)
    seen = set(loaded['seen']); digest = loaded['digest'].hexdigest()
    parent = 'msg'; rows = []
    for kind in setup:
        row = dict(type=kind, id=kind, parentId=parent, timestamp=STAMP)
        row.update(dict(provider='fixture', modelId='exact-model') if kind == 'model_change'
            else dict(thinkingLevel='high'))
        rows.append(row); parent = kind
    with path.open('ab') as stream:
        for row in rows:stream.write((json.dumps(row)+'\n').encode())
    with pytest.raises(ValueError, match='unaccounted append'):
        pi_sessions.recheck_prefix(loaded, phase='prelaunch')
    pi_sessions.recheck_prefix(loaded, phase='native_setup')
    assert loaded['seen'] == seen and loaded['digest'].hexdigest() == digest
    assert loaded['info'].st_size == loaded['session']['size_bytes'] == len(historical)
    # Construct after SDK startup too: current EOF must never become baseline.
    meter = pi_sessions.SessionUsage(home, work, session, resume_original=loaded)
    setup_observed = meter.poll()
    assert setup_observed['baseline_offset'] == len(historical)
    assert setup_observed['offset'] == path.stat().st_size > len(historical)
    assert set(setup) <= meter.seen
    assert setup_observed['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    with path.open('ab') as stream:
        for row in [dict(type='message', id='msg2', parentId=parent, timestamp=STAMP,
                message=message(stamp=2)), dict(type='usage', id='shutdown', parentId='msg2',
                timestamp=STAMP, usage=AUX)]:
            stream.write((json.dumps(row)+'\n').encode())
    observed = meter.terminal()
    assert observed['coverage'] == 'terminal' and observed['tokens'] == TOKENS
    assert observed['baseline_offset'] == len(historical)
    assert observed['session_binding']['size_bytes'] == path.stat().st_size
    assert path.read_bytes().startswith(historical)


@pytest.mark.parametrize('change', ['early_rewrite_append', 'header_append', 'replace', 'truncate',
    'unknown', 'usage', 'message', 'wrong_model', 'wrong_thinking', 'duplicate_id',
    'orphan', 'branch', 'extra_field', 'non_string_model', 'invalid_timestamp', 'invalid_type',
    'partial', 'malformed'])
def test_resume_post_start_setup_rejects_mutation_work_and_non_native_metadata(tmp_path, change):
    rows = [dict(type='message', id='user', parentId=None, timestamp=STAMP,
        message=dict(role='user', content='Original task'+'x'*8192, timestamp=0)),
        dict(type='message', id='msg', parentId='user', timestamp=STAMP,
            message=message('padding beyond anchor'+'y'*8192))]
    home, work, path, session = original(tmp_path, rows)
    loaded = pi_sessions.load_resume_original(session, work, home)
    data = path.read_bytes()
    row = dict(type='model_change', id='setup', parentId='msg', timestamp=STAMP,
        provider='fixture', modelId='exact-model')
    if change == 'early_rewrite_append':
        changed = data.replace(b'Original task', b'Changed! task')
        assert len(changed) == len(data) and changed[-4096:] == data[-4096:]
        data = changed
    elif change == 'header_append':data = data.replace(b'exact-session', b'other-session')
    elif change == 'unknown':row['type'] = 'custom'
    elif change == 'usage':row = dict(type='usage', id='setup', parentId='msg', timestamp=STAMP, usage=AUX)
    elif change == 'message':row = dict(type='message', id='setup', parentId='msg', timestamp=STAMP, message=message(stamp=2))
    elif change == 'wrong_model':row['modelId'] = 'different'
    elif change == 'wrong_thinking':
        row = dict(type='thinking_level_change', id='setup', parentId='msg', timestamp=STAMP, thinkingLevel='low')
    elif change == 'duplicate_id':row['id'] = 'msg'
    elif change == 'orphan':row['parentId'] = 'missing'
    elif change == 'branch':row['parentId'] = 'user'
    elif change == 'extra_field':row['usage'] = AUX
    elif change == 'non_string_model':row['modelId'] = None
    elif change == 'invalid_timestamp':row['timestamp'] = 0
    elif change == 'invalid_type':row['type'] = []
    tail = (json.dumps(row)+'\n').encode()
    if change == 'partial':tail = tail[:-1]
    elif change == 'malformed':tail = b'{broken}\n'
    if change == 'replace':
        replacement = home/'replacement'; replacement.write_bytes(data+tail); replacement.replace(path)
    else:path.write_bytes(data[:-1] if change == 'truncate' else data+tail)
    with pytest.raises(ValueError):pi_sessions.recheck_prefix(loaded, phase='native_setup')
    assert loaded['session'] == session and loaded['info'].st_size == session['size_bytes']
    assert loaded['digest'].hexdigest() == session['sha256']


@pytest.mark.parametrize('limit', ['line', 'snapshot', 'entries'])
def test_resume_post_start_setup_enforces_finite_read_and_entry_limits(tmp_path, monkeypatch, limit):
    home, work, path, session = original(tmp_path)
    loaded = pi_sessions.load_resume_original(session, work, home)
    tail = (json.dumps(dict(type='model_change', id='setup', parentId='msg',
        timestamp=STAMP, provider='fixture', modelId='exact-model'))+'\n').encode()
    with path.open('ab') as stream:stream.write(tail)
    if limit == 'line':
        # Compact control-header bounds do not limit native setup records.
        monkeypatch.setattr(pi_sessions, 'LINE_BYTES', len(tail)-1)
    elif limit == 'snapshot':
        # A finite acquisition chunk does not limit the full original snapshot.
        monkeypatch.setattr(pi_sessions, 'READ_BYTES', len(tail)-1)
        assert path.stat().st_size > pi_sessions.READ_BYTES
    else:monkeypatch.setattr(pi_sessions, 'MAX_ENTRIES', len(loaded['seen']))
    if limit == 'entries':
        with pytest.raises(ValueError):pi_sessions.recheck_prefix(loaded, phase='native_setup')
    else:pi_sessions.recheck_prefix(loaded, phase='native_setup')
    assert loaded['info'].st_size == session['size_bytes']


@pytest.mark.parametrize('change', ['append', 'replace', 'rewrite'])
def test_resume_post_start_recheck_requires_stable_bounded_snapshot(tmp_path, monkeypatch, change):
    home, work, path, session = original(tmp_path)
    loaded = pi_sessions.load_resume_original(session, work, home)
    with path.open('ab') as stream:
        stream.write((json.dumps(dict(type='model_change', id='setup', parentId='msg',
            timestamp=STAMP, provider='fixture', modelId='exact-model'))+'\n').encode())
    open_original = pi_sessions.open_original
    class ChangingReader:
        def __enter__(self):
            self.stream = open_original(home, path); self.changed = False; return self
        def __exit__(self, *args):self.stream.close()
        def fileno(self):return self.stream.fileno()
        def tell(self):return self.stream.tell()
        def readline(self, count):return self.stream.readline(count)
        def read(self, count):
            assert 0 < count <= pi_sessions.READ_BYTES
            data = self.stream.read(count)
            if not self.changed:
                self.changed = True
                if change == 'replace':
                    replacement = home/'replacement'; replacement.write_bytes(path.read_bytes()); replacement.replace(path)
                elif change == 'rewrite':path.write_bytes(path.read_bytes().replace(b'Original task', b'Changed! task'))
                else:
                    with path.open('ab') as stream:stream.write(b'\n')
            return data
    monkeypatch.setattr(pi_sessions, 'open_original', lambda *a: ChangingReader())
    with pytest.raises(ValueError):pi_sessions.recheck_prefix(loaded, phase='native_setup')


@pytest.mark.parametrize('mismatch', ['workspace', 'model', 'thread', 'changed_original',
    'unaccounted_append', 'unaccounted_model', 'unaccounted_thinking'])
def test_resume_transport_refuses_wrong_original_before_spawning_or_prompting(tmp_path, monkeypatch, mismatch):
    home, work, path, session = original(tmp_path)
    monkeypatch.chdir(work)
    if mismatch == 'workspace':session['workspace'] = str(tmp_path/'different')
    elif mismatch == 'model':session['profile'] = dict(PROFILE, model='fixture/different')
    elif mismatch == 'thread':session['thread_id'] = 'different'
    elif mismatch.startswith('unaccounted_'):
        row = dict(type='usage', id='late', parentId='msg', timestamp=STAMP, usage=AUX)
        if mismatch == 'unaccounted_model':
            row = dict(type='model_change', id='late', parentId='msg', timestamp=STAMP,
                provider='fixture', modelId='exact-model')
        elif mismatch == 'unaccounted_thinking':
            row = dict(type='thinking_level_change', id='late', parentId='msg', timestamp=STAMP,
                thinkingLevel='high')
        with path.open('ab') as stream:stream.write((json.dumps(row)+'\n').encode())
    else:path.write_bytes(path.read_bytes().replace(b'Original task', b'Changed! task'))
    argv = ['--resource-policy', json.dumps(pi_transport.resource_policy()),
        '--resume-proof', json.dumps(session), '--', 'never-spawn', '--mode', 'rpc',
        *pi.RESOURCE_FLAGS, '--tools', 'read,bash,edit,write,grep,find,ls',
        '--session', str(path), '--session-dir', str(path.parent), '--provider', 'fixture',
        '--model', 'exact-model', '--thinking', 'high']
    events = []
    def forbidden(*a, **kw):pytest.fail('Invalid original reached another native process/prompt')
    assert pi_transport.run(argv, 'Same original scope', events.append, popen=forbidden) == 1
    assert events == [dict(type='pi_transport_fault')]


@pytest.mark.parametrize('mismatch', ['model', 'thinking', 'thread', 'path', 'pending'])
def test_resume_rpc_initial_state_must_match_before_model_set_or_prompt(mismatch):
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session', 'same scope',
        expected_session='exact-session')
    state = dict(model=dict(provider='fixture', id='exact-model', api='fixture'), thinkingLevel='high',
        sessionId='exact-session', sessionFile='/private/session', isStreaming=False,
        isCompacting=False, pendingMessageCount=0)
    if mismatch == 'model':state['model']['id'] = 'different'
    elif mismatch == 'thinking':state['thinkingLevel'] = 'low'
    elif mismatch == 'thread':state['sessionId'] = 'different'
    elif mismatch == 'path':state['sessionFile'] = '/different'
    else:state['pendingMessageCount'] = 1
    command = binding.start()
    with pytest.raises(pi_transport.TransportFault):
        binding.response(dict(type='response', id=command['id'], command='get_state', success=True, data=state))
    assert binding.prompt_sent is False and binding.number == 1


@pytest.mark.parametrize('category', ['permanent', 'unknown'])
def test_existing_native_recovery_veto_and_consumed_counters_are_not_reset(category):
    limits = native_recovery.policy(dict(enabled=True, max_continues=1, max_retries=1))
    used = dict(continues=1, retries=1, wait_seconds=17.)
    outcome = dict(terminal_observed=False, native_failure=dict(category=category, not_accepted=False),
        cleanup='observed_group_cleaned', stop_reason='native_failure_event', thread_id='exact-session')
    decided = native_recovery.decision(outcome, limits, used, session_available=True, activity_observed=True)
    assert decided['action'] == 'stop' and decided['counters'] == used
    for retryable in ('temporary', 'incomplete'):
        decided = native_recovery.decision(dict(outcome, native_failure=dict(category=retryable)),
            limits, used, session_available=True, activity_observed=True)
        assert decided['action'] == 'stop' and decided['counters'] == used


def alias_facts(tmp_path, monkeypatch):
    root = tmp_path/'instance'; work = root/'project/nodes/001'
    (work/'work').mkdir(parents=True); (work/'artifacts').mkdir(); (work/'experiments').mkdir()
    (work/'.git').mkdir(); (work/'.git/config').write_text('metadata original')
    task = work/'science-task.json'; task.write_text('task original')
    outside = tmp_path/'untracked-original'; outside.write_text('external original')
    scientific = tmp_path/'scientific-original'; scientific.write_text('scientific original')
    os.link(outside, work/'artifacts/external-alias')
    os.link(scientific, work/'artifacts/scientific-alias')
    os.link(task, work/'work/task-alias')
    os.link(work/'.git/config', work/'work/metadata-alias')
    (work/'work/ordinary').write_text('writable')
    job = dict(role='H', formal_review={'fixture': True})
    sources = dict(root=root, work=work, target=work, job=job, material_paths={},
        native=dict(record=None, intent={}, records=()), policy=None, author=None, wake=[], review_code=[])
    monkeypatch.setattr(accepted_prefix, 'input_rows', lambda job: [])
    monkeypatch.setattr(experiment_client, '_input_rows', lambda job: [])
    facts = views.load_pi_mount_facts(root, job, work, source_facts=sources, selected_code=[],
        mounts=[(scientific, work/'work/scientific')], protected_paths=[task])
    return root, work, facts, (outside, scientific)


def test_pi_workspace_inventory_protects_task_metadata_science_and_untracked_external_aliases(tmp_path, monkeypatch):
    root, work, facts, _ = alias_facts(tmp_path, monkeypatch)
    mappings = facts['mappings']
    for name in ('artifacts/external-alias', 'artifacts/scientific-alias', 'work/task-alias', 'work/metadata-alias', '.git'):
        assert str(work/name) in mappings
    assert str(work/'work/ordinary') not in mappings
    assert mappings[str(work/'artifacts/scientific-alias')] == work/'artifacts/scientific-alias'
    args = views.mount_arguments_from_facts(facts)
    assert '--bind' not in args  # An output-like path never defeats an explicit original.


def test_pi_workspace_inventory_rejects_hardlink_count_change_at_final_boundary(tmp_path, monkeypatch):
    original_walk = views._Inventory.walk; calls = {}
    work = tmp_path/'instance/project/nodes/001'
    def changed(inventory, path):
        yield from original_walk(inventory, path)
        if path == work:
            calls['rechecked'] = True
            os.link(work/'artifacts/external-alias', tmp_path/'late-external-alias')
    monkeypatch.setattr(views._Inventory, 'walk', changed)
    from scidiscovery.research_loop.experiments import UnsupportedExecution
    with pytest.raises(UnsupportedExecution, match='workspace alias changed'):
        alias_facts(tmp_path, monkeypatch)
    assert calls['rechecked']


@pytest.mark.process_e2e
@pytest.mark.parametrize('conclusion_exists', [False, True])
def test_actual_pi_workspace_write_preserves_immutable_aliases_and_chmod_denial(tmp_path, monkeypatch, conclusion_exists):
    program = require_namespace()
    root, work, mounts, external = alias_facts(tmp_path, monkeypatch)
    conclusion = work/'conclusion.json'
    if conclusion_exists:conclusion.write_text('framework conclusion original')
    assert conclusion.exists() is conclusion_exists
    sessions = root/'native-homes/001/sessions'; sessions.mkdir(parents=True)
    auth = tmp_path/'agent'; auth.mkdir()
    runtime = tuple((p, p) for p in dict.fromkeys((Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
        Path(sys.executable).resolve())) if any(p.is_relative_to(t) for t in pi_namespace.TEMPORARY))
    facts = dict(root=root, work=work, sessions=sessions, auth=auth, runtime=runtime, readonly=False)
    probe = '''import pathlib,sys
work=pathlib.Path(sys.argv[1])
assert (work/'conclusion.json').exists() == (sys.argv[2]=='yes')
for name in ('conclusion.json','science-task.json','.git/config','artifacts/external-alias','artifacts/scientific-alias','work/task-alias','work/metadata-alias','work/scientific'):
 p=work/name
 for action in (lambda:p.write_text('ILLEGAL'),lambda:p.chmod(0o600)):
  try:action()
  except OSError:pass
  else:raise AssertionError('original mutation allowed: '+name)
for name in ('science-result.schema.json','author-closeout-facts.json'):
 p=work/name
 assert not p.exists()
 for action in (lambda:p.write_text('ILLEGAL'),lambda:p.mkdir()):
  try:action()
  except OSError:pass
  else:raise AssertionError('absent control creation allowed: '+name)
 assert not p.exists()
(work/'work/ordinary').write_text('changed')
(work/'work/new-code.py').write_text('generated code')
(work/'artifacts/new-output').write_text('new output')
(work/'experiments/new-science').mkdir()
(work/'experiments/new-science/report').write_text('scientific report')
print('workspace-write-ok')
'''
    result = subprocess.run([program, *pi_namespace.arguments_from_facts(facts),
        *views.mount_arguments_from_facts(mounts), '--remount-ro', str(root), '--chdir', str(work),
        '--', str(Path(sys.executable).resolve()), '-c', probe, str(work), 'yes' if conclusion_exists else 'no'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert result.stdout == b'workspace-write-ok\n'
    assert (work/'work/ordinary').read_text() == 'changed'
    assert (work/'artifacts/new-output').read_text() == 'new output'
    assert (work/'work/new-code.py').read_text() == 'generated code'
    assert (work/'experiments/new-science/report').read_text() == 'scientific report'
    assert conclusion.exists() is conclusion_exists
    if conclusion_exists:assert conclusion.read_text() == 'framework conclusion original'
    for name in ('science-result.schema.json', 'author-closeout-facts.json'):
        assert not (work/name).exists()  # No bwrap-created fake host publication.
    assert [p.read_text() for p in external] == ['external original', 'scientific original']


# Reuse the Phase-1 actual peer, not a result fixture or an inline runner. Only
# session append semantics and first/second final text differ in this peer.
RESUME_RPC = FAKE_RPC.replace('assert not path.exists()', 'resumed=path.exists()').replace(
    "def append(v):\n with path.open('ab') as f:",
    "def append(v):\n if v['type']=='session' and resumed:return\n"
    " if v['type']!='session':\n"
    "  v=dict(v,id=v['id']+('-2' if resumed else '-1'),parentId=(v['parentId']+('-2' if resumed else '-1') if v['parentId'] else ('setup-thinking-2' if resumed and v['type']=='message' else 'shutdown-1' if resumed else None)))\n"
    " with path.open('ab') as f:").replace(
    "final=os.environ['PI_FIXTURE_FINAL']",
    "final=os.environ['PI_FIXTURE_FINAL'] if resumed else os.environ.get('PI_INTERFACE_INITIAL','{malformed')").replace(
    "timestamp=1,\n   thinkingLevel", "timestamp=2 if resumed else 1,\n   thinkingLevel").replace(
    "(auth/'commands.json').write_text(json.dumps(commands))",
    "(auth/('commands-2.json' if resumed else 'commands-1.json')).write_text(json.dumps(commands))").replace(
    "(auth/'session-original.bin').write_bytes(path.read_bytes())",
    "(auth/('session-2.bin' if resumed else 'session-1.bin')).write_bytes(path.read_bytes())").replace(
    "for line in sys.stdin.buffer:",
    "if resumed:append(dict(type='model_change',id='setup-model',parentId=None,timestamp=stamp,provider='fixture',modelId='exact-model'))\n"
    "for line in sys.stdin.buffer:").replace(
    "elif kind=='set_thinking_level':assert cmd['level']=='high'",
    "elif kind=='set_thinking_level':\n"
    "  assert cmd['level']=='high'\n"
    "  if resumed:append(dict(type='thinking_level_change',id='setup-thinking',parentId='setup-model',timestamp=stamp,thinkingLevel='high'))")


def prepare_resumable(tmp_path, monkeypatch, peer=RESUME_RPC, *, automatic=False):
    require_namespace()
    agent = tmp_path/'fixture-agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    monkeypatch.setenv('PI_FIXTURE_FINAL', FINAL)
    monkeypatch.setenv('PI_FIXTURE_ORIGINAL_HOME', os.environ['HOME'])
    monkeypatch.delenv('PI_FIXTURE_FAULT', raising=False)
    launcher = tmp_path/'fake-pi'; launcher.write_text(peer); launcher.chmod(0o700)
    values = options(tmp_path, launcher=[str(launcher)], max_steps=3, resume_policy=dict(RESUME),
        node_budget_policy={r: dict(max_observed_tokens=1000, max_seconds=25) for r in 'HWAE'})
    if automatic:
        values['resume_policy']['automatic_recovery'] = dict(enabled=True, max_continues=1,
            max_retries=1, initial_backoff_seconds=0, max_backoff_seconds=0, max_wait_seconds=0,
            jitter_fraction=0)
    values['public_root'].mkdir(); values['objective'].write_text('Original bounded H task')
    research_config.prepare(**values)
    root = values['root']; c.build_job(root, 1, 'H', [])
    c.write(root/'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'pi_interface_fixture')
    return root, agent


@pytest.mark.process_e2e
def test_accepted_output_repair_continues_exact_private_session_scope_and_tail_only_billing(tmp_path, monkeypatch):
    root, agent = prepare_resumable(tmp_path, monkeypatch)
    original_job = (root/'jobs/001.json').read_bytes()
    result = collect_separately(root)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    first = execution.origin(root, 1); second = first/'continuations/001'
    first_outcome, outcome = c.read(first/'native-outcome.json'), c.read(second/'native-outcome.json')
    intent = c.read(second/'native-intent.json')
    assert intent['continuation_kind'] == 'repair' and intent['output_repair']['source_attempt'] == 0
    assert 'work_repair' not in intent and intent['task_ref'] == {'path': str(root/'jobs/001.json')}
    assert intent['session'] == first_outcome['native_identity']['session']
    assert intent['budget_slice']['previous_tokens'] == TOKENS['input_tokens'] + TOKENS['output_tokens']
    assert c.read(first/'usage.json') == c.read(second/'usage.json') == TOKENS
    assert first_outcome['thread_id'] == outcome['thread_id'] == 'exact-session'
    assert first_outcome['native_identity']['path'] == outcome['native_identity']['path']
    assert outcome['metering']['baseline_offset'] == len((agent/'session-1.bin').read_bytes())
    assert outcome['metering']['coverage'] == 'terminal' and outcome['stop_reason'] is None
    first_bytes = (agent/'session-1.bin').read_bytes()
    second_bytes = (agent/'session-2.bin').read_bytes()
    assert second_bytes.startswith(first_bytes)
    tail = [json.loads(line) for line in second_bytes[len(first_bytes):].splitlines()]
    assert [row['type'] for row in tail[:2]] == ['model_change', 'thinking_level_change']
    assert tail[0]['parentId'] == 'shutdown-1' and tail[1]['parentId'] == tail[0]['id']
    assert {'setup-model-2', 'setup-thinking-2'} <= {row['id'] for row in tail}
    assert first_outcome['native_identity']['session']['size_bytes'] == len(first_bytes)
    assert (first/'science-output.json').read_bytes() == b'{malformed'
    assert (second/'science-output.json').read_bytes() == FINAL.encode()
    assert (root/'jobs/001.json').read_bytes() == original_job
    for number in (1, 2):assert c.read(agent/f'commands-{number}.json').count('prompt') == 1
    assert (first/'result-identity.json').is_file()


@pytest.mark.process_e2e
def test_failed_pi_rpc_settlement_resumes_original_session_and_retains_consumed_budget(tmp_path, monkeypatch):
    from scidiscovery.research_loop import pi_observation
    peer = RESUME_RPC.replace(
        "thinkingLevel='high',content=[dict(type='text',text=final)],stopReason='stop',usage=usage)",
        "thinkingLevel='high',content=[dict(type='text',text=final)] if resumed else [],"
        "stopReason='stop' if resumed else 'error',usage=usage,"
        "**({} if resumed else dict(errorMessage='fetch failed')))")
    command = pi_observation.command_proof('__fixture_extension__')
    peer = peer.replace('  inventory=[]',
        '  inventory=[' + repr(command) + ']\n'
        "  inventory[0]['sourceInfo']['path']=option('--extension')")
    startup = """extension=pathlib.Path(option('--extension'))
nonce=extension.name[len('.scid-pi-observation-'):-len('.ts')]
assert len(nonce)==64
start=dict(type='pi_observer_start',nonce=nonce,path=str(path),workspace=str(work),session_id='exact-session')
emit(dict(type='extension_ui_request',id='fixture-observer-start',method='notify',message=json.dumps(start),notifyType='info'))
"""
    peer = peer.replace('for line in sys.stdin.buffer:', startup + 'for line in sys.stdin.buffer:')
    root, agent = prepare_resumable(tmp_path, monkeypatch, peer, automatic=True)
    originals = {path: path.read_bytes() for path in (root/'settings.json', root/'jobs/001.json')}
    collector = """import json,sys
from pathlib import Path
from scidiscovery.research_loop import controller
root=Path(sys.argv[1])
with controller._writer(root), controller._inactive_on_error(root):
 result=controller._native_call(root,1,launch=True)
print(json.dumps(result))
"""
    monkeypatch.setattr(sys.modules[collect_separately.__module__], 'COLLECTOR', collector)
    result = collect_separately(root)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    first = execution.origin(root, 1); second = first/'continuations/001'
    assert execution.records(root, 1) == [first, second]
    stopped, completed = c.read(first/'native-outcome.json'), c.read(second/'native-outcome.json')
    assert stopped['returncode'] == 0 and stopped['terminal_observed'] is False
    assert stopped['cleanup'] == 'observed_group_cleaned' and stopped['stop_reason'] == 'native_failure_event'
    assert stopped['native_failure']['category'] == 'temporary'
    assert stopped['native_failure']['codes'] == ['connection_error']
    assert stopped['native_failure']['not_accepted'] is False
    assert stopped['native_identity']['participant_scope'] == 'unsupported'
    assert 'pi_authorship_exit_invalid' not in json.dumps(stopped['metering'])
    stream = [json.loads(line) for line in (first/'native.jsonl').read_text().splitlines()]
    assert stream[-1]['type'] == pi_transport.CONTROLLED_EXIT
    assert stream[-1]['settled'] == dict(type='agent_settled', aborted=False)
    assert any(row['type'] == 'message_end' and row['message'].get('errorMessage') == 'fetch failed'
        and row['message']['content'] == [] for row in stream)
    assert not (first/'science-output.json').exists() and not (first/'result.pending.json').exists()
    resumed = c.read(second/'native-intent.json')
    assert resumed['continuation_kind'] == 'automatic_recovery'
    assert resumed['automatic_recovery']['action'] == 'retry'
    assert resumed['automatic_recovery']['counters'] == dict(continues=0, retries=0, wait_seconds=0.)
    assert resumed['session'] == stopped['native_identity']['session']
    assert resumed['previous'] == execution.proof(first)
    assert resumed['budget_slice']['previous_tokens'] == TOKENS['input_tokens'] + TOKENS['output_tokens']
    assert c.read(first/'usage.json') == c.read(second/'usage.json') == TOKENS
    assert completed['thread_id'] == stopped['thread_id'] == 'exact-session'
    assert completed['terminal_observed'] is True and completed['stop_reason'] is None
    assert (second/'science-output.json').read_bytes() == FINAL.encode()
    assert (agent/'session-2.bin').read_bytes().startswith((agent/'session-1.bin').read_bytes())
    assert all(path.read_bytes() == data for path, data in originals.items())
    for number in (1, 2):assert c.read(agent/f'commands-{number}.json').count('prompt') == 1


@pytest.mark.process_e2e
@pytest.mark.parametrize('failure', ['permanent', 'unknown', 'unsettled', 'changed_original'])
def test_accepted_failed_original_never_adds_prompt_or_resets_consumed_budget(tmp_path, monkeypatch, failure):
    peer = RESUME_RPC
    if failure in {'permanent', 'unknown'}:
        diagnostic = '401: {"type":"invalid_api_key"}' if failure == 'permanent' else 'unqualified provider prose'
        peer = peer.replace("stopReason='stop',usage=usage)",
            "stopReason='error',errorMessage="+repr(diagnostic)+",usage=usage)")
    elif failure == 'changed_original':
        peer += "\nwith path.open('ab') as f:f.write(b'{broken original\\n')\n"
    root, agent = prepare_resumable(tmp_path, monkeypatch, peer, automatic=True)
    if failure == 'unsettled':monkeypatch.setenv('PI_FIXTURE_FAULT', 'missing_settlement')
    original_job = (root/'jobs/001.json').read_bytes()
    result = collect_separately(root)
    assert result.returncode != 0
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert outcome['native_started'] and outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['metering']['coverage'] == 'unknown'
    assert c.read(record/'usage.json') == TOKENS
    assert c.read(agent/'commands-1.json').count('prompt') == 1
    assert not (agent/'commands-2.json').exists()
    assert not (record/'continuations/001/native-intent.json').exists()
    assert not (record/'result-identity.json').exists()
    assert (root/'jobs/001.json').read_bytes() == original_job
    if failure in {'permanent', 'unknown'}:
        assert outcome['native_failure']['category'] == failure


def retain_installed_repair_diagnostics(root, log_dir, collector_stderr):
    """Local ignored engineering logs only, never accepted science evidence."""
    if not log_dir:
        return None
    destination = Path(log_dir)/'installed-pi-output-repair-diagnostics'
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    limit = 1024 * 1024
    manifest = dict(scope='Selected synthetic installed-Pi engineering diagnostics; not scientific evidence',
        max_record_bytes=limit, records=[])
    first = execution.origin(root, 1)
    names = ('native-outcome.json', 'native-intent.json', 'runner-process.json',
        'native-process.json', 'usage.json', 'usage-observation.json', 'native.jsonl',
        'science-output.json', 'native.stderr', 'shared-input-launch-diagnostic.json')
    selected = [(record/name, Path(label)/name)
        for record, label in ((first, 'attempt-000'), (first/'continuations/001', 'attempt-001'))
        for name in names]
    session = Path('native-homes/001/sessions/pi-session.jsonl')
    selected.append((root/session, session))

    def publish(row, relative, data):
        if len(data) > limit:
            row['status'] = 'too_large'
            return
        target = destination/relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
        row.update(status='copied', size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    # No discovery, recursive copies, auth/runtime configuration or fallback
    # session lookup. Missing originals get manifest entries, not fake files.
    def capture(source, relative):
        row = dict(source=source.relative_to(root).as_posix(), diagnostic=relative.as_posix())
        manifest['records'].append(row)
        # A repeated capture must not leave an old copy for a now-missing original.
        (destination/relative).unlink(missing_ok=True)
        try:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    row['status'] = 'not_regular'
                    return None
                if info.st_size > limit:
                    row['status'] = 'too_large'
                    return None
                data = stream.read(limit + 1)
        except FileNotFoundError:
            row['status'] = 'missing'
        except OSError as error:
            row.update(status='read_error', error_type=type(error).__name__, errno=error.errno)
        else:
            publish(row, relative, data)
            return data if row['status'] == 'copied' else None
        return None

    metadata = {'attempt-000': None, 'attempt-001': None}
    for source, relative in selected:
        data = capture(source, relative)
        if source.name != 'native.jsonl' or data is None:
            continue
        # Acquire the nonce only from this already-bounded exact stream. Never
        # follow a transcript/config/source pointer or search the session home.
        try:
            rows = [json.loads(line) for line in data.splitlines()]
            frames = [row for row in rows if isinstance(row, dict)
                and row.get('type') == 'pi_observer_bound']
        except (ValueError, UnicodeError, RecursionError):
            continue
        if (len(frames) == 1 and frames[0].get('path') == str(root/session)
                and isinstance(frames[0].get('nonce'), str)
                and re.fullmatch(r'[0-9a-f]{64}', frames[0]['nonce'])):
            metadata[relative.parent.as_posix()] = (root/session).parent / (
                '.pi-observation-' + frames[0]['nonce'] + '.json')
    for label, path in metadata.items():
        for name, original in (
                ('metadata-delivery.json', path),
                ('metadata-delivery-diagnostic.json', path.with_suffix('.diagnostic.json') if path else None)):
            relative = Path(label)/name
            if original is not None:
                capture(original, relative)
            else:
                (destination/relative).unlink(missing_ok=True)
                manifest['records'].append(dict(source='exact observer binding unavailable',
                    diagnostic=relative.as_posix(), status='binding_unavailable'))
    row = dict(source='collector stderr (captured pipe)', diagnostic='collector.stderr')
    manifest['records'].append(row)
    (destination/'collector.stderr').unlink(missing_ok=True)
    if collector_stderr is None:
        row['status'] = 'missing'
    else:
        data = collector_stderr.encode('utf-8') if isinstance(collector_stderr, str) else collector_stderr
        publish(row, Path('collector.stderr'), data)
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return destination


@pytest.mark.process_e2e
def test_installed_pi_output_repair_resumes_actual_session_append_only_and_bills_tail_once(tmp_path, monkeypatch, record_property):
    # The installed fixture deliberately clears non-allowlisted environment.
    diagnostic_log_dir = os.environ.get('SCID_TEST_LOG_DIR')
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    malformed = ' \n```json\n{坏}\n```\n\t'
    repaired = FINAL.replace('Mechanical transport only', 'Mechanical transport only — 修复')
    node_limits = {r: dict(max_observed_tokens=1000, max_seconds=25) for r in 'HWAE'}
    values = options(tmp_path, launcher=[launcher], max_steps=3,
        resume_policy=dict(RESUME), node_budget_policy=node_limits)
    values['public_root'].mkdir(); values['objective'].write_text('Original bounded H task')
    with loopback((malformed, repaired)) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        # Public preparation freezes the installed launcher and explicit policy;
        # no fake peer, injected acceptance, inline runner or resume fallback.
        research_config.prepare(**values)
        root = values['root']; c.build_job(root, 1, 'H', [])
        c.write(root/'host-started.json', budget.clock_stamp())
        budget.transition(root, 'advance', 'installed_pi_session_repair_fixture')
        work = c.workspace(root, 1)
        originals = {path: path.read_bytes() for path in (root/'jobs/001.json',
            root/'settings.json', work/'science-task.json')}
        collector_stderr = None
        try:
            result = collect_separately(root)
            collector_stderr = result.stderr
        except subprocess.TimeoutExpired as error:
            collector_stderr = error.stderr
            raise
        finally:
            diagnostics = retain_installed_repair_diagnostics(root, diagnostic_log_dir, collector_stderr)
            if diagnostics is not None:
                record_property('pi_repair_diagnostics', str(diagnostics))
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert len(requests) == 2 and errors == []
    # Check again after bounded server shutdown: no late POST is hidden by an
    # early count, and the finite response sequence cannot serve a third turn.
    assert len(requests) == 2 and errors == []
    first = execution.origin(root, 1); second = first/'continuations/001'
    assert list(execution.records(root, 1)) == [first, second]
    assert not (first/'continuations/002/native-intent.json').exists()
    first_outcome, outcome = c.read(first/'native-outcome.json'), c.read(second/'native-outcome.json')
    first_intent, intent = c.read(first/'native-intent.json'), c.read(second/'native-intent.json')
    session = first_outcome['native_identity']['session']
    final_session = outcome['native_identity']['session']
    path = root/'native-homes/001/sessions/pi-session.jsonl'
    assert session['path'] == final_session['path'] == str(path)
    assert session['home'] == final_session['home'] == str(root/'native-homes/001')
    assert session['workspace'] == final_session['workspace'] == str(work)
    assert session['profile'] == final_session['profile'] == PROFILE
    thread = session['thread_id']
    assert isinstance(thread, str) and thread and thread != 'exact-session'
    assert first_outcome['thread_id'] == outcome['thread_id'] == final_session['thread_id'] == thread
    assert first_outcome['native_identity']['session_id'] == outcome['native_identity']['session_id'] == thread
    assert intent['session'] == session
    data = path.read_bytes(); info = path.stat()
    prefix, tail = data[:session['size_bytes']], data[session['size_bytes']:]
    assert tail and prefix.endswith(b'\n') and tail.endswith(b'\n')
    assert session['identity'] == final_session['identity'] == [info.st_dev, info.st_ino]
    assert hashlib.sha256(prefix).hexdigest() == session['sha256']
    assert len(prefix) == session['size_bytes'] < final_session['size_bytes'] == len(data)
    assert hashlib.sha256(data).hexdigest() == final_session['sha256']
    assert data.split(b'\n', 1)[0].decode()+'\n' == session['header'] == final_session['header']
    header = json.loads(session['header'])
    assert header['type'] == 'session' and header['id'] == thread and header['cwd'] == str(work)
    debit = dict(input_tokens=17, cached_input_tokens=7, output_tokens=5)
    native_user_entry_ids = []
    for number, (record, receipt, body, portion) in enumerate(((first, first_outcome, malformed, prefix),
            (second, outcome, repaired, tail))):
        assert receipt['native_started'] and receipt['returncode'] == 0
        assert receipt['stop_reason'] is None and receipt['native_failure'] is None
        assert receipt['cleanup'] == 'observed_group_cleaned' and receipt['terminal_observed']
        assert receipt['native_backend'] == 'pi'
        assert receipt['native_identity']['participant_scope'] == pi.PARTICIPANT_SCOPE
        assert receipt['native_identity']['controlled']['kind'] == pi.PARTICIPANT_PROOF
        assert receipt['metering']['coverage'] == 'terminal'
        assert receipt['metering']['tokens'] == c.read(record/'usage.json') == debit
        assert receipt['metering']['reporting_delay_unmeasured'] is False
        assert receipt['metering']['inflight_usage_unknown'] is False
        assert receipt['metering']['baseline_offset'] == (0 if number == 0 else len(prefix))
        assert receipt['metering']['observation_capabilities']['native_calls']['status'] == 'unsupported'
        assert (record/'science-output.json').read_bytes() == body.encode('utf-8')
        assert c.file_ref(record/'science-output.json') == receipt['raw_ref']
        assert (record/'runner-process.json').is_file() and (record/'native-process.json').is_file()
        rows = [json.loads(row) for row in portion.split(b'\n') if row]
        assert sum(row.get('type') == 'session' for row in rows) == (1 if number == 0 else 0)
        messages = [row['message'] for row in rows if row.get('type') == 'message']
        # Each accepted attempt adds exactly one prompt and one final, not a
        # synthetic transcript or a replay of the first accepted prompt.
        users_in_tail = [row for row in rows if row.get('type') == 'message'
            and row['message']['role'] == 'user']
        assert len(users_in_tail) == 1
        native_user_entry_id = users_in_tail[0]['id']
        assert isinstance(native_user_entry_id, str) and native_user_entry_id
        # The controlled observer reports this attempt's persisted native user
        # entry, never an RPC request ID, tool call or reused prior-turn ID.
        assert receipt['metering']['turn_id'] == native_user_entry_id
        assert native_user_entry_id not in native_user_entry_ids
        native_user_entry_ids.append(native_user_entry_id)
        assistants = [msg for msg in messages if msg['role'] == 'assistant']
        assert len(assistants) == 1 and assistants[0]['stopReason'] == 'stop'
        assert ''.join(block['text'] for block in assistants[0]['content'] if block['type'] == 'text') == body
        assert pi_sessions.usage_tokens(assistants[0]['usage']) == debit
        stream = [json.loads(row) for row in (record/'native.jsonl').read_bytes().split(b'\n') if row]
        assert sum(row['type'] == 'agent_start' for row in stream) == 1
        assert sum(row['type'] == 'agent_settled' and row.get('aborted') is False for row in stream) == 1
        assert not any(row['type'].startswith('tool_execution_') for row in stream)
        finals = [row['message'] for row in stream if row['type'] == 'message_end'
            and row.get('message', {}).get('role') == 'assistant']
        assert len(finals) == 1
        assert ''.join(block['text'] for block in finals[0]['content'] if block['type'] == 'text') == body
    assert intent['attempt'] == 1 and intent['continuation_kind'] == 'repair'
    assert not any(key in intent for key in ('work_repair', 'execution_wake', 'automatic_recovery', 'context_mode'))
    assert intent['task_ref'] == first_intent['task_ref'] == {'path': str(root/'jobs/001.json')}
    assert intent['root_intent_ref'] == c.record_ref(first/'native-intent.json')
    assert intent['previous'] == execution.proof(first)
    assert intent['output_repair'] == continuation.output_request(root, 1, first,
        inventory=None, source_attempt=0, verify_task=False)
    assert intent['output_repair']['source_attempt'] == 0
    assert intent['output_repair']['raw_ref'] == first_outcome['raw_ref']
    assert intent['output_repair']['outcome_ref'] == c.record_ref(first/'native-outcome.json')
    # Initial acceptance has no predecessor; only continuations retain a
    # previous-debit slice. Do not invent a zero-valued field in that original.
    assert 'previous_tokens' not in first_intent['budget_slice']
    assert 'previous_seconds' not in first_intent['budget_slice']
    assert intent['budget_slice']['previous_tokens'] == 22
    assert intent['budget_slice']['previous_seconds'] >= first_outcome['budget_slice']['advance_seconds'] > 0
    assert outcome['budget_slice']['advance_seconds'] >= intent['budget_slice']['previous_seconds']
    assert intent['budget_slice']['limits'] == first_intent['budget_slice']['limits']
    assert intent['budget_slice']['limits']['max_observed_tokens'] == 1000
    assert intent['budget_slice']['limits']['max_seconds'] == 25
    assert first_outcome['budget_slice']['observed_tokens'] == 22
    assert outcome['budget_slice']['observed_tokens'] == 44
    total = native.usage(root)
    assert total['observed_tokens'] == total['accounted_tokens'] == 44
    assert total['unknown_steps'] == []
    # The real SDK must have loaded the bad assistant message, not started a
    # fresh conversation with a fixture session ID or a copied result.
    def wire_text(content):
        return content if isinstance(content, str) else ''.join(block['text'] for block in content if block['type'] == 'text')
    assert [msg for msg in requests[0]['messages'] if msg['role'] == 'assistant'] == []
    assert [wire_text(msg['content']) for msg in requests[1]['messages'] if msg['role'] == 'assistant'] == [malformed]
    users = [[wire_text(msg['content']) for msg in request['messages'] if msg['role'] == 'user'] for request in requests]
    assert len(users[0]) == 1 and len(users[1]) == 2 and users[1][0] == users[0][0]
    assert native.OUTPUT_REPAIR_INSTRUCTION in users[1][1]
    assert '"previous_consumed_observed_tokens":22' in users[1][1]
    prepared = c.read(root/'settings.json')
    assert {key: prepared['resume_policy'][key] for key in RESUME} == RESUME
    assert prepared['native_capabilities']['native_launcher_contract'] is None
    assert prepared['native_capabilities']['complete_native_authorship'] is True
    assert pi.DEFAULT_RESUME_POLICY['output_only'] is True and adapter().complete_authorship is True
    assert (first/'result-identity.json').is_file()
    c.validate(work)
    for original_path, body in originals.items():assert original_path.read_bytes() == body
    assert path.read_bytes() == data
