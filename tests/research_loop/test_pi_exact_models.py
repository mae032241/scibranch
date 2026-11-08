"""Literal model identity, not selector semantics or scientific qualification.

Source tests admit potential IDs without catalog IO. Only process_e2e nodes
exercise installed Pi, with isolated synthetic credentials and loopback POSTs;
no real OpenRouter service, model quality or exhaustive native-call claim.
"""
import copy
import json
import os
import selectors
import subprocess
import time

import pytest

from scidiscovery.research_loop import contracts as c, execution
from scidiscovery.research_loop import pi_backend as pi, pi_transport
from test_pi_backend_v2 import (FINAL, PROFILE, collect_separately,
    configure_installed_pi_fixture, installed_pi_fixture, loopback, prepare_instance)


@pytest.mark.parametrize('requested,substitute', [
    ('model:high', 'model'), ('model[1m]', 'model1m'),
    ('model*', 'model-expanded'), ('model?', 'model1'),
    ('model', 'model-expanded'), ('~route', 'route'),
])
@pytest.mark.parametrize('boundary', ['set_model', 'get_state'])
def test_native_selector_or_fuzzy_substitution_is_refused_before_prompt(requested, substitute, boundary):
    pi.validate_profile(dict(PROFILE, model='fixture/' + requested))
    binding = pi_transport.Binding('fixture', requested, 'low', '/private/session', 'Never submit')
    native_model = dict(provider='fixture', id=substitute, api='openai-completions')
    state = dict(model=native_model, thinkingLevel='low', sessionId='session',
        sessionFile='/private/session', isStreaming=False, isCompacting=False, pendingMessageCount=0)
    def reply(command, data=None):
        return dict(type='response', id=command['id'], command=command['type'], success=True, data=data)
    command = binding.response(reply(binding.start(), state))
    assert command['type'] == 'set_model' and command['modelId'] == requested
    if boundary == 'get_state':
        command = binding.response(reply(command, dict(native_model, id=requested)))
        assert command['type'] == 'set_thinking_level' and command['level'] == 'low'
        command = binding.response(reply(command))
        assert command['type'] == 'get_state'
    with pytest.raises(pi_transport.TransportFault, match='model_binding_mismatch'):
        binding.response(reply(command, native_model if boundary == 'set_model' else state))
    assert not binding.prompt_sent and 'prompt' not in binding.pending.values()


@pytest.mark.parametrize('model_id', ['nested/model:exacto', 'model:high', 'model[1m]'])
def test_literal_id_and_separate_thinking_must_both_bind_before_prompt(model_id):
    binding = pi_transport.Binding('fixture', model_id, 'low', '/private/session', 'Original scope')
    model = dict(provider='fixture', id=model_id, api='openai-completions')
    state = dict(model=model, thinkingLevel='low', sessionId='session',
        sessionFile='/private/session', isStreaming=False, isCompacting=False, pendingMessageCount=0)
    def reply(command, data=None):
        return dict(type='response', id=command['id'], command=command['type'], success=True, data=data)
    command = binding.response(reply(binding.start(), state))
    assert command['modelId'] == model_id
    command = binding.response(reply(command, model))
    assert command['type'] == 'set_thinking_level' and command['level'] == 'low'
    command = binding.response(reply(command))
    command = binding.response(reply(command, state))
    assert command['type'] == 'get_commands' and not binding.prompt_sent
    command = binding.response(reply(command, dict(commands=[])))
    assert command['type'] == 'prompt' and command['message'] == 'Original scope'


