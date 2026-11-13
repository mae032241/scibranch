"""Declared experiment dependencies use the selected Python, before mutation."""
import copy
from pathlib import Path
import sys
import time

import pytest
from packaging.markers import default_environment

from scidiscovery.plugin_configuration import PluginConfigurationError
from scidiscovery.research_loop import controller, experiments as e, python_environment as p
from scidiscovery.research_loop.local_experiments import REGISTRATION, configure


def configuration(requirement='packaging>=0', module='json', argv=None):
    return dict(run_argv=argv or [sys.executable], python_requirements=[
        dict(requirement=requirement, import_name=module)])


def test_undeclared_and_empty_requirements_preserve_legacy_shape(monkeypatch):
    monkeypatch.setattr(p, '_probe', lambda *a, **k: pytest.fail('No declared checks'))
    for config in ({}, {'run_argv':['arbitrary-command', '--flag']}, {'python_requirements':[]}):
        before = copy.deepcopy(config)
        assert configure(config, None, binding='command') == before


def test_target_markers_and_distribution_import_mapping(monkeypatch):
    target = dict(default_environment(), python_version='2.7', python_full_version='2.7.18', sys_platform='target-platform')
    calls = []
    def probe(prefix, request, **kwargs):
        calls.append((prefix, request, kwargs['deadline']))
        if request['phase'] == 'environment':
            return target
        assert request['requirements'] == [dict(distribution='scikit-learn', import_name='sklearn')]
        return [dict(status='ok', version='1.6.1')]
    monkeypatch.setattr(p, '_probe', probe)
    config = dict(run_argv=['/declared/venv/bin/python', '-S'], python_requirements=[
        dict(requirement='scikit-learn>=1.4,<2; python_version < "3" and sys_platform == "target-platform"', import_name='sklearn'),
        dict(requirement='missing-package; python_version >= "3"', import_name='missing_module'),
        dict(requirement='missing-platform-package; sys_platform != "target-platform"', import_name='missing_module')])
    before = copy.deepcopy(config)
    assert configure(config, None, binding='command') == before
    assert calls[0][0] == ['/declared/venv/bin/python', '-S', '-I', '-B']
    assert calls[0][2] == calls[1][2]


@pytest.mark.parametrize('row', [None, {}, {'requirement':'pkg>=1'},
    {'requirement':'pkg>=1', 'import_name':'invalid-name'},
    {'requirement':'bad ??', 'import_name':'pkg'},
    {'requirement':'pkg[extra]>=1', 'import_name':'pkg'},
    {'requirement':'pkg @ https://example.invalid/pkg.whl', 'import_name':'pkg'},
    {'requirement':'pkg; extra == "a"', 'import_name':'pkg'}])
def test_invalid_or_unsupported_declarations_fail_without_probe(monkeypatch, row):
    monkeypatch.setattr(p, '_probe', lambda *a, **k: pytest.fail('Invalid declaration reached probe'))
    with pytest.raises(PluginConfigurationError) as caught:
        configure(dict(run_argv=[sys.executable], python_requirements=[row]), None, binding='command')
    assert caught.value.field_path.startswith('executor.config.configuration.python_requirements.0')


@pytest.mark.parametrize('argv', [['python'], ['/usr/bin/env', 'python'], ['/bin/sh'],
    ['/absolute/python', '-m', 'module'], ['/absolute/python', '-c', 'pass'],
    ['/absolute/python', '/wrapper.py'], ['/absolute/python', '-X', 'importtime']])
def test_local_rejects_ambiguous_invocation_before_probe(monkeypatch, argv):
    monkeypatch.setattr(p, '_probe', lambda *a, **k: pytest.fail('Unsupported invocation reached probe'))
    with pytest.raises(PluginConfigurationError) as caught:
        configure(configuration(argv=argv), None, binding='command')
    assert caught.value.field_path.endswith('.run_argv')


@pytest.mark.parametrize('result,code,field', [
    ({'status':'missing'}, 'python_dependency_missing', 'requirement'),
    ({'status':'ok','version':'0.1'}, 'python_dependency_version_mismatch', 'requirement'),
    ({'status':'ok','version':'invalid version'}, 'python_dependency_version_invalid', 'requirement'),
    ({'status':'import_failed','version':'1.6.1'}, 'python_dependency_import_failed', 'import_name'),
])
def test_dependency_failures_are_field_located_and_safe(monkeypatch, result, code, field):
    monkeypatch.setattr(p, '_probe', lambda prefix, request, **kwargs:
        default_environment() if request['phase'] == 'environment' else [result])
    config = configuration('private-distribution>=1.4', 'private_module', ['/private/interpreter/python'])
    with pytest.raises(PluginConfigurationError) as caught:
        configure(config, None, binding='command')
    assert caught.value.code == code
    assert caught.value.field_path.endswith('python_requirements.0.' + field)
    public = str(caught.value) + caught.value.hint
    assert '/private/' not in public and 'private-distribution' not in public and 'private_module' not in public


