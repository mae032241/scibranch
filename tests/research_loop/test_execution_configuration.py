"""Operator selection -> real native host wiring, without models or backends."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, experiments as e, native
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginFactory
from scidiscovery.plugin_configuration import PluginConfigurationError, read_execution_selection
from execution_fixtures import execution_registry, remote_registry
from test_composed_execution_results import composed_registry


NATIVE = '''import json,os,pathlib,subprocess,sys
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE'];prompt=sys.stdin.read()
task=json.loads((work/'science-task.json').read_text())
payload=dict(summary=role+' fixture finding',scientific_result='Mechanical registration only.',limitations=['No science qualification.'],implementation_selection=None)
if role=='H':payload.update(next_action='experiment',route_reason='Read one bounded opaque execution',search_plan=None)
if role=='W':
 assert 'Managed registered experiments:' in prompt and 'Managed local experiments:' not in prompt
 assert 'unsupported, never a passing verdict' in prompt
 command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(work/'work/experiment-config.json')]
 listed=subprocess.run([*command,'capabilities'],capture_output=True,text=True);assert listed.returncode==0,(listed.stdout,listed.stderr)
 capabilities=json.loads(listed.stdout);assert capabilities['contracts'] and not capabilities['lifecycle']['cancel']
 contract=capabilities['contracts'][0];assert contract['media_type']=='application/schema+json'
 schema_path=work/'work/public-schema.json'
 described=subprocess.run([*command,'describe','--key',contract['key'],'--output',str(schema_path)],capture_output=True,text=True)
 assert described.returncode==0,(described.stdout,described.stderr)
 schema=json.loads(schema_path.read_text())
 source=work/'work/payload.bin';source.write_text(json.dumps({key:value['const'] for key,value in schema['properties'].items()}))
 result=subprocess.run([*command,'run','--payload',str(source)],capture_output=True,text=True)
 assert result.returncode==0,(result.stdout,result.stderr)
 result=json.loads(result.stdout);assert result['status']=='known_success',result
 call=result['call_id']
 checked=subprocess.run([*command,'query','--call-id',call],capture_output=True,text=True)
 assert checked.returncode==0 and json.loads(checked.stdout)['state']=='succeeded',(checked.stdout,checked.stderr)
 payload.update(stage_intent='review',mechanism_findings=None)
if role=='A':payload.update(next_action='finish',route_reason='Retain exact fixture evidence',candidate_recommendation=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
args=sys.argv[1:];pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps({'type':'thread.started','thread_id':'selection-fixture-'+role}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}),flush=True)
'''


def inputs(tmp_path):
    public=tmp_path/'public';public.mkdir();goal=public/'goal.md';goal.write_text('Exercise exact registered host wiring.')
    return public,goal


def cli(*args):
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(str(path) for path in sys.path if path))
    reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.cli',*map(str,args)],env=env,capture_output=True,text=True,timeout=30)
    assert reply.stdout,reply.stderr
    return reply,json.loads(reply.stdout)


@pytest.mark.process_e2e
def test_cli_selection_is_frozen_before_real_native_host_wiring(tmp_path,monkeypatch,remote_registry):
    executor='remote_execution:fixture';options=dict(include_result=True,require_contract=True)
    public,goal=inputs(tmp_path);root=tmp_path/'instance'
    monkeypatch.setenv('PYTHONPATH',os.pathsep.join([str(Path(e.__file__).parents[2]),str(Path(__file__).parent),os.environ.get('PYTHONPATH','')]))
    launcher=tmp_path/'native.py';launcher.write_text(NATIVE)
    selection=tmp_path/'execution.json';c.write(selection,dict(executor=executor,configuration=options))
    search=tmp_path/'search.json';c.write(search,None)
    prepared,result=cli('prepare','--root',root,'--objective',goal,'--public-root',public,
        '--native-launcher-json',json.dumps([sys.executable,str(launcher)]),
        '--execution-config',selection,'--search-policy',search,'--max-tokens',1000,'--max-seconds',60,'--max-steps',4,'--model','no-model')
    assert prepared.returncode==0,result
    assert result['execution']==dict(executor=executor,capabilities_status='awaiting_host_task_configuration')
    settings=c.settings(root);frozen=settings['execution_config']
    assert frozen['configuration']==options and frozen['executor']==executor
    assert settings['experiment_mode']=='local'  # Ownership, not the backend type.
    assert not list((root/'jobs').glob('*')) and not list((root/'project/nodes').glob('*/work/experiment-config.json'))
    before=(root/'settings.json').read_bytes();selection.write_text('{}')
    ran,result=cli('run','--root',root)
    assert ran.returncode==0 and result['status']=='finish',result
    work=c.workspace(root,2);job=c.read(root/'jobs/002.json');config=c.read(execution.origin(root,2)/'experiment-config.json')
    assert (root/'settings.json').read_bytes()==before and job['execution_config_ref']
    assert config['execution_interface']['executor']==executor
    assert config['scope']['job_ref']==c.record_ref(root/'jobs/002.json') and config['native_attempt']['number']==0
    from scidiscovery.research_loop import experiment_client
    assert config['record_dir']==str(experiment_client.record_directory(root,2)) and config['admission']['mode']=='soft'
    assert config.get('registered_implementation')==frozen.get('implementation')
    assert config['execution_interface']['public_capabilities'][0]['schema_id'].startswith('fixture.')
    assert 'execution_config' not in (work/'science-task.json').read_text()
    seal=c.read(execution.origin(root,2)/'result-identity.json');observed=c.read(root/seal['observations']['path'])
    assert len(observed['managed_calls'])==1 and observed['managed_calls'][0]['status']=='known_success'
    assert observed['managed_calls'][0]['outputs']
    status=cli('status','--root',root)[1]['nodes'][1]['execution']
    assert status['executor']==executor and status['capabilities']['query'] and 'configuration' not in status
    from scidiscovery.research_loop.local_experiments import cleanup
    assert cleanup(root,job)=='not_applicable'
    frozen_calls={p:p.read_bytes() for p in e.Records(config).root.rglob('*') if p.is_file()}
    repeated=cli('run','--root',root)[1]
    assert {key:value for key,value in repeated.items() if key!='budget'}=={key:value for key,value in result.items() if key!='budget'}
    assert all(p.read_bytes()==raw for p,raw in frozen_calls.items())
    original_registration=e.registration
    def replaced(key):
        declared,binding=original_registration(key)
        return ExecutionInterfaceRegistration(declared.plugin_id,declared.version+'-replaced',declared.factory),binding
    monkeypatch.setattr(e,'registration',replaced)
    call=e.Records(config).calls()[0].name
    _,adapter=e.original_adapter(config,call,'query')
    assert adapter.status_details(call)['state']=='succeeded'
    assert e.dispatch(config,work/'work/payload.bin')['status']=='known_success'
    assert all(p.read_bytes()==raw for p,raw in frozen_calls.items())


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
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('Invalid configuration must not launch native'))
    with pytest.raises(ValueError):native.run_native(root,2)
    assert not (record/'native-process.json').exists() and not list((c.workspace(root,2)/'work/lineage/calls').glob('*'))


@pytest.mark.process_e2e
def test_default_local_cli_keeps_workspace_cwd_for_single_script(tmp_path,execution_registry):
    from test_history_handoff import finish
    from scidiscovery.research_loop import profiles
    public,goal=inputs(tmp_path);root=tmp_path/'instance'
    settings=controller.prepare(root,goal,public,['codex'],search_policy=None,
        max_tokens=1000,max_seconds=60,max_steps=4,model='no-model',reasoning_effort='high')
    selected=settings['execution_config'];assert selected['executor']=='local_execution:command'
    finish(root,1,'H',[],action='experiment');job=c.build_job(root,2,'W',[1]);record=execution.origin(root,2)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),**profiles.binding(settings,job)))
    work=c.workspace(root,2);source=work/'work/child/run.py';source.parent.mkdir()
    (work/'relative-original.txt').write_text('workspace original')
    source.write_text('import pathlib\nassert pathlib.Path("relative-original.txt").read_text()=="workspace original"\npathlib.Path("artifacts/cwd-proof.txt").write_text(str(pathlib.Path.cwd()))\n')
    path=work/'work/experiment-config.json'
    config=e.configure(root,2,selected['executor'],selected['configuration'],path)
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(str(p) for p in sys.path if p))
    reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(path),'run','--script',str(source)],
        cwd=source.parent,env=env,capture_output=True,text=True,timeout=10)
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    result=json.loads(reply.stdout);assert result['status']=='known_success'
    assert config['cwd']==str(work) and (work/'artifacts/cwd-proof.txt').read_text()==str(work)


@pytest.mark.process_e2e
def test_accepted_package_upgrade_allows_query_reconcile_and_submit(tmp_path,monkeypatch):
    from test_history_handoff import finish
    from scidiscovery.research_loop import profiles
    package=tmp_path/'accepted_package';package.mkdir();(package/'__init__.py').write_text('')
    helper=package/'helper.py';helper.write_text('VERSION=1\n')
    (package/'factory.py').write_text('from . import helper\nimport json\n'
        'from scidiscovery.research_loop.local_experiments import LocalExecution\n'
        'from scidiscovery.operations.execution import ExecutionInterfaceRegistration\n'
        'from scidiscovery.operations.runtime_plugins import RuntimePluginContribution,RuntimePluginFactory\n'
        'def build(context):return RuntimePluginContribution(execution_adapters={"command":LocalExecution(json.loads(context.config_bytes))})\n'
        'REGISTRATION=ExecutionInterfaceRegistration("accepted_fixture","1",RuntimePluginFactory(build))\n')
    metadata=tmp_path/'accepted_fixture.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: accepted-fixture\nVersion: 1\n')
    (metadata/'entry_points.txt').write_text('[scidiscovery.execution_interfaces]\naccepted_fixture = accepted_package.factory:REGISTRATION\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    public,goal=inputs(tmp_path);root=tmp_path/'instance'
    options=dict(run_argv=[sys.executable],max_seconds=5)
    controller.prepare(root,goal,public,['never-launched'],max_tokens=100,max_seconds=20,max_steps=3,search_policy=None,
        model='no-model',reasoning_effort='high',execution_config=dict(executor='accepted_fixture:command',configuration=options))
    finish(root,1,'H',[],action='experiment');job=c.build_job(root,2,'W',[1]);record=execution.origin(root,2)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),**profiles.binding(c.settings(root),job)))
    path=c.workspace(root,2)/'work/experiment-config.json';config=e.configure(root,2,'accepted_fixture:command',options,path)
    script=tmp_path/'source.py';script.write_text('print("one original dispatch")\n')
    result=e.dispatch(config,script);assert result['status']=='known_success'
    originals={p:p.read_bytes() for p in e.Records(config).root.rglob('*') if p.is_file()}
    helper.write_text('VERSION=2\n')
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(str(p) for p in sys.path if p))
    for action in ('query','reconcile','run'):
        args=['--script',str(script)] if action=='run' else ['--call-id',result['call_id']]
        reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(path),action,*args],
            env=env,capture_output=True,text=True,timeout=10)
        assert reply.returncode==0,(reply.stdout,reply.stderr)
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert len(e.Records(config).calls())==2


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
