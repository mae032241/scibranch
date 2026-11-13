"""Unified configuration uses real preparation validation without native calls."""
import json
import os
import subprocess
import sys

import pytest

from scidiscovery.research_loop import backends, cli, contracts as c, protocol, research_config


def declaration(tmp_path, extra='', *, root='instance'):
    public = tmp_path / 'public'
    public.mkdir(exist_ok=True)
    (public / 'objective.md').write_text('Compare alternatives and retain limitations.')
    path = tmp_path / 'research.toml'
    path.write_text(f'''version = 1
[research]
root = {json.dumps(root)}
objective = "public/objective.md"
public_root = "public"
[agent]
launcher = ["never-launched"]
[budget]
max_tokens = 1000
max_seconds = 120
max_steps = 8
''' + extra)
    return path


def test_project_declaration_roundtrip_preserves_frozen_identity(tmp_path):
    project = dict(base_root='/exact/base', editable=['src', 'tools'], scratch=['build'], outputs=['out'])
    path = declaration(tmp_path, '\n[project]\nbase_root = "/exact/base"\n'
        'editable = ["src", "tools"]\nscratch = ["build"]\noutputs = ["out"]\n')
    parsed = research_config.from_document(research_config.read_declaration(path), base=tmp_path)
    assert parsed['project'] == project
    frozen = {**project, 'base_identity': dict(object_format='sha256', commit='a' * 64)}
    settings = dict(objective='Original task', public_root=str(tmp_path / 'public'),
        native_launcher_argv=['never-launched'], model='model', project=frozen,
        handoff_protocol=protocol.CURRENT, research_strategy_version=3, advisory_scope_version=1,
        node_budget_mode='soft', final_a_completion_policy=dict(version=1),
        max_tokens=1000, max_seconds=120, max_steps=8)
    document = research_config.to_document(tmp_path / 'instance', settings)
    assert document['project'] == project
    assert 'base_identity' not in document['project']
    assert settings['project'] == frozen


@pytest.mark.parametrize('change', ['replace', 'remove', 'add'])
def test_project_is_immutable_before_operational_revalidation(tmp_path, monkeypatch, change):
    from scidiscovery.research_loop import configuration_updates
    frozen = dict(base_root='/exact/base', editable=['src'], scratch=[], outputs=[],
        base_identity=dict(object_format='sha1', commit='a' * 40))
    settings = dict(objective='Original task', public_root=str(tmp_path / 'public'),
        native_launcher_argv=['never-launched'], model='model',
        handoff_protocol=protocol.CURRENT, research_strategy_version=3, advisory_scope_version=1,
        node_budget_mode='soft', final_a_completion_policy=dict(version=1),
        max_tokens=1000, max_seconds=120, max_steps=8)
    if change != 'add':
        settings['project'] = frozen
    monkeypatch.setattr(c, 'settings', lambda root: settings)
    monkeypatch.setattr(configuration_updates, 'editable', lambda root: settings)
    document = research_config.to_document(tmp_path / 'instance', settings)
    if change == 'remove':
        # Missing means retain unchanged in an operational declaration; an
        # explicit null is an attempted removal of the frozen project.
        document['project'] = None
    else:
        document['project'] = dict(base_root='/another/base', editable=['src'])
    with pytest.raises(ValueError, match='project is immutable'):
        research_config.validate_update(tmp_path / 'instance', document)


def output(capsys):
    return json.loads(capsys.readouterr().out)


def test_legacy_output_byte_fields_roundtrip_without_capacity_relationship(tmp_path):
    path=declaration(tmp_path,'max_output_item_bytes = 8\nmax_output_total_bytes = 1\n')
    settings=research_config.prepare(path,validate_only=True)
    assert settings['max_output_item_bytes']==8 and settings['max_output_total_bytes']==1
    document=research_config.to_document(tmp_path/'instance',settings)
    parsed=research_config.from_document(document,base=tmp_path)
    assert parsed['max_output_item_bytes']==8 and parsed['max_output_total_bytes']==1
    assert settings['max_output_item_bytes']==8 and settings['max_output_total_bytes']==1


