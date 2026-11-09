"""Configured extension state stays opaque across exact native continuation.

All session content here is synthetic. Process coverage uses only the installed
SDK and a local fixture package/provider, never inherited packages or auth.
"""
import copy
import json

import pytest

from scidiscovery.research_loop import continuation, contracts as c, execution
from scidiscovery.research_loop import pi_sessions, pi_transport
from test_pi_backend_interfaces import RESUME, original
from test_pi_backend_v2 import AUX, PROFILE, STAMP, TOKENS, adapter, message
from test_pi_native_packages import CONFIGURED
from test_pi_resources import reply


def custom(identity='state', parent='msg', **changes):
    return dict(type='custom', id=identity, parentId=parent, timestamp=STAMP,
        customType='synthetic-package-cache', data=dict(opaque=['fixture', 1, None]), **changes)


def with_state(tmp_path):
    rows = [dict(type='message', id='user', parentId=None, timestamp=STAMP,
        message=dict(role='user', content='Synthetic task', timestamp=0)),
        dict(type='message', id='msg', parentId='user', timestamp=STAMP, message=message()), custom()]
    return original(tmp_path, rows)


def append(path, row):
    with path.open('ab') as stream:stream.write((json.dumps(row)+'\n').encode())


def test_configured_resume_admits_original_custom_only_from_explicit_owner_mode(tmp_path):
    home, work, path, session = with_state(tmp_path)
    before = path.read_bytes(); retained = copy.deepcopy(session)
    with pytest.raises(ValueError, match='extension state unsupported'):
        pi_sessions.load_resume_original(session, work, home)
    with pytest.raises(ValueError, match='extension state unsupported'):
        adapter().validate_resume_session(session, work, home)
    backend = adapter(native_options=CONFIGURED, resume_policy=dict(RESUME))
    assert backend.validate_resume_session(session, work, home) == session
    assert backend._resume_original['resource_mode'] == 'configured'
    assert backend._resume_original['leaf'] == 'state'
    assert backend._resume_original['seen'] == {'user', 'msg', 'state'}
    assert backend.complete_authorship is False
    assert path.read_bytes() == before and session == retained


def test_configured_setup_custom_preserves_prefix_and_charges_only_new_tail(tmp_path, monkeypatch):
    home, work, path, session = with_state(tmp_path)
    historical = path.read_bytes()
    backend = adapter(native_options=CONFIGURED, resume_policy=dict(RESUME))
    backend.validate_resume_session(session, work, home)
    loaded = backend._resume_original; digest = loaded['digest'].hexdigest()
    backend.bind_attempt(home, work, session)
    args = backend.launch(PROFILE, work, None, None, 'Synthetic continuation')
    backend.resume(args, PROFILE, work, None, None, session)
    append(path, custom('startup-state', 'state'))
    with pytest.raises(ValueError, match='unaccounted append'):pi_sessions.recheck_prefix(loaded)
    pi_sessions.recheck_prefix(loaded, phase='native_setup')
    assert loaded['digest'].hexdigest() == digest and loaded['leaf'] == 'state'
    assert loaded['session']['size_bytes'] == len(historical)
    monkeypatch.setattr(pi_sessions, 'load_resume_original',
        lambda *a, **k: pytest.fail('Meter reacquired the owner original'))
    meter = backend.meter(home, work, session)
    assert meter.poll()['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    append(path, dict(type='message', id='next', parentId='startup-state',
        timestamp=STAMP, message=message(stamp=2)))
    append(path, dict(type='usage', id='shutdown', parentId='next', timestamp=STAMP, usage=AUX))
    observed = backend.finish_observation(meter, required=False)
    assert observed['coverage'] == 'terminal' and observed['tokens'] == TOKENS
    assert observed['baseline_offset'] == len(historical)
    assert path.read_bytes().startswith(historical)
    assert backend.complete_authorship is False


@pytest.mark.parametrize('mode', ['closed', 'configured'])
def test_native_setup_custom_requires_configured_mode(tmp_path, mode):
    home, work, path, session = original(tmp_path)
    loaded = pi_sessions.load_resume_original(session, work, home, resource_mode=mode)
    append(path, custom())
    if mode == 'configured':pi_sessions.recheck_prefix(loaded, phase='native_setup')
    else:
        with pytest.raises(ValueError):pi_sessions.recheck_prefix(loaded, phase='native_setup')


@pytest.mark.parametrize('damage', ['rewrite', 'unaccounted_append', 'model', 'thinking',
    'duplicate', 'orphan', 'timestamp', 'custom_type', 'custom_usage', 'custom_extra'])
