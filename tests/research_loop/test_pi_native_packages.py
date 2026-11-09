"""Configured native extensions keep primary-session observation, not authorship.

Only the marked installed-SDK case starts a process; its provider is a bounded
loopback fixture with synthetic authentication, never the user's packages/auth.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (backends, budget, contracts as c, execution,
    pi_backend as pi, pi_namespace, pi_observation, pi_transport, research_config)
from test_pi_authorship import compact_pi_facts
from test_pi_backend_v2 import (FINAL, OPTIONS, PROFILE, adapter, collect_separately,
    configure_installed_pi_fixture, installed_pi_fixture, loopback, options, settings)
from test_pi_resources import command_inventory, reply

CONFIGURED = dict(OPTIONS, resource_mode='configured', web='live')
PACKAGES = ['npm:pi-web-access', 'npm:pi-guard-sandbox', 'npm:pi-gauge']


def configured_handshake(resources=(), *, observer=None):
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session',
        'Original scope', resources=resources, observation=observer, resource_mode='configured')
    state = dict(model=dict(provider='fixture', id='exact-model', api='fixture'),
        thinkingLevel='high', isStreaming=False, isCompacting=False, pendingMessageCount=0,
        sessionFile='/private/session', sessionId='exact-session')
    if observer is not None:
        binding.event(dict(type=pi_observation.START, nonce=observer['nonce'],
            path=observer['path'], workspace=observer['workspace'], session_id='exact-session'))
    command = binding.start()
    for data in (state, state['model'], None, state):
        command = binding.response(reply(command, data))
    assert command['type'] == 'get_commands'
    return binding, command


def observer_proof():
    nonce = 'a' * 64
    return dict(nonce=nonce, path='/private/session', workspace='/work', required=True,
        metadata='/private/.pi-observation-' + nonce + '.json',
        extension='/private/.scid-pi-observation-' + nonce + '.ts')


def extension_command(name='fixture-native-command'):
    return dict(name=name, description='Native fixture extension', source='extension',
        sourceInfo=dict(path='/native/package/extension.ts', source='package', scope='user', origin='package'))


def test_original_packages_are_admitted_only_by_explicit_profile_without_copies(tmp_path):
    agent, work = tmp_path/'agent', tmp_path/'work'
    agent.mkdir(); work.mkdir()
    original = json.dumps(dict(packages=PACKAGES, extensions=[])).encode()
    (agent/'settings.json').write_bytes(original)
    with pytest.raises(pi_transport.TransportFault, match='configured package'):
        pi_transport.load_resource_settings(agent, work)
    assert pi_transport.load_resource_settings(agent, work, mode='configured') == pi_transport.resource_policy('configured')
    assert (agent/'settings.json').read_bytes() == original
    assert not (work/'.pi').exists()
    assert pi.resource_flags() == pi.RESOURCE_FLAGS
    assert pi.resource_flags('closed') == pi.RESOURCE_FLAGS
    assert pi.resource_flags('configured') == tuple(flag for flag in pi.RESOURCE_FLAGS if flag != '--no-extensions')


@pytest.mark.parametrize('mode', ['closed', 'configured'])
@pytest.mark.parametrize('scope', ['global', 'project'])
def test_custom_resolver_and_inherited_prompts_remain_unsupported(tmp_path, mode, scope):
    agent, work = tmp_path/'agent', tmp_path/'work'
    agent.mkdir(); (work/'.pi').mkdir(parents=True)
    target = agent if scope == 'global' else work/'.pi'
    (target/'settings.json').write_text(json.dumps(dict(npmCommand=['private-custom-resolver'])))
    with pytest.raises(pi_transport.TransportFault, match='npmCommand'):
        pi_transport.load_resource_settings(agent, work, mode=mode)
    (target/'settings.json').write_text('{}')
    for name in ('SYSTEM.md', 'APPEND_SYSTEM.md'):
        (target/name).write_text('Private fixture prompt; never read')
        with pytest.raises(pi_transport.TransportFault, match='system prompt'):
            pi_transport.load_resource_settings(agent, work, mode=mode)
        (target/name).unlink()


@pytest.mark.parametrize('change', [dict(web='disabled'), dict(web='cached'), dict(subagents=True),
    dict(network=False), dict(resource_mode='unknown'), dict(resource_mode=None)])
def test_configured_profile_requires_explicit_valid_permissions(change):
    with pytest.raises(ValueError):
        backends.configuration('pi', ['pi'], {**CONFIGURED, **change})


def test_configured_declaration_form_export_and_actual_prepare_share_provider_adapter(tmp_path, monkeypatch):
    public = tmp_path/'public'; public.mkdir()
    configured = dict(CONFIGURED, workspace='workspace-write')
    document = dict(version=1,
        research=dict(root='instance', public_root='public', objective_text='Original bounded fixture'),
        agent=dict(backend='pi', launcher=[sys.executable], model=PROFILE['model'], reasoning_effort='high',
            native=dict(resource_mode='configured')),
        permissions={key: configured[key] for key in OPTIONS},
        budget=dict(max_tokens=1000, max_seconds=25, max_steps=2), executor=dict(mode='local'))
    value = research_config.from_document(document, base=tmp_path)
    assert value['native_options'] == configured
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('Preparation launched a process'))
    # This traverses controller's real native-delegation gate, unlike a direct
    # Backend construction; subagents=false must permit ordinary W/A preparation.
    prepared = research_config.prepare(**value, validate_only=True)
    assert prepared['native_options'] == configured
    assert prepared['native_capabilities']['complete_native_authorship'] is False
    exported = research_config.to_document(tmp_path/'instance', prepared)
    assert exported['agent']['native'] == dict(resource_mode='configured')
    assert exported['permissions']['subagents'] is False
    fields = {field['path']: field for field in pi.Pi.configuration_fields()}
    assert fields['agent.native.resource_mode']['choices'] == ('closed', 'configured')
    assert 'live' in fields['permissions.web']['choices']
    assert adapter(native_options=CONFIGURED).public_options(CONFIGURED) == CONFIGURED
    assert not (tmp_path/'instance').exists()


def test_configured_retained_true_cannot_upgrade_authorship_or_disable_primary_observation():
    backend = adapter(native_options=CONFIGURED, native_capabilities=dict(complete_native_authorship=True))
    assert not backend.complete_authorship
    assert backend.subagent_capability()['status'] == 'unsupported'
    assert backend.observation_capability('context')['status'] == 'supported'
    assert backend.observation_capability('compactions')['status'] == 'supported'
    assert backend.observation_capability('native_calls')['status'] == 'unsupported'
    permission = backend.permissions(settings(native_options=CONFIGURED))
    assert permission['requested']['subagents'] is False
    assert permission['effective']['subagents'] is None
    assert permission['enforcement']['subagents'] == 'extension_dependent'
    assert permission['observation_scope'] == 'primary_session'
    value = dict(policy=dict(CONFIGURED, workspace='workspace-write'))
    assert pi_namespace.execution_arguments_from_facts(value, ['python', 'science.py'],
        policy_cwd='/work', project=None, cwd='/work') == ['python', 'science.py']
    assert pi_namespace.project_policy(settings(native_options=value['policy'])) == value['policy']


def test_configured_extra_native_commands_coexist_with_exact_selected_skill_and_observer():
    resource = dict(path='/installation/alpha/SKILL.md'); observer = observer_proof()
    binding, command = configured_handshake([resource], observer=observer)
    data = command_inventory([resource])
    data['commands'] += [extension_command(), pi_observation.command_proof(observer['extension'])]
    result = binding.response(reply(command, data))
    assert result == dict(id=result['id'], type='prompt', message='Original scope')
    assert binding.observer_bound and binding.resources_bound


@pytest.mark.parametrize('damage', ['missing-observer', 'wrong-observer', 'duplicate-observer',
    'observer-alias', 'missing-skill', 'wrong-skill-source', 'duplicate-skill', 'skill-impersonation'])
def test_configured_extensions_do_not_weaken_observer_or_selected_skill_binding(damage):
    resource = dict(path='/installation/alpha/SKILL.md'); observer = observer_proof()
    binding, command = configured_handshake([resource], observer=observer)
    data = command_inventory([resource])
    internal = pi_observation.command_proof(observer['extension'])
    data['commands'] += [internal, extension_command()]
    if damage == 'missing-observer': data['commands'].remove(internal)
    elif damage == 'wrong-observer': internal['sourceInfo']['path'] = '/unselected/observer.ts'
    elif damage == 'duplicate-observer': data['commands'].append(copy.deepcopy(internal))
    elif damage == 'observer-alias': data['commands'][-1]['sourceInfo']['path'] = observer['extension']
    elif damage == 'missing-skill': data['commands'].pop(0)
    elif damage == 'wrong-skill-source': data['commands'][0]['sourceInfo']['origin'] = 'package'
    elif damage == 'duplicate-skill': data['commands'].append(copy.deepcopy(data['commands'][0]))
    else: data['commands'][-1]['name'] = 'skill:alpha'
    with pytest.raises(pi_transport.TransportFault): binding.response(reply(command, data))
    assert not binding.prompt_sent


@pytest.mark.parametrize('damage', ['model', 'thinking'])
def test_configured_rpc_still_rejects_wrong_exact_profile_before_prompt(damage):
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session',
        'Original scope', resource_mode='configured')
    state = dict(model=dict(provider='fixture', id='exact-model', api='fixture'),
        thinkingLevel='high', isStreaming=False, isCompacting=False, pendingMessageCount=0,
        sessionFile='/private/session', sessionId='exact-session')
    command = binding.response(reply(binding.start(), state))
    command = binding.response(reply(command, state['model']))
    command = binding.response(reply(command))
    if damage == 'model': state['model']['id'] = 'substituted-model'
    else: state['thinkingLevel'] = 'low'
    with pytest.raises(pi_transport.TransportFault): binding.response(reply(command, state))
    assert not binding.prompt_sent


@pytest.mark.parametrize('damage', ['missing-policy', 'unknown-profile', 'forged-policy', 'closed-flags', 'unknown-mode'])
def test_unproven_runtime_profile_or_flags_never_reach_popen(tmp_path, damage):
    backend = adapter(native_options=CONFIGURED)
    backend.bind_attempt(tmp_path/'home', tmp_path/'work')
    argv = backend.launch(PROFILE, tmp_path/'work', None, None, 'scope')[3:]
    policy = pi_transport.resource_policy('configured')
    if damage == 'unknown-profile': policy['profile'] = 'unknown'
    elif damage == 'forged-policy': policy['complete_native_authorship'] = 0
    elif damage == 'closed-flags': argv.append('--no-extensions')
    elif damage == 'unknown-mode':
        with pytest.raises(ValueError): pi.resource_flags('unknown')
        return
    if damage != 'missing-policy':
        argv = ['--resource-policy', json.dumps(policy), '--resource-proof', '[]', *argv]
    with pytest.raises(ValueError):
        pi_transport.run(argv, 'scope', lambda event: None,
            popen=lambda *a, **kw: pytest.fail('Unproven configured profile reached Popen'))


def test_configured_exit_is_diagnostic_and_cannot_be_retained_as_closed_authorship():
    outcome, intent, _, work = compact_pi_facts()
    # Original closed proof still validates byte-for-byte supplied facts.
    assert pi._participant(outcome, intent, work) == ('pi_native_session', 'sdk-session')
    proof = outcome['native_identity']['controlled']
    launch, transport = proof['launch'], proof['transport']
    launch['kind'] = 'pi_cli_configured_extensions_v1'
    launch['resource_policy'] = pi_transport.resource_policy('configured')
    transport['resource_policy'] = copy.deepcopy(launch['resource_policy'])
    transport['argv'].remove('--no-extensions')
    pi._controlled_transport(transport, launch, proof['session'], transport['prompt_sha256'])
    with pytest.raises(ValueError): pi._participant(outcome, intent, work)


@pytest.mark.process_e2e
def test_installed_pi_loads_native_local_package_and_preserves_primary_observer(tmp_path, monkeypatch, record_property):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    # Local already-installed package under the newly created fixture runtime;
    # native settings select it, without --extension conversion or npm/network.
    package = agent/'fixture-native-package'; package.mkdir()
    (package/'package.json').write_text(json.dumps(dict(name='scid-native-package-fixture',
        version='1.0.0', pi=dict(extensions=['./extension.ts']))))
    (package/'extension.ts').write_text('''import fs from "node:fs";
import path from "node:path";
export default function(pi) {
  pi.registerCommand("scid-native-package-fixture", {
    description: "Local engineering package fixture", handler: async () => {}
  });
  pi.on("session_start", async () => {
    fs.writeFileSync(path.join(process.env.PI_CODING_AGENT_DIR, "native-package-loaded.json"),
      JSON.stringify({loaded: true}));
  });
}
''')
    value = options(tmp_path, launcher=[launcher], native_options=dict(CONFIGURED, workspace='workspace-write'))
    value['public_root'].mkdir(); value['objective'].write_text('Installed local package engineering fixture only')
    with loopback(FINAL) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        config = c.read(agent/'settings.json'); config['packages'] = [str(package)]
        c.write(agent/'settings.json', config)
        original_settings = (agent/'settings.json').read_bytes()
        research_config.prepare(**value)
        root = value['root']; c.build_job(root, 1, 'H', [])
        c.write(root/'host-started.json', budget.clock_stamp())
        budget.transition(root, 'advance', 'configured-native-package')
        result = collect_separately(root)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert len(requests) == 1 and errors == []
    assert c.read(agent/'native-package-loaded.json') == dict(loaded=True)
    assert (agent/'settings.json').read_bytes() == original_settings
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['metering']['coverage'] == 'terminal'
    assert outcome['native_identity']['participant_scope'] == 'unsupported'
    assert 'controlled' not in outcome['native_identity']
    assert c.read(record/'usage.json') == dict(input_tokens=17, cached_input_tokens=7, output_tokens=5)
    assert (record/'science-output.json').read_bytes() == FINAL.encode()
    stream = [json.loads(line) for line in (record/'native.jsonl').read_bytes().splitlines()]
    receipt = stream[-1]
    assert receipt['type'] == pi_transport.CONTROLLED_EXIT
    assert receipt['resource_policy'] == pi_transport.resource_policy('configured')
    assert receipt['observer_command']['name'] == 'scid-internal-observation-v1'
    assert '--no-extensions' not in receipt['argv'] and '--offline' in receipt['argv']
    assert all(not (root/'native-homes/001'/name).exists() for name in ('auth.json', 'models.json', 'settings.json'))