@pytest.mark.parametrize('value',[0,1,'true',None])
def test_execution_wake_permission_requires_boolean(value):
    from scidiscovery.research_loop import continuation
    with pytest.raises(ValueError,match='explicit finite attempts'):
        continuation.policy(dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
            execution_wake=value),[sys.executable])


def test_narrow_wake_permission_preserves_legacy_full_resume():
    from scidiscovery.research_loop import continuation
    full=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn')
    assert continuation.wake_allowed(full)
    assert not continuation.wake_allowed(None)
    output=dict(full,output_only=True,output_repair=True)
    assert not continuation.wake_allowed(output)
    assert not continuation.wake_allowed(dict(output,execution_wake=False))
    assert continuation.wake_allowed(dict(output,execution_wake=True))


def normalization_options(tmp_path, **options):
    return dict(root=tmp_path / 'instance', objective=tmp_path / 'objective.md',
        public_root=tmp_path / 'public', launcher=['codex'],
        max_tokens=1000, max_seconds=120, max_steps=8, **options)


def test_standard_defaults_separate_automatic_recovery_from_output_only(tmp_path, monkeypatch):
    binary = tmp_path / 'codex'
    binary.write_text('not invoked')
    home = tmp_path / 'inherited-home'
    monkeypatch.setattr(backends.shutil, 'which', lambda executable: str(binary))
    monkeypatch.setenv('CODEX_HOME', str(home))
    options = normalization_options(tmp_path, native_options=dict(network=False, web='cached'))
    prepared = research_config.normalize(options)
    policy = prepared['resume_policy']
    assert policy['output_only'] and policy['output_repair']
    assert policy['automatic_recovery'] == dict(enabled=True, max_attempts=8, max_continues=8,
        max_retries=8, initial_backoff_seconds=5, max_backoff_seconds=300, jitter_fraction=0, max_wait_seconds=1800)
    assert policy['native_home'] == str(home)
    assert prepared['native_options'] == options['native_options']
    assert 'resume_policy' not in options and not home.exists()
    policy['automatic_recovery']['max_retries'] = 0
    assert research_config.normalize(options)['resume_policy']['automatic_recovery']['max_retries'] == 8


def test_new_standard_worker_context_windows_default_and_explicit_roundtrip(tmp_path, monkeypatch):
    from scidiscovery.research_loop import checkpoint
    monkeypatch.setattr(backends.shutil, 'which', lambda executable: str(tmp_path / 'codex'))
    prepared = research_config.normalize(normalization_options(tmp_path))
    assert prepared['worker_fragment_policy'] == dict(context='same_session', max_context_windows=1,
        context_handoff_version=2)
    path = declaration(tmp_path, '\n[policies]\nworker_context="new_context"\nworker_context_windows=1\n')
    parsed = research_config.load(path)
    assert parsed['worker_fragment_policy'] == dict(context='new_context', max_context_windows=1)
    assert checkpoint.policy(parsed['worker_fragment_policy']) == parsed['worker_fragment_policy']
    explicit=research_config.normalize(normalization_options(tmp_path,
        worker_fragment_policy=dict(context='new_context',max_context_windows=2)))
    assert explicit['worker_fragment_policy']==dict(context='new_context',max_context_windows=2,context_handoff_version=2)
    for invalid in (0, 3, True, '2', None):
        with pytest.raises(ValueError, match='context windows'):
            checkpoint.policy(dict(context='same_session', max_context_windows=invalid))
    assert checkpoint.policy(dict(context='same_session')) == dict(context='same_session')