def test_configured_custom_does_not_relax_original_or_setup_binding(tmp_path, damage):
    home, work, path, session = with_state(tmp_path)
    loaded = pi_sessions.load_resume_original(session, work, home, resource_mode='configured')
    row = custom('startup-state', 'state')
    if damage == 'rewrite':path.write_bytes(path.read_bytes().replace(b'Synthetic task', b'Synthetic edit'))
    elif damage == 'unaccounted_append':
        append(path, row)
        with pytest.raises(ValueError, match='unaccounted append'):
            pi_sessions.load_resume_original(session, work, home, resource_mode='configured')
        return
    elif damage == 'model':row = dict(type='model_change', id='setup', parentId='state', timestamp=STAMP, provider='fixture', modelId='other')
    elif damage == 'thinking':row = dict(type='thinking_level_change', id='setup', parentId='state', timestamp=STAMP, thinkingLevel='low')
    elif damage == 'duplicate':row['id'] = 'state'
    elif damage == 'orphan':row['parentId'] = 'missing'
    elif damage == 'timestamp':row['timestamp'] = 'invalid'
    elif damage == 'custom_type':row['customType'] = 7
    elif damage == 'custom_usage':row['usage'] = AUX
    else:row['provider'] = 'substituted'
    if damage != 'rewrite':append(path, row)
    with pytest.raises(ValueError):pi_sessions.recheck_prefix(loaded, phase='native_setup')


@pytest.mark.parametrize('damage', ['model', 'thinking', 'virtual_type', 'virtual_api', 'session'])
def test_configured_resume_exact_sdk_state_is_checked_before_set_model_or_prompt(damage):
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session',
        'Synthetic task', expected_session='exact-session', resource_mode='configured')
    state = dict(model=dict(provider='fixture', id='exact-model', api='fixture'),
        thinkingLevel='high', isStreaming=False, isCompacting=False, pendingMessageCount=0,
        sessionFile='/private/session', sessionId='exact-session')
    if damage == 'model':state['model']['id'] = 'different'
    elif damage == 'thinking':state['thinkingLevel'] = 'low'
    elif damage == 'virtual_type':state['model']['type'] = 'virtual'
    elif damage == 'virtual_api':state['model']['api'] = 'pi-virtual'
    else:state['sessionId'] = 'different'
    with pytest.raises(pi_transport.TransportFault):binding.response(reply(binding.start(), state))
    assert not binding.prompt_sent and binding.number == 1


def test_transport_acquires_configured_resume_with_owner_policy_before_popen(tmp_path, monkeypatch):
    home, work, path, session = with_state(tmp_path)
    backend = adapter(native_options=CONFIGURED)
    backend.validate_resume_session(session, work, home); backend.bind_attempt(home, work, session)
    args = backend.launch(PROFILE, work, None, None, 'Synthetic task')
    args, _ = backend.resume(args, PROFILE, work, None, None, session)
    monkeypatch.chdir(work)
    class LaunchReached(Exception):pass
    def stop_before_launch(*a, **kw):raise LaunchReached
    argv = ['--resource-policy', json.dumps(pi_transport.resource_policy('configured')),
        '--resource-proof', '[]', *args[3:]]
    with pytest.raises(LaunchReached):pi_transport.run(argv, 'Synthetic task', lambda event: None, popen=stop_before_launch)


def test_pi_consultation_return_uses_fresh_context_before_opening_unsupported_resume(tmp_path, monkeypatch):
    root = tmp_path/'instance'
    binding = dict(version=1, requester_step=1, advice_step=2,
        requester_job=dict(path=str(root/'jobs/001.json')), advice_job=dict(path=str(root/'jobs/002.json')),
        requester_seal=dict(path=str(execution.origin(root, 1)/'result-identity.json')),
        advice_seal=dict(path=str(execution.origin(root, 2)/'result-identity.json')))
    job = dict(step=3, role='W', worker_continuity_version=1, worker_continuation=binding,
        direct_refs=[dict(path=f'inputs/parents/{step:03d}/conclusion.json') for step in (1, 2)])
    backend = adapter(native_options=CONFIGURED)
    def forbidden(*a, **kw):pytest.fail('Unsupported consultation return acquired original files')
    monkeypatch.setattr(c, 'read', forbidden); monkeypatch.setattr(c, 'regular', forbidden)
    monkeypatch.setattr(backend, 'validate_resume_session', forbidden)
    monkeypatch.setattr(pi_sessions, 'load_resume_original', forbidden)
    planned = continuation.consultation_launch(root, job, settings={}, backend=backend, profile=PROFILE)
    assert planned == dict(context_mode='new_context', worker_continuation=dict(version=1,
        requester_step=1, advice_step=2, mode='new_context', fallback_reason='consultation_resume_unsupported_backend'))