def native_readback(launcher, work, model_id):
    """Actual SDK getModel lookup via set_model, and two native get_state views.

    Pi exposes no get_model RPC. No prompt is sent by this readback process.
    A distinct thinking level proves a literal :high is not our thinking flag.
    """
    commands = [dict(id='initial', type='get_state'),
        dict(id='model', type='set_model', provider='fixture', modelId=model_id),
        dict(id='thinking', type='set_thinking_level', level='low'),
        dict(id='bound', type='get_state')]
    process = subprocess.Popen([launcher, '--mode', 'rpc', *pi.RESOURCE_FLAGS, '--no-session',
        '--provider', 'fixture', '--model', model_id, '--thinking', 'low'], cwd=work,
        env=dict(os.environ), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    replies = {}; pending = b''; deadline = time.monotonic() + 15
    try:
        # Keep stdin open until native replies: an eager EOF must not race the
        # SDK's asynchronous command handlers. Every read has a finite deadline.
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            for command in commands:
                process.stdin.write((json.dumps(command) + '\n').encode()); process.stdin.flush()
                while command['id'] not in replies:
                    remaining = deadline - time.monotonic()
                    assert remaining > 0 and selector.select(remaining), 'Native readback timed out'
                    chunk = os.read(process.stdout.fileno(), 65536)
                    assert chunk, 'Native readback ended before reply'
                    pending += chunk
                    assert len(pending) <= 1024 * 1024
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        row = json.loads(line)
                        if row.get('type') == 'response':
                            assert row['id'] not in replies
                            replies[row['id']] = row
        process.stdin.close(); process.stdin = None
        tail, stderr = process.communicate(timeout=5)
        extra = pending + tail
        assert not extra or extra.endswith(b'\n')
        assert all(json.loads(line).get('type') != 'response' for line in extra.splitlines())
        assert process.returncode == 0, stderr.decode(errors='replace')
        assert set(replies) == {row['id'] for row in commands}
        assert all(replies[row['id']]['command'] == row['type'] for row in commands)
        return replies
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None: stream.close()


@pytest.mark.process_e2e
@pytest.mark.parametrize('model_id', ['qwen/qwen3-coder:exacto', 'exact-model:high', 'bracketed-model[1m]'])
def test_installed_literal_model_survives_readback_argv_final_and_usage(tmp_path, monkeypatch, record_property, model_id):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    record_property('qualification', 'Installed Pi plus synthetic loopback only; not OpenRouter or scientific qualification')
    profile = dict(PROFILE, model='fixture/' + model_id)
    with loopback(FINAL, model_id=model_id) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url, model_id=model_id)
        replies = native_readback(launcher, tmp_path, model_id)
        assert all(row['success'] is True for row in replies.values())
        for key in ('initial', 'bound'):
            assert replies[key]['data']['model']['provider'] == 'fixture'
            assert replies[key]['data']['model']['id'] == model_id
            assert replies[key]['data']['thinkingLevel'] == 'low'
        assert replies['model']['data']['id'] == model_id
        assert requests == [] and errors == []
        root, _ = prepare_instance(tmp_path, launcher, profile=profile)
        result = collect_separately(root)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert len(requests) == 1 and requests[0]['model'] == model_id and errors == []
    record = execution.origin(root, 1)
    outcome = c.read(record/'native-outcome.json')
    intent = c.read(record/'native-intent.json')
    assert outcome['returncode'] == 0 and outcome['terminal_observed']
    assert outcome['stop_reason'] is None and outcome['native_failure'] is None
    assert outcome['metering']['coverage'] == 'terminal'
    assert c.read(record/'usage.json') == dict(input_tokens=17, cached_input_tokens=7, output_tokens=5)
    assert (record/'science-output.json').read_bytes() == FINAL.encode()
    identity = outcome['native_identity']
    assert identity['participant_scope'] == pi.PARTICIPANT_SCOPE
    assert identity['session']['profile'] == intent['native_profile'] == profile
    proof = identity['controlled']
    assert proof['transport']['profile'] == profile
    argv = proof['transport']['argv']
    assert argv[argv.index('--model') + 1] == model_id
    assert argv[argv.index('--thinking') + 1] == 'high'
    assert all(row['profile'] == profile for row in proof['persisted']['responses'])
    pi._participant(outcome, intent, identity['session']['workspace'])
    # A valid receipt for a literal ID must not become one for its base ID.
    changed = copy.deepcopy(outcome)
    changed['native_identity']['controlled']['persisted']['responses'][0]['profile']['model'] = 'fixture/other'
    with pytest.raises(ValueError): pi._participant(changed, intent, identity['session']['workspace'])
    rows = [json.loads(line) for line in (record/'native.jsonl').read_bytes().splitlines()]
    finals = [row['message'] for row in rows if row['type'] == 'message_end'
        and row.get('message', {}).get('role') == 'assistant']
    assert len(finals) == 1 and finals[0]['model'] == model_id
    assert finals[0]['provider'] == 'fixture'
    assert ''.join(block['text'] for block in finals[0]['content'] if block['type'] == 'text') == FINAL
    assert outcome['metering']['observation_capabilities']['native_calls']['status'] == 'unsupported'


@pytest.mark.process_e2e
@pytest.mark.parametrize('requested', ['exact-model:high', 'exact', 'exact-model*', 'exact-model[1m]'])
def test_installed_missing_literal_id_refuses_native_fallback_before_provider_post(tmp_path, monkeypatch, record_property, requested):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    record_property('qualification', 'Synthetic catalog/fallback refusal only; no provider request or scientific qualification')
    with loopback(FINAL) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)  # Only base exact-model exists.
        replies = native_readback(launcher, tmp_path, requested)
        if requested in {'exact-model:high', 'exact'}:
            # Prove the installed CLI really selected the DIFFERENT base model,
            # not merely that our pure validator accepted/rejected a string.
            assert replies['initial']['success'] is True
            assert replies['initial']['data']['model']['id'] == 'exact-model'
        assert replies['model']['success'] is False  # SDK exact getModel lookup.
        assert requests == [] and errors == []
        root, _ = prepare_instance(tmp_path, launcher, profile=dict(PROFILE, model='fixture/' + requested))
        result = collect_separately(root)
        assert result.returncode != 0
    assert requests == [] and errors == []
    record = execution.origin(root, 1)
    outcome = c.read(record/'native-outcome.json')
    assert outcome['native_started'] and (record/'native-process.json').is_file()
    assert outcome['returncode'] != 0 and not outcome['terminal_observed']
    assert outcome['native_identity']['participant_scope'] == 'unsupported'
    assert not (record/'science-output.json').exists() and not (record/'result-identity.json').exists()
    rows = [json.loads(line) for line in (record/'native.jsonl').read_bytes().splitlines()]
    assert any(row['type'] == 'pi_transport_fault' for row in rows)
    assert not any(row['type'] in {'agent_start', 'message_end', 'agent_settled'} for row in rows)