@pytest.mark.parametrize('maximum', [1, 2])
def test_worker_context_projection_roundtrips_window_limit_and_handoff_version(tmp_path, maximum):
    settings = dict(objective='Original task', public_root=str(tmp_path / 'public'),
        native_launcher_argv=['codex'], model='model', handoff_protocol=protocol.CURRENT,
        research_strategy_version=3, advisory_scope_version=1, node_budget_mode='soft', final_a_completion_policy=dict(version=1),
        max_tokens=1000, max_seconds=120, max_steps=8,
        worker_fragment_policy=dict(context='same_session', context_handoff_version=2))
    settings['worker_fragment_policy']['max_context_windows'] = maximum
    document = research_config.to_document(tmp_path / 'instance', settings)
    assert document['policies']['worker_context_windows'] == maximum
    assert research_config.from_document(document, base=tmp_path)['worker_fragment_policy'] == settings['worker_fragment_policy']


@pytest.mark.parametrize('mode', ['legacy', 'disabled', 'custom', 'protocol'])
def test_automatic_recovery_defaults_do_not_expand_explicit_or_custom_launchers(tmp_path, monkeypatch, mode):
    options = normalization_options(tmp_path)
    legacy = dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn',
        output_repair=True, output_only=True)
    if mode in {'legacy', 'disabled'}:
        options['resume_policy'] = dict(legacy)
        if mode == 'disabled':
            options['resume_policy']['automatic_recovery'] = dict(enabled=False)
    elif mode == 'custom':
        options['launcher'] = ['codex', '--declared-argument']
    elif mode == 'protocol':
        options.update(native_backend='protocol', model='declared-model')
    monkeypatch.setattr(backends.shutil, 'which', lambda executable: pytest.fail('must retain explicit launcher'))
    prepared = research_config.normalize(options)
    assert prepared.get('resume_policy') == options.get('resume_policy')
    assert prepared['launcher'] == options['launcher']


def test_toml_automatic_recovery_roundtrips_independent_policy(tmp_path):
    path = declaration(tmp_path, '''
[policies.resume]
max_attempts = 2
protocol = "codex_exec_resume"
usage_scope = "turn"
output_only = true
output_repair = true
[policies.resume.automatic_recovery]
enabled = false
max_continues = 1
max_retries = 4
initial_backoff_seconds = 0.5
max_backoff_seconds = 2
jitter_fraction = 0
max_wait_seconds = 5
''')
    options = research_config.load(path)
    policy = options['resume_policy']
    assert policy['output_only'] is True
    assert policy['automatic_recovery'] == dict(enabled=False, max_continues=1, max_retries=4,
        initial_backoff_seconds=.5, max_backoff_seconds=2, jitter_fraction=0, max_wait_seconds=5)
    settings = research_config.prepare(validate_only=True,
        **{key: value for key, value in options.items() if key != 'resume_policy'})
    settings['resume_policy'] = policy
    document = research_config.to_document(tmp_path / 'instance', settings)
    assert document['policies']['resume'] == policy
    assert research_config.from_document(document, base=tmp_path)['resume_policy'] == policy


@pytest.mark.parametrize('state,bound,query,wake,verified,expected',[
    ('running',True,True,True,False,'return_control'),
    ('running',True,True,False,False,'poll_original'),
    ('accepted',True,True,False,False,'poll_original'),
    ('submitted',True,True,False,False,'poll_original'),
    ('queued',True,True,False,False,'poll_original'),
    ('cancelling',True,True,False,False,'poll_original'),
    ('running',False,True,True,False,'reconcile_or_stop'),
    ('running',True,False,True,False,'reconcile_or_stop'),
    ('running',True,False,False,False,'reconcile_or_stop'),
    ('unknown',True,True,False,False,'reconcile_or_stop'),
    ('unknown',True,True,True,False,'reconcile_or_stop'),
    ('unknown',True,True,True,True,'reconcile_or_stop'),
    ('known_success',True,True,True,False,'reconcile_or_stop'),
    ('known_failed',True,True,True,False,'reconcile_or_stop'),
    ('known_success',True,True,True,True,'consume_terminal'),
    ('succeeded',True,True,True,False,'reconcile_or_stop'),
    ('succeeded',True,True,True,True,'consume_terminal'),
    ('known_failed',True,True,True,True,'consume_terminal'),
    ('failed',True,True,True,True,'consume_terminal'),
    ('cancelled',True,True,True,True,'consume_terminal'),
    ('cancelled',True,True,False,False,'reconcile_or_stop'),
    ('cancelled',True,True,False,True,'consume_terminal'),
])
def test_executor_and_host_share_handoff_rules(state,bound,query,wake,verified,expected):
    from scidiscovery.research_loop import managed_wait
    assert managed_wait.handoff_action(state,original_bound=bound,query=query,
        wake_authorized=wake,terminal_verified=verified)==expected


