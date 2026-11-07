"""Generic frozen directory inputs; synthetic transport has no Local runtime."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry
from test_history_handoff import finish
from scidiscovery.operations.execution import ExecutionInterfaceRegistration
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution, RuntimePluginFactory
from scidiscovery.research_loop import (accepted_budget, budget, code_versions, contracts as c, controller,
    execution, experiment_client, experiments as e, local_experiments, search)


@pytest.fixture
def directory_registry(monkeypatch, execution_registry):
    state=dict(prepared=[],submitted=[],before_prepare=None)

    class DirectoryExecution(DifferentExternalIdentity):
        def capabilities(self):
            return ((experiment_client.frozen_code_directory_capability(),)
                if self.config.get('directory_inputs',True) else ())

        def prepare(self,payload,*,preparation_profile,exchange_directory):
            assert preparation_profile=='research_loop.v1'
            assert exchange_directory==self.records.root
            state['prepared'].append(payload)
            if state['before_prepare'] is not None:state['before_prepare'](payload)
            return payload

        def submit(self,payload):
            state['submitted'].append(dict(prepared=payload.model_dump(mode='json'),
                code_version=dict(self.accepted['code_version']),
                entry=Path(payload.local_path).read_bytes(),
                helper=Path(payload.local_path).with_name('helper.py').read_bytes()
                    if self.accepted['code_version']['selection']=='directory' else None))
            return super().submit(payload)

    def build(context):
        config=json.loads(context.config_bytes)
        config['execution_interface']=context.frozen_execution_interface
        return RuntimePluginContribution(execution_adapters={'command':DirectoryExecution(config)})

    registration=ExecutionInterfaceRegistration('directory_fixture','1',RuntimePluginFactory(build))
    original=e.registration
    monkeypatch.setattr(e,'registration',lambda key:
        (registration,'command') if key=='directory_fixture:command' else original(key))
    return state


def _project(tmp_path):
    base=tmp_path/'base-project'
    (base/'src').mkdir(parents=True)
    (base/'data').mkdir()
    (base/'src/main.py').write_text('print("base is not the selected draft")\n')
    (base/'data/context.txt').write_text('Read-only project context.\n')
    git=shutil.which('git',path=os.defpath)
    assert git
    environment=dict(PATH=os.defpath,HOME=os.devnull,XDG_CONFIG_HOME=os.devnull,
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,LC_ALL='C',
        GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',
        GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for command in [('init','--quiet','--object-format=sha1','--template='),
            ('add','--','src/main.py','data/context.txt'),
            ('commit','--quiet','-m','Exact directory fixture base')]:
        subprocess.run([git,'-c','core.hooksPath='+os.devnull,*command],cwd=base,
            env=environment,check=True,capture_output=True,timeout=5)
    return dict(base_root=str(base),editable=['src'],scratch=['build'],outputs=['out'])


def _case(tmp_path,*,project=False,directory_inputs=True):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Synthetic directory input contract only.')
    root=tmp_path/'instance'
    options=(dict(project=_project(tmp_path),native_options=dict(workspace='workspace-write',network=False))
        if project else {})
    launcher=tmp_path/'codex'
    if project:
        launcher.write_text('# synthetic declared launcher; never executed\n')
        launcher.chmod(0o700)
    controller.prepare(root,objective,public,[str(launcher)] if project else ['never-launched'],max_tokens=10000,max_seconds=120,
        max_steps=10,model='no-model',reasoning_effort='high',node_budget_mode='soft',
        experiment_mode='external',**options)
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='Two explicit synthetic directory mechanisms',
        commitments=[dict(label=label,minimal_test='Observe the exact captured code',existing_ref=None)
            for label in ('P','Q')],
        focus=dict(index=None,action='screen')))
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    anchor=budget.accept_node(root,job)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),
        **(dict(budget_slice=anchor) if anchor is not None else dict(search_slice=search.accept(root,job)))))
    accepted_budget.accept_native(root,2,record,c.read(record/'native-intent.json'))
    work=c.workspace(root,2);config_path=work/'work/experiment-config.json'
    config=e.configure(root,2,'directory_fixture:command',dict(directory_inputs=directory_inputs),config_path)
    code=work/('project/src' if project else 'code');code.mkdir(parents=True,exist_ok=True)
    (code/'main.py').write_bytes(b'from helper import VALUE\nprint(VALUE)\n')
    (code/'helper.py').write_bytes(b'VALUE = "captured sibling"\n')
    return root,job,work,code,config,config_path


def _public_capability(item):
    class Declaration:
        def capabilities(self):return (item,)
    return e._public_capabilities(Declaration())[0]


def test_directory_capability_requires_exact_live_and_frozen_contract():
    capability=experiment_client.frozen_code_directory_capability()
    frozen=dict(public_capabilities=[_public_capability(capability)])
    assert experiment_client.supports_frozen_code_directory(frozen)
    assert not experiment_client.supports_frozen_code_directory({})
    assert experiment_client.frozen_code_directory_capability() in local_experiments.LocalExecution.capabilities()
    # Same-sized bytes and JSON's bool/int equality cannot amend the contract.
    with pytest.raises(e.IntegrityError,match='code directory capability'):
        _public_capability(replace(capability,content=capability.content.replace(b'directory',b'DIRECTORY')))
    with pytest.raises(e.IntegrityError,match='code directory capability'):
        _public_capability(replace(capability,public_summary=dict(capability.public_summary,version=True)))
    for changed in (dict(frozen['public_capabilities'][0],payload_schema_version=True),
            dict(frozen['public_capabilities'][0],public_summary=dict(capability.public_summary,version=True))):
        with pytest.raises(e.IntegrityError,match='Frozen code directory capability'):
            experiment_client.supports_frozen_code_directory(dict(public_capabilities=[changed]))
    with pytest.raises(e.IntegrityError,match='Frozen code directory capability'):
        experiment_client.supports_frozen_code_directory(dict(public_capabilities=frozen['public_capabilities']*2))


@pytest.mark.process_e2e
@pytest.mark.parametrize('project',[False,True])
def test_declared_provider_receives_frozen_directory_and_query_survives_export_removal(
        tmp_path,monkeypatch,capsys,directory_registry,project):
    root,job,work,code,config,config_path=_case(tmp_path,project=project)
    (work/'work/unselected.py').write_bytes(b'not part of code\n')
    helper=(code/'helper.py').read_bytes()

    def edit_draft(payload):
        (code/'helper.py').write_bytes(b'VALUE = "later mutable draft"\n')
        assert Path(payload.local_path).with_name('helper.py').read_bytes()==helper

    directory_registry['before_prepare']=edit_draft
    result=e.dispatch(config,code/'main.py',new=True,mechanism='h001.m1')
    assert result['status']=='known_success'
    call=result['call_id'];accepted=e.Records(config).accepted(call);version=accepted['code_version']
    assert set(version)=={'version','object_format','commit','tree_path','selection','entry','logical_root'}
    assert version['selection']=='directory'
    assert version['logical_root']==('project' if project else 'code')
    assert version['entry']==('src/main.py' if project else 'main.py')
    assert accepted['source_script']==str(code/'main.py')
    assert not (e.Records(config).directory(call)/'source.py').exists()
    assert directory_registry['submitted']==[dict(prepared=accepted['prepared'],code_version=version,
        entry=(code/'main.py').read_bytes(),helper=helper)]
    exported=Path(accepted['prepared']['local_path'])
    assert exported!=code/'main.py'
    assert exported.with_name('helper.py').read_bytes()==helper
    assert set(code_versions.verify(root,step=2,call_id=call,code_version=version))=={
        version['entry'],'src/helper.py' if project else 'helper.py'}
    imported=subprocess.run([sys.executable,'-B',str(exported)],cwd=tmp_path,
        capture_output=True,text=True,check=True,timeout=5)
    assert imported.stdout=='captured sibling\n'
    assert (code/'helper.py').read_bytes()!=helper
    assert e._code_version_binding(config,accepted)==root
    undeclared=dict(config,execution_interface={key:value for key,value in config['execution_interface'].items()
        if key!='public_capabilities'})
    with pytest.raises(e.IntegrityError,match='selected logical source'):
        e._code_version_binding(undeclared,accepted)
    code_versions.cleanup_export(root,step=2,call_id=call)
    assert not exported.exists()
    # Same-call CLI query must use retained controls, never recreate/consume code.
    monkeypatch.setattr(code_versions,'materialize',lambda *args,**kwargs:pytest.fail('query materialized code'))
    monkeypatch.setattr(code_versions,'verify',lambda *args,**kwargs:pytest.fail('query consumed code'))
    assert e.main(['--config',str(config_path),'query','--call-id',call])==0
    queried=json.loads(capsys.readouterr().out)
    assert queried['state']=='succeeded' and queried['call_id']==call
    assert len(directory_registry['submitted'])==1
    assert not exported.exists()


@pytest.mark.process_e2e
def test_undeclared_provider_keeps_file_selection_and_cannot_admit_project_directory(tmp_path,directory_registry):
    ordinary=tmp_path/'ordinary';ordinary.mkdir()
    root,job,work,code,config,_=_case(ordinary,directory_inputs=False)
    selection=experiment_client.code_selection(config,code/'main.py')
    assert selection['selection']=='file' and selection['code_root']==code
    result=e.dispatch(config,code/'main.py',new=True,mechanism='h001.m1')
    assert result['status']=='known_success'
    accepted=e.Records(config).accepted(result['call_id'])
    assert accepted['code_version']['selection']=='file'
    assert not Path(accepted['prepared']['local_path']).with_name('helper.py').exists()
    code_versions.cleanup_export(root,step=2,call_id=result['call_id'])
    project=tmp_path/'project';project.mkdir()
    _,_,_,project_code,project_config,_=_case(project,project=True,directory_inputs=False)
    prepared=len(directory_registry['prepared'])
    with pytest.raises(e.UnsupportedExecution,match='does not declare frozen code directory inputs'):
        e.dispatch(project_config,project_code/'main.py',new=True,mechanism='h001.m1')
    assert len(directory_registry['prepared'])==prepared
    assert e.Records(project_config).calls()==[]


@pytest.mark.process_e2e
def test_directory_capability_preserves_code_boundary_and_local_runtime_semantics(tmp_path,directory_registry):
    root,job,work,code,config,_=_case(tmp_path)
    facts=experiment_client.load_client_facts(config)
    outside=work/'work/outside.py';outside.write_bytes(b'print("outside")\n')
    with pytest.raises(e.CallerError,match='declared code/'):
        experiment_client._observe_code_selection(config,outside,resolved=outside,
            experiment_directory=None,facts=facts)
    with pytest.raises(e.CallerError,match='runtime directory'):
        experiment_client._observe_code_selection(config,code/'main.py',resolved=code/'main.py',
            experiment_directory=work/'experiments/run',facts=facts)
    assert not (work/'experiments/run').exists()
    local=dict(config,execution_interface=dict(config['execution_interface'],executor='local_execution:command',
        public_capabilities=e._public_capabilities(local_experiments.LocalExecution)))
    ordinary=experiment_client._observe_code_selection(local,code/'main.py',resolved=code/'main.py',
        experiment_directory=None,facts=facts)
    assert ordinary==dict(code_root=code,entry='main.py',selection='directory',logical_root='code')
    selected=experiment_client._observe_code_selection(local,code/'main.py',resolved=code/'main.py',
        experiment_directory=work/'experiments/run',facts=facts)
    assert selected==dict(code_root=code,entry='main.py',selection='directory',logical_root='code')
    with pytest.raises(e.CallerError,match='runtime directory'):
        experiment_client._observe_code_selection(local,code/'main.py',resolved=code/'main.py',
            experiment_directory=work/'experiments/nested/run',facts=facts)
    bound=dict(path='code/input.dat',source=str(code/'input.dat'),size_bytes=1)
    bound_job=dict(job,direct_materials=[*job.get('direct_materials',[]),bound])
    bound_facts=dict(facts,job=bound_job)
    with pytest.raises(e.CallerError,match='overlaps a bound scientific input'):
        experiment_client._observe_code_selection(config,code/'main.py',resolved=code/'main.py',
            experiment_directory=None,facts=bound_facts)
    linked=code/'alias.py';linked.symlink_to(code/'main.py')
    with pytest.raises(e.IntegrityError,match='exact ordinary path'):
        experiment_client._observe_code_selection(config,linked,resolved=linked,
            experiment_directory=None,facts=facts)


def test_pure_result_parser_reuses_decoded_value_and_exact_retained_names(monkeypatch):
    value=dict(version=1,metrics=[dict(name='score',value=2)],
        outputs=[dict(path='artifacts/model.py')],
        candidates=[dict(artifact='artifacts/model.py',metric='score')])
    monkeypatch.setattr(e,'read',lambda *args,**kwargs:pytest.fail('pure result parser read a file'))
    monkeypatch.setattr(local_experiments.json,'loads',lambda *args,**kwargs:pytest.fail('pure result parser decoded again'))
    result=local_experiments.LocalExecution.parse_result(value,[dict(name='model.py')])
    assert result==dict(status='available',metrics=value['metrics'],outputs=value['outputs'],candidates=value['candidates'])
    missing=local_experiments.LocalExecution.parse_result(value,[])
    assert missing['outputs']==[] and missing['candidates']==[]
    assert {row['code'] for row in missing['diagnostics']}=={
        'output_original_mapping_invalid','candidate_original_mapping_invalid'}
    assert local_experiments.LocalExecution.parse_result({},[])==dict(status='invalid',metrics=[],outputs=[],
        diagnostic='result_json_invalid',detail='result requires version=1 and metrics and outputs arrays')
