"""New Backend / protocol-33 Pi contracts; no real provider or installation.

Only process_e2e nodes launch processes/namespaces or the installed native Pi.
The collector runs in a separate owner process: never inline native group cleanup
in pytest. Installed availability skips mean not-run, not passed native evidence.
"""
import copy
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from scidiscovery.research_loop import (backends, budget, contracts as c, execution,
    controller, native, profiles, research_config, shared_input_views)
from scidiscovery.research_loop import pi_backend as pi, pi_namespace, pi_sessions, pi_transport

OPTIONS = dict(workspace='read-only', network=True, web='disabled', subagents=False)
# Deliberate fixed-scope peers: one prompt, no repair/wake/replay. These are not
# default-activation fixtures; public defaults are covered independently.
NO_REPAIR = dict(protocol='pi_rpc_v1', usage_scope='turn', max_attempts=1,
    output_repair=False, output_only=False, execution_wake=False)
PROFILE = dict(model='fixture/exact-model', reasoning_effort='high', service_tier=None)
STAMP = '2026-01-01T00:00:00Z'
USAGE = dict(input=10, output=2, cacheRead=7, cacheWrite=3, totalTokens=22)
AUX = dict(input=2, output=1, cacheRead=3, cacheWrite=0, totalTokens=6)
TOKENS = dict(input_tokens=25, cached_input_tokens=10, output_tokens=3)
FINAL = ' \n' + json.dumps(dict(summary='Mechanical transport only',
    scientific_result='No scientific qualification', limitations=['Synthetic native peer'],
    next_action='wait', route_reason='No further work requested')) + '\n\t'


def settings(**changes):
    return {**dict(native_backend='pi', native_launcher_argv=['pi'], native_options=dict(OPTIONS),
        experiment_mode='external'), **PROFILE, **changes}


def adapter(**changes): return backends.selected(settings(**changes))


def options(tmp_path, **changes):
    return {**dict(root=tmp_path/'instance', public_root=tmp_path/'public', objective=tmp_path/'objective.md',
        native_backend='pi', launcher=['pi'], native_options=dict(OPTIONS),
        experiment_mode='external', resume_policy=dict(NO_REPAIR), worker_fragment_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=25) for role in 'HWAE'},
        max_tokens=1000, max_seconds=25, max_steps=2, search_policy=None), **PROFILE, **changes}


def message(text=FINAL, stamp=1, reason='stop'):
    return dict(role='assistant', api='fixture', provider='fixture', model='exact-model',
        timestamp=stamp, content=[dict(type='thinking', thinking='PRIVATE_REASONING'),
            dict(type='text', text=text)], stopReason=reason, usage=dict(USAGE), thinkingLevel='high')


def bind_engine(tmp_path):
    backend = adapter()
    backend.engine.binding = dict(workspace=str(tmp_path/'workspace'))
    backend.engine.profile = dict(PROFILE)
    header = dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(tmp_path/'workspace'))
    assert backend.event(header) == dict(session='exact-session')
    return backend


def test_complete_abc_registration_and_no_codex_protocol_inheritance():
    assert not pi.Pi.__abstractmethods__
    assert not backends.Codex.__abstractmethods__ and not backends.Protocol.__abstractmethods__
    assert issubclass(pi.Pi, backends.Backend)
    assert not issubclass(pi.Pi, (backends.Codex, backends.Protocol))
    assert backends.provider('pi') is pi.Pi
    assert 'pi' in backends.names()
    frozen = backends.configuration('pi', ['pi'], dict(OPTIONS))
    assert frozen['native_capabilities'] == dict(native_launcher_contract=None,
        response_schema_transport='prompt', session_resume=True, complete_native_authorship=True)
    assert adapter().complete_authorship is True
    assert adapter().authorship_scope(dict(participant_scope='unsupported')) is False