def test_research_plugin_file_is_relative_to_declaration_and_freezes_once(tmp_path, monkeypatch):
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
    from scidiscovery.research_loop import experiments
    calls = []
    def configure(config, source, *, binding):
        assert binding == 'binding'
        calls.append(source)
        return config
    def build(_):
        raise AssertionError('configuration must not build a runtime')
    declared = ExecutionInterfaceRegistration('third_party', '1', RuntimePluginFactory(
        build, configure=configure, requires_config_file=True))
    monkeypatch.setattr(experiments, 'registration', lambda executor: (declared, 'binding'))
    plugin = tmp_path / 'plugin.toml'; plugin.write_text('setting = 9\n')
    config = declaration(tmp_path, '\n[executor.config]\nexecutor = "third_party:binding"\nconfig_file = "plugin.toml"\n')
    monkeypatch.chdir(tmp_path.parent)
    settings = research_config.prepare(config)
    assert settings['execution_config'] == dict(executor='third_party:binding', configuration={'setting': 9})
    plugin.write_text('invalid TOML = [')
    assert research_config.inspect(root=tmp_path/'instance')['settings']['execution_config'] == settings['execution_config']
    assert calls == [plugin]


def test_real_tcad_registration_rejects_invalid_file_before_instance_creation(tmp_path, monkeypatch):
    import importlib.metadata
    from scidiscovery.plugin_configuration import PluginConfigurationError
    from scidiscovery.research_loop import experiments
    from tcad_artifact import managed_execution as managed
    entry = importlib.metadata.EntryPoint(name='tcad_execution',
        value='tcad_artifact.managed_execution:REGISTRATION', group=experiments.GROUP)
    original = importlib.metadata.entry_points
    class Installed:
        def select(self, **kwargs):
            if kwargs == dict(group=experiments.GROUP, name='tcad_execution'):
                return [entry]
            return original().select(**kwargs)
    monkeypatch.setattr(importlib.metadata, 'entry_points', lambda: Installed())
    monkeypatch.setattr(managed, 'build_adapter', lambda *a, **k: pytest.fail('configuration built a TCAD adapter'))
    plugin = tmp_path / 'tcad.toml'; plugin.write_text('transport = "invalid"\n')
    config = declaration(tmp_path, '\n[executor.config]\nexecutor = "tcad_execution:tcad"\nconfig_file = "tcad.toml"\n')
    assert experiments.registration('tcad_execution:tcad') == (managed.REGISTRATION, 'tcad')
    before = sorted(tmp_path.rglob('*'))
    with pytest.raises(PluginConfigurationError) as caught:
        research_config.prepare(config)
    assert caught.value.code == 'tcad_configuration_invalid'
    assert caught.value.plugin_id == 'tcad_execution'
    assert not (tmp_path / 'instance').exists()
    assert sorted(tmp_path.rglob('*')) == before


