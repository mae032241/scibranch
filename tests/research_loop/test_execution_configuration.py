"""Operator selection -> real native host wiring, without models or backends."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, execution, experiments as e, native
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
from scidiscovery.plugin_configuration import PluginConfigurationError, read_execution_selection
from execution_fixtures import execution_registry, remote_registry
from test_composed_execution_results import composed_registry
from test_nonlocal_execution_observation import research, research_path


def inputs(tmp_path):
    public=tmp_path/'public';public.mkdir();goal=public/'goal.md';goal.write_text('Exercise exact registered host wiring.')
    return public,goal


def cli(*args):
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(str(path) for path in sys.path if path))
    reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',*map(str,args)],env=env,capture_output=True,text=True,timeout=30)
    assert reply.stdout,reply.stderr
    return reply,json.loads(reply.stdout)


def forbidden_factory(context):
    raise AssertionError('prepare must not construct an adapter or fabricate host scope')


@pytest.mark.parametrize('extension,body', [('json', '{"setting":7}'), ('toml', 'setting = 7\n')])
def test_generic_plugin_file_loading_and_frozen_validation(tmp_path, monkeypatch, extension, body):
    calls = []
    def configure(config, source, *, binding):
        assert binding == 'binding'
        calls.append((config, source))
        return dict(config, nested_path=str(source.parent / 'nested.json'))
    declared = ExecutionInterfaceRegistration('third_party', '1', RuntimePluginFactory(
        forbidden_factory, configure=configure, requires_config_file=True))
    monkeypatch.setattr(e, 'registration', lambda executor: (declared, 'binding'))
    directory = tmp_path / 'selection'; directory.mkdir()
    plugin = directory / ('plugin.' + extension); plugin.write_text(body)
    wrapper = directory / ('execution.' + extension)
    wrapper.write_text(json.dumps(dict(executor='third_party:binding', config_file=plugin.name))
        if extension == 'json' else f'executor = "third_party:binding"\nconfig_file = "{plugin.name}"\n')
    monkeypatch.chdir(tmp_path.parent)
    frozen = e.operator_configuration(read_execution_selection(wrapper))
    assert frozen == dict(executor='third_party:binding', configuration=dict(
        setting=7, nested_path=str(directory / 'nested.json')))
    assert calls == [({'setting': 7}, plugin)]
    plugin.unlink(); wrapper.unlink()
    assert e.validate_operator_configuration(frozen) == frozen
    assert len(calls) == 1
    with pytest.raises(PluginConfigurationError) as caught:
        e.operator_configuration(dict(executor='third_party:binding', configuration={}))
    assert caught.value.code == 'plugin_config_file_required'


@pytest.mark.parametrize('problem', ['missing', 'invalid', 'directory', 'oversize', 'ambiguous'])
def test_plugin_file_failures_precede_instance_mutation(tmp_path, monkeypatch, problem):
    declared = ExecutionInterfaceRegistration('third_party', '1', RuntimePluginFactory(
        forbidden_factory, configure=lambda *_, **__: pytest.fail('invalid file reached plugin'), requires_config_file=True))
    monkeypatch.setattr(e, 'registration', lambda executor: (declared, 'binding'))
    source = tmp_path / 'private-config.json'
    selection = dict(executor='third_party:binding', config_file=str(source))
    if problem == 'invalid': source.write_text('{"credential":"sensitive-value",')
    elif problem == 'directory': source.mkdir()
    elif problem == 'oversize': source.write_text(' ' * 256001)
    elif problem == 'ambiguous': selection['configuration'] = {}
    public, goal = inputs(tmp_path)
    with pytest.raises(PluginConfigurationError) as caught:
        controller.prepare(tmp_path/'instance', goal, public, ['never-launched'],
            max_tokens=100, max_seconds=10, max_steps=2, model='fixture', reasoning_effort='high', execution_config=selection)
    assert not (tmp_path / 'instance').exists()
    assert caught.value.field_path in {'executor.config.config_file', 'executor.config.executor'}
    assert str(tmp_path) not in str(caught.value) + caught.value.hint
    assert 'sensitive-value' not in str(caught.value) + caught.value.hint


def frozen_fixture(tmp_path,executor,options):
    selected=e.operator_configuration(dict(executor=executor,configuration=options))
    path=tmp_path/'experiment-config.json'
    config=e.freeze(dict(options,
        admission=dict(version=1,mode='hard'),record_dir=str(tmp_path/'records'),
        scope=dict(handoff_protocol=c.CURRENT,step=2,role='W',job_ref=dict(path='/fixture/jobs/002.json'),
            execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'),mechanism_ref=None)),executor,path=path)
    c.write(path,config)
    return path,config


def test_second_registered_implementation_accepts_same_frozen_interface(tmp_path,composed_registry):
    _,config=frozen_fixture(tmp_path,'composed_execution:fixture',dict(synchronous=True,require_contract=True))
    contract,=config['execution_interface']['public_capabilities']
    assert contract['schema_id']=='fixture.composed-input.v1'
    source=tmp_path/'payload.json';c.write(source,dict(observation='composed-original'))
    result=e.dispatch(config,source)
    assert result['status']=='known_success' and len(e.Records(config).calls())==1
    _,adapter=e.original_adapter(config,result['call_id'],'query')
    assert not hasattr(adapter,'code_assets')
    assert adapter.status_details(result['call_id'])['state']=='succeeded'


@pytest.mark.stress
def test_describe_retains_complete_large_public_contract(tmp_path,remote_registry,capsys):
    path,config=frozen_fixture(tmp_path,'remote_execution:fixture',dict(contract_padding=256001))
    contract,=config['execution_interface']['public_capabilities']
    output=tmp_path/'public-schema.json'
    assert e.main(['--config',str(path),'describe','--key',contract['key'],'--output',str(output)])==0
    result=json.loads(capsys.readouterr().out)
    assert output.stat().st_size>256000 and e.proof(output)==result['contract']
    assert result['contract']==contract
    assert c.read(output)['description']=='x'*256001


def test_prepare_does_not_build_an_adapter_or_fake_task(tmp_path,monkeypatch):
    public,goal=inputs(tmp_path)
    registration=ExecutionInterfaceRegistration('fixture','1',RuntimePluginFactory(forbidden_factory))
    monkeypatch.setattr(e,'registration',lambda executor:(registration,'binding'))
    settings=controller.prepare(tmp_path/'instance',goal,public,['custom'],max_tokens=100,max_seconds=10,max_steps=2,
        model='no-model',reasoning_effort='high',search_policy=None,
        execution_config=dict(executor='fixture:binding',configuration={}))
    assert settings['execution_config']['executor']=='fixture:binding'
    assert not list((tmp_path/'instance/jobs').glob('*'))


@pytest.mark.parametrize('field',[
    'scope','record_dir','native_attempt','admission','breadth',
    'engineering_repair','execution_interface','registered_implementation','frozen_execution_interface',
])
def test_operator_configuration_rejects_host_identity_and_permissions(execution_registry,field):
    with pytest.raises(ValueError,match='host identity'):
        e.operator_configuration(dict(executor='local_execution:command',configuration={field:{}}))


@pytest.mark.process_e2e
def test_cli_rejects_operator_host_field_injection_before_creating_instance(tmp_path,execution_registry):
    public,goal=inputs(tmp_path);path=tmp_path/'config.json';c.write(path,dict(executor='local_execution:command',configuration={'admission':{}}))
    reply,result=cli('prepare','--root',tmp_path/'instance','--objective',goal,'--public-root',public,
        '--native-launcher-json','["codex"]','--execution-config',path,'--max-tokens',100,'--max-seconds',10,'--max-steps',2)
    assert reply.returncode==1 and 'host identity' in result['error']
    assert not (tmp_path/'instance').exists()


@pytest.mark.parametrize('selection',[
    {},dict(executor='local_execution:command',configuration=[],extra=True),
    dict(executor='missing_plugin:binding',configuration={}),
])
def test_invalid_or_missing_selection_cannot_prepare(tmp_path,execution_registry,selection):
    public,goal=inputs(tmp_path)
    with pytest.raises(ValueError):controller.prepare(tmp_path/'instance',goal,public,['custom'],max_tokens=100,max_seconds=10,max_steps=2,
        model='no-model',reasoning_effort='high',execution_config=selection)
    assert not (tmp_path/'instance').exists()


def test_external_owned_mode_rejects_execution_config(tmp_path):
    public,goal=inputs(tmp_path)
    with pytest.raises(ValueError,match='conflicts with external'):
        controller.prepare(tmp_path/'instance',goal,public,['custom'],max_tokens=100,max_seconds=10,max_steps=2,
            model='no-model',reasoning_effort='high',experiment_mode='external',execution_config={})
    assert not (tmp_path/'instance').exists()


def test_duplicate_registration_is_not_cached_or_chosen(monkeypatch,execution_registry):
    original=importlib.metadata.entry_points
    assert e.operator_configuration(dict(executor='local_execution:command',configuration={}))
    class Duplicate:
        def select(self,**kwargs):
            matches=list(original().select(**kwargs));return matches+matches
    monkeypatch.setattr(importlib.metadata,'entry_points',lambda:Duplicate())
    with pytest.raises(PluginConfigurationError) as caught:
        e.operator_configuration(dict(executor='local_execution:command',configuration={}))
    assert caught.value.code == 'plugin_executor_unavailable'
    assert caught.value.field_path == 'executor.config.executor'


@pytest.mark.parametrize('change',['modify','add','delete','symlink'])
def test_registered_package_upgrade_does_not_change_operator_configuration(tmp_path,monkeypatch,change):
    package_name='selection_package_'+change
    package=tmp_path/package_name;package.mkdir();(package/'__init__.py').write_text('')
    (package/'adapter.py').write_text('VERSION=1\n')
    (package/'factory.py').write_text('from . import adapter\ndef build(context):raise AssertionError("No adapter construction")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    build=importlib.import_module(package_name+'.factory').build
    declared=ExecutionInterfaceRegistration('fixture','1',RuntimePluginFactory(build))
    monkeypatch.setattr(e,'registration',lambda executor:(declared,'binding'))
    frozen=e.operator_configuration(dict(executor='fixture:binding',configuration={}))
    helper=package/'adapter.py'
    if change=='modify':helper.write_text('VERSION=2\n')
    elif change=='add':(package/'optional.py').write_text('VERSION=2\n')
    elif change=='delete':helper.unlink()
    else:(package/'optional.py').symlink_to(helper)
    assert e.validate_operator_configuration(frozen)==frozen


@pytest.mark.parametrize('executor,configuration',[
    ('local_execution:absent',dict(run_argv=[sys.executable])),
    ('local_execution:command',dict(run_argv=[])),
])
def test_binding_and_adapter_config_errors_precede_native_launch(tmp_path,execution_registry,monkeypatch,executor,configuration):
    from test_history_handoff import finish
    public,goal=inputs(tmp_path);root=tmp_path/'instance'
    controller.prepare(root,goal,public,['never-launch'],max_tokens=100,max_seconds=20,max_steps=3,search_policy=None,
        model='no-model',reasoning_effort='high',execution_config=dict(executor=executor,configuration=configuration))
    finish(root,1,'H',[],action='experiment');job=c.build_job(root,2,'W',[1]);record=execution.origin(root,2)
    from scidiscovery.research_loop import profiles
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),**profiles.binding(c.settings(root),job)))
    accepted_budget.accept_native(root,2,record,c.read(record/'native-intent.json'))
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('Invalid configuration must not launch native'))
    with pytest.raises(ValueError):native.run_native(root,2)
    assert not (record/'native-process.json').exists() and not list((c.workspace(root,2)/'work/lineage/calls').glob('*'))






@pytest.mark.parametrize('changed',['task','configuration','permission'])
def test_source_metadata_upgrade_keeps_task_configuration_and_permission_checks(tmp_path,execution_registry,changed):
    import copy
    _,config=frozen_fixture(tmp_path,'local_execution:command',dict(run_argv=[sys.executable]))
    # Persisted JSON has independent copies of the scope and frozen config
    # record; the in-memory freeze return may share those nested dictionaries.
    config=json.loads(e.encoded(config))
    # Existing accepted configurations may retain proofs for a removed installation.
    config['execution_interface']['code']=[dict(path=str(tmp_path/'retired-tool.py'),size_bytes=1)]
    config['execution_interface']['plugin_version']='previous-installed-version'
    assert e.load(config) is not None
    altered=copy.deepcopy(config)
    if changed=='task':altered['scope']['job_ref']={'path':'/other-fixture/jobs/002.json'}
    elif changed=='configuration':altered['run_argv']=[sys.executable,'-I']
    else:altered['execution_interface']['capabilities']['query']=False
    with pytest.raises(e.IntegrityError,match='contract or configuration'):
        e.load(altered)