def test_configuration_permission_projection_is_pure_and_explicit(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('Pure provider facts acquired filesystem state')
    monkeypatch.setattr(Path, 'stat', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    value = backends.configuration('pi', ['pi'], dict(OPTIONS))
    assert value['native_permissions']['effective'] == OPTIONS
    assert value['native_permissions']['enforcement']['workspace'] == 'host_readonly_mounts'
    assert value['native_permissions']['enforcement']['network'] == 'unrestricted'
    assert adapter().supports_resume
    for fact in ('context', 'compactions'):
        assert adapter().observation_capability(fact)['status'] == 'supported'
    assert adapter().observation_capability('native_calls')['status'] == 'unsupported'
    assert 'retry_and_cache_warming' in adapter().observation_capability('native_calls')['reason']
    assert adapter().observation_capability('terminal')['status'] == 'supported'


@pytest.mark.parametrize('change', [None, {}, dict(OPTIONS, network=False), dict(OPTIONS, network=1),
    dict(OPTIONS, workspace='danger-full-access'), dict(OPTIONS, web='live'), dict(OPTIONS, subagents=True),
    dict(OPTIONS, subagents=0), dict(OPTIONS, pi_extensions=[]), dict(OPTIONS, features={}),
    dict(OPTIONS, sandbox_workspace_write={'writable_roots': ['/outside']})])
def test_unported_native_options_refuse_without_default_or_write_grant(change):
    with pytest.raises(ValueError, match='Pi requires explicit'):
        backends.configuration('pi', ['pi'], change)


@pytest.mark.parametrize('change', [dict(model=None), dict(model=[]), dict(model='bare'),
    dict(model='fixture/'), dict(model='fixture//model'), dict(model='/model'),
    dict(model='fixture/model\x00suffix'), dict(model='fixture/model name'),
    dict(model='fixture/model\n'), dict(model='fixture name/model'),
    dict(service_tier='default'), dict(reasoning_effort=None), dict(reasoning_effort='ultra')])
def test_exact_model_and_explicit_thinking_no_codex_defaults(change):
    with pytest.raises(ValueError): pi.validate_profile({**PROFILE, **change})


@pytest.mark.parametrize('model', ['openrouter/qwen/qwen3-coder:exacto',
    'fixture/nested/path/model:7b', 'fixture/model:high', 'fixture/model[1m]',
    'openrouter/~typesafe/jev-latest', 'fixture/literal*', 'fixture/literal?'])
def test_literal_exact_model_declarations_are_pure_not_catalog_or_selector_resolution(monkeypatch, model):
    profile = dict(PROFILE, model=model)
    value = settings(model=model, role_profiles={r: dict(profile) for r in 'HWAE'},
        worker_profiles={'unused': dict(profile)})
    before = copy.deepcopy(value)
    def forbidden(*a, **kw): pytest.fail('Literal declaration acquired catalog, configuration or process state')
    for name in ('open', 'stat', 'resolve', 'home'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert pi.validate_profile(profile) is profile
    pi.Pi.validate_declaration(value)
    assert value == before
    # These are potential literal IDs, not proof of existence. Native exact
    # readback must refuse fallback even when the string looks like a selector.


@pytest.mark.parametrize('entry', ['normalize', 'toml', 'controller', 'prepare'])
@pytest.mark.parametrize('target', ['base', 'overridden_base', 'H', 'W', 'A', 'E', 'unused_worker'])
def test_every_resolved_role_and_unused_named_worker_validated_before_preparation(tmp_path, monkeypatch, target, entry):
    value = options(tmp_path)
    if target in {'base', 'overridden_base'}:
        value['service_tier'] = 'default'
        if target == 'overridden_base': value['role_profiles'] = {role: dict(PROFILE) for role in 'HWAE'}
    elif target == 'unused_worker': value['worker_profiles'] = {'unused': {'model': 'not-qualified'}}
    else: value['role_profiles'] = {target: {'reasoning_effort': 'not-thinking'}}
    if entry == 'toml':
        agent = dict(backend='pi', **{k: value[k] for k in profiles.FIELDS if value[k] is not None})
        if 'role_profiles' in value: agent['roles'] = value['role_profiles']
        if 'worker_profiles' in value: agent['workers'] = value['worker_profiles']
        document = dict(version=1, research=dict(root='instance', public_root='public', objective_text='Original bounded task'),
            agent=agent, permissions=dict(OPTIONS), budget=dict(max_tokens=1000, max_seconds=25, max_steps=2),
            executor=dict(mode='external'))
        value = research_config.from_document(document, base=tmp_path)
    from scidiscovery.research_loop import project_layout, experiments
    def forbidden(*args, **kwargs): pytest.fail('Rejected profile reached filesystem/plugin/publication/native effects')
    with monkeypatch.context() as guarded:
        guarded.setattr(subprocess, 'Popen', forbidden)
        for name in ('resolve', 'open', 'stat', 'mkdir', 'home'): guarded.setattr(Path, name, forbidden)
        guarded.setattr(project_layout, 'prepare', forbidden)
        guarded.setattr(experiments, 'operator_configuration', forbidden)
        guarded.setattr(experiments, 'registration', forbidden)
        guarded.setattr(c, 'write', forbidden)
        guarded.setattr(c.atomic_files, 'publish_bytes', forbidden)
        with pytest.raises(ValueError):
            if entry == 'controller': controller.prepare(**value)
            elif entry == 'prepare': research_config.prepare(**value)
            else: research_config.normalize(value)
    assert not value['root'].exists()


@pytest.mark.parametrize('validate_only', [False, True])
def test_direct_prepare_provider_hook_receives_normalized_settings_before_publication(tmp_path, monkeypatch, validate_only):
    value = options(tmp_path, native_backend='protocol', native_options={}, resume_policy=None,
        role_profiles={'W': {'worker': 'named'}}, worker_profiles={'named': {'model': 'worker-model'}})
    value['public_root'].mkdir(); value['objective'].write_text('Original bounded task')
    admitted = []
    def reject(self, prepared):
        admitted.append(prepared)
        assert prepared['experiment_mode'] == 'external'
        assert set(prepared['role_profiles']) == set('HWAE')
        assert prepared['role_profiles']['W']['model'] == 'worker-model'
        assert prepared['role_profiles']['A']['model'] == PROFILE['model']
        assert prepared['worker_profiles'] == value['worker_profiles']
        assert prepared['native_options'] == dict(workspace='workspace-write', network=False, web='disabled', subagents=False)
        raise ValueError('provider admission veto')
    def forbidden(*args, **kwargs): pytest.fail('Provider veto reached publication/native effects')
    monkeypatch.setattr(backends.Protocol, 'validate_preparation', reject)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(c, 'write', forbidden)
    monkeypatch.setattr(c.atomic_files, 'publish_bytes', forbidden)
    with pytest.raises(ValueError, match='provider admission veto'):
        controller.prepare(**value, validate_only=validate_only)
    assert len(admitted) == 1 and not value['root'].exists()


def test_pi_prepared_admission_is_pure_and_consumes_normalized_role_rows(monkeypatch):
    backend = adapter()
    value = settings(role_profiles=profiles.normalize({'W': {'worker': 'named'}}, PROFILE,
        {'named': {'reasoning_effort': 'low'}}), worker_profiles={'named': {'reasoning_effort': 'low'}})
    original = copy.deepcopy(value)
    def forbidden(*args, **kwargs): pytest.fail('Prepared admission reacquired or rebuilt owner data')
    monkeypatch.setattr(profiles, 'normalize', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'stat', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    backend.validate_preparation(value)
    assert value == original
    for target in ('H', 'W', 'A', 'E'):
        invalid = copy.deepcopy(value); invalid['role_profiles'][target]['service_tier'] = 'default'
        with pytest.raises(ValueError, match='service_tier'): backend.validate_preparation(invalid)
    for worker in ({'model': 'bare'}, {'service_tier': 'default'},
            {'reasoning_effort': 'ultra'}, {'skill_set': 'unused'}):
        invalid = copy.deepcopy(value); invalid['worker_profiles']['unused'] = worker
        with pytest.raises(ValueError): backend.validate_preparation(invalid)


@pytest.mark.parametrize('policy,value', [('resume_policy', {})])
def test_explicit_unported_resume_protocol_refuses_before_effects(tmp_path, monkeypatch, policy, value):
    declared = options(tmp_path); declared[policy] = value
    before = copy.deepcopy(declared)
    with pytest.raises(ValueError, match='Pi'): research_config.normalize(declared)
    assert declared == before and not declared['root'].exists()
    backend = adapter()
    prepared = settings(**{policy: value})
    before = copy.deepcopy(prepared)
    with pytest.raises(ValueError, match='Pi'): backend.validate_preparation(prepared)
    assert prepared == before
    from scidiscovery.research_loop import project_layout, experiments, skill_supply, continuation
    declared['public_root'].mkdir(); declared['objective'].write_text('Original bounded task')
    def forbidden(*args, **kwargs): pytest.fail('Unsupported declaration reached preparation effects')
    monkeypatch.setattr(project_layout, 'prepare', forbidden)
    monkeypatch.setattr(experiments, 'registration', forbidden)
    monkeypatch.setattr(experiments, 'operator_configuration', forbidden)
    monkeypatch.setattr(skill_supply, 'prepare', forbidden)
    monkeypatch.setattr(continuation, 'policy', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='Pi'):
        controller.prepare(**declared)
    assert not declared['root'].exists()


def test_explicit_required_worker_windows_admit_without_changing_defaults(tmp_path, monkeypatch):
    value = options(tmp_path, worker_fragment_policy={'context': 'same_session', 'max_context_windows': 1})
    before = copy.deepcopy(value)
    def forbidden(*args, **kwargs): pytest.fail('Pure observation admission acquired filesystem/process state')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(c, 'write', forbidden)
    monkeypatch.setattr(c.atomic_files, 'publish_bytes', forbidden)
    normalized = research_config.normalize(value)
    assert normalized['worker_fragment_policy'] == value['worker_fragment_policy']
    assert adapter().default_experiment_mode() == 'local'
    assert pi.DEFAULT_RESUME_POLICY == dict(max_attempts=2, protocol='pi_rpc_v1', usage_scope='turn',
        output_repair=True, output_only=True, execution_wake=True)
    assert value == before and not value['root'].exists()


def test_early_declaration_admission_is_pure(monkeypatch):
    value = settings(); before = copy.deepcopy(value)
    def forbidden(*args, **kwargs): pytest.fail('Early declaration admission acquired filesystem/process state')
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'stat', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pi.Pi.validate_declaration(value)
    assert value == before
    pi.Pi.validate_declaration(dict(value, experiment_mode='local',
        native_options=dict(OPTIONS, workspace='workspace-write'),
        project={'base_root': '/unread', 'editable': ['src']}))
    assert backends.Codex.validate_declaration(value) is None
    assert backends.Protocol.validate_declaration(value) is None


@pytest.mark.parametrize('field', ['launcher', 'native_launcher_argv'])
def test_early_launcher_aliases_are_explicit_data_and_refuse_arguments_without_io(monkeypatch, field):
    value = settings(); value.pop('native_launcher_argv'); value[field] = ['pi', '--extension']
    before = copy.deepcopy(value)
    def forbidden(*a, **kw): pytest.fail('Launcher admission resolved native paths or runtime')
    for name in ('resolve', 'open', 'stat', 'home'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='single executable'): pi.Pi.validate_declaration(value)
    assert value == before
    value[field] = ['pi']
    pi.Pi.validate_declaration(value)
    value['launcher'] = ['other-pi']; value['native_launcher_argv'] = ['pi']
    with pytest.raises(ValueError, match='launcher declarations conflict'): pi.Pi.validate_declaration(value)
    value.pop('launcher'); value.pop('native_launcher_argv')
    pi.Pi.validate_declaration(value)  # Static ['pi'] default; no launcher/home probe.


def test_controller_forwards_declared_native_fields_to_generic_early_veto(tmp_path, monkeypatch):
    value = options(tmp_path, native_backend='protocol', native_options={})
    admitted = []
    def reject(cls, declared):
        admitted.append(declared)
        assert declared['native_options'] is value['native_options']
        assert declared['native_launcher_argv'] is value['launcher']
        assert 'launcher' not in declared
        raise ValueError('generic early veto')
    def forbidden(*a, **kw): pytest.fail('Generic early veto reached path or process effects')
    monkeypatch.setattr(backends.Protocol, 'validate_declaration', classmethod(reject))
    for name in ('resolve', 'open', 'stat', 'mkdir', 'home'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='generic early veto'): controller.prepare(**value)
    assert len(admitted) == 1


@pytest.mark.parametrize('hook', ['declaration', 'preparation'])
@pytest.mark.parametrize('change,match', [
    (dict(experiment_mode='local'), 'Local.*workspace-write'),
    (dict(experiment_mode=None, execution_config={}), 'Local.*workspace-write'),
    (dict(project={}), 'projects.*workspace-write'),
    (dict(native_options=dict(OPTIONS, network=False)), 'Pi requires explicit'),
    (dict(native_options=None), 'Pi requires explicit'),
    (dict(native_launcher_argv=['pi', '--extension']), 'single executable'),
    (dict(execution_config={}), 'conflicts with external'),
    (dict(experiment_mode='local', native_options=dict(OPTIONS, workspace='workspace-write'),
        native_capabilities=dict(native_launcher_contract={})), 'Local.*custom launcher'),
    (dict(project={}, native_options=dict(OPTIONS, workspace='workspace-write'),
        native_capabilities=dict(native_launcher_contract={})), 'projects.*custom launcher'),
])
def test_both_pi_admission_hooks_refuse_unhonorable_permissions_purely(monkeypatch, hook, change, match):
    backend = adapter()
    value = settings(**change); before = copy.deepcopy(value)
    def forbidden(*a, **kw): pytest.fail('Pure admission acquired paths, native configuration or plugins')
    from scidiscovery.research_loop import project_layout, experiments
    for name in ('resolve', 'open', 'stat', 'mkdir', 'home'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(project_layout, 'prepare', forbidden)
    monkeypatch.setattr(experiments, 'operator_configuration', forbidden)
    monkeypatch.setattr(experiments, 'registration', forbidden)
    monkeypatch.setattr(pi_namespace, 'agent_directory', forbidden)
    with pytest.raises(ValueError, match=match):
        if hook == 'declaration': pi.Pi.validate_declaration(value)
        else: backend.validate_preparation(value)
    assert value == before


@pytest.mark.process_e2e
def test_direct_prepare_real_git_project_accepts_explicit_writable_projection(tmp_path, monkeypatch):
    from scidiscovery.research_loop import project_layout
    git = shutil.which('git')
    if git is None: pytest.skip('Git unavailable; real-project refusal evidence NOT RUN')
    repo = tmp_path/'repository'; repo.mkdir()
    for name in ('src', 'scratch', 'outputs'): (repo/name).mkdir()
    (repo/'src/original.txt').write_text('Unchanged project original\n')
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for argv in ([git, 'init'], [git, 'add', 'src/original.txt'], [git, 'commit', '-m', 'fixture']):
        subprocess.run(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
    project = project_layout.configuration(dict(base_root=str(repo), editable=['src'],
        scratch=['scratch'], outputs=['outputs']))
    # Direct controller preparation deliberately has no normalization defaults.
    value = options(tmp_path, project=project, resume_policy=None,
        native_options=dict(OPTIONS,workspace='workspace-write'))
    value['public_root'].mkdir(); value['objective'].write_text('Original bounded task')
    original = (repo/'src/original.txt').read_bytes()
    controller.prepare(**value)
    frozen=c.settings(value['root'])
    assert frozen['project']['base_root']==str(repo)
    assert frozen['project']['editable']==['src']
    assert backends.selected(frozen).project_policy(frozen)['workspace']=='workspace-write'
    assert frozen['native_capabilities']['complete_native_authorship'] is True
    assert (repo/'src/original.txt').read_bytes() == original


def test_configuration_form_and_toml_share_registered_backend(tmp_path):
    from scidiscovery.research_loop import configuration_form
    assert 'pi' in next(f for f in configuration_form.FIELDS if f.path == 'agent.backend').choices
    document = dict(version=1, research=dict(root='instance', public_root='public', objective_text='Original bounded task'),
        agent=dict(backend='pi', model=PROFILE['model'], reasoning_effort='high'), permissions=dict(OPTIONS),
        budget=dict(max_tokens=1000, max_seconds=25, max_steps=2), executor=dict(mode='external'),
        policies=dict(resume=dict(NO_REPAIR)))
    parsed = research_config.from_document(document, base=tmp_path)
    normalized = research_config.normalize(parsed)
    assert normalized['launcher'] == ['pi'] and normalized['service_tier'] is None
    assert normalized['native_options'] == OPTIONS
    assert normalized['resume_policy'] == NO_REPAIR
    assert normalized['worker_fragment_policy'] == dict(context='same_session', max_context_windows=1)
    assert normalized['search_policy'] is not None  # Generic defaults were not erased.


def test_owner_binding_precedes_argv_and_keeps_inherited_auth(tmp_path):
    backend = adapter(); home = tmp_path/'private-home'; work = tmp_path/'workspace'
    with pytest.raises(ValueError, match='owner-bound'): backend.launch(PROFILE, work, None, None, 'scope')
    backend.bind_attempt(home, work)
    args = backend.launch(PROFILE, work, None, None, 'scope')
    assert args[:4] == [sys.executable, '-m', 'scidiscovery.research_loop.pi_transport', '--']
    assert args[args.index('--session')+1] == str(home/'sessions/pi-session.jsonl')
    assert all(args.count(flag) == 1 for flag in pi.RESOURCE_FLAGS)
    assert not set(args) & {'--extension', '--skill', '--output-last-message'}
    assert '--offline' in args  # Suppress automatic catalog/package network, not native tool access.
    env = {'HOME': '/original/home', 'PI_CODING_AGENT_DIR': '/original/runtime'}
    original = dict(env); backend.apply_home(env, home, isolated=False)
    assert env == original
    with pytest.raises(ValueError): backend.wrap_launch(args + ['--extension', '/outside'], {})


def test_rpc_exact_thinking_is_verified_before_the_only_prompt():
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session', 'Original scope')
    state = dict(model=dict(provider='fixture', id='exact-model', api='fixture'), thinkingLevel='high',
        isStreaming=False, isCompacting=False, pendingMessageCount=0,
        sessionFile='/private/session', sessionId='exact-session')
    def reply(command, data=None):
        return dict(type='response', id=command['id'], command=command['type'], success=True, data=data)
    command = binding.start()
    command = binding.response(reply(command, state)); assert command['type'] == 'set_model'
    command = binding.response(reply(command, state['model'])); assert command['type'] == 'set_thinking_level'
    command = binding.response(reply(command)); assert command['type'] == 'get_state'
    with pytest.raises(pi_transport.TransportFault, match='thinking_binding_mismatch'):
        binding.response(reply(command, dict(state, thinkingLevel='low')))
    assert binding.prompt_sent is False
    assert 'prompt' not in binding.pending.values()


def test_namespace_has_only_narrow_runtime_session_and_ephemeral_writes(tmp_path, monkeypatch):
    facts = dict(root=tmp_path/'instance', work=tmp_path/'instance/project/nodes/001',
        sessions=tmp_path/'instance/native-homes/001/sessions', auth=tmp_path/'agent', runtime=())
    monkeypatch.setattr(Path, 'stat', lambda *a, **k: pytest.fail('Pure namespace assembly probed files'))
    args = pi_namespace.arguments_from_facts(facts)
    binds = [args[i+1:i+3] for i, arg in enumerate(args) if arg == '--bind']
    assert binds == [[str(facts['sessions'])]*2, [str(facts['auth'])]*2]
    assert ['--ro-bind', str(facts['work']), str(facts['work'])] == args[args.index(str(facts['work']))-1:args.index(str(facts['work']))+2]
    assert str(tmp_path/'instance/native-evidence/001/result.pending.json') not in args
    assert args.count('--tmpfs') == 3  # Two ephemeral temporary roots and hidden private instance.


def test_workspace_write_namespace_keeps_framework_parent_readonly_and_only_scientific_slots_writable(tmp_path, monkeypatch):
    work = tmp_path/'instance/project/nodes/001'
    facts = dict(root=tmp_path/'instance', work=work, sessions=tmp_path/'sessions',
        auth=tmp_path/'agent', runtime=(), readonly=False)
    def forbidden(*a, **kw):pytest.fail('Pure namespace projection acquired filesystem facts')
    monkeypatch.setattr(Path, 'stat', forbidden); monkeypatch.setattr(Path, 'mkdir', forbidden)
    args = pi_namespace.arguments_from_facts(facts)
    binds = [args[i+1:i+3] for i, arg in enumerate(args) if arg == '--bind']
    assert binds == [[str(work/name)]*2 for name in ('work', 'artifacts', 'experiments')] + [
        [str(facts['sessions'])]*2, [str(facts['auth'])]*2]
    assert args[args.index(str(work))-1:args.index(str(work))+2] == ['--ro-bind', str(work), str(work)]
    assert not any(str(work/name) in args for name in pi_namespace.CONTROL_FILES)
    assert '/dev/null' not in args
    limitations = adapter().permissions(settings(native_options=dict(OPTIONS, workspace='workspace-write')))['limitations']
    assert any('work/, artifacts/ and experiments/ plus the current W code draft' in item for item in limitations)
    assert any('cannot reserve absent names' in item for item in limitations)


def test_required_observations_refuse_without_disabling_generic_gate(tmp_path):
    from scidiscovery.research_loop.native_usage import MetadataUnavailable
    backend = adapter(); meter = pi_sessions.SessionUsage(tmp_path/'home', tmp_path/'workspace')
    with pytest.raises(MetadataUnavailable): backend.configure_observation(meter, tmp_path, 0, None, {}, required=True)
    assert meter.snapshot()['context_windows']['coverage'] == 'unknown'


def test_same_loaded_task_is_validated_without_reopening_records(monkeypatch):
    job = dict(role='H', public_root='/public', global_materials=[{'path': 'inputs/global/objective.md'}], direct_refs=[])
    task = dict(role='H', public_root='/public', parents=[],
        **{'global': dict(objective='inputs/global/objective.md', updates=[])})
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('Task consumer read another original'))
    shared_input_views.validate_task_from_facts(job, task)
    for field, replacement in [('role', 'W'), ('public_root', '/outside'), ('parents', [{'conclusion': '/outside'}]), ('global', {})]:
        with pytest.raises(ValueError, match='Frozen scientific task'): shared_input_views.validate_task_from_facts(job, {**task, field: replacement})


def test_native_estimate_is_not_qualified_context_or_billing(tmp_path):
    value = pi_sessions.session_context(dict(sessionId='id', sessionFile='/exact',
        contextUsage=dict(tokens=34, contextWindow=49), tokens=dict(total=900000)), 'id', '/exact', 12)
    assert value['ratio'] == 34/49 and 'turn_id' not in value and 'current_window' not in value
    meter = pi_sessions.SessionUsage(tmp_path/'home', tmp_path/'workspace')
    events = pi_sessions.UsageEvents(); meter.use_events(events); events.context = value
    assert meter.snapshot()['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    assert meter.snapshot()['has_observation'] is False
    assert meter.snapshot()['context'] == dict(status='unavailable')


def test_exact_final_text_and_strict_lf_jsonl_selector(tmp_path):
    backend = bind_engine(tmp_path)
    backend.event(dict(type='message_end', message=message(' \n```json\n{broken}\n```\t')))
    final = backend.event(dict(type='agent_settled', aborted=False))
    assert final == dict(completed=True, result_text=' \n```json\n{broken}\n```\t')
    selector = pi.PiEventSelector()
    raw = (json.dumps(dict(type='message', text='a\u2028b'), ensure_ascii=False)+'\n').encode()
    assert selector.feed(raw[:8]) == []
    assert selector.feed(raw[8:]) == [dict(type='message', text='a\u2028b')]
    assert selector.feed(b'{bad}\n') == [dict(type='pi_transport_fault')]
    assert selector.feed(b'{}', final=True) == [dict(type='pi_transport_fault')]


@pytest.mark.parametrize('first', [dict(type='error', code='invalid_api_key'), dict(type='error', message='unqualified prose')])
def test_unrelated_clean_completion_cannot_erase_permanent_unknown_failure(tmp_path, first):
    backend = bind_engine(tmp_path)
    failure = backend.event(first)['failure']
    backend.event(dict(type='message_end', message=message()))
    assert not backend.event(dict(type='agent_settled', aborted=False)).get('completed')
    assert backend.combine_failures([failure], completed_before=1)['category'] in {'permanent', 'unknown'}
    assert not backend.terminal_reconciliation_candidate(dict(stop_reason='native_failure_event', returncode=0))


@pytest.mark.parametrize('text,category', [
    ('429: {"type":"rate_limit_exceeded"}', 'temporary'),
    ('429: {"type":"insufficient_quota"}', 'permanent'),
    ('503: {"error":{"type":"rate_limit_exceeded"}}', 'unknown'),
    ('Provider diagnostic mentioning 429', 'unknown'),
])
def test_original_provider_diagnostics_keep_permanent_unknown_veto(text, category):
    assert pi.failure_projection(dict(errorMessage=text))['category'] == category
    permanent = pi.failure_projection(dict(code='invalid_api_key', errorMessage=text))
    assert permanent['category'] == 'permanent'


def test_temporary_failure_needs_same_native_retry_proof(tmp_path):
    backend = bind_engine(tmp_path)
    failure = backend.event(dict(type='error', code='rate_limit_exceeded'))['failure']
    backend.event(dict(type='message_end', message=message()))
    assert backend.combine_failures([failure], completed_before=1) is not None
    backend = bind_engine(tmp_path)
    failure = backend.event(dict(type='error', code='rate_limit_exceeded'))['failure']
    backend.event(dict(type='auto_retry_start'))
    backend.event(dict(type='message_end', message=message()))
    backend.event(dict(type='auto_retry_end', success=True))
    assert backend.event(dict(type='agent_settled', aborted=False))['completed']
    assert backend.combine_failures([failure], completed_before=1) is None
    assert backend.combine_failures([failure], completed_before=None) is not None


def test_session_terminal_keeps_auxiliary_debit_and_unknown_lower_bound(tmp_path):
    home, work = tmp_path/'home', tmp_path/'workspace'
    (home/'sessions').mkdir(parents=True)
    path = home/'sessions/pi-session.jsonl'
    header = dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work))
    msg = message()
    rows = [header, dict(type='message', id='msg', parentId=None, timestamp=STAMP, message=msg),
        dict(type='usage', id='aux', parentId='msg', timestamp=STAMP, kind='future-native-operation', usage=AUX)]
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    backend = adapter(); events = pi_sessions.UsageEvents()
    meter = pi_sessions.SessionUsage(home, work); meter.use_events(events)
    events.add(USAGE, identity=pi_sessions.message_identity(msg))
    observation = backend.finish_observation(meter, required=False)
    assert meter.finished and observation['coverage'] == 'terminal' and observation['tokens'] == TOKENS
    assert backend.terminal_usage(dict(completed=True, post_exit=True), meter) == TOKENS
    with path.open('ab') as stream: stream.write(b'{partial')
    observation = meter.terminal()
    assert observation['coverage'] == 'unknown' and observation['tokens'] == TOKENS
    assert backend.terminal_usage(dict(completed=True, post_exit=True), meter) is None


FAKE_RPC = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
def option(k):return args[args.index(k)+1]
path=pathlib.Path(option('--session'));work=pathlib.Path.cwd()
assert option('--provider')=='fixture' and option('--model')=='exact-model'
assert option('--thinking')=='high'
assert not path.exists()
assert '--no-context-files' in args and '--no-extensions' in args and '--no-skills' in args
state=dict(model=dict(provider='fixture',id='exact-model',api='fixture'),thinkingLevel='high',
 isStreaming=False,isCompacting=False,pendingMessageCount=0,sessionFile=str(path),sessionId='exact-session')
commands=[];prompted=False;fault=os.environ.get('PI_FIXTURE_FAULT','')
stamp='2026-01-01T00:00:00Z'
usage=dict(input=10,output=2,cacheRead=7,cacheWrite=3,totalTokens=22)
aux=dict(input=2,output=1,cacheRead=3,cacheWrite=0,totalTokens=6)
def emit(v):print(json.dumps(v,ensure_ascii=False),flush=True)
def append(v):
 with path.open('ab') as f:f.write((json.dumps(v,ensure_ascii=False)+'\n').encode())
for line in sys.stdin.buffer:
 cmd=json.loads(line);commands.append(cmd['type']);kind=cmd['type'];data=None
 if kind=='get_state':data=dict(state)
 elif kind=='set_model':data=state['model']
 elif kind=='set_thinking_level':assert cmd['level']=='high'
 elif kind=='get_commands':
  inventory=[]
  for index,arg in enumerate(args[:-1]):
   if arg!='--skill':continue
   manual=pathlib.Path(args[index+1]);text=manual.read_text()
   name=next(row[6:] for row in text.splitlines() if row.startswith('name: '))
   description=next(row[13:] for row in text.splitlines() if row.startswith('description: '))
   inventory.append(dict(name='skill:'+name,description=description,source='skill',
    sourceInfo=dict(path=str(manual),source='local',scope='temporary',origin='top-level',baseDir=str(manual.parent))))
  data=dict(commands=inventory)
 elif kind=='get_session_stats':data=dict(sessionFile=str(path),sessionId='exact-session',contextUsage=dict(tokens=22,contextWindow=100))
 elif kind=='prompt':
  assert not prompted;prompted=True
  assert commands[:4]==['get_state','set_model','set_thinking_level','get_state']
  assert 'Return only one JSON object' in cmd['message']
  # Real readonly negative controls: no extra host writable grant for finals,
  # metadata, inputs, workspace, arbitrary paths or control records.
  for target in (work/'work/illegal',work/'science-task.json',work/'inputs/global/objective.md',
                 path.parents[3]/'settings.json',path.parents[3]/'native-evidence/001/result.pending.json'):
   try:
    with target.open('ab') as f:f.write(b'ILLEGAL')
   except OSError:pass
   else:raise AssertionError('Writable namespace escape: '+str(target))
  # Auth/runtime semantics are inherited, not copied into the private home.
  auth=pathlib.Path(os.environ['PI_CODING_AGENT_DIR']);(auth/'runtime-marker').write_text('ok')
  assert os.environ['HOME']==os.environ['PI_FIXTURE_ORIGINAL_HOME']
  # Native persists the session/user boundary before executing any tools.
  # Managed peers inject their work at final selection below, not before this
  # original exists. Resume peers still suppress the header and remap IDs once.
  append(dict(type='session',version=3,id='exact-session',timestamp=stamp,
   cwd='/invalid-fixture-workspace' if fault=='invalid_header' else str(work)))
  append(dict(type='message',id='user',parentId=None,timestamp=stamp,message=dict(role='user',content=cmd['message'],timestamp=0)))
  final=os.environ['PI_FIXTURE_FINAL']
  msg=dict(role='assistant',api='fixture',provider='fixture',model='exact-model',timestamp=1,
   thinkingLevel='high',content=[dict(type='text',text=final)],stopReason='stop',usage=usage)
  append(dict(type='message',id='msg',parentId='user',timestamp=stamp,message=msg))
  data=dict(disposition='started')
 else:raise AssertionError(kind)
 emit(dict(type='response',id=cmd['id'],command=kind,success=True,**(dict(data=data) if data is not None else {})))
 if kind=='prompt':
  emit(dict(type='message_end',message=msg));emit(dict(type='agent_end'))
  if fault in {'missing_settlement','partial_eof'}:
   if fault=='partial_eof':sys.stdout.write('{unterminated');sys.stdout.flush()
   break
  emit(dict(type='agent_settled',aborted=False))
  if fault=='duplicate_settlement':emit(dict(type='agent_settled',aborted=False))
assert commands.count('prompt')==1
# Auxiliary shutdown accounting exists only after EOF. Event-time metering
# cannot establish the final debit, even if no context policy was requested.
append(dict(type='usage',id='shutdown',parentId='msg',timestamp=stamp,kind='cache_warm',usage=aux))
(auth/'commands.json').write_text(json.dumps(commands))
(auth/'session-original.bin').write_bytes(path.read_bytes())
'''

COLLECTOR = '''import json,sys
from pathlib import Path
from scidiscovery.research_loop import native
root=Path(sys.argv[1])
result=native.execute_native(root,1)
print(json.dumps(result))
'''


def require_namespace():
    from scidiscovery.research_loop.experiments import UnsupportedExecution
    try: return shared_input_views.ensure_capability()
    except UnsupportedExecution as error: pytest.skip('Native namespace unavailable; process evidence NOT RUN: ' + str(error))


@pytest.mark.process_e2e
def test_actual_pi_namespace_denies_writes_and_chmod_keeps_inherited_home_and_runtime(tmp_path):
    program = require_namespace()
    root = tmp_path/'instance'; work = root/'project/nodes/001'
    sessions = root/'native-homes/001/sessions'; auth = tmp_path/'fixture-agent'
    (work/'work').mkdir(parents=True); (work/'inputs/global').mkdir(parents=True)
    sessions.mkdir(parents=True); auth.mkdir(mode=0o700)
    targets = [work/'work/original', work/'science-task.json', work/'inputs/global/objective.md']
    for path in [*targets, root/'settings.json']: path.write_bytes(b'unchanged\n')
    (auth/'auth.json').write_bytes(b'{"fixture":"synthetic-not-a-credential"}\n')
    facts = dict(root=root, work=work, sessions=sessions, auth=auth, runtime=())
    # Restore only Python runtime paths if a test environment puts them under
    # ephemeral /tmp, using the same readonly runtime mechanism as production.
    runtime = tuple((path, path) for path in dict.fromkeys((Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(), Path(sys.executable).resolve()))
        if any(path.is_relative_to(temp) for temp in pi_namespace.TEMPORARY))
    facts['runtime'] = runtime
    probe = '''import json,os,pathlib,sys
work,auth,sessions=map(pathlib.Path,sys.argv[1:4])
assert os.environ['HOME']==sys.argv[4]
assert os.environ['PI_CODING_AGENT_DIR']==str(auth)
for relative in ('work/original','science-task.json','inputs/global/objective.md'):
 p=work/relative
 assert p.read_bytes()==b'unchanged\\n'
 for action in ('write','chmod'):
  try:
   if action=='write':
    with p.open('ab') as f:f.write(b'ILLEGAL')
   else:p.chmod(0o600)
  except OSError:pass
  else:raise AssertionError(action+' was allowed: '+str(p))
for p in (work/'work/new',work.parents[2]/'settings.json'):
 try:p.write_bytes(b'ILLEGAL')
 except OSError:pass
 else:raise AssertionError('Control/workspace write allowed')
# Only fixture runtime data is touched, never inherited real credentials.
assert (auth/'auth.json').read_bytes()==b'{"fixture":"synthetic-not-a-credential"}\\n'
(auth/'auth.json').write_bytes(b'fixture-runtime-write\\n')
(auth/'auth.json').chmod(0o600)
(sessions/'probe.jsonl').write_bytes(b'fixture-session-write\\n')
(sessions/'probe.jsonl').chmod(0o600)
print('namespace-probe-ok')
'''
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL') if key in os.environ}
    env['PI_CODING_AGENT_DIR'] = str(auth)
    result = subprocess.run([program, *pi_namespace.arguments_from_facts(facts),
        '--remount-ro', str(root), '--chdir', str(work), '--', str(Path(sys.executable).resolve()),
        '-c', probe, str(work), str(auth), str(sessions), os.environ['HOME']], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert result.stdout == b'namespace-probe-ok\n'
    for path in [*targets, root/'settings.json']: assert path.read_bytes() == b'unchanged\n'
    assert not (work/'work/new').exists()
    assert (auth/'auth.json').read_bytes() == b'fixture-runtime-write\n'
    assert (sessions/'probe.jsonl').read_bytes() == b'fixture-session-write\n'


def prepare_instance(tmp_path, launcher, *, profile=PROFILE):
    public = tmp_path/'public'; public.mkdir()
    objective = tmp_path/'objective.md'; objective.write_text('Bounded original H transport fixture; no scientific qualification.')
    values = options(tmp_path); values.update(profile); values['launcher'] = [str(launcher)]
    research_config.prepare(**values)
    root = values['root']; job = c.build_job(root, 1, 'H', [])
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'pi_phase1_mechanical_fixture')
    return root, job


_COLLECT_DIAGNOSTICS = None


def collect_separately(root, *, timeout=40):
    # Production durable runner owns native process/group cleanup, outside pytest.
    env = dict(os.environ)
    source = str(Path(c.__file__).resolve().parents[2])
    env['PYTHONPATH'] = source + os.pathsep + env.get('PYTHONPATH', '')
    result = None
    collector_stderr = None
    try:
        result = subprocess.run([sys.executable, '-c', COLLECTOR, str(root)], env=env,
            cwd=root.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        collector_stderr = result.stderr
        return result
    except subprocess.TimeoutExpired as error:
        collector_stderr = error.stderr
        raise
    finally:
        if _COLLECT_DIAGNOSTICS is not None and (result is None or result.returncode != 0):
            log_dir, record_property = _COLLECT_DIAGNOSTICS
            from test_pi_backend_interfaces import retain_installed_repair_diagnostics
            destination = Path(log_dir) / ('collector-' + root.parent.name)
            diagnostics = retain_installed_repair_diagnostics(root, destination, collector_stderr)
            assert diagnostics is not None and (diagnostics / 'manifest.json').is_file()
            record_property('pi_collector_failure_diagnostics', str(diagnostics))


@pytest.mark.process_e2e
@pytest.mark.parametrize('text,valid', [(FINAL, True), (' \n```json\n{broken}\n```\n\t', False)])
def test_real_rpc_process_accepted_runner_exact_bytes_postexit_usage(tmp_path, monkeypatch, text, valid):
    require_namespace()
    monkeypatch.delenv('PI_FIXTURE_FAULT', raising=False)
    agent = tmp_path/'fixture-agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    monkeypatch.setenv('PI_FIXTURE_FINAL', text)
    monkeypatch.setenv('PI_FIXTURE_ORIGINAL_HOME', os.environ['HOME'])
    launcher = tmp_path/'fake-pi'; launcher.write_text(FAKE_RPC); launcher.chmod(0o700)
    root, job = prepare_instance(tmp_path, launcher)
    original_job = (root/'jobs/001.json').read_bytes()
    result = collect_separately(root)
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert outcome['native_started'] and outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['cleanup'] == 'observed_group_cleaned' and outcome['terminal_observed']
    assert outcome['native_backend'] == 'pi' and outcome['metering']['coverage'] == 'terminal'
    assert outcome['metering']['reporting_delay_unmeasured'] is False
    assert c.read(record/'usage.json') == TOKENS
    assert (record/'science-output.json').read_bytes() == text.encode('utf-8')
    assert (root/'jobs/001.json').read_bytes() == original_job
    assert outcome['native_identity']['participant_scope'] == 'unsupported'
    stream = [json.loads(line) for line in (record/'native.jsonl').read_bytes().split(b'\n') if line]
    assert stream[0]['type'] == 'session' and stream[-1]['type'] == 'agent_settled'
    assert sum(row['type'] == 'agent_settled' for row in stream) == 1
    assert (agent/'runtime-marker').read_text() == 'ok'
    for name in ('auth.json', 'settings.json', 'models.json'):
        assert not (root/'native-homes/001'/name).exists()
    assert (record/'runner-process.json').is_file() and (record/'native-process.json').is_file()
    assert (result.returncode == 0) if valid else (result.returncode != 0)
    if valid:
        c.validate(c.workspace(root, 1))
        assert (record/'result-identity.json').is_file()
    else:
        assert not (record/'result-identity.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault', ['missing_settlement', 'duplicate_settlement', 'invalid_header', 'partial_eof'])
def test_real_rpc_transport_fault_keeps_billable_originals_without_seal_or_reprompt(tmp_path, monkeypatch, fault):
    require_namespace()
    agent = tmp_path/'fixture-agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    monkeypatch.setenv('PI_FIXTURE_FINAL', FINAL)
    monkeypatch.setenv('PI_FIXTURE_FAULT', fault)
    monkeypatch.setenv('PI_FIXTURE_ORIGINAL_HOME', os.environ['HOME'])
    launcher = tmp_path/'fake-pi'; launcher.write_text(FAKE_RPC); launcher.chmod(0o700)
    root, job = prepare_instance(tmp_path, launcher)
    original_job = (root/'jobs/001.json').read_bytes()
    result = collect_separately(root)
    assert result.returncode != 0
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert outcome['native_started'] and outcome['returncode'] != 0
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['stop_reason'] == 'native_failure_event'
    assert outcome['terminal_observed'] is False
    assert outcome['metering']['coverage'] == 'unknown'
    assert outcome['metering']['reporting_delay_unmeasured'] is True
    assert outcome['metering']['inflight_usage_unknown'] is True
    debit = c.read(record/'usage.json')
    assert debit == (pi_sessions.usage_tokens(USAGE) if fault == 'invalid_header' else TOKENS)
    assert outcome['metering']['tokens'] == debit
    assert (record/'runner-process.json').is_file() and (record/'native-process.json').is_file()
    assert not (record/'result-identity.json').exists()
    assert not (record/'science-output.json').exists() and not (record/'result.pending.json').exists()
    assert not (record/'output-repair.json').exists()
    assert (root/'jobs/001.json').read_bytes() == original_job
    assert c.read(agent/'commands.json').count('prompt') == 1
    assert {key: c.read(root/'settings.json')['resume_policy'][key] for key in NO_REPAIR} == NO_REPAIR
    original = agent/'session-original.bin'
    session = root/'native-homes/001/sessions/pi-session.jsonl'
    assert session.read_bytes() == original.read_bytes()
    rows = [json.loads(row) for row in original.read_bytes().splitlines()]
    assert rows[2]['message']['content'] == [dict(type='text', text=FINAL)]
    assert rows[2]['message']['usage'] == USAGE and rows[-1]['usage'] == AUX
    stream = [json.loads(row) for row in (record/'native.jsonl').read_bytes().splitlines()]
    assert any(row['type'] == 'pi_transport_fault' for row in stream)
    assert not any(row['type'] == 'agent_settled' for row in stream)
    assert any(row['type'] == 'message_end' and row['message']['usage'] == USAGE for row in stream)


@pytest.mark.process_e2e
def test_changed_task_refuses_before_original_pi_process(tmp_path, monkeypatch):
    require_namespace()
    agent = tmp_path/'fixture-agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    launcher = tmp_path/'fake-pi'; launcher.write_text(FAKE_RPC); launcher.chmod(0o700)
    root, job = prepare_instance(tmp_path, launcher)
    path = c.workspace(root, 1)/'science-task.json'
    task = c.read(path); task['public_root'] = '/different'; c.write(path, task)
    result = collect_separately(root)
    assert result.returncode != 0
    assert not (agent/'runtime-marker').exists()
    assert not (root/'native-homes/001/sessions/pi-session.jsonl').exists()
    assert not (execution.origin(root, 1)/'native-process.json').exists()


@contextmanager
def loopback(final, *, model_id='exact-model'):
    """Explicitly one or two deterministic POSTs; no remote provider/model."""
    assert isinstance(final, (str, tuple, list))
    assert isinstance(final, str) or len(final) in (1, 2)
    responses = (final,) if isinstance(final, str) else tuple(final)
    assert all(isinstance(text, str) for text in responses)
    requests = []; errors = []; stop = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def setup(self): self.request.settimeout(2); super().setup()
        def log_message(self, *args): pass
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0')); assert 0 < size <= 256 * 1024
                body = json.loads(self.rfile.read(size)); requests.append(body)
                assert len(requests) <= len(responses) and self.path == '/v1/chat/completions'
                assert body['model'] == model_id and body['scid_fixture_thinking'] == 'high'
                assert self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential'
                text = responses[len(requests)-1]
                chunks = [dict(choices=[dict(index=0, delta=dict(role='assistant', content=text), finish_reason=None)]),
                    dict(choices=[dict(index=0, delta={}, finish_reason='stop')]),
                    dict(choices=[], usage=dict(prompt_tokens=17, completion_tokens=5, total_tokens=22,
                        prompt_tokens_details=dict(cached_tokens=7)))]
                response_id = 'synthetic-one' if len(requests) == 1 else 'synthetic-two'
                wire = b''.join(b'data: '+json.dumps(dict(id=response_id, object='chat.completion.chunk',
                    created=1, model=model_id, **chunk)).encode()+b'\n\n' for chunk in chunks)+b'data: [DONE]\n\n'
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(wire))); self.send_header('Connection', 'close'); self.end_headers()
                self.wfile.write(wire); self.wfile.flush(); self.close_connection = True
            except Exception as error:
                errors.append(type(error).__name__); self.close_connection = True
    server = HTTPServer(('127.0.0.1', 0), Handler); server.timeout = .1
    def serve():
        while not stop.is_set(): server.handle_request()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port)+'/v1', requests, errors
    finally:
        stop.set()
        try: thread.join(timeout=3)
        finally: server.server_close()
        assert not thread.is_alive()


def installed_pi_fixture(tmp_path, monkeypatch, record_property):
    """Installed executable and fresh offline runtime; never inherited auth."""
    log_dir = os.environ.get('SCID_TEST_LOG_DIR')
    monkeypatch.setattr(sys.modules[__name__], '_COLLECT_DIAGNOSTICS',
        (log_dir, record_property) if log_dir else None)
    require_namespace()
    launcher = shutil.which('pi')
    if launcher is None: pytest.skip('Installed Pi unavailable; native Pi evidence NOT RUN')
    launcher = str(Path(launcher).resolve(strict=True))
    # Retain original HOME; use only newly created fixture auth/runtime data.
    for key in tuple(os.environ):
        if key not in {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'PYTHONPATH'}: monkeypatch.delenv(key)
    agent = tmp_path/'fixture-agent'; agent.mkdir(mode=0o700)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent)); monkeypatch.setenv('PI_OFFLINE', '1')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    version = subprocess.run([launcher, '--version'], env=dict(os.environ), cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4, check=True)
    record_property('pi_version', version.stdout.decode().strip())
    return launcher, agent


def configure_installed_pi_fixture(agent, url, *, model_id='exact-model'):
    c.write(agent/'models.json', dict(providers={'fixture': dict(baseUrl=url,
        api='openai-completions', apiKey='synthetic-not-a-credential', models=[dict(id=model_id,
            name='Synthetic transport', reasoning=True, input=['text'], contextWindow=131072,
            maxTokens=2048, cost=dict(input=0, output=0, cacheRead=0, cacheWrite=0),
            samplingParamsByThinkingLevel=dict(high=dict(scid_fixture_thinking='high')))])}))
    c.write(agent/'settings.json', dict(defaultProvider='fixture', defaultModel=model_id,
        defaultThinkingLevel='high', defaultProjectTrust='never', cacheWarming='off',
        compaction=dict(enabled=False), retry=dict(enabled=False, maxRetries=0,
            provider=dict(maxRetries=0, timeoutMs=2500)), httpIdleTimeoutMs=2500,
        transport='sse', enableInstallTelemetry=False, enableAnalytics=False,
        packages=[], extensions=[], skills=[], prompts=[], themes=[]))


@pytest.mark.process_e2e
@pytest.mark.parametrize('text,valid', [(FINAL, True), (' \n```json\n{broken}\n```\n\t', False)])
def test_installed_pi_loopback_uses_current_accepted_collector(tmp_path, monkeypatch, record_property, text, valid):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    with loopback(text) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        root, job = prepare_instance(tmp_path, launcher)
        result = collect_separately(root)
        assert (result.returncode == 0) if valid else (result.returncode != 0)
        assert len(requests) == 1 and errors == []
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['cleanup'] == 'observed_group_cleaned' and outcome['terminal_observed']
    assert outcome['metering']['coverage'] == 'terminal'
    # The host retains original final UTF-8 even when scientific JSON is bad;
    # successful native RPC settlement does not repair or qualify the science.
    assert (record/'science-output.json').read_bytes() == text.encode('utf-8')
    assert c.read(record/'usage.json') == dict(input_tokens=17, cached_input_tokens=7, output_tokens=5)
    assert outcome['native_identity']['participant_scope'] == pi.PARTICIPANT_SCOPE
    assert outcome['native_identity']['controlled']['kind'] == pi.PARTICIPANT_PROOF
    assert {key: c.read(root/'settings.json')['resume_policy'][key] for key in NO_REPAIR} == NO_REPAIR
    assert not (record/'output-repair.json').exists()
    assert (record/'result-identity.json').is_file() is valid
    stream = [json.loads(row) for row in (record/'native.jsonl').read_bytes().splitlines()]
    assert sum(row['type'] == 'agent_settled' and row.get('aborted') is False for row in stream) == 1
    session = root/'native-homes/001/sessions/pi-session.jsonl'
    original = session.read_bytes()
    rows = [json.loads(row) for row in original.splitlines()]
    finals = [row['message'] for row in rows if row.get('type') == 'message'
        and row.get('message', {}).get('role') == 'assistant' and row['message'].get('stopReason') == 'stop']
    assert len(finals) == 1
    assert ''.join(block['text'] for block in finals[0]['content'] if block.get('type') == 'text') == text
    assert session.read_bytes() == original
    if valid: c.validate(c.workspace(root, 1))