def test_basic_token_budget_edit_retains_all_other_declaration_semantics(tmp_path):
    path = declaration(tmp_path, '\n[permissions]\nweb = "cached"\nnetwork = false\n')
    for original in (path.read_text(), path.read_text().replace('max_tokens = 1000\n', '')):
        edited = research_config.with_token_budget(original, 1234)
        expected = research_config.tomllib.loads(original)
        expected['budget']['max_tokens'] = 1234
        assert research_config.tomllib.loads(edited) == expected
        assert '[permissions]\nweb = "cached"\nnetwork = false' in edited
    assert research_config.read_declaration(path)['budget']['max_tokens'] == 1000
    for invalid in (None, 0, -1, True, '1000'):
        with pytest.raises(ValueError, match='positive integer'):
            research_config.with_token_budget(path.read_text(), invalid)


def test_basic_budget_editor_fails_closed_for_unsupported_layout():
    original = 'budget = {max_tokens = 1, max_steps = 2, max_seconds = 3}\n'
    with pytest.raises(ValueError, match='advanced editor'):
        research_config.with_token_budget(original, 1000)


def test_real_cli_config_check_is_read_only_and_prepare_freezes_effective_fields(tmp_path, capsys, monkeypatch):
    config = declaration(tmp_path, '''
[agent.roles.W]
model = "worker-model"
[permissions]
workspace = "danger-full-access"
network = true
web = "cached"
[ui]
port = 7000
approval_secret_file = "private/operator.key"
''')
    # Credential references are not files to import, copy, or disclose as science.
    monkeypatch.chdir(tmp_path.parent)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob('*'))
    code = cli.main(['config-check', '--config', str(config)])
    checked = output(capsys)
    assert code == 0, checked
    assert checked['source'] == 'research_config'
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob('*')) == before
    assert not (tmp_path / 'instance').exists()
    assert cli.main(['prepare', '--config', str(config)]) == 0
    assert output(capsys)['status'] == 'prepared'
    root = tmp_path / 'instance'
    original = (root / 'settings.json').read_bytes()
    settings = c.settings(root)
    assert settings['native_options']['network'] is True
    assert settings['native_options']['workspace'] == 'danger-full-access'
    assert settings['role_profiles']['W']['model'] == 'worker-model'
    assert settings['role_profiles']['H']['model'] == research_config.DEFAULT_MODEL
    assert settings['ui']['approval_secret_file'] == str(tmp_path / 'private/operator.key')
    assert not (tmp_path / 'private').exists()
    config.write_text('invalid TOML = [')
    assert cli.main(['config-inspect', '--root', str(root)]) == 0
    assert output(capsys)['settings'] == settings
    assert (root / 'settings.json').read_bytes() == original
    job = c.build_job(root, 1, 'H', [])
    science = json.dumps(c.read(c.workspace(root, 1) / 'science-task.json'))
    assert 'approval_secret_file' not in science
    assert 'operator.key' not in science
    assert job['native_profile']['model'] == research_config.DEFAULT_MODEL


