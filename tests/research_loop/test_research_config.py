"""Unified configuration uses real preparation validation without native calls."""
import json
import os
import subprocess
import sys

import pytest

from scidiscovery.research_loop import cli, contracts as c, research_config


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
        native_launcher_argv=['never-launched'], model='model', project=frozen)
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
                    native_launcher_argv=['never-launched'], model='model')
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
    monkeypatch.setattr(research_config.shutil, 'which', lambda executable: str(binary))
    monkeypatch.setenv('CODEX_HOME', str(home))
    options = normalization_options(tmp_path, native_options=dict(network=False, web='cached'))
    prepared = research_config.normalize(options)
    policy = prepared['resume_policy']
    assert policy['output_only'] and policy['output_repair']
    assert policy['automatic_recovery'] == dict(enabled=True, max_continues=2, max_retries=3,
        initial_backoff_seconds=30, max_backoff_seconds=300, jitter_fraction=.1, max_wait_seconds=1800)
    assert policy['native_home'] == str(home)
    assert prepared['native_options'] == options['native_options']
    assert 'resume_policy' not in options and not home.exists()
    policy['automatic_recovery']['max_retries'] = 0
    assert research_config.normalize(options)['resume_policy']['automatic_recovery']['max_retries'] == 3


@pytest.mark.parametrize('mode', ['legacy', 'disabled', 'custom', 'protocol', 'fragment'])
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
    else:
        options['worker_fragment_policy'] = dict(context='same_session')
    monkeypatch.setattr(research_config.shutil, 'which', lambda executable: pytest.fail('must retain explicit launcher'))
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
subagents = true
[ui]
port = 7000
approval_secret_file = "private/operator.key"
''')
    # Credential references are not files to import, copy, or disclose as science.
    monkeypatch.chdir(tmp_path.parent)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob('*'))
    assert cli.main(['config-check', '--config', str(config)]) == 0
    checked = output(capsys)
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
def test_cli_rejects_oversized_initial_editable_seed_before_instance_creation(tmp_path, capsys, command):
    from scidiscovery.research_loop import code_versions
    base = tmp_path / 'base'
    (base / 'src').mkdir(parents=True)
    with (base / 'src/main.py').open('wb') as stream:
        stream.truncate(code_versions.DEFAULT_LIMITS['max_file_bytes'] + 1)
    environment = dict(PATH=os.defpath, HOME=os.devnull, GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid', LC_ALL='C')
    for arguments in [('init', '--quiet', '--template='), ('add', '--', 'src/main.py'),
                      ('commit', '--quiet', '-m', 'oversized editable seed')]:
        subprocess.run(['git', '-C', str(base), *arguments], env=environment,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    config = declaration(tmp_path, '\n[project]\nbase_root = ' + json.dumps(str(base)) + '\neditable = ["src"]\n')
    original = config.read_bytes()
    assert cli.main([command, '--config', str(config)]) == 1
    assert 'max_file_bytes' in output(capsys)['error']
    assert not (tmp_path / 'instance').exists()
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