def test_prepare_checks_before_mutation_and_frozen_validation_never_reprobes(tmp_path, monkeypatch):
    monkeypatch.setattr(e, 'registration', lambda executor:(REGISTRATION, 'command'))
    calls = []
    def probe(prefix, request, **kwargs):
        calls.append(request)
        return default_environment() if request['phase'] == 'environment' else [dict(status='missing')]
    monkeypatch.setattr(p, '_probe', probe)
    public = tmp_path/'public'; public.mkdir()
    goal = public/'goal.md'; goal.write_text('Dependency gate fixture.')
    root = tmp_path/'instance'
    selection = dict(executor='local_execution:command', configuration=configuration())
    with pytest.raises(PluginConfigurationError, match='distribution'):
        controller.prepare(root, goal, public, ['never-launched'], search_policy=None,
            max_tokens=100, max_seconds=10, max_steps=2, model='fixture', reasoning_effort='high',
            execution_config=selection)
    assert not root.exists() and len(calls) == 2
    calls.clear()
    assert e.validate_operator_configuration(selection) == selection
    assert calls == []


def test_cov_callback_preserves_script_prefix_and_reuses_checks(monkeypatch):
    from cov_execution.experiments import REGISTRATION as cov_registration
    calls = []
    monkeypatch.setattr(p, '_probe', lambda prefix, request, **kwargs:
        calls.append(prefix) or (default_environment() if request['phase'] == 'environment'
            else [dict(status='ok', version='1')]))
    config = configuration(argv=['/declared/python', '-B', '-I', '/public/runner.py'])
    assert cov_registration.factory.configure(config, None, binding='experiment') == config
    assert calls[0][0] == '/declared/python' and '/public/runner.py' not in calls[0]


def test_native_runtime_capture_uses_selected_settings_without_reopening_configuration(tmp_path, monkeypatch):
    from scidiscovery.research_loop import configuration_updates, native_execution
    captures = []
    def forbidden(*args, **kwargs):
        pytest.fail('Runtime consumer reopened the selected configuration')
    monkeypatch.setattr(configuration_updates, 'for_job', forbidden)
    monkeypatch.setattr(p, 'runtime_roots', lambda interpreter: captures.append(interpreter) or ['/runtime/root'])
    for python in ('/scientific-one/bin/python', '/scientific-two/bin/python3.12'):
        settings=dict(execution_config=dict(executor='local_execution:command',configuration=dict(run_argv=[python, '-I'])))
        assert native_execution._experiment_python(settings, sys.executable) == dict(
            run_argv=[python, '-I'], runtime_roots=['/runtime/root'])
    assert captures == ['/scientific-one/bin/python', '/scientific-two/bin/python3.12']
    for executor, argv in (('local_execution:command', [sys.executable]),
            ('local_execution:command', ['/bin/sh']), ('cov_execution:experiment', ['/scientific/bin/python'])):
        settings=dict(execution_config=dict(executor=executor, configuration=dict(run_argv=argv)))
        assert native_execution._experiment_python(settings, sys.executable) is None
    assert len(captures) == 2


def test_native_prepare_freezes_runtime_roots_without_launch_or_dependency_probe(tmp_path, monkeypatch):
    from scidiscovery.research_loop import contracts as c, execution, native_execution
    public=tmp_path/'public'; public.mkdir()
    goal=public/'goal.md'; goal.write_text('Private native runtime capture fixture.')
    root=tmp_path/'instance'
    python=tmp_path/'scientific-env/bin/python'
    controller.prepare(root,goal,public,['codex'],max_tokens=100,max_seconds=20,max_steps=2,
        model='fixture',reasoning_effort='high',search_policy=None,
        execution_config=dict(executor='local_execution:command',configuration=dict(run_argv=[str(python)])))
    c.build_job(root,1,'W',[])
    work=c.workspace(root,1); record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    home=tmp_path/'native-home'; home.mkdir()
    policy=dict(writable_roots=[],network_access=False,exclude_tmpdir_env_var=False,exclude_slash_tmp=False)
    monkeypatch.setattr(native_execution,'_permissions',lambda *a:(policy,[]))
    monkeypatch.setattr(native_execution,'_native_program',lambda *a:Path('/usr/bin/true'))
    monkeypatch.setattr(native_execution.shutil,'which',lambda *a:'/usr/bin/bwrap')
    captures=[]
    monkeypatch.setattr(p,'runtime_roots',lambda interpreter:captures.append(interpreter) or [str(python.parent.parent)])
    monkeypatch.setattr(p,'check_python_requirements',lambda *a,**k:pytest.fail('Native runtime capture is not dependency preflight'))
    arguments=['codex','exec','--sandbox','workspace-write','-C',str(work),'-']
    _,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=True)
    snapshot=c.read(record/'native-execution-permissions.json')
    assert 'unsupported' not in snapshot
    assert snapshot['experiment_python']==dict(run_argv=[str(python)],runtime_roots=[str(python.parent.parent)])
    assert captures==[str(python)] and callable(launch)


