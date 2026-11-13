"""Exact live V3 resources through Pi's native resource API, not prompt copies.

Only process_e2e nodes launch processes. Native cleanup stays in the separate
production runner; deterministic loopback responses confer no science verdict.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (backends, budget, configuration_form, pi_backend,
    contracts as c, execution, pi_namespace, pi_observation, pi_transport, profiles,
    research_config, shared_input_views as views, skill_supply)
from test_pi_backend_interfaces import retain_installed_repair_diagnostics
from test_pi_backend_v2 import (FAKE_RPC, FINAL, OPTIONS, PROFILE, TOKENS, adapter,
    collect_separately, configure_installed_pi_fixture, installed_pi_fixture,
    loopback, options, require_namespace, settings)


def selected_library(base):
    source = base / 'installation-skills'
    for name in ('alpha', 'beta'):
        bundle = source / name
        (bundle / 'scripts').mkdir(parents=True)
        (bundle / '.git').mkdir()
        (bundle / '.git/config').write_text('Skill metadata control\n')
        (bundle / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Live {name} instructions\n---\nManual version one.\n')
        (bundle / 'scripts/run.py').write_text('print("live resource")\n')
        (bundle / 'scripts/run.py').chmod(0o755)
        # A nested manual is a resource, never another selected instruction.
        (bundle / 'scripts/SKILL.md').write_text(
            '---\nname: unselected-nested\ndescription: Must not be loaded\n---\n')
    return source


def selected_settings(source, *, role='H'):
    value = settings(skills=dict(version=3, root=str(source),
        sets=dict(chosen=dict(items=['alpha']), unused=dict(items=['beta'])),
        launcher=dict(kind='pi_explicit_skills', version=1)),
        role_profiles=profiles.normalize({role: dict(skill_set='chosen')}, PROFILE,
            skill_sets=dict(chosen=dict(items=['alpha']), unused=dict(items=['beta']))))
    job = dict(step=1, role=role)
    job['skill_binding'] = skill_supply.binding(value, job)
    return value, job


def command_inventory(resources, *, name='alpha', description='Live alpha instructions'):
    # Synthetic RPC data, not a second implementation of native YAML parsing.
    return dict(commands=[dict(name='skill:' + name, description=description,
        source='skill', sourceInfo=dict(path=row['path'], source='local', scope='temporary',
            origin='top-level', baseDir=str(Path(row['path']).parent))) for row in resources])


def reply(command, data=None, *, success=True):
    return dict(type='response', id=command['id'], command=command['type'], success=success, data=data)


def handshake(resources=()):
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session',
        'Original role scope', resources=resources)
    state = dict(model=dict(provider='fixture', id='exact-model', api='fixture'),
        thinkingLevel='high', isStreaming=False, isCompacting=False, pendingMessageCount=0,
        sessionFile='/private/session', sessionId='exact-session')
    command = binding.start()
    command = binding.response(reply(command, state))
    command = binding.response(reply(command, state['model']))
    command = binding.response(reply(command))
    command = binding.response(reply(command, state))
    assert command['type'] == 'get_commands' and not binding.prompt_sent
    return binding, command


@pytest.mark.parametrize('role', list('HWAE'))
@pytest.mark.parametrize('workspace', ['read-only', 'workspace-write'])
def test_selected_skill_declarations_use_shared_catalog_and_v3_binding_purely(tmp_path, monkeypatch, role, workspace):
    source = tmp_path / 'unread-installation'
    value, job = selected_settings(source, role=role)
    value['native_options'] = dict(OPTIONS, workspace=workspace)
    before = copy.deepcopy(value)
    def forbidden(*a, **kw): pytest.fail('Pure selection/admission acquired assets or controls')
    for name in ('stat', 'lstat', 'resolve', 'open', 'mkdir'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(skill_supply, 'catalog', forbidden)
    monkeypatch.setattr(skill_supply, '_metadata', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    backend = backends.selected(value)
    backend.validate_preparation(value)
    backend.project_skills(tmp_path, value, job)
    assert backend.skill_loading_contract(['pi']) == dict(kind='pi_explicit_skills', version=1)
    assert skill_supply.selected_paths(value, job) == {'alpha': source / 'alpha'}
    assert value == before and backend.complete_authorship
    changed = copy.deepcopy(job); changed['skill_binding']['items'] = ['beta']
    with pytest.raises(ValueError, match='Skill binding changed'):
        backend.project_skills(tmp_path, value, changed)
    with pytest.raises(ValueError, match='identity changed'):
        backend.skill_loading_contract(['pi'], retained=dict(kind='other', version=1))


def test_live_native_command_name_is_not_the_frozen_bundle_directory(tmp_path):
    source = selected_library(tmp_path)
    value, job = selected_settings(source)
    root = tmp_path / 'instance'; c.workspace(root, 1).mkdir(parents=True)
    manual = source / 'alpha/SKILL.md'
    manual.write_text(manual.read_text().replace('name: alpha', 'name: live-native-name'))
    facts = skill_supply.load_selected_resources(root, value, job, rebuild=True)
    assert job['skill_binding']['items'] == ['alpha']
    assert facts['resources'] == (dict(path=str(manual)),)
    binding, command = handshake(facts['resources'])
    prompt = binding.response(reply(command, command_inventory(facts['resources'],
        name='live-native-name', description='Native live description, not frozen metadata')))
    assert prompt['type'] == 'prompt' and prompt['message'] == 'Original role scope'
    # Other providers retain their original directory/name matching contract.
    with pytest.raises(ValueError, match='directory and native name must match'):
        skill_supply._metadata(source / 'alpha')


def test_public_form_toml_and_named_worker_keep_shared_selection_and_current_defaults(tmp_path, monkeypatch):
    document = dict(version=1, research=dict(root='instance', public_root='public', objective_text='Original task'),
        agent=dict(backend='pi', launcher=[sys.executable], model=PROFILE['model'], reasoning_effort='high',
            workers={'selected': {'skill_set': 'chosen'}}, roles={'W': {'worker': 'selected'}}),
        skills=dict(sets=dict(chosen=dict(items=['alpha']), unused=dict(items=['missing']))),
        permissions=dict(OPTIONS, workspace='workspace-write'),
        budget=dict(max_tokens=1000, max_seconds=25, max_steps=2), executor=dict(mode='local'))
    # Display and source inventory remain shared, not a Pi-specific catalog.
    assert any(field.path == 'skills.sets' for field in configuration_form.FIELDS)
    value = research_config.normalize(research_config.from_document(document, base=tmp_path))
    assert value['skills'] == document['skills']
    assert value['role_profiles'] == {'W': {'worker': 'selected'}}
    assert value['worker_profiles'] == {'selected': {'skill_set': 'chosen'}}
    assert {key:value['resume_policy'][key] for key in pi_backend.DEFAULT_RESUME_POLICY} == pi_backend.DEFAULT_RESUME_POLICY
    assert value['resume_policy']['automatic_recovery']['max_attempts'] == 8
    assert value['worker_fragment_policy'] == dict(context='same_session', max_context_windows=1,context_handoff_version=2)
    assert set(value['node_budget_policy']) == set('HWAE')
    assert adapter().default_experiment_mode() == 'local'


def test_launch_owner_freezes_selection_observes_exact_manual_without_parsing_and_never_snapshots_resources(tmp_path, monkeypatch):
    source = selected_library(tmp_path)
    value, job = selected_settings(source)
    root = tmp_path / 'instance'; work = c.workspace(root, 1); work.mkdir(parents=True)
    # An unselected declared set must not require even its native manual.
    (source / 'beta/SKILL.md').unlink()
    manual = source / 'alpha/SKILL.md'
    original_binding = copy.deepcopy(job['skill_binding'])
    manual.write_text(manual.read_text().replace('version one', 'LIVE EDITED VERSION'))
    resource = source / 'alpha/scripts/run.py'; resource.write_text('print("edited live resource")\n')
    acquired = []; manual_path = skill_supply._manual_path
    def acquire(path):
        acquired.append(path); return manual_path(path)
    monkeypatch.setattr(skill_supply, '_manual_path', acquire)
    monkeypatch.setattr(skill_supply, '_metadata', lambda *a: pytest.fail('Pi predicted native YAML'))
    monkeypatch.setattr(skill_supply, 'catalog', lambda: pytest.fail('Launch discovered a catalog'))
    monkeypatch.setattr(c, 'read', lambda *a: pytest.fail('Launch reopened immutable control records'))
    facts = skill_supply.load_selected_resources(root, value, job, rebuild=True)
    assert acquired == [source / 'alpha']
    assert facts['resources'] == (dict(path=str(manual)),)
    assert facts['mounts'] == ((work / '.agents/skills',) * 2, (source / 'alpha',) * 2)
    assert (work / '.agents/skills/alpha/SKILL.md').samefile(manual)
    assert 'LIVE EDITED VERSION' in (work / '.agents/skills/alpha/SKILL.md').read_text()
    assert (work / '.agents/skills/alpha/scripts/run.py').samefile(resource)
    assert resource.stat().st_mode & 0o111
    assert job['skill_binding'] == original_binding
    assert not (root / 'host-control/skills').exists()
    assert not (work / '.agents/skills/beta').exists()
    acquired.clear()
    skill_supply.load_selected_resources(root, value, job)
    assert acquired == [source / 'alpha']


@pytest.mark.parametrize('damage', ['absent', 'substituted', 'unselected', 'duplicate', 'invalid-name',
    'empty-description', 'autoload', 'cli-label', 'package', 'scope', 'missing-base',
    'wrong-base', 'missing-provenance', 'extension', 'prompt'])
def test_rpc_resource_provenance_negative_vetoes_before_single_prompt_without_io(monkeypatch, damage):
    resources = [dict(path='/installation/alpha/SKILL.md')]
    data = command_inventory(resources)
    row = data['commands'][0]
    if damage == 'absent': data['commands'] = []
    elif damage == 'substituted': row['sourceInfo']['path'] = '/installation/beta/SKILL.md'
    elif damage == 'unselected':
        data['commands'].extend(command_inventory([dict(path='/installation/beta/SKILL.md')], name='beta')['commands'])
    elif damage == 'duplicate': data['commands'].append(copy.deepcopy(row))
    elif damage == 'invalid-name': row['name'] = 'skill:invalid/name'
    elif damage == 'empty-description': row['description'] = '  '
    elif damage == 'autoload': row['sourceInfo']['source'] = 'auto'
    elif damage == 'cli-label': row['sourceInfo']['source'] = 'cli'
    elif damage == 'package': row['sourceInfo']['origin'] = 'package'
    elif damage == 'scope': row['sourceInfo']['scope'] = 'user'
    elif damage == 'missing-base': row['sourceInfo'].pop('baseDir')
    elif damage == 'wrong-base': row['sourceInfo']['baseDir'] = '/installation/beta'
    elif damage == 'missing-provenance': row.pop('sourceInfo')
    else: row['source'] = damage
    def forbidden(*a, **kw): pytest.fail('RPC validator reopened assets or controls')
    for name in ('stat', 'lstat', 'resolve', 'open'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    binding, command = handshake(resources)
    with pytest.raises(pi_transport.TransportFault, match='resource_binding_mismatch'):
        binding.response(reply(command, data))
    assert not binding.prompt_sent and 'prompt' not in binding.pending.values()


@pytest.mark.parametrize('resources', [[], [dict(path='/installation/alpha/SKILL.md')]])
def test_rpc_exact_inventory_binds_once_including_empty_selection(resources):
    binding, command = handshake(resources)
    prompt = binding.response(reply(command, command_inventory(resources)))
    assert prompt['type'] == 'prompt' and prompt['message'] == 'Original role scope'
    assert binding.resources_bound and binding.prompt_sent
    with pytest.raises(pi_transport.TransportFault): binding.response(reply(command, command_inventory(resources)))
    assert list(binding.pending.values()) == ['prompt']


def test_empty_selection_rejects_native_autoloaded_command():
    binding, command = handshake()
    data = command_inventory([dict(path='/other/alpha/SKILL.md')], description='Unexpected')
    with pytest.raises(pi_transport.TransportFault): binding.response(reply(command, data))
    assert not binding.prompt_sent


def test_resource_argv_and_mount_consumers_are_pure_exact_and_do_not_change_native_auth(tmp_path, monkeypatch):
    backend = adapter(); home = tmp_path / 'home'; work = tmp_path / 'instance/project/nodes/001'
    backend.bind_attempt(home, work)
    args = backend.launch(PROFILE, work, None, None, 'Original role')
    resource = dict(path='/installation/alpha/SKILL.md')
    facts = dict(program='/exact/bwrap', root=tmp_path / 'instance', work=work, home=home,
        sessions=home / 'sessions', auth=tmp_path / 'agent', runtime=(), readonly=True,
        launcher=Path('/exact/pi'), launcher_argument='pi',
        rpc_prefix=tuple(args[:3]), resources=(resource,), resource_policy=pi_transport.resource_policy(),
        mounts=dict(target=work, mappings={'/installation/alpha': Path('/installation/alpha')},
            input_mappings=[], project_aliases=[]))
    def forbidden(*a, **kw): pytest.fail('Pure resource argv consumer acquired filesystem facts')
    for name in ('stat', 'lstat', 'resolve', 'open', 'mkdir'): monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    wrapped, notice = backend.prepare_launch(args, work, 0, {}, facts, None)
    command = wrapped[wrapped.command_index + 1:]
    assert command[:3] == args[:3]
    assert command[3] == '--resource-policy' and json.loads(command[4]) == pi_transport.resource_policy()
    assert command[5] == '--resource-proof' and json.loads(command[6]) == [resource]
    assert command[-2:] == ['--skill', resource['path']]
    parsed = pi_transport._arguments(command[7:])
    assert parsed[-1] == (resource['path'],)
    assert parsed[0][0] == '/exact/pi'
    assert notice['status'] == 'unavailable'
    env = dict(HOME='/original/home', PI_CODING_AGENT_DIR='/original/agent')
    backend.apply_home(env, home, isolated=True)
    assert env == dict(HOME='/original/home', PI_CODING_AGENT_DIR='/original/agent')
    assert backend.complete_authorship


@pytest.mark.parametrize('flag', ['--skill', '--extension', '--prompt-template', '--system-prompt',
    '--package', '--install', '--approve', '--no-offline'])
def test_unowned_or_unproven_resource_flags_never_reach_native_process(tmp_path, flag):
    home = tmp_path / 'home'; work = tmp_path / 'work'
    backend = adapter(); backend.bind_attempt(home, work)
    args = backend.launch(PROFILE, work, None, None, 'scope')[3:]
    args += [flag, '/outside/alpha/SKILL.md']
    def forbidden(*a, **kw): pytest.fail('Unproven resources reached native Popen')
    with pytest.raises(ValueError): pi_transport.run(args, 'scope', lambda event: None, popen=forbidden)


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['absent', 'substituted', 'unselected'])
def test_actual_rpc_inventory_mismatch_stops_before_prompt_and_preserves_originals(tmp_path, monkeypatch, damage):
    require_namespace()
    source = selected_library(tmp_path)
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
    agent = tmp_path / 'agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    monkeypatch.setenv('PI_FIXTURE_FINAL', FINAL)
    monkeypatch.setenv('PI_FIXTURE_ORIGINAL_HOME', os.environ['HOME'])
    mutation = ("data['commands']=[]" if damage == 'absent' else
        "data['commands'][0]['sourceInfo']['path']='/substituted/alpha/SKILL.md'" if damage == 'substituted' else
        "data['commands'].append(dict(data['commands'][0],name='skill:unselected'))")
    peer = FAKE_RPC.replace('  data=dict(commands=inventory)',
        '  data=dict(commands=inventory)\n  ' + mutation).replace(
        "cmd=json.loads(line);commands.append(cmd['type']);kind=cmd['type'];data=None",
        "cmd=json.loads(line);commands.append(cmd['type']);kind=cmd['type'];data=None\n (pathlib.Path(os.environ['PI_CODING_AGENT_DIR'])/'observed-commands.json').write_text(json.dumps(commands))").replace(
        "assert commands.count('prompt')==1", "if not prompted:raise SystemExit(0)\nassert commands.count('prompt')==1")
    launcher = tmp_path / 'peer-pi'; launcher.write_text(peer); launcher.chmod(0o700)
    value = options(tmp_path, launcher=[str(launcher)], skills=dict(sets=dict(chosen=dict(items=['alpha']))),
        role_profiles={'H': dict(skill_set='chosen')})
    value['public_root'].mkdir(); value['objective'].write_text('Exact selected resources; no model submission')
    research_config.prepare(**value)
    root = value['root']; job = c.build_job(root, 1, 'H', [])
    originals = {path: path.read_bytes() for path in (root / 'settings.json', root / 'jobs/001.json',
        c.workspace(root, 1) / 'science-task.json', source / 'alpha/SKILL.md')}
    c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'resource-negative')
    result = collect_separately(root)
    assert result.returncode != 0
    commands = c.read(agent / 'observed-commands.json')
    assert commands == ['get_state', 'set_model', 'set_thinking_level', 'get_state', 'get_commands']
    record = execution.origin(root, 1); outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] and outcome['cleanup'] == 'observed_group_cleaned'
    assert not (record / 'science-output.json').exists() and not (record / 'result-identity.json').exists()
    assert not (record / 'continuations/001/native-intent.json').exists()
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert job['skill_binding']['items'] == ['alpha']


# A transparent wrapper around INSTALLED Pi, not a simulated RPC resource API.
# It observes get_commands in the real namespace without adding a prompt/query.
INSTALLED_OBSERVER = r'''#!/usr/bin/env python3
import hashlib,json,os,pathlib,subprocess,sys,threading
args=sys.argv[1:];agent=pathlib.Path(os.environ['PI_CODING_AGENT_DIR'])
extensions=[pathlib.Path(args[i+1]) for i,arg in enumerate(args[:-1]) if arg=='--extension']
assert len(extensions)==1
extension=extensions[0];original=extension.read_bytes();info=extension.stat()
assert info.st_nlink==1 and info.st_mode & 0o777==0o400
for action in (lambda:extension.write_bytes(b'ILLEGAL'),lambda:extension.chmod(0o600)):
 try:action()
 except OSError:pass
 else:raise AssertionError('mutable controlled observer')
assert extension.read_bytes()==original
(agent/'observer-source.json').write_text(json.dumps(dict(path=str(extension),
 sha256=hashlib.sha256(original).hexdigest(),readonly_mount_verified=True)))
manuals=[pathlib.Path(args[i+1]) for i,arg in enumerate(args[:-1]) if arg=='--skill']
assert len(manuals)==1
manual=manuals[0];bundle=manual.parent
assert 'LIVE EDITED VERSION' in manual.read_text()
assert (bundle/'scripts/run.py').stat().st_mode & 0o111
assert (bundle/'scripts/run.py').read_text()=='print("LIVE EDITED RESOURCE")\n'
asset=subprocess.run([sys.executable,str(bundle/'scripts/run.py')],check=True,capture_output=True,timeout=3)
assert asset.stdout==b'LIVE EDITED RESOURCE\n'
# Ordinary scientific scratch is writable; only the resource aliases are not.
scratch=pathlib.Path.cwd()/'work/ordinary-scratch'
scratch.write_text('writable scientific scratch');scratch.chmod(0o600)
assert scratch.read_text()=='writable scientific scratch'
for p in (manual,bundle/'scripts/run.py',bundle/'.git/config',pathlib.Path.cwd()/'work/manual-alias',pathlib.Path.cwd()/__RESOURCE_ALIAS__):
 for action in (lambda:p.write_text('ILLEGAL'),lambda:p.chmod(0o600)):
  try:action()
  except OSError:pass
  else:raise AssertionError('mutable resource '+str(p))
(agent/'live-manual.txt').write_text(manual.read_text())
process=subprocess.Popen([__PI__,*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
commands=[];inventory=[]
def forward():
 for line in sys.stdin.buffer:
  command=json.loads(line);commands.append(command['type'])
  process.stdin.write(line);process.stdin.flush()
 process.stdin.close()
thread=threading.Thread(target=forward);thread.start()
for line in process.stdout:
 value=json.loads(line)
 if value.get('type')=='response' and value.get('command')=='get_commands':inventory.append(value)
 sys.stdout.buffer.write(line);sys.stdout.buffer.flush()
thread.join(timeout=5);assert not thread.is_alive()
rc=process.wait(timeout=5)
(agent/'resource-commands.json').write_text(json.dumps(commands))
(agent/'resource-inventory.json').write_text(json.dumps(inventory))
raise SystemExit(rc)
'''


NATIVE_YAML = [
    ('name: live-native-name\ndescription: Live alpha instructions', 'Live alpha instructions'),
    ('"name": "live-native-name"\n"description": >-\n  Live alpha\n  instructions', 'Live alpha instructions'),
    ('{name: live-native-name, description: "Live alpha instructions"}', 'Live alpha instructions'),
    ('name: &native live-native-name # native YAML comment\ndescription: |-\n  Live alpha\n  instructions',
        'Live alpha\ninstructions'),
]


@pytest.mark.process_e2e
@pytest.mark.parametrize('frontmatter,native_description', NATIVE_YAML)
def test_installed_pi_nonempty_selection_native_commands_live_manual_namespace_and_current_accounting(tmp_path, monkeypatch, record_property, frontmatter, native_description):
    _installed_pi_live_resource_case(tmp_path, monkeypatch, record_property, frontmatter, native_description)


@pytest.mark.process_e2e
def test_installed_pi_resource_alias_deliverable_refused_after_clean_native_and_known_usage(tmp_path, monkeypatch, record_property):
    # One control, not another cross-product of native YAML formats.
    _installed_pi_live_resource_case(tmp_path, monkeypatch, record_property, *NATIVE_YAML[0],
        artifact_alias=True)


def _installed_pi_live_resource_case(tmp_path, monkeypatch, record_property, frontmatter, native_description, *, artifact_alias=False):
    # Bind before the installed fixture clears non-allowlisted environment.
    # Fixed parameter indices avoid private origin paths and sibling overwrites.
    diagnostic_log_dir = os.environ.get('SCID_TEST_LOG_DIR')
    if diagnostic_log_dir:
        metadata_index = NATIVE_YAML.index((frontmatter, native_description))
        case = f'live-native-skill-metadata-{metadata_index:03d}'
        if artifact_alias:
            case += '-artifact-alias'
        diagnostic_log_dir = Path(diagnostic_log_dir) / case
    installed, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    source = selected_library(tmp_path)
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
    wrapper = tmp_path / 'observed-pi'
    # Scratch aliases are not deliverables; artifacts/ aliases are enumerated
    # and must remain subject to the collector's mutable hard-link veto.
    alias_relative = 'artifacts/resource-alias' if artifact_alias else 'work/resource-alias'
    wrapper.write_text(INSTALLED_OBSERVER.replace('__PI__', repr(installed)).replace(
        '__RESOURCE_ALIAS__', repr(alias_relative))); wrapper.chmod(0o700)
    value = options(tmp_path, launcher=[str(wrapper)], skills=dict(sets=dict(chosen=dict(items=['alpha']))),
        role_profiles={'H': dict(skill_set='chosen')}, native_options=dict(OPTIONS, workspace='workspace-write'))
    value['public_root'].mkdir(); value['objective'].write_text('Installed native resource integration only')
    with loopback(FINAL) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        # Automatic configured sources must NOT enter the native inventory.
        config = c.read(agent / 'settings.json'); config['skills'] = [str(source / 'beta')]
        c.write(agent / 'settings.json', config)
        research_config.prepare(**value)
        root = value['root']; job = c.build_job(root, 1, 'H', []); work = c.workspace(root, 1)
        manual = source / 'alpha/SKILL.md'
        manual.write_text('---\n' + frontmatter + '\n---\nLIVE EDITED VERSION\n')
        (source / 'alpha/scripts/run.py').write_text('print("LIVE EDITED RESOURCE")\n')
        os.link(manual, work / 'work/manual-alias')
        os.link(source / 'alpha/scripts/run.py', work / alias_relative)
        assert (work / 'work/manual-alias').samefile(manual)
        assert (work / alias_relative).samefile(source / 'alpha/scripts/run.py')
        original_job = (root / 'jobs/001.json').read_bytes()
        c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'installed-resource')
        collector_stderr = None
        try:
            result = collect_separately(root)
            collector_stderr = result.stderr
        except subprocess.TimeoutExpired as error:
            collector_stderr = error.stderr
            raise
        finally:
            if diagnostic_log_dir:
                # Fixed attempt/session originals only; engineering diagnostics,
                # never scientific evidence or an alternate acceptance oracle.
                diagnostics = retain_installed_repair_diagnostics(root,
                    diagnostic_log_dir, collector_stderr)
                assert diagnostics is not None and (diagnostics / 'manifest.json').is_file()
                record_property('pi_resource_diagnostics', str(diagnostics))
        if artifact_alias:
            assert result.returncode != 0
            assert b"Mutable deliverable 'resource-alias' must not have hard-link aliases" in result.stderr
        else:
            assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert len(requests) == 1 and errors == []
    assert len(requests) == 1 and errors == []
    commands = c.read(agent / 'resource-commands.json')
    assert commands[:6] == ['get_state', 'set_model', 'set_thinking_level', 'get_state', 'get_commands', 'prompt']
    assert commands.count('prompt') == 1 and commands.count('get_commands') == 1
    assert 'get_available_models' not in commands and 'get_messages' not in commands
    inventory = c.read(agent / 'resource-inventory.json')
    assert len(inventory) == 1
    rows = inventory[0]['data']['commands']
    skills = [row for row in rows if row.get('source') == 'skill']
    internal = [row for row in rows if row.get('source') != 'skill']
    assert len(rows) == 2 and len(skills) == len(internal) == 1
    assert skills[0] == dict(name='skill:live-native-name', description=native_description,
        source='skill', sourceInfo=dict(path=str(manual), source='local', scope='temporary',
            origin='top-level', baseDir=str(manual.parent)))
    # The only non-Skill command must come from the exact private, readonly
    # framework source actually passed to installed Pi, not any extension.
    observed_source = c.read(agent / 'observer-source.json')
    extension = Path(observed_source['path'])
    sessions = root / 'native-homes/001/sessions'
    assert extension.parent == sessions
    assert extension.name.startswith('.scid-pi-observation-') and extension.suffix == '.ts'
    nonce = extension.name[len('.scid-pi-observation-'):-len('.ts')]
    proof = pi_transport.observation_proof(dict(nonce=nonce,
        path=str(sessions / 'pi-session.jsonl'), workspace=str(work),
        metadata=str(sessions / ('.pi-observation-' + nonce + '.json')),
        extension=str(extension), required=False))
    original_source = pi_observation.extension_source(work, proof).encode('utf-8')
    assert observed_source == dict(path=str(extension),
        sha256=hashlib.sha256(original_source).hexdigest(), readonly_mount_verified=True)
    assert extension.read_bytes() == original_source
    assert internal == [dict(name='scid-internal-observation-v1',
        description='Framework native observation adapter v1 (no action)', source='extension',
        sourceInfo=dict(path=str(extension), source='cli', scope='temporary', origin='top-level'))]
    assert nonce not in internal[0]['description']
    assert nonce.encode() not in (execution.origin(root, 1) / 'native-public.jsonl').read_bytes()
    assert 'LIVE EDITED VERSION' in (agent / 'live-manual.txt').read_text()
    assert c.read(agent / 'settings.json')['packages'] == []
    assert not (agent / 'npm').exists() and not (agent / 'git').exists()
    assert not (agent / 'tmp/extensions').exists()
    assert manual.read_text() == '---\n' + frontmatter + '\n---\nLIVE EDITED VERSION\n'
    assert (work / 'work/manual-alias').samefile(manual)
    assert (work / alias_relative).samefile(source / 'alpha/scripts/run.py')
    assert (source / 'alpha/scripts/run.py').read_text() == 'print("LIVE EDITED RESOURCE")\n'
    assert (source / 'alpha/scripts/run.py').stat().st_mode & 0o111
    assert (source / 'alpha/.git/config').read_text() == 'Skill metadata control\n'
    assert (work / 'work/ordinary-scratch').read_text() == 'writable scientific scratch'
    # Offline is resource policy, not a network=false fiction: the actual
    # explicit provider POST above succeeded, with original credentials.
    assert c.read(root / 'settings.json')['native_permissions']['effective']['network'] is True
    assert job['skill_binding']['version'] == 3 and job['skill_binding']['items'] == ['alpha']
    assert (root / 'jobs/001.json').read_bytes() == original_job
    record = execution.origin(root, 1); outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is True and outcome['returncode'] == 0
    assert outcome['cleanup'] == 'observed_group_cleaned' and outcome['stop_reason'] is None
    assert outcome['metering']['coverage'] == 'terminal'
    debit = c.read(record / 'usage.json')
    assert debit == dict(input_tokens=17, cached_input_tokens=7, output_tokens=5)
    assert debit['input_tokens'] + debit['output_tokens'] == 22
    assert outcome['native_identity']['participant_scope'] == pi_backend.PARTICIPANT_SCOPE
    assert outcome['native_identity']['controlled']['kind'] == pi_backend.PARTICIPANT_PROOF
    if artifact_alias:
        # Clean native settlement and known debit are not collector acceptance
        # or scientific authority; keep the aliased original in place.
        for name in ('result.pending.json', 'result-identity.json', 'output-repair.json'):
            assert not (record / name).exists()
        assert not (record / 'continuations/001/native-intent.json').exists()
    else:
        assert (record / 'result-identity.json').is_file()
    # Collector refusal must not erase the exact native original or its debit.
    assert (record / 'science-output.json').read_bytes() == FINAL.encode()
    stream = [json.loads(line) for line in (record / 'native.jsonl').read_bytes().splitlines()]
    assert stream[0]['type'] == 'session' and stream[0]['version'] == 3
    assert stream[-2] == dict(type='agent_settled', aborted=False)
    assert stream[-1]['type'] == pi_transport.CONTROLLED_EXIT
    assert all(not (root / 'native-homes/001' / name).exists() for name in ('auth.json', 'models.json', 'settings.json'))
    if not artifact_alias:
        c.validate(work)


@pytest.mark.parametrize('redirect', [False, True], ids=['ordinary-live-assets', 'reject-resource-symlink'])
def test_managed_local_excluding_instructions_still_protects_skill_resources_and_output_aliases(tmp_path, monkeypatch, redirect):
    from scidiscovery.research_loop import accepted_prefix, experiment_client
    root = tmp_path / 'instance'; work = c.workspace(root, 1); record = execution.origin(root, 1)
    (work / 'work').mkdir(parents=True); (work / 'artifacts').mkdir(); record.mkdir(parents=True)
    source = selected_library(tmp_path)
    value, job = selected_settings(source, role='W')
    job['formal_review'] = {'mechanical_fixture': True}
    output = views.dynamic_root(root, work, 'retained-experiments') / 'call/outputs'
    output.mkdir(parents=True)
    target = work / 'experiments/run/artifacts'; target.mkdir(parents=True)
    aliases = []
    for original, name in [(source / 'alpha/SKILL.md', 'manual'),
            (source / 'alpha/scripts/run.py', 'executable'),
            (source / 'alpha/.git/config', 'metadata')]:
        alias = output / name; os.link(original, alias); aliases.append(alias)
    fresh = output / 'fresh'; fresh.write_text('legitimate output')
    sources = dict(root=root, work=work, target=work, job=job, policy=value,
        native=dict(record=record, intent={}, records=(record,)), material_paths={},
        author=None, wake=[], review_code=[])
    monkeypatch.setattr(accepted_prefix, 'input_rows', lambda job: [])
    monkeypatch.setattr(experiment_client, '_input_rows', lambda job: [])
    def forbidden(*a, **kw): pytest.fail('Managed Local loaded Skill instructions or projection')
    monkeypatch.setattr(skill_supply, 'native_mounts', forbidden)
    monkeypatch.setattr(skill_supply, 'native_mounts_from_policy', forbidden)
    monkeypatch.setattr(skill_supply, '_metadata', forbidden)
    originals = views.load_execution_original_facts(root, job, work, native=sources['native'])
    if redirect:
        from scidiscovery.research_loop.experiments import UnsupportedExecution
        # No outside tree is discovered or mounted to resolve this redirect.
        (source / 'alpha/scripts/outside').symlink_to(tmp_path / 'never-probe')
        with pytest.raises(UnsupportedExecution, match='Skill resources.*symlinks'):
            views.load_pi_mount_facts(root, job, work, protected_paths=[], source_facts=sources,
                selected_code=[], output_artifacts=output, writable_output_mounts=[(output, target)], **originals)
        return
    facts = views.load_pi_mount_facts(root, job, work, protected_paths=[], source_facts=sources,
        selected_code=[], output_artifacts=output, writable_output_mounts=[(output, target)], **originals)
    readonly = {destination: original for original, destination in facts['project_aliases']}
    assert all(readonly[target / alias.name] == alias for alias in aliases)
    assert target / 'fresh' not in readonly
    assert str(source / 'alpha') not in facts['mappings']
    assert str(work / '.agents/skills') not in facts['mappings']
    assert (source / 'alpha/scripts/run.py').stat().st_mode & 0o111
    assert not {'PI_CODING_AGENT_DIR', 'CODEX_HOME'} & set(pi_namespace.execution_arguments_from_facts(
        dict(policy=dict(OPTIONS, workspace='workspace-write')), ['/usr/bin/python3', 'run.py'],
        policy_cwd=work, project=None, cwd=str(work)))


@pytest.mark.parametrize('scope', ['global', 'project'])
@pytest.mark.parametrize('declared', [
    dict(packages=['npm:never-install']),
    dict(packages=['git:example.invalid/never-fetch']),
    dict(packages=['/never-probe/local-package']),
    dict(packages=[dict(source='npm:never-install', autoload=False,
        extensions=[], skills=[], prompts=[], themes=[])]),
    dict(packages=None), dict(packages={}), dict(packages='npm:never-install'),
    dict(npmCommand=['/never-execute/manager', '--custom']), dict(npmCommand='npm'),
    dict(npmCommand=False)])
def test_native_resource_profile_rejects_both_scopes_purely_even_filtered_or_empty_selection(monkeypatch, scope, declared):
    values = dict(global_settings={}, project_settings={})
    values[scope + '_settings'] = declared
    before = copy.deepcopy(values)
    def forbidden(*a, **kw): pytest.fail('Pure settings admission performed IO or package resolution')
    for name in ('stat', 'resolve', 'open', 'mkdir', 'home'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(pi_transport.TransportFault, match='NativeRuntimeProfile'):
        pi_transport.validate_resource_settings(**values)
    assert values == before


def test_native_resource_settings_owner_reads_only_two_exact_originals_once_without_copy(tmp_path, monkeypatch):
    agent = tmp_path / 'agent'; agent.mkdir()
    work = tmp_path / 'workspace'; (work / '.pi').mkdir(parents=True)
    paths = (agent / 'settings.json', work / '.pi/settings.json')
    paths[0].write_bytes(b'\xef\xbb\xbf{"packages":[],"defaultProvider":"fixture","private":"never propagate"}')
    paths[1].write_text('{"packages":[],"skills":["/not-selected"]}')
    originals = {path: path.read_bytes() for path in paths}
    acquired = []; open_original = os.open
    def acquire(path, *args, **kwargs):
        acquired.append(Path(path)); return open_original(path, *args, **kwargs)
    def forbidden(*a, **kw): pytest.fail('Owner copied config or discovered package/cache/auth')
    monkeypatch.setattr(os, 'open', acquire)
    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(os, 'scandir', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    facts = pi_transport.load_resource_settings(agent, work)
    assert acquired == list(paths)
    assert facts == pi_transport.resource_policy() and 'private' not in json.dumps(facts)
    assert all(path.read_bytes() == original for path, original in originals.items())
    permissions = adapter().permissions(settings())
    assert permissions['resource_policy'] == facts
    assert permissions['effective']['network'] is True
    assert permissions['enforcement']['network'] == 'unrestricted'
    assert any('NativeRuntimeProfile limitation' in item for item in permissions['limitations'])
    assert any('not explicit provider API requests' in item for item in permissions['limitations'])


@pytest.mark.parametrize('declared', [{}, dict(packages=[]), dict(packages=[], npmCommand=[]),
    dict(npmCommand=None)])
def test_empty_native_settings_remain_supported_without_disabling_provider_or_network(monkeypatch, declared):
    before = copy.deepcopy(declared)
    def forbidden(*a, **kw): pytest.fail('Pure settings validation acquired anything')
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pi_transport.validate_resource_settings(declared, {})
    pi_transport.validate_resource_settings({}, declared)
    assert declared == before
    assert pi_transport.resource_policy()['provider_requests'] == 'unchanged'
    assert pi_transport.resource_policy()['native_network'] == 'unrestricted'


@pytest.mark.parametrize('damage', ['malformed', 'array', 'symlink', 'directory', 'oversized'])
def test_native_resource_settings_owner_refuses_unsafe_exact_originals(tmp_path, damage):
    agent = tmp_path / 'agent'; agent.mkdir()
    work = tmp_path / 'workspace'; work.mkdir()
    path = agent / 'settings.json'
    if damage == 'symlink':
        original = tmp_path / 'original.json'; original.write_text('{}'); path.symlink_to(original)
    elif damage == 'directory': path.mkdir()
    else: path.write_text('{bad' if damage == 'malformed' else '[]' if damage == 'array'
        else ' ' * (pi_transport.LINE_BYTES + 1))
    with pytest.raises((OSError, ValueError)):
        pi_transport.load_resource_settings(agent, work)


@pytest.mark.parametrize('scope', ['global', 'project'])
@pytest.mark.parametrize('selected', [False, True], ids=['no-resources', 'selected-skills'])
@pytest.mark.parametrize('declared', [dict(packages=['npm:never-resolve']),
    dict(npmCommand=['/never-execute/custom-manager'])])
def test_namespace_refuses_current_package_refs_before_probe_projection_or_native_effects(tmp_path, monkeypatch, scope, selected, declared):
    root = tmp_path / 'instance'; work = c.workspace(root, 1); (work / '.pi').mkdir(parents=True)
    home = root / 'native-homes/001'; (home / 'sessions').mkdir(parents=True)
    agent = tmp_path / 'agent'; agent.mkdir()
    path = (agent / 'settings.json' if scope == 'global' else work / '.pi/settings.json')
    c.write(path, declared)
    value, job = selected_settings(tmp_path / 'never-read-library') if selected else (settings(), dict(role='H'))
    job = dict(job, role='H', project=None)
    sources = dict(policy=value)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    def forbidden(*a, **kw): pytest.fail('Rejected package refs reached probe/asset/native effects')
    monkeypatch.setattr(views, 'ensure_capability', forbidden)
    monkeypatch.setattr(skill_supply, 'load_selected_resources', forbidden)
    monkeypatch.setattr(skill_supply, 'project_from_targets', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(pi_transport.TransportFault, match='NativeRuntimeProfile'):
        pi_namespace.load_facts(root, job, work, policy=value, native_home=home,
            native_argv=['never-spawn'], context=None, selected_code=[], source_facts=sources)
    assert c.read(path) == declared
    assert not (work / '.agents/skills').exists()


@pytest.mark.parametrize('selected', [False, True])
def test_transport_requires_owner_admitted_runtime_profile_before_native_popen(tmp_path, selected):
    backend = adapter(); home = tmp_path / 'home'; work = tmp_path / 'workspace'
    backend.bind_attempt(home, work)
    args = backend.launch(PROFILE, work, None, None, 'scope')[3:]
    resources = ([dict(path='/installation/alpha/SKILL.md')] if selected else [])
    args = ['--resource-proof', json.dumps(resources), *args,
        *[part for row in resources for part in ('--skill', row['path'])]]
    def forbidden(*a, **kw): pytest.fail('Missing owner resource profile reached native Popen')
    events = []
    with pytest.raises(pi_transport.TransportFault,match='resource_policy_missing_or_changed'):
        pi_transport.run(args, 'scope', events.append, popen=forbidden)
    assert events == []


@pytest.mark.parametrize('selected', [False, True])
def test_transport_consumes_admitted_profile_and_exact_selection_without_settings_fallback(tmp_path, monkeypatch, selected):
    backend = adapter(); home = tmp_path / 'home'; work = tmp_path / 'workspace'
    backend.bind_attempt(home, work)
    args = backend.launch(PROFILE, work, None, None, 'scope')[3:]
    resources = ([dict(path='/installation/alpha/SKILL.md')] if selected else [])
    argv = ['--resource-policy', json.dumps(pi_transport.resource_policy()),
        '--resource-proof', json.dumps(resources), *args,
        *[part for row in resources for part in ('--skill', row['path'])]]
    monkeypatch.setattr(pi_transport, '_header_line', lambda *a, **kw: None)
    def forbidden(*a, **kw): pytest.fail('Transport consumer reacquired settings or resource files')
    monkeypatch.setattr(pi_transport, 'load_resource_settings', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    for name in ('stat', 'resolve', 'open'): monkeypatch.setattr(Path, name, forbidden)
    class ReachedNative(Exception): pass
    def admitted(child, **kwargs):
        assert child[0] == 'pi' and '--offline' in child and '--no-skills' in child
        assert child.count('--skill') == len(resources)
        assert not {'--resource-policy', '--resource-proof', '--extension'} & set(child)
        raise ReachedNative()
    with pytest.raises(ReachedNative):
        pi_transport.run(argv, 'scope', forbidden, popen=admitted)


def test_missing_native_settings_mean_defaults_and_do_not_create_files(tmp_path, monkeypatch):
    agent = tmp_path / 'agent'; agent.mkdir()
    work = tmp_path / 'workspace'; work.mkdir()
    def forbidden(*a, **kw): pytest.fail('Missing settings caused discovery, creation or package work')
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(os, 'scandir', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert pi_transport.load_resource_settings(agent, work) == pi_transport.resource_policy()
    assert not (agent / 'settings.json').exists() and not (work / '.pi').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('scope', ['global', 'project'])
@pytest.mark.parametrize('selected', [False, True], ids=['no-resources', 'selected-skills'])
def test_accepted_runner_refuses_package_effects_before_native_start_without_config_copy(tmp_path, monkeypatch, scope, selected):
    require_namespace()
    agent = tmp_path / 'agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    marker = agent / 'forbidden-package-effect'
    launcher = tmp_path / 'never-pi'
    launcher.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ILLEGAL")\n')
    launcher.chmod(0o700)
    value = options(tmp_path, launcher=[str(launcher)])
    if selected:
        source = selected_library(tmp_path)
        monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
        value.update(skills=dict(sets=dict(chosen=dict(items=['alpha']))),
            role_profiles={'H': dict(skill_set='chosen')})
    value['public_root'].mkdir(); value['objective'].write_text('No native/package submission permitted')
    research_config.prepare(**value)
    root = value['root']; c.build_job(root, 1, 'H', []); work = c.workspace(root, 1)
    path = agent / 'settings.json' if scope == 'global' else work / '.pi/settings.json'
    path.parent.mkdir(exist_ok=True)
    c.write(path, dict(packages=['npm:never-installed'], npmCommand=[str(launcher)]))
    original = path.read_bytes()
    c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'package-refusal')
    result = collect_separately(root)
    assert result.returncode != 0 and not marker.exists()
    record = execution.origin(root, 1)
    assert not (record / 'native-process.json').exists()
    assert not (root / 'native-homes/001/sessions/pi-session.jsonl').exists()
    assert not (record / 'science-output.json').exists() and not (record / 'result-identity.json').exists()
    assert path.read_bytes() == original
    for name in ('settings.json', 'auth.json', 'models.json'):
        assert not (root / 'native-homes/001' / name).exists()
    assert not (agent / 'npm').exists() and not (agent / 'git').exists()


@pytest.mark.parametrize('frontmatter,native_description', NATIVE_YAML)
def test_pi_prepare_and_validation_observe_manual_identity_without_python_yaml(tmp_path, monkeypatch, frontmatter, native_description):
    source = selected_library(tmp_path)
    manual = source / 'alpha/SKILL.md'
    manual.write_text('---\n' + frontmatter + '\n---\nLive native body\n')
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
    def forbidden(*a, **kw): pytest.fail('Pi selection parsed native metadata or launched a process')
    monkeypatch.setattr(skill_supply, '_metadata', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    declared = skill_supply.prepare(dict(sets=dict(chosen=dict(items=['alpha']))),
        backend='pi', launcher=['pi'], role_profiles={'H': dict(skill_set='chosen')})
    value = settings(skills=declared, role_profiles={'H': dict(skill_set='chosen')})
    assert skill_supply.validate(tmp_path, value) == declared
    assert set(declared) == {'version', 'root', 'sets', 'launcher'}
    assert 'live-native-name' not in json.dumps(declared)


@pytest.mark.parametrize('damage', ['collision', 'same-source-two-names', 'missing-selected'])
def test_native_inventory_requires_bijective_manual_sources_and_unique_native_names(damage):
    resources = [dict(path='/installation/alpha/SKILL.md'), dict(path='/installation/beta/SKILL.md')]
    first = command_inventory(resources[:1], name='live-alpha')['commands'][0]
    second = command_inventory(resources[1:], name='live-beta')['commands'][0]
    if damage == 'collision': second['name'] = first['name']
    elif damage == 'same-source-two-names': second['sourceInfo'] = dict(first['sourceInfo'])
    data = dict(commands=[first] if damage == 'missing-selected' else [first, second])
    binding, command = handshake(resources)
    with pytest.raises(pi_transport.TransportFault, match='resource_binding_mismatch'):
        binding.response(reply(command, data))
    assert not binding.prompt_sent and 'prompt' not in binding.pending.values()


@pytest.mark.parametrize('present', [(True, False, False, False), (False, True, False, False),
    (False, False, True, False), (False, False, False, True), (), [False] * 4, (0,) * 4])
def test_prompt_source_validator_is_pure_and_requires_four_absence_facts(monkeypatch, present):
    def forbidden(*a, **kw): pytest.fail('Pure prompt-source admission performed IO')
    for name in ('lstat', 'stat', 'resolve', 'open', 'read_text'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(os, 'scandir', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(pi_transport.TransportFault, match='inherited system prompt'):
        pi_transport.validate_prompt_sources(present)
    pi_transport.validate_prompt_sources((False,) * 4)


def test_prompt_source_owner_observes_only_sdk_four_fixed_candidates_without_content_reads(tmp_path, monkeypatch):
    agent = tmp_path / 'agent'; agent.mkdir()
    work = tmp_path / 'workspace'; work.mkdir()
    observed = []; original_lstat = Path.lstat
    def observe(path, *a, **kw):
        observed.append(path); return original_lstat(path, *a, **kw)
    def forbidden(*a, **kw): pytest.fail('Prompt-source admission read contents or discovered ancestors/trees')
    monkeypatch.setattr(Path, 'lstat', observe)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(os, 'scandir', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pi_transport.load_prompt_sources(agent, work)
    assert observed == [agent / 'SYSTEM.md', agent / 'APPEND_SYSTEM.md',
        work / '.pi/SYSTEM.md', work / '.pi/APPEND_SYSTEM.md']


@pytest.mark.parametrize('scope', ['global', 'project'])
@pytest.mark.parametrize('name', ['SYSTEM.md', 'APPEND_SYSTEM.md'])
@pytest.mark.parametrize('selected', [False, True], ids=['no-resources', 'selected-skills'])
@pytest.mark.parametrize('entry', ['file', 'directory', 'dangling-symlink'])
def test_namespace_refuses_inherited_prompt_before_any_native_or_resource_effect(tmp_path, monkeypatch, scope, name, selected, entry):
    root = tmp_path / 'instance'; work = c.workspace(root, 1); (work / '.pi').mkdir(parents=True)
    home = root / 'native-homes/001'; (home / 'sessions').mkdir(parents=True)
    agent = tmp_path / 'agent'; agent.mkdir()
    path = (agent if scope == 'global' else work / '.pi') / name
    if entry == 'file': path.write_text('PRIVATE_INSTALLED_SYSTEM_SENTINEL')
    elif entry == 'directory': path.mkdir()
    else: path.symlink_to(tmp_path / 'never-read-target')
    value, job = selected_settings(tmp_path / 'never-read-library') if selected else (settings(), dict(role='H'))
    job = dict(job, project=None)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    before_env = dict(os.environ)
    def forbidden(*a, **kw): pytest.fail('Inherited instructions reached content reads/probe/projection/Popen')
    monkeypatch.setattr(views, 'ensure_capability', forbidden)
    monkeypatch.setattr(skill_supply, 'load_selected_resources', forbidden)
    monkeypatch.setattr(skill_supply, 'project_from_targets', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(os, 'scandir', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(pi_transport.TransportFault, match='inherited system prompt') as refused:
        pi_namespace.load_facts(root, job, work, policy=value, native_home=home,
            native_argv=['never-spawn'], context=None, selected_code=[], source_facts=dict(policy=value))
    assert 'PRIVATE_INSTALLED_SYSTEM_SENTINEL' not in str(refused.value)
    assert dict(os.environ) == before_env
    assert not (work / '.agents/skills').exists()


INSTALLED_SENTINEL = r'''#!/usr/bin/env python3
import os,pathlib
(pathlib.Path(os.environ['PI_CODING_AGENT_DIR'])/'forbidden-native-start').write_text('ILLEGAL')
os.execv(__PI__, [__PI__, *os.sys.argv[1:]])
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('scope', ['global', 'project'])
@pytest.mark.parametrize('name', ['SYSTEM.md', 'APPEND_SYSTEM.md'])
@pytest.mark.parametrize('selected', [False, True], ids=['no-resources', 'selected-skills'])
def test_installed_pi_inherited_system_sentinel_refused_before_start_and_zero_provider_posts(tmp_path, monkeypatch, record_property, scope, name, selected):
    installed, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    wrapper = tmp_path / 'sentinel-pi'
    wrapper.write_text(INSTALLED_SENTINEL.replace('__PI__', repr(installed))); wrapper.chmod(0o700)
    value = options(tmp_path, launcher=[str(wrapper)])
    if selected:
        source = selected_library(tmp_path)
        monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
        value.update(skills=dict(sets=dict(chosen=dict(items=['alpha']))),
            role_profiles={'H': dict(skill_set='chosen')})
    value['public_root'].mkdir(); value['objective'].write_text('No hidden native prompt or provider submission permitted')
    hidden = b'PRIVATE_INSTALLED_SYSTEM_SENTINEL_NEVER_IN_SCIENTIFIC_EVIDENCE'
    with loopback(FINAL) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        research_config.prepare(**value)
        root = value['root']; c.build_job(root, 1, 'H', []); work = c.workspace(root, 1)
        directory = agent if scope == 'global' else work / '.pi'
        directory.mkdir(exist_ok=True)
        path = directory / name; path.write_bytes(hidden)
        originals = {p: p.read_bytes() for p in (path, agent / 'settings.json', agent / 'models.json',
            root / 'settings.json', root / 'jobs/001.json', work / 'science-task.json')}
        c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'system-prompt-refusal')
        result = collect_separately(root)
        assert result.returncode != 0 and requests == [] and errors == []
    record = execution.origin(root, 1); outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is False and outcome['returncode'] is None
    assert not (agent / 'forbidden-native-start').exists()
    assert not (record / 'native-process.json').exists()
    assert not (root / 'native-homes/001/sessions/pi-session.jsonl').exists()
    for name in ('science-output.json', 'result.pending.json', 'result-identity.json', 'output-repair.json'):
        assert not (record / name).exists()
    for name in ('native.jsonl', 'native-public.jsonl', 'native.stderr', 'native-outcome.json'):
        assert hidden not in (record / name).read_bytes()
    assert hidden not in result.stdout + result.stderr
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert not (agent / 'npm').exists() and not (agent / 'git').exists()
    assert not (record / 'continuations/001/native-intent.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['missing-description', 'native-name-collision'])
def test_installed_pi_native_yaml_missing_or_colliding_commands_refuse_before_provider_prompt(tmp_path, monkeypatch, record_property, damage):
    installed, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    source = selected_library(tmp_path)
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
    selected = ['alpha', 'beta'] if damage == 'native-name-collision' else ['alpha']
    value = options(tmp_path, launcher=[installed], skills=dict(sets=dict(chosen=dict(items=selected))),
        role_profiles={'H': dict(skill_set='chosen')})
    value['public_root'].mkdir(); value['objective'].write_text('Native resource admission negative; no provider submission')
    with loopback(FINAL) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        research_config.prepare(**value)
        root = value['root']; c.build_job(root, 1, 'H', [])
        # Selection remains frozen while the installed SDK parses live metadata.
        if damage == 'missing-description':
            (source / 'alpha/SKILL.md').write_text('---\nname: live-name\ndescription: [not, a, string]\n---\n')
        else:
            for item in selected:
                (source / item / 'SKILL.md').write_text('---\nname: live-collision\ndescription: Native live description\n---\n')
        originals = {source / item / 'SKILL.md': (source / item / 'SKILL.md').read_bytes() for item in selected}
        c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'native-yaml-refusal')
        result = collect_separately(root)
        assert result.returncode != 0 and requests == [] and errors == []
    record = execution.origin(root, 1); outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is True and outcome['cleanup'] == 'observed_group_cleaned'
    stream = [json.loads(row) for row in (record / 'native.jsonl').read_bytes().splitlines()]
    assert any(row['type'] == 'pi_transport_fault' for row in stream)
    assert not (record / 'science-output.json').exists() and not (record / 'result-identity.json').exists()
    assert not (record / 'continuations/001/native-intent.json').exists()
    assert all(path.read_bytes() == original for path, original in originals.items())