@pytest.mark.process_e2e
@pytest.mark.parametrize('command', ['config-check', 'prepare'])
def test_cli_accepts_initial_editable_seed_above_legacy_byte_limit(tmp_path, capsys, monkeypatch, command):
    from scidiscovery.research_loop import code_versions
    monkeypatch.setitem(code_versions.DEFAULT_LIMITS,'max_file_bytes',8)
    base = tmp_path / 'base'
    (base / 'src').mkdir(parents=True)
    seed=b'# code seed above the legacy byte quota\n'
    (base/'src/main.py').write_bytes(seed)
    environment = dict(PATH=os.defpath, HOME=os.devnull, GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid', LC_ALL='C')
    for arguments in [('init', '--quiet', '--template='), ('add', '--', 'src/main.py'),
                      ('commit', '--quiet', '-m', 'editable seed above legacy byte quota')]:
        subprocess.run(['git', '-C', str(base), *arguments], env=environment,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    launcher=tmp_path/'codex';launcher.write_text('# synthetic launcher identity; never executed\n');launcher.chmod(0o700)
    monkeypatch.setenv('CODEX_HOME',str(tmp_path/'native-home'))
    config = declaration(tmp_path, '\n[project]\nbase_root = ' + json.dumps(str(base)) + '\neditable = ["src"]\n'
        '\n[permissions]\nworkspace = "workspace-write"\nnetwork = false\nweb = "disabled"\nsubagents = false\n'
        '\n[agent.native.sandbox_workspace_write]\nwritable_roots = []\nnetwork_access = false\n'
        'exclude_tmpdir_env_var = false\nexclude_slash_tmp = false\n')
    config.write_text(config.read_text().replace('launcher = ["never-launched"]',
        'launcher = '+json.dumps([str(launcher)])))
    original = config.read_bytes()
    assert cli.main([command, '--config', str(config)]) == 0
    result=output(capsys)
    if command=='prepare':
        assert result['status']=='prepared' and (tmp_path/'instance').is_dir()
    else:
        assert result['source']=='research_config' and not (tmp_path/'instance').exists()
    assert (base/'src/main.py').read_bytes()==seed
    assert config.read_bytes() == original


def test_toml_and_existing_cli_flags_share_defaults(tmp_path, capsys):
    config = declaration(tmp_path)
    assert cli.main(['prepare', '--config', str(config)]) == 0
    output(capsys)
    assert cli.main(['prepare', '--root', str(tmp_path / 'flags'),
        '--objective', str(tmp_path / 'public/objective.md'), '--public-root', str(tmp_path / 'public'),
        '--native-launcher-json', '["never-launched"]', '--max-tokens', '1000',
        '--max-seconds', '120', '--max-steps', '8']) == 0
    output(capsys)
    a, b = (c.settings(tmp_path / name) for name in ('instance', 'flags'))
    for settings in (a, b):
        for key in ('goal_id', 'source_provenance'):
            settings.pop(key)
    assert a == b


@pytest.mark.parametrize('extra, expected', [
    ('\n[permissions]\nnetwrok = true\n', 'unknown netwrok'),
    ('\n[agent.native]\napi_key = "private-value-must-not-appear"\n', 'unknown api_key'),
    ('\n[ui]\nport = -1\n', 'ui.port'),
    ('\n[budget.correction]\nreview_after = 0\npause_after = 2\n', 'positive integer'),
    ('\n[budget.breadth]\ninitial_calls = 1\n', 'Search policy'),
])
def test_meaningful_typos_and_invalid_policy_reject_before_creation(tmp_path, capsys, extra, expected):
    config = declaration(tmp_path, extra)
    assert cli.main(['prepare', '--config', str(config)]) == 1
    result = output(capsys)
    assert expected in result['error']
    assert 'private-value-must-not-appear' not in result['error']
    assert not (tmp_path / 'instance').exists()


def test_config_cannot_mix_an_override_or_write_existing_instance(tmp_path, capsys):
    config = declaration(tmp_path)
    assert cli.main(['prepare', '--config', str(config), '--model', 'other']) == 1
    assert 'cannot be combined' in output(capsys)['error']
    assert not (tmp_path / 'instance').exists()
    assert cli.main(['prepare', '--config', str(config)]) == 0
    output(capsys)
    original = (tmp_path / 'instance/settings.json').read_bytes()
    assert cli.main(['prepare', '--config', str(config)]) == 1
    assert 'fresh directory' in output(capsys)['error']
    assert (tmp_path / 'instance/settings.json').read_bytes() == original


def test_protocol_configuration_requires_its_own_model(tmp_path):
    path = declaration(tmp_path)
    path.write_text(path.read_text().replace('[agent]', '[agent]\nbackend = "protocol"'))
    with pytest.raises(ValueError, match='explicit model'):
        research_config.prepare(path, validate_only=True)
    assert not (tmp_path / 'instance').exists()


@pytest.mark.parametrize('enabled', [True, False])
def test_policy_preview_and_prepared_instance_agree_without_preview_artifacts(tmp_path, capsys, enabled):
    value = str(enabled).lower()
    config = declaration(tmp_path, f'\n[policies]\nformal_review = {value}\nexecution_authority = {value}\n')
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob('*'))
    assert cli.main(['config-check', '--config', str(config)]) == 0
    preview = output(capsys)['settings']
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob('*')) == before
    assert cli.main(['prepare', '--config', str(config)]) == 0
    output(capsys)
    root = tmp_path / 'instance'
    prepared = c.settings(root)
    for key in ('formal_review_policy', 'execution_authority_policy'):
        assert (key in preview) is enabled
        assert (key in prepared) is enabled
        if enabled:
            assert preview[key] == {'version': 1}
            assert prepared[key]['version'] == preview[key]['version']
            assert (root / prepared[key]['contract']['path']).is_file()
        else:
            assert not (root / 'qualification/review-contract.json').exists()
            assert not (root / 'execution-authority/contract.json').exists()