@pytest.mark.process_e2e
def test_installed_pi_configured_package_custom_state_survives_actual_repair_resume(tmp_path, monkeypatch, record_property):
    import hashlib
    from pathlib import Path
    from scidiscovery.research_loop import budget, research_config
    from test_pi_backend_v2 import (FINAL, collect_separately, configure_installed_pi_fixture,
        installed_pi_fixture, loopback, options)
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    package = agent/'synthetic-state-package'; package.mkdir()
    (package/'package.json').write_text(json.dumps(dict(name='scid-configured-resume-fixture',
        version='1.0.0', pi=dict(extensions=['./extension.ts']))))
    (package/'extension.ts').write_text('''export default function(pi) {
  pi.on("session_start", async () => {
    pi.appendEntry("scid-synthetic-resume-state", {opaque: ["fixture", 1, null]});
  });
}
''')
    values = options(tmp_path, launcher=[launcher], native_options=dict(CONFIGURED, workspace='workspace-write'),
        resume_policy=dict(RESUME))
    values['public_root'].mkdir(); values['objective'].write_text('Synthetic installed-SDK state continuation fixture')
    with loopback(('{malformed', FINAL)) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        config = c.read(agent/'settings.json'); config['packages'] = [str(package)]
        c.write(agent/'settings.json', config); original_settings = (agent/'settings.json').read_bytes()
        research_config.prepare(**values)
        root = values['root']; c.build_job(root, 1, 'H', [])
        c.write(root/'host-started.json', budget.clock_stamp())
        budget.transition(root, 'advance', 'synthetic-configured-resume')
        result = collect_separately(root)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert len(requests) == 2 and errors == []
    first = execution.origin(root, 1); second = first/'continuations/001'
    prior, resumed = c.read(first/'native-outcome.json'), c.read(second/'native-outcome.json')
    intent = c.read(second/'native-intent.json'); retained = prior['native_identity']['session']
    assert intent['continuation_kind'] == 'repair' and intent['session'] == retained
    assert intent['output_repair']['source_attempt'] == 0 and 'work_repair' not in intent
    assert intent['budget_slice']['previous_tokens'] == 22
    assert prior['thread_id'] == resumed['thread_id']
    assert retained['path'] == resumed['native_identity']['session']['path']
    data = Path(retained['path']).read_bytes()
    prefix, tail = data[:retained['size_bytes']], data[retained['size_bytes']:]
    assert hashlib.sha256(prefix).hexdigest() == retained['sha256']
    original_rows = [json.loads(line) for line in prefix.splitlines()]
    tail_rows = [json.loads(line) for line in tail.splitlines()]
    first_state = [row for row in original_rows if row.get('type') == 'custom'
        and row.get('customType') == 'scid-synthetic-resume-state']
    next_state = [row for row in tail_rows if row.get('type') == 'custom'
        and row.get('customType') == 'scid-synthetic-resume-state']
    assert len(first_state) == len(next_state) == 1
    assert first_state[0]['customType'] == next_state[0]['customType'] == 'scid-synthetic-resume-state'
    assert first_state[0]['data'] == next_state[0]['data'] == dict(opaque=['fixture', 1, None])
    assert next_state[0]['id'] != first_state[0]['id']
    position = tail_rows.index(next_state[0])
    assert next_state[0]['parentId'] == (tail_rows[position-1] if position else original_rows[-1])['id']
    assert not any(row.get('message', {}).get('role') == 'user' for row in tail_rows[:position])
    assert resumed['metering']['baseline_offset'] == len(prefix)
    expected = dict(input_tokens=17, cached_input_tokens=7, output_tokens=5)
    assert c.read(first/'usage.json') == c.read(second/'usage.json') == expected
    for outcome in (prior, resumed):
        assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
        assert outcome['metering']['coverage'] == 'terminal'
        assert outcome['native_identity']['participant_scope'] == 'unsupported'
        assert 'controlled' not in outcome['native_identity']
    assert (first/'science-output.json').read_bytes() == b'{malformed'
    assert (second/'science-output.json').read_bytes() == FINAL.encode()
    assert (first/'result-identity.json').is_file()
    assert (agent/'settings.json').read_bytes() == original_settings
