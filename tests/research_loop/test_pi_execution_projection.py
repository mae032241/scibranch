"""Pi supplies a projection, not a second Local lifecycle or qualification owner.

Only marked nodes run Git/processes. Peers are mechanical transport fixtures;
no provider, solver, installation or scientific qualification is asserted.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (accepted_prefix, backends, budget, contracts as c,
    controller, execution, experiment_client, experiments as e, local_experiments, native_execution, pi_namespace,
    project_layout, research_config, shared_input_views as views)
from test_pi_backend_v2 import (FAKE_RPC, OPTIONS, PROFILE, TOKENS, adapter,
    collect_separately, options, require_namespace, settings)


@pytest.mark.parametrize('mode',['local','external'])
def test_local_declaration_is_explicit_and_keeps_local_default(tmp_path,monkeypatch,mode):
    value=options(tmp_path,experiment_mode=mode,
        native_options=dict(OPTIONS,workspace='workspace-write' if mode=='local' else 'read-only'))
    before=copy.deepcopy(value)
    normalized=research_config.normalize(value)
    def forbidden(*a,**kw):pytest.fail('Pure declaration acquired filesystem/process facts')
    monkeypatch.setattr(Path,'stat',forbidden);monkeypatch.setattr(Path,'open',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    backends.provider('pi').validate_declaration(normalized)
    assert normalized['experiment_mode']==mode and value==before
    assert adapter().default_experiment_mode()=='local'
    assert adapter().complete_authorship is True


def test_explicit_local_execution_config_reaches_the_existing_registration_owner(tmp_path,execution_registry):
    selected=dict(executor='local_execution:command',configuration=dict(run_argv=[sys.executable],max_seconds=10))
    value=options(tmp_path,experiment_mode='local',execution_config=selected,
        native_options=dict(OPTIONS,workspace='workspace-write'))
    normalized=research_config.normalize(value)
    assert normalized['execution_config']==selected
    assert adapter(experiment_mode='local',execution_config=selected,
        native_options=dict(OPTIONS,workspace='workspace-write')).default_experiment_mode()=='local'


@pytest.mark.parametrize('entry',['prepare','controller','normalize'])
@pytest.mark.parametrize('case',[
    'readonly-local','readonly-project','readonly-local-project','readonly-implied-local',
    'readonly-project-implied-local','invalid-options-project','missing-options-project',
    'invalid-launcher-project','external-execution-conflict'])
def test_public_admission_refuses_before_paths_git_plugins_or_root_effects(tmp_path,monkeypatch,entry,case):
    project=dict(base_root='/unread/project',editable=['src'],scratch=['scratch'],outputs=['out'])
    selected=dict(executor='forbidden:factory',configuration={})
    value=options(tmp_path)
    if 'project' in case:value['project']=project
    if case in {'readonly-local','readonly-local-project'}:value['experiment_mode']='local'
    elif case in {'readonly-implied-local','readonly-project-implied-local'}:
        value.update(experiment_mode=None,execution_config=selected)
    elif case=='invalid-options-project':
        value['native_options']=dict(OPTIONS,workspace='workspace-write',network=False)
    elif case=='missing-options-project':value.pop('native_options')
    elif case=='invalid-launcher-project':
        value.update(launcher=['pi','--extension'],native_options=dict(OPTIONS,workspace='workspace-write'))
    elif case=='external-execution-conflict':
        value.update(experiment_mode='external',execution_config=selected,
            native_options=dict(OPTIONS,workspace='workspace-write'))
    before=copy.deepcopy(value)
    if case.startswith('readonly'):
        match='Pi projects require' if case=='readonly-project' else 'Pi Local requires'
    elif case=='invalid-launcher-project':match='single executable'
    elif case=='external-execution-conflict':match='conflicts with external'
    else:match='Pi requires explicit'
    def forbidden(*a,**kw):pytest.fail('Rejected declaration reached filesystem/Git/plugin/control effects')
    with monkeypatch.context() as guarded:
        for name in ('resolve','open','stat','mkdir','read_text','home'):
            guarded.setattr(Path,name,forbidden)
        guarded.setattr(os,'open',forbidden)
        guarded.setattr(subprocess,'Popen',forbidden)
        guarded.setattr(project_layout,'prepare',forbidden)
        guarded.setattr(e,'registration',forbidden)
        guarded.setattr(e,'operator_configuration',forbidden)
        guarded.setattr(pi_namespace,'agent_directory',forbidden)
        guarded.setattr(c,'write',forbidden)
        guarded.setattr(c.atomic_files,'publish_bytes',forbidden)
        with pytest.raises(ValueError,match=match):
            if entry=='prepare':research_config.prepare(**value)
            elif entry=='controller':controller.prepare(**value)
            else:research_config.normalize(value)
    assert value==before
    assert not value['root'].exists() and not value['public_root'].exists() and not value['objective'].exists()


def test_readonly_local_projection_refuses_before_runtime_or_write_grants(monkeypatch):
    value=settings(experiment_mode='local')
    def forbidden(*a,**kw):pytest.fail('Read-only declaration reached a filesystem effect')
    monkeypatch.setattr(Path,'stat',forbidden);monkeypatch.setattr(Path,'mkdir',forbidden)
    monkeypatch.setattr(views,'ensure_capability',forbidden)
    with pytest.raises(e.UnsupportedExecution,match='read-only'):
        pi_namespace.execution_projection()['prepare']({},[],root=Path('/instance'),record=Path('/record'),
            work=Path('/work'),home=Path('/session'),job=dict(role='W'),settings=value,resume_position=None)


@pytest.mark.parametrize('workspace',['read-only','workspace-write'])
def test_project_policy_is_pure_and_never_translates_pi_to_codex(workspace,monkeypatch):
    value=settings(native_options=dict(OPTIONS,workspace=workspace))
    def forbidden(*a,**kw):pytest.fail('Pure provider policy consulted native configuration')
    monkeypatch.setattr(Path,'stat',forbidden);monkeypatch.setattr(Path,'resolve',forbidden)
    monkeypatch.setattr(Path,'open',forbidden);monkeypatch.setattr(os,'open',forbidden)
    if workspace=='read-only':
        with pytest.raises(e.UnsupportedExecution,match='workspace-write'):adapter().project_policy(value)
    else:
        assert adapter().project_policy(value)==dict(workspace='workspace-write',network=True,web='disabled',subagents=False)


def test_scientific_argv_projectors_are_pure_and_pi_has_no_native_auth_layer(monkeypatch):
    command=['/usr/bin/python3','/workspace/code/run.py','argument']
    value=dict(policy=dict(OPTIONS,workspace='workspace-write'))
    codex_policy=dict(network_access=False,exclude_slash_tmp=False,exclude_tmpdir_env_var=False)
    codex=dict(policy=codex_policy,native=dict(path='/exact/codex'),
        backend_permission_profile=dict(configuration=native_execution._profile(codex_policy,Path('/workspace'),'/task-tmp',virtual=True)))
    def forbidden(*a,**kw):pytest.fail('Pure argv projector performed IO')
    for name in ('stat','resolve','open','mkdir'):monkeypatch.setattr(Path,name,forbidden)
    monkeypatch.setattr(os,'open',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    monkeypatch.setattr(local_experiments,'artifact_directory_from_facts',forbidden)
    monkeypatch.setattr(views,'dynamic_root',forbidden)
    kwargs=dict(policy_cwd=Path('/workspace'),project=None,cwd='/workspace')
    assert pi_namespace.execution_arguments_from_facts(value,command,**kwargs)==command
    assert pi_namespace.execution_arguments_from_facts(value,command,
        **dict(kwargs,project=dict(base_root='/project'),cwd='/project'))==[
            '/bin/sh','-c','cd "$1" && shift && exec "$@"','scid-project-cwd','/project',*command]
    assert native_execution._codex_scientific_arguments(codex,command,**kwargs)==[
        '/exact/codex','sandbox','-P',native_execution.PERMISSION_PROFILE,'-C','/workspace','--',*command]
    assert not {'CODEX_HOME','PI_CODING_AGENT_DIR','--session','--model','sandbox'} & set(command)


def test_execution_fact_validator_never_reopens_originals(monkeypatch):
    root=Path('/instance');work=root/'project/nodes/001';record=root/'native-evidence/001'
    job=dict(step=1,role='W');intent=dict(task_ref=dict(path='/instance/jobs/001.json'))
    policy=settings(experiment_mode='local')
    facts=dict(root=root,work=work,target=work,job=job,policy=policy,native=dict(record=record,intent=intent))
    def forbidden(*a,**kw):pytest.fail('Fact validator reacquired immutable originals')
    for name in ('stat','resolve','open'):monkeypatch.setattr(Path,name,forbidden)
    monkeypatch.setattr(c,'read',forbidden)
    native_execution.validate_source_facts(root,1,record,work,job,intent,policy,facts)
    with pytest.raises(e.IntegrityError):
        native_execution.validate_source_facts(root,1,record,work,job,intent,policy,dict(facts,target=Path('/other')))


@pytest.mark.parametrize('attempt',[0,1])
def test_existing_bridge_closure_consumes_owner_facts_without_read_fallback(tmp_path,monkeypatch,attempt):
    root=tmp_path/'instance';work=c.workspace(root,1);record=execution.origin(root,1)
    if attempt:record=record/'continuations'/f'{attempt:03d}'
    work.mkdir(parents=True);record.mkdir(parents=True)
    home=root/'native-homes/001';home.mkdir(parents=True)
    public=tmp_path/'public';public.mkdir()
    job=dict(step=1,role='W',public_root=str(public))
    job_path=root/'jobs/001.json';job_path.parent.mkdir();c.write(job_path,job)
    intent=dict(task_ref=c.record_ref(job_path),**(dict(attempt=attempt) if attempt else {}))
    original=(json.dumps(intent,indent=3)+'\n \n').encode()
    intent_path=record/'native-intent.json';intent_path.write_bytes(original)
    native=native_execution.load_native_facts(record,task_ref=intent['task_ref'])
    policy=settings(experiment_mode='local')
    facts=dict(root=root,work=work,target=work,job=job,policy=policy,native=native)
    selected=[];materials={};calls=[]
    def prepare(value,args,**kwargs):
        value.update(python='/usr/bin/python3',runtime_roots=['/usr'],runtime_programs=[],private_roots=[])
    projection=dict(prepare=prepare,validate_runtime=lambda *a:None)
    monkeypatch.setattr(native_execution,'_experiment_python',lambda *a:None)
    def launch(*a,**kw):
        assert kw['source_facts'] is facts and kw['selected_code'] is selected
        assert kw['material_sources'] is materials and kw['projection'] is projection
        calls.append((a,kw));return ['projected'],None,None
    monkeypatch.setattr(native_execution,'_launch_from_facts',launch)
    def forbidden(*a,**kw):pytest.fail('Preparation reloaded immutable owner facts')
    path_open=Path.open;path_stat=Path.stat
    def mutable_open(path,*a,**kw):
        if path in (job_path,intent_path):forbidden()
        return path_open(path,*a,**kw)
    def mutable_stat(path,*a,**kw):
        if path in (job_path,intent_path):forbidden()
        return path_stat(path,*a,**kw)
    with monkeypatch.context() as guarded:
        guarded.setattr(c,'read',forbidden);guarded.setattr(c,'read_job',forbidden)
        guarded.setattr(c,'record_ref',forbidden);guarded.setattr(e,'read',forbidden)
        guarded.setattr(e,'proof',forbidden)
        guarded.setattr(Path,'open',mutable_open);guarded.setattr(Path,'stat',mutable_stat)
        for invalid in (dict(native,task_ref=dict(path=str(root/'jobs/002.json'))),
                dict(native,intent_proof=dict(native['intent_proof'],path=str(record/'other.json'))),
                {key:value for key,value in native.items() if key!='intent_proof'}):
            with pytest.raises(e.IntegrityError):
                native_execution.prepare_from_facts(root,1,record,work,home,['peer'],resume_position=None,
                    job=job,intent=intent,settings=policy,source_facts=dict(facts,native=invalid),
                    selected_code=selected,material_sources=materials,projection=projection)
        args,closure=native_execution.prepare_from_facts(root,1,record,work,home,['peer'],resume_position=None,
            job=job,intent=intent,settings=policy,source_facts=facts,selected_code=selected,
            material_sources=materials,projection=projection)
    snapshot=c.read(record/'native-execution-permissions.json')
    assert snapshot['intent']==native['intent_proof']
    assert snapshot['intent']['sha256']==hashlib.sha256(original).hexdigest()
    assert snapshot['intent']['size_bytes']==len(original)
    assert snapshot['native_attempt']==dict(number=attempt,
        execution_key=dict(path=str(intent_path)) if attempt else intent['task_ref'])
    scope=dict(job_ref=intent['task_ref']);accepted=dict(scope=scope,native_attempt=snapshot['native_attempt'])
    monkeypatch.setattr(c,'read',forbidden);monkeypatch.setattr(e,'proof',forbidden)
    monkeypatch.setattr(Path,'stat',forbidden);monkeypatch.setattr(Path,'open',forbidden)
    assert closure(dict(scope=scope),accepted,['command'],str(work))==(['projected'],None,None)
    assert args==['peer'] and len(calls)==1
    with pytest.raises(e.IntegrityError):closure(dict(scope=scope),dict(accepted,native_attempt={}),[],None)
    assert len(calls)==1


def test_native_intent_owner_reads_one_original_and_preserves_proof_through_source_facts(tmp_path,monkeypatch):
    from scidiscovery.research_loop import handoff_state
    root=tmp_path/'instance';work=c.workspace(root,1);record=execution.origin(root,1)
    record.mkdir(parents=True)
    task_ref=dict(path=str(root/'jobs/001.json'))
    intent=dict(task_ref=task_ref,step=1)
    original=(json.dumps(intent,indent=3)+'\n \n').encode()
    path=record/'native-intent.json';path.write_bytes(original)
    opened=[];open_original=os.open
    def acquire(path,*args,**kwargs):
        opened.append(Path(path));return open_original(path,*args,**kwargs)
    def forbidden(*a,**kw):pytest.fail('Intent/source owner reacquired supplied immutable originals')
    with monkeypatch.context() as guarded:
        guarded.setattr(os,'open',acquire)
        guarded.setattr(c,'read',forbidden);guarded.setattr(e,'read',forbidden)
        guarded.setattr(e,'proof',forbidden)
        guarded.setattr(Path,'read_bytes',forbidden);guarded.setattr(Path,'read_text',forbidden)
        native=native_execution.load_native_facts(record,task_ref=task_ref)
    assert opened==[path] and native['intent']==intent
    assert native['intent_proof']['sha256']==hashlib.sha256(original).hexdigest()
    assert native['intent_proof']['size_bytes']==len(original)
    job=dict(step=1,role='W');policy=settings(experiment_mode='local')
    monkeypatch.setattr(handoff_state,'load_material_sources',lambda *a,**kw:None)
    monkeypatch.setattr(handoff_state,'material_paths',lambda *a,**kw:{})
    monkeypatch.setattr(accepted_prefix,'input_rows',lambda job:[])
    monkeypatch.setattr(experiment_client,'_input_rows',lambda job:[])
    monkeypatch.setattr(Path,'stat',forbidden);monkeypatch.setattr(Path,'open',forbidden)
    monkeypatch.setattr(c,'read',forbidden);monkeypatch.setattr(c,'read_job',forbidden)
    monkeypatch.setattr(c,'record_ref',forbidden);monkeypatch.setattr(e,'proof',forbidden)
    facts=views.load_source_facts(root,job,work,native=native,policy=policy,
        include_wake=False,include_review=False)
    assert facts['native']['intent'] is native['intent']
    assert facts['native']['task_ref'] is task_ref
    assert facts['native']['intent_proof'] is native['intent_proof']
    with pytest.raises(ValueError,match='original proof'):
        native_execution.prepare(root,1,record,work,root/'home',[],standard=False,
            job=job,intent=intent,settings=policy)
    def consume(*a,**kw):
        assert kw['intent'] is intent
        assert kw['source_facts']['native']['intent_proof'] is native['intent_proof']
        return ['prepared'],None
    monkeypatch.setattr(native_execution,'prepare_from_facts',consume)
    assert native_execution.prepare(root,1,record,work,root/'home',[],standard=False,
        job=job,intent=intent,settings=policy,intent_proof=native['intent_proof'])==(['prepared'],None)


@pytest.mark.parametrize('changed',['missing','symlink','wrong-task'])
def test_native_intent_owner_requires_the_exact_existing_original(tmp_path,changed):
    root=tmp_path/'instance';record=execution.origin(root,1);record.mkdir(parents=True)
    task_ref=dict(path=str(root/'jobs/001.json'));path=record/'native-intent.json'
    intent=dict(task_ref=dict(path=str(root/'jobs/002.json')) if changed=='wrong-task' else task_ref)
    if changed=='symlink':
        other=tmp_path/'other.json';c.write(other,intent);path.symlink_to(other)
    elif changed!='missing':c.write(path,intent)
    with pytest.raises(ValueError):native_execution.load_native_facts(record,task_ref=task_ref)


def _artifact_owner(tmp_path):
    root=tmp_path/'instance';work=c.workspace(root,1)
    work.mkdir(parents=True)
    job=dict(step=1,role='W',worker_view_version=1)
    record=experiment_client._record_directory_from_facts(root,1,job)
    config=dict(record_dir=str(record),scope=dict(step=1,role='W',job_ref=dict(path=str(root/'jobs/001.json'))),
        execution_interface=dict(executor='local_execution:command'),max_seconds=10)
    (record/'calls/exact-call').mkdir(parents=True)
    return config,root,work,job


@pytest.mark.parametrize('changed',['none','job','step','job_ref','record_dir','call_id'])
def test_artifact_owner_validation_is_pure_and_requires_exact_original_bindings(tmp_path,monkeypatch,changed):
    config,root,work,job=_artifact_owner(tmp_path);call_id='exact-call'
    if changed=='job':job=None
    elif changed=='step':job=dict(job,step=2)
    elif changed=='job_ref':config['scope']['job_ref']=dict(path=str(root/'jobs/002.json'))
    elif changed=='record_dir':config['record_dir']=str(root/'other-records')
    elif changed=='call_id':call_id='../other-call'
    def forbidden(*a,**kw):pytest.fail('Pure artifact validation acquired filesystem facts')
    for name in ('stat','lstat','resolve','open','mkdir'):monkeypatch.setattr(Path,name,forbidden)
    monkeypatch.setattr(c,'read',forbidden);monkeypatch.setattr(e.Records,'directory',forbidden)
    monkeypatch.setattr(views,'dynamic_root',forbidden)
    if changed=='none':
        local_experiments._validate_artifact_owner(config,call_id,root=root,work=work,job=job)
    else:
        with pytest.raises(e.IntegrityError):
            local_experiments._validate_artifact_owner(config,call_id,root=root,work=work,job=job)


@pytest.mark.parametrize('changed',['missing-call','call-symlink','retained-symlink'])
def test_artifact_producer_refuses_current_missing_or_symlinked_original_holders(tmp_path,changed):
    config,root,work,job=_artifact_owner(tmp_path)
    retained=views.dynamic_root(root,work,'retained-experiments')
    if changed=='retained-symlink':slot=retained
    else:slot=Path(config['record_dir'])/'calls/exact-call'
    slot.rmdir()
    if changed!='missing-call':
        replacement=tmp_path/'replacement';replacement.mkdir();slot.symlink_to(replacement,target_is_directory=True)
    with pytest.raises((e.IntegrityError,e.UnsupportedExecution)):
        local_experiments.artifact_directory_from_facts(config,'exact-call',root=root,work=work,job=job)


def test_local_launch_uses_owner_selected_nondefault_artifact_layout_without_reopening_originals(tmp_path,monkeypatch):
    from scidiscovery.research_loop import code_versions, project_layout
    config,root,work,job=_artifact_owner(tmp_path)
    code=work/'code';code.mkdir();source=code/'run.py';source.write_text('print("mechanical")')
    target=work/'experiments/run';target.mkdir(parents=True)
    export=tmp_path/'captured-code';export.mkdir()
    public=tmp_path/'public';public.mkdir()
    output_root=work/'artifacts/owner-selected-layout';output_root.mkdir(parents=True)
    output=output_root/'exact-call/outputs';output.mkdir(parents=True)
    command=['/usr/bin/python3',str(source)]
    accepted=dict(call_id='exact-call',command=command,source_script=str(source),script_index=1,
        code_version=dict(logical_root='code',entry='run.py'),cwd=str(target),
        experiment_directory=str(target),snapshot_files=[])
    facts=dict(root=root,work=work,target=work,job=job,material_paths={},native=dict(intent=dict(attempt=0)))
    value=dict(workspace=str(work),native_attempt=dict(number=0),policy=dict(OPTIONS,workspace='workspace-write'),
        python=command[0],runtime_roots=[],runtime_programs=[],private_roots=[],home=str(tmp_path/'session'),
        bwrap=dict(path='/exact/bwrap'),workspace_readonly=True,configuration_mounts=[],metadata=[],environment={})
    original_dynamic=views.dynamic_root;observed=[]
    def selected_root(owner_root,owner_work,name):
        assert (owner_root,owner_work,name)==(root,work,'retained-experiments')
        original_dynamic(owner_root,owner_work,name)  # Necessary mutable holder observation is allowed.
        observed.append(output_root)
        return output_root
    monkeypatch.setattr(views,'dynamic_root',selected_root)
    monkeypatch.setattr(views,'ensure_capability',lambda **kw:None)
    monkeypatch.setattr(views,'workspace_arguments',lambda *a,**kw:[])
    monkeypatch.setattr(native_execution,'_public_root',lambda *a:None)
    monkeypatch.setattr(project_layout,'validate_code',lambda project,version,**kw:version)
    monkeypatch.setattr(code_versions,'materialize',lambda *a,**kw:export)
    def forbidden(*a,**kw):pytest.fail('Launch artifact producer reopened an immutable original or ran a process')
    read=c.read
    def mutable_binding_only(path):
        assert Path(path)==views._binding(root,work)
        return read(path)
    monkeypatch.setattr(c,'read',mutable_binding_only)
    monkeypatch.setattr(c,'read_job',forbidden);monkeypatch.setattr(e,'read',forbidden)
    monkeypatch.setattr(e.Records,'accepted',forbidden)
    monkeypatch.setattr(experiment_client,'root_for_configuration',forbidden)
    monkeypatch.setattr(execution,'accepted_intents',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    def mounts(*a,**kw):
        assert kw['output']==output
        assert kw['output_mounts']==[(output,target/'artifacts')]
        return dict(mappings={},target=work,input_mappings=[],project_aliases=[])
    projection=dict(project=pi_namespace.execution_arguments_from_facts,mounts=mounts,validate_runtime=lambda *a:None)
    argv,cwd,finish=native_execution._launch_from_facts(value,config,accepted,command,str(target),
        public=public,root=root,source_facts=facts,selected_code=[],material_sources={},projection=projection)
    assert observed==[output_root] and cwd is None and finish is None
    index=argv.index(str(output))
    assert argv[index-1:index+2]==['--bind',str(output),str(target/'artifacts')]
    assert str(work/'work/retained-experiments/exact-call/outputs') not in argv


def test_pi_local_inventory_keeps_provenance_over_output_exceptions_and_skill_inodes(tmp_path,monkeypatch):
    root=tmp_path/'instance';work=c.workspace(root,1);record=execution.origin(root,1)
    (work/'work').mkdir(parents=True);(work/'artifacts').mkdir();(work/'experiments/run/artifacts').mkdir(parents=True)
    record.mkdir(parents=True)
    task=work/'science-task.json';task.write_text('raw task original')
    (work/'.git').mkdir();(work/'.git/config').write_text('metadata original')
    (root/'jobs').mkdir();(root/'jobs/001.json').write_text('private task original')
    (root/'settings.json').write_text('private settings original')
    skill=tmp_path/'skills/selected';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('selected manual')
    outside=tmp_path/'untracked';outside.write_text('external original')
    output=views.dynamic_root(root,work,'retained-experiments')/'exact-call/outputs';output.mkdir(parents=True)
    (output/'fresh').write_text('legitimate mutable output')
    for source,name in [(task,'task-alias'),(outside,'external-alias'),
            (skill/'SKILL.md','skill-alias'),(root/'jobs/001.json','private-alias'),
            (work/'.git/config','metadata-alias')]:os.link(source,output/name)
    job=dict(step=1,role='W',worker_view_version=1,formal_review={'mechanical_fixture':True},
        skill_binding=dict(root=str(skill.parent),items=['selected']))
    sources=dict(root=root,work=work,target=work,job=job,material_paths={},author=None,wake=[],
        review_code=[],policy=None,native=dict(record=record,intent={},records=(record,)))
    monkeypatch.setattr(accepted_prefix,'input_rows',lambda job:[])
    monkeypatch.setattr(experiment_client,'_input_rows',lambda job:[])
    protected=views.load_execution_original_facts(root,job,work,native=sources['native'])
    facts=views.load_pi_mount_facts(root,job,work,source_facts=sources,selected_code=[],protected_paths=[task],
        writable_output_mounts=[(output,work/'experiments/run/artifacts')],output_artifacts=output,**protected)
    aliases={target:source for source,target in facts['project_aliases']}
    for name in ('task-alias','external-alias','skill-alias','private-alias','metadata-alias'):
        assert aliases[work/'experiments/run/artifacts'/name]==output/name
    assert work/'experiments/run/artifacts/fresh' not in aliases
    assert not any('--bind'==arg for arg in views.mount_arguments_from_facts(facts))


SCIENTIFIC = '''import json,os,pathlib,sys
here=pathlib.Path.cwd()
probe=json.loads((here/'probe.json').read_text())
work=pathlib.Path(probe['work'])
for key in ('PI_CODING_AGENT_DIR','CODEX_HOME','THIN_HWA_NATIVE_HOME','PI_ROOT','PI_FIXTURE_FINAL'):
 assert key not in os.environ,key
assert os.environ['HOME']=='/task-tmp'
assert not list(pathlib.Path('/task-tmp').iterdir())
for item in probe.get('skill_files',[]):
 p=pathlib.Path(item['path'])
 assert p.read_text()==item['text']
 assert bool(p.stat().st_mode & 0o111)==item['executable']
 for action in (lambda:p.write_text('ILLEGAL'),lambda:p.chmod(0o600)):
  try:action()
  except OSError:pass
  else:raise AssertionError('captured Skill asset writable')
for name in ('private','auth','sessions','sibling'):
 assert not pathlib.Path(probe[name]).exists(),name
for name in ('science-task.json','work/task-alias','work/private-alias','work/external-alias','.git/config',*probe['project_aliases']):
 p=work/name
 for action in (lambda:p.write_text('ILLEGAL'),lambda:p.chmod(0o600)):
  try:action()
  except OSError:pass
  else:raise AssertionError('mutable original '+name)
for path in (work/'conclusion.json',work/'science-result.schema.json'):
 try:path.write_text('ILLEGAL')
 except OSError:pass
 else:raise AssertionError('absent publication writable')
import time
(here/'started').write_text('accepted captured code is running')
until=time.monotonic()+10
while not (here/'release').exists():
 assert time.monotonic()<until,'native peer did not release the captured-code barrier'
 time.sleep(.02)
source=pathlib.Path(probe['source'])
assert source.read_text().startswith('import json,os,pathlib,sys')
try:source.write_text('changed captured code')
except OSError:pass
else:raise AssertionError('captured code writable')
if probe['base']:
 base=pathlib.Path(probe['base'])
 assert not (base/'.git/HEAD').exists()
 assert (base/'data/original').read_text()=='base original'
 for p in (base/'data/original',base/'.git/HEAD'):
  try:p.write_text('ILLEGAL')
  except OSError:pass
  else:raise AssertionError('base/Git writable')
 (base/'out/legitimate').write_text('project output')
(here/'artifacts/legitimate').write_text('local output')
(here/'artifacts/result.json').write_text(json.dumps(dict(version=1,metrics=[dict(name='mechanical',value=1)],outputs=[dict(path='artifacts/legitimate')])))
print('exact-captured-local-once')
'''

MANAGED = '''
  import time
  # Like native SDK tools, bridge effects start only after the original user
  # entry has been persisted. Do not synthesize another header or usage row.
  before_tools=[json.loads(row) for row in path.read_bytes().splitlines()]
  assert len(before_tools)==2
  assert before_tools[0]['type']=='session' and before_tools[0]['id']==state['sessionId']
  assert before_tools[1]['type']=='message' and before_tools[1]['id']=='user'
  assert before_tools[1]['message']['role']=='user'
  assert before_tools[1]['message']['content']==cmd['message']
  slot=work/'work/experiment'
  def request(number,arguments):
   value=dict(submission_ref='S%03d'%number,arguments=arguments)
   temporary=slot/'peer.pending';temporary.write_text(json.dumps(value));temporary.replace(slot/'submit.json')
   until=time.monotonic()+25
   while time.monotonic()<until:
    runtime=work/'experiments/run'
    if (runtime/'started').exists() and not (runtime/'release').exists():
     source.write_text('raise AssertionError("mutable draft must never execute")\\n')
     (runtime/'release').write_text('draft changed after capture')
    try:response=json.loads((slot/'response.json').read_text())
    except (FileNotFoundError,json.JSONDecodeError):time.sleep(.02);continue
    if response.get('request')==value:return response
    time.sleep(.02)
   raise AssertionError('original host bridge did not reply')
  probe=json.loads((work/'experiments/run/probe.json').read_text())
  source=pathlib.Path(probe['source'])
  research=work/'work/research.json'
  research.write_text(json.dumps(dict(declarations=[dict(action='declare',mechanism_ref='w001.capture',
   question='Does the captured inert source remain independent of its mutable draft?',exploration=True)],
   mechanism_ref='w001.capture',experiment_question=dict(
    question='Does the original captured source complete after the native peer changes its draft?',
    change_criterion='Keep the captured output if it completes; report a capture failure otherwise.'))))
  arguments=['run','--script',str(source),'--experiment-dir',str(work/'experiments/run'),'--research',str(research)]
  assert request(1,['capabilities'])['code']==0
  response=request(2,arguments)
  assert response['code']==0,response
  assert response['result']['status']=='known_success',response
  assert source.read_text().startswith('raise AssertionError("mutable draft')
  call=response['result']['call_id']
  assert request(3,['query','--call-id',call])['result']['state']=='succeeded'
  # Nonconsecutive exact same submission must hit the existing durable cache.
  assert request(2,arguments)==response
  collected=request(4,['collect','--call-id',call]);assert collected['code']==0,collected
  assert request(5,['collect','--call-id',call])['result']==collected['result']
  for item in collected['result']['outputs']:
   p=pathlib.Path(item['path'])
   for action in (lambda:p.write_text('ILLEGAL'),lambda:p.chmod(0o600)):
    try:action()
    except OSError:pass
    else:raise AssertionError('collected original writable')
  (work/'artifacts/managed.json').write_text(json.dumps(dict(call_id=call,collected=collected['result'])))
'''


def _project(tmp_path):
    git=shutil.which('git',path=os.defpath)
    if git is None:pytest.skip('Git unavailable; project process evidence NOT RUN')
    base=tmp_path/'base'
    (base/'src').mkdir(parents=True);(base/'data').mkdir()
    (base/'src/run.py').write_text('raise AssertionError("physical base must not execute")\n')
    (base/'data/original').write_text('base original')
    for name in ('scratch','out'):(base/name).mkdir()
    env=dict(PATH=os.defpath,HOME=os.devnull,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',
        GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for args in (['init','--quiet','--template='],['add','--','src/run.py','data/original'],['commit','--quiet','-m','fixture']):
        subprocess.run([git,*args],cwd=base,env=env,check=True,capture_output=True,timeout=5)
    return dict(base_root=str(base),editable=['src'],scratch=['scratch'],outputs=['out'])


@pytest.mark.process_e2e
@pytest.mark.parametrize('project_mode',[False,True],ids=['local','project-local'])
@pytest.mark.parametrize('skills_selected',[False,True],ids=['no-skills','selected-skills'])
def test_accepted_runner_managed_local_projection_captured_code_current_collector_no_replay(tmp_path,monkeypatch,project_mode,execution_registry,skills_selected):
    require_namespace()
    agent=tmp_path/'agent';agent.mkdir();(agent/'auth.json').write_text('synthetic auth; never a credential')
    monkeypatch.setenv('PI_CODING_AGENT_DIR',str(agent))
    monkeypatch.setenv('PI_FIXTURE_ORIGINAL_HOME',os.environ['HOME'])
    monkeypatch.delenv('PI_FIXTURE_FAULT',raising=False)
    payload=dict(summary='Mechanical Local transport',scientific_result='Inert process only; not scientific qualification.',
        limitations=['No provider or solver ran.'],implementation_selection=None,stage_intent='review',
        consultation_question='Does this retained capture observation support only the stated mechanical claim?',
        mechanism_findings=[dict(mechanism='Fixture mechanism',basis='observed',finding='An inert process completed.')])
    final=' \n'+json.dumps(payload)+'\n\t'
    monkeypatch.setenv('PI_FIXTURE_FINAL',final)
    # Reuse exact RPC/session behavior, replacing only the readonly work probe
    # and adding ordinary requests to the production managed execution bridge.
    peer=FAKE_RPC.replace("work/'work/illegal'","work/'conclusion.json'").replace(
        "  final=os.environ['PI_FIXTURE_FINAL']",MANAGED+"  final=os.environ['PI_FIXTURE_FINAL']")
    launcher=tmp_path/'peer-pi';launcher.write_text(peer);launcher.chmod(0o700)
    project=_project(tmp_path) if project_mode else None
    value=options(tmp_path,launcher=[str(launcher)],experiment_mode='local',
        native_options=dict(OPTIONS,workspace='workspace-write'),max_tokens=10000,max_seconds=80,
        execution_config=dict(executor='local_execution:command',configuration=dict(run_argv=[sys.executable],max_seconds=30)),
        **(dict(project=project) if project is not None else {}))
    skill_root=None
    if skills_selected:
        from test_pi_resources import selected_library
        from scidiscovery.research_loop import skill_supply
        skill_root=selected_library(tmp_path)
        monkeypatch.setattr(skill_supply,'source_root',lambda:skill_root)
        value.update(skills=dict(sets=dict(selected=dict(items=['alpha']))),
            role_profiles={'W':dict(skill_set='selected')})
    value['public_root'].mkdir();value['objective'].write_text('Mechanical Local projection check')
    research_config.prepare(**value)
    root=value['root'];c.build_job(root,1,'W',[]);work=c.workspace(root,1)
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','pi_local_projection_fixture')
    target=work/'experiments/run';target.mkdir(parents=True)
    source=work/('project/src/run.py' if project_mode else 'code/run.py')
    source.parent.mkdir(parents=True,exist_ok=True);source.write_text(SCIENTIFIC)
    (work/'.git').mkdir(exist_ok=True);(work/'.git/config').write_text('metadata original')
    sibling=tmp_path/'sibling';sibling.write_text('unrelated original')
    for original,name in [(work/'science-task.json','task-alias'),(root/'jobs/001.json','private-alias'),(sibling,'external-alias')]:
        os.link(original,work/'work'/name)
    project_aliases=[];skill_files=[]
    if skill_root is not None:
        for original,name in [(skill_root/'alpha/SKILL.md','captured-skill-manual.md'),
                (skill_root/'alpha/scripts/run.py','captured-skill-resource.py')]:
            captured=source.parent/name;os.link(original,captured)
            skill_files.append(dict(path=str(captured),text=original.read_text(),
                executable=bool(original.stat().st_mode & 0o111)))
        for original,name in [(skill_root/'alpha/SKILL.md','selected-manual-alias'),
                (skill_root/'alpha/scripts/run.py','selected-resource-alias'),
                (skill_root/'alpha/.git/config','selected-control-alias')]:
            os.link(original,work/'work'/name);project_aliases.append('work/'+name)
    if project is not None:
        for original,name in [(Path(project['base_root'])/'.git/HEAD','base-git-alias'),
                (Path(project['base_root'])/'data/original','base-input-alias'),
                (Path(project['base_root'])/'src/run.py','physical-editable-alias')]:
            os.link(original,work/'work'/name);project_aliases.append('work/'+name)
    c.write(target/'probe.json',dict(work=str(work),source=str(source),private=str(root/'jobs/001.json'),
        auth=str(agent),sessions=str(root/'native-homes/001'),sibling=str(sibling),
        project_aliases=project_aliases,skill_files=skill_files,base=project['base_root'] if project else None))
    original_job=(root/'jobs/001.json').read_bytes();original_task=(work/'science-task.json').read_bytes()
    result=collect_separately(root,timeout=70)
    record=execution.origin(root,1)
    diagnostics=[]
    for path in (record/'native-outcome.json',record/'native.stderr',record/'runner.stderr',
            root/'host-runtime/experiment-control/001/bridge-diagnostics/latest-error.json'):
        if path.is_file():diagnostics.append(path.name+': '+path.read_text(errors='replace')[-4000:])
    assert result.returncode==0,result.stderr.decode(errors='replace')+'\n'+'\n'.join(diagnostics)
    config=c.read(record/'experiment-config.json');records=e.Records(config)
    assert config['research_strategy_version']==3
    calls=records.calls();assert len(calls)==1
    call=calls[0];accepted=records.accepted(call.name)
    assert accepted['code_version']['logical_root']==('project' if project_mode else 'code')
    binding=accepted['mechanism_binding']
    assert binding['mechanism_ref']=='w001.capture' and binding['research_binding']['exploration'] is True
    batch=c.read(work/'work/research.json')
    assert binding['research_binding']['question']==batch['declarations'][0]['question']
    assert binding['research_binding']['experiment_question']==batch['experiment_question']
    assert c.read(call/'completed.json')['status']=='known_success'
    assert (call/'stdout.raw').read_bytes()==b'exact-captured-local-once\n'
    assert c.read(work/'artifacts/managed.json')['call_id']==call.name
    assert source.read_text().startswith('raise AssertionError("mutable draft')
    assert (record/'science-output.json').read_bytes()==final.encode()
    assert c.read(record/'usage.json')==TOKENS
    session=root/'native-homes/001/sessions/pi-session.jsonl'
    assert session.read_bytes()==(agent/'session-original.bin').read_bytes()
    session_rows=[json.loads(row) for row in session.read_bytes().splitlines()]
    assert [row['type'] for row in session_rows]==['session','message','message','usage']
    assert [row['id'] for row in session_rows]==['exact-session','user','msg','shutdown']
    assert session_rows[-1]['parentId']=='msg'
    assert c.read(record/'native-outcome.json')['native_identity']['participant_scope']=='unsupported'
    assert (root/'jobs/001.json').read_bytes()==original_job
    assert (work/'science-task.json').read_bytes()==original_task
    assert sibling.read_text()=='unrelated original'
    assert (agent/'auth.json').read_text()=='synthetic auth; never a credential'
    if skill_root is not None:
        assert c.read(root/'jobs/001.json')['skill_binding']['items']==['alpha']
        assert (skill_root/'alpha/scripts/run.py').stat().st_mode & 0o111
        assert (skill_root/'alpha/SKILL.md').read_text().endswith('Manual version one.\n')
        assert (skill_root/'alpha/.git/config').read_text()=='Skill metadata control\n'
    snapshot=c.read(record/'native-execution-permissions.json')
    assert not {'native','backend_configuration','backend_permission_profile'} & snapshot.keys()
    assert not {'CODEX_HOME','PI_CODING_AGENT_DIR','PI_ROOT'} & snapshot['environment'].keys()
    completed=(call/'completed.json').read_bytes();process=(call/'process.json').read_bytes()
    assert c.read(work/'artifacts/managed.json')['collected']['status']=='collected'
    assert len(records.calls())==1 and (call/'completed.json').read_bytes()==completed and (call/'process.json').read_bytes()==process
    if project_mode:
        assert (Path(project['base_root'])/'src/run.py').read_text().startswith('raise AssertionError')
        assert (Path(project['base_root'])/'data/original').read_text()=='base original'