def test_policy_preview_rejects_authority_without_review_before_creation(tmp_path, capsys):
    config = declaration(tmp_path, '\n[policies]\nformal_review = false\nexecution_authority = true\n')
    assert cli.main(['config-check', '--config', str(config)]) == 1
    assert 'requires the explicit formal review contract' in output(capsys)['error']
    assert not (tmp_path / 'instance').exists()


def test_registered_provider_owns_preparation_contract_without_codex_fallback(tmp_path, monkeypatch):
    from pathlib import Path
    from scidiscovery.research_loop import continuation, configuration_form, public_output, skill_supply
    binary=tmp_path/'codex';binary.write_text('never invoked');binary.chmod(0o700)
    public=tmp_path/'public';public.mkdir()
    source=tmp_path/'library';manual=source/'chosen'/'SKILL.md';manual.parent.mkdir(parents=True)
    manual.write_text('---\nname: chosen\ndescription: Provider selection fixture\n---\nExact fixture.\n')
    calls=[]
    class Independent(backends.Protocol):
        name='independent_fixture'
        supports_resume=True
        @staticmethod
        def default_launcher(): return [str(binary)]
        @staticmethod
        def profile_defaults():
            return dict(model='independent-model',reasoning_effort='independent-effort',service_tier='independent-tier')
        @staticmethod
        def default_home():
            calls.append('home');return tmp_path/'independent-home'
        @staticmethod
        def declaration_options(options,*,base):
            return dict(resource=str((Path(base)/options['resource']).resolve()))
        @staticmethod
        def validate_configuration(options):
            return dict(backends.Protocol.validate_configuration({}),**options)
        def default_resume_policy(self):
            calls.append('resume_default')
            return dict(launcher=[str(binary)],policy=dict(max_attempts=1,
                protocol='independent_resume_v1',usage_scope='turn'))
        def validate_resume_policy(self,value):
            calls.append('resume_validate')
            if value['protocol']!='independent_resume_v1' or value['usage_scope']!='turn':
                raise ValueError('Independent resume protocol required')
        def default_experiment_mode(self): return 'external'
        def public_options(self,options): return dict(network=options['network'])
        @staticmethod
        def configuration_fields(): return (dict(path='agent.native.resource',label='Provider resource'),)
        def skill_loading_contract(self,launcher,*,retained=None):
            calls.append('skills')
            value=dict(kind='independent_fixture_cwd',version=1)
            if retained is not None and retained!=value:raise ValueError('Changed provider Skill contract')
            return value
    monkeypatch.setitem(backends._PROVIDERS,Independent.name,Independent)
    def forbidden(*args,**kwargs):
        raise AssertionError('Provider-neutral preparation invoked Codex')
    for method in ('default_launcher','profile_defaults','declaration_options','default_home',
            'default_resume_policy','validate_resume_policy','default_experiment_mode','skill_loading_contract','public_options','configuration_fields'):
        monkeypatch.setattr(backends.Codex,method,forbidden)
    monkeypatch.setattr(skill_supply,'source_root',lambda:source)
    document=dict(version=1,research=dict(root='instance',public_root='public',objective_text='Keep original scope.'),
        agent=dict(backend=Independent.name,native=dict(resource='provider-data'),roles=dict(H=dict(skill_set='selected'))),
        skills=dict(sets=dict(selected=dict(items=['chosen']))),
        budget=dict(max_tokens=1000,max_seconds=120,max_steps=8,
            node_targets={role:dict(max_observed_tokens=500,max_seconds=60) for role in 'HWAE'}))
    options=research_config.from_document(document,base=tmp_path)
    normalized=research_config.normalize(options)
    assert normalized['launcher']==[str(binary)]
    assert normalized['model']=='independent-model'
    assert normalized['reasoning_effort']=='independent-effort'
    assert normalized['service_tier']=='independent-tier'
    assert 'worker_fragment_policy' not in normalized
    settings=research_config.prepare(validate_only=True,**options)
    assert settings['experiment_mode']=='external'
    assert settings['resume_policy']['protocol']=='independent_resume_v1'
    assert settings['native_options']['resource']==str(tmp_path/'provider-data')
    assert settings['skills']['launcher']==dict(kind='independent_fixture_cwd',version=1)
    exported=research_config.to_document(tmp_path/'instance',settings)
    assert exported['agent']['native']==dict(resource=str(tmp_path/'provider-data'))
    assert public_output.settings_view(settings)['native_options']==dict(network=False)
    clean=public_output.cleaner(tmp_path/'instance',settings)
    assert clean(str(tmp_path/'independent-home'))=='[private]'
    assert clean(str(tmp_path/'provider-data'))=='[private]'
    assert {'home','resume_default','resume_validate','skills'}<=set(calls)
    assert Independent.name in configuration_form.render(configuration_form.imported(document,base=tmp_path))
    # Provider validation cannot waive the control-owned finite recovery budget.
    with pytest.raises(ValueError,match='explicit finite attempts'):
        continuation.policy(dict(max_attempts=3,protocol='independent_resume_v1',usage_scope='turn'),
            [str(binary)],backend=Independent.name)
    assert not (tmp_path/'instance').exists()