@pytest.mark.parametrize('changed',[None,'configuration','command','private-root'])
def test_native_alternate_python_roots_are_readonly_and_match_frozen_command(tmp_path,monkeypatch,changed):
    from scidiscovery.research_loop import code_versions, native_execution, shared_input_views
    from test_default_native_local_execution import _fixed_local_code, _local_code_launch_inputs
    fixed=_fixed_local_code(tmp_path,monkeypatch)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    environment=tmp_path/'scientific-env'; environment.mkdir()
    argv=[str(environment/'bin/python'),'-I']
    config['run_argv']=list(argv)
    accepted['command']=[*argv,*accepted['command'][1:]]
    accepted['script_index']=len(argv)
    value['experiment_python']=dict(run_argv=list(argv),runtime_roots=[str(environment)])
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **k:'/argv-only/bwrap')
    monkeypatch.setattr(code_versions,'materialize',lambda *a,**k:export)
    monkeypatch.setattr(p,'runtime_roots',lambda *a:pytest.fail('Launch must use the private frozen runtime capture'))
    if changed=='configuration':config['run_argv']=[str(environment/'bin/python3')]
    elif changed=='command':accepted['command'][0]=str(environment/'bin/python3')
    elif changed=='private-root':value['experiment_python']['runtime_roots']=[str(root)]
    if changed is not None:
        with pytest.raises(e.UnsupportedExecution if changed=='private-root' else e.IntegrityError):
            native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    else:
        command,_,_=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
        bindings=[command[index:index+3] for index,argument in enumerate(command) if argument in ('--bind','--ro-bind')]
        assert ['--ro-bind',str(environment),str(environment)] in bindings
        assert not any(row[0]=='--bind' and row[1]==str(environment) for row in bindings)


@pytest.mark.process_e2e
@pytest.mark.parametrize('requirement,module,code', [
    ('packaging>=0','json',None),
    ('scidiscovery-absent-dependency-fixture>=1','json','python_dependency_missing'),
    ('packaging<0','json','python_dependency_version_mismatch'),
    ('packaging>=0','scidiscovery_absent_module_fixture','python_dependency_import_failed'),
    ('scidiscovery-absent-dependency-fixture; python_version < "0"','absent_module',None),
])
def test_real_selected_interpreter_dependency_checks(requirement, module, code):
    config = configuration(requirement, module)
    if code is None:
        assert configure(config, None, binding='command') == config
    else:
        with pytest.raises(PluginConfigurationError) as caught:
            configure(config, None, binding='command')
        assert caught.value.code == code


@pytest.mark.process_e2e
def test_real_probe_keeps_site_disabling_flag():
    with pytest.raises(PluginConfigurationError) as caught:
        configure(configuration(argv=[sys.executable, '-S']), None, binding='command')
    assert caught.value.code == 'python_dependency_missing'


@pytest.mark.process_e2e
@pytest.mark.parametrize('script,code', [
    ('import time; time.sleep(10)', 'python_probe_timeout'),
    ('import sys; sys.stdout.write("x"*40000)', 'python_probe_output_limit'),
    ('import sys; sys.stderr.write("private-secret"*4000)', 'python_probe_output_limit'),
    ('raise RuntimeError("private-secret")', 'python_probe_failed'),
])
def test_real_probe_bounds_diagnostics_and_lifetime(monkeypatch, script, code):
    monkeypatch.setattr(p, '_SCRIPT', script)
    with pytest.raises(PluginConfigurationError) as caught:
        p._probe([sys.executable, '-I', '-B'], {'phase':'environment'},
            deadline=time.monotonic()+(.3 if code == 'python_probe_timeout' else 3), plugin_id='local_execution')
    assert caught.value.code == code
    assert 'private-secret' not in str(caught.value) + caught.value.hint


@pytest.mark.process_e2e
def test_real_probe_omits_inherited_credentials_and_pythonpath(monkeypatch):
    monkeypatch.setenv('SCID_PRIVATE_TOKEN', 'private-secret')
    monkeypatch.setenv('PYTHONPATH', '/private/site')
    monkeypatch.setattr(p, '_SCRIPT', 'import json,os; print(json.dumps(sorted(os.environ)))')
    result = p._probe([sys.executable, '-I', '-B'], {'phase':'environment'},
        deadline=time.monotonic()+2, plugin_id='local_execution')
    assert 'SCID_PRIVATE_TOKEN' not in result and 'HOME' not in result and 'PYTHONPATH' not in result