def test_cli_accepts_registered_backend_before_preparation(tmp_path,monkeypatch,capsys):
    class Independent(backends.Protocol):
        name='cli_provider_fixture'
    monkeypatch.setitem(backends._PROVIDERS,Independent.name,Independent)
    requests=[]
    def prepare(**options):
        requests.append(options)
        return dict(goal_id='fixture-goal')
    monkeypatch.setattr(research_config,'prepare',prepare)
    assert cli.main(['prepare','--root',str(tmp_path/'instance'),
        '--objective',str(tmp_path/'objective.md'),'--public-root',str(tmp_path/'public'),
        '--native-launcher-json','["never-invoked"]','--native-backend',Independent.name,
        '--max-tokens','1000','--max-seconds','60','--max-steps','3'])==0
    assert requests[0]['native_backend']==Independent.name
    assert json.loads(capsys.readouterr().out)['status']=='prepared'


def test_launcher_declaration_preserves_invoked_symlink_identity(tmp_path):
    target=tmp_path/'native.js';target.write_text('Fixture executable.');target.chmod(0o700)
    invoked=tmp_path/'codex';invoked.symlink_to(target)
    document=dict(version=1,research=dict(root='instance',public_root='public',objective_text='Fixture'),
        agent=dict(launcher=['./codex','literal-argument']),
        budget=dict(max_tokens=1000,max_seconds=60,max_steps=3))
    options=research_config.from_document(document,base=tmp_path)
    assert options['launcher']==[str(invoked),'literal-argument']
    assert invoked.resolve()==target
