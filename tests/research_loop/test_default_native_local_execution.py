"""Default local execution owns its receiver; only model communication is fake."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

from scidiscovery.research_loop import budget, contracts as c, execution, experiments as e, native, runtime
from scidiscovery.research_loop import controller
from test_history_handoff import finish


def actual_native():
    entry=shutil.which('codex')
    package=Path(entry).resolve().parent.parent if entry else Path('/unavailable')
    vendor=package/'node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex'
    actual=Path(os.environ.get('SCID_TEST_CODEX_NATIVE',str(vendor)))
    if 'SCID_TEST_CODEX_NATIVE' in os.environ:
        assert actual.is_file() and shutil.which('bwrap'), 'Explicit real-native verification requirements are missing'
    if not actual.is_file() or shutil.which('bwrap') is None:
        pytest.skip('Actual installed native sandbox and bwrap are required')
    return actual.resolve()


@pytest.mark.parametrize('standard',[False,True],ids=['custom-launcher','named-permissions'])
@pytest.mark.process_e2e
def test_unproven_native_permissions_refuse_managed_launch_without_overrides(tmp_path, standard):
    from scidiscovery.research_loop import native_execution
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Inert permission negative control.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['custom-launcher'],experiment_mode='local',
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('default_permissions="named"\n[permissions.named.network]\nenabled=true\n')
    arguments=['custom-launcher','exec','-']
    applied,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=standard)
    assert applied==arguments
    with pytest.raises(e.UnsupportedExecution,match='unsupported'):
        launch.check_available()
    snapshot=c.read(record/'native-execution-permissions.json')
    scope=dict(job_ref=snapshot['task_ref'])
    accepted=dict(native_attempt=snapshot['native_attempt'],scope=scope)
    with pytest.raises(e.UnsupportedExecution,match='unsupported'):
        launch(dict(scope=scope),accepted,['must-never-be-spawned'],str(work))
    assert not list(record.glob('**/process.json'))


@pytest.mark.parametrize('overlap',['control-ancestor','native-home-ancestor','native-home-child','runtime','temporary-mount'])
@pytest.mark.process_e2e
def test_declared_public_root_overlap_refuses_before_scientific_launch(tmp_path,monkeypatch,overlap):
    from scidiscovery.research_loop import native_execution
    actual=actual_native()
    public=tmp_path/'public';public.mkdir()
    home=tmp_path/'home';home.mkdir()
    if overlap=='control-ancestor':public=tmp_path
    elif overlap=='native-home-ancestor':home=public/'home';home.mkdir()
    elif overlap=='native-home-child':public=home/'public';public.mkdir()
    elif overlap=='runtime':public=Path('/usr')
    elif overlap=='temporary-mount':public=Path('/tmp')
    objective=tmp_path/'objective.md';objective.write_text('Inert public namespace negative control.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(actual)],experiment_mode='local',
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=false\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    arguments=[str(actual),'exec','--sandbox','workspace-write','-C',str(work),'-']
    _,launch=native_execution.prepare(root,1,record,work,home,arguments,standard=True)
    snapshot=c.read(record/'native-execution-permissions.json')
    assert 'public root overlaps' in snapshot['unsupported']
    scope=dict(job_ref=snapshot['task_ref'])
    accepted=dict(native_attempt=snapshot['native_attempt'],scope=scope)
    with pytest.raises(e.UnsupportedExecution,match='public root overlaps'):
        launch(dict(scope=scope),accepted,['must-never-be-spawned'],str(work))
    assert not list(record.glob('**/process.json'))


@pytest.mark.parametrize('exclude_tmpdir,exclude_slash_tmp,tmpdir,workspace,expected',[
    (True,False,'/work/cache','/work',{}),
    (True,False,'/tmp/cache','/work',{}),
    (False,True,'/tmp','/work',{}),
    (False,True,'/tmp/cache','/work',{':slash_tmp':'read'}),
    (True,False,'/separate/cache','/work',{':tmpdir':'read'}),
    (True,True,'/tmp/cache','/tmp/work',{':tmpdir':'read',':slash_tmp':'read'}),
])
def test_tmp_exclusions_preserve_other_legacy_write_roots(exclude_tmpdir,exclude_slash_tmp,tmpdir,workspace,expected):
    from scidiscovery.research_loop import native_execution
    policy=dict(network_access=False,exclude_tmpdir_env_var=exclude_tmpdir,exclude_slash_tmp=exclude_slash_tmp)
    profile=native_execution._profile(policy,Path(workspace),tmpdir,virtual=True)
    assert profile['filesystem']==expected
    assert profile['extends']==':workspace' and profile['network']==dict(enabled=False)


def test_tmp_exclusions_use_each_native_namespace_and_relative_native_tmpdir(tmp_path):
    from scidiscovery.research_loop import native_execution
    work=tmp_path/'work';work.mkdir()
    policy=dict(network_access=True,exclude_tmpdir_env_var=True,exclude_slash_tmp=False)
    # The native TMPDIR inherits a surviving workspace grant; the scientific
    # backend's independent /task-tmp does not. The declaration stays identical.
    parent=native_execution._profile(policy,work,'scratch')
    backend=native_execution._profile(policy,work/'experiments/sentinel','/task-tmp',virtual=True)
    assert parent['filesystem']=={}
    assert backend['filesystem']=={':tmpdir':'read'}
    assert parent['network']==backend['network']==dict(enabled=True)


@pytest.mark.parametrize('resume',[False,True],ids=['exec-parser','exec-resume-parser'])
@pytest.mark.process_e2e
def test_real_exec_parser_accepts_the_applied_legacy_permission_arguments(tmp_path,monkeypatch,resume):
    from scidiscovery.research_loop import native_execution
    actual=actual_native()
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('No-provider parser check.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(actual)],max_tokens=1000,max_seconds=30,max_steps=2,
        model='resume',reasoning_effort='high',search_policy=None)
    c.build_job(root,1,'W',[]);work=c.workspace(root,1);record=execution.origin(root,1)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json')))
    home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=false\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    arguments=[str(actual),'exec','--sandbox','workspace-write','-C',str(work)]
    resume_position=len(arguments) if resume else None
    if resume:arguments.extend(['resume','--json','--model','resume','11111111-1111-4111-8111-111111111111'])
    else:arguments.extend(['--model','resume'])
    arguments.append('-')
    applied,_=native_execution.prepare(root,1,record,work,home,arguments,standard=True,resume_position=resume_position)
    assert '-P' not in applied and '--sandbox' in applied
    assert applied[applied.index('--model')+1]=='resume'
    if resume:
        override=next(index for index,value in enumerate(applied) if value.startswith('sandbox_workspace_write='))
        assert override<applied.index('resume')
    # Help exits in the real argument parser before model/auth communication.
    parsed=subprocess.run([*applied[:-1],'--help'],env=dict(os.environ),capture_output=True,text=True,timeout=10)
    assert parsed.returncode==0,(parsed.stdout,parsed.stderr)
    assert 'Usage:' in parsed.stdout


# These G1 nodes inspect metadata and argv without executing Git or a backend.
def _fixed_local_code(tmp_path,monkeypatch,*,directory=True):
    """Source/argv fixture only; no Git, namespace or backend is executed."""
    from scidiscovery.research_loop import local_experiments, protocol
    def forbidden(*args,**kwargs):pytest.fail('Pure G1 argv fixture reached a process')
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Engineering argv inspection only.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['argv-only-launcher'],
        max_tokens=1000,max_seconds=30,max_steps=2,model='no-model',reasoning_effort='high',search_policy=None)
    # The existing task producer owns the empty accepted prefix and input facts.
    # This creates no native intent, qualification verdict or execution call.
    job=c.build_job(root,1,'W',[]);work=c.workspace(root,1)
    scope=protocol.stage_scope(root,job)
    code=work/'code';code.mkdir()
    source=code/'run.py';source.write_text("print('fixed source')\n")
    if directory:
        (code/'helper.py').write_text("VALUE='fixed helper'\n")
        (code/'resource.txt').write_text('fixed resource')
    call_id='0001-source-argv'
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',
        entry='run.py',selection='directory' if directory else 'file',logical_root='code')
    export=root/'host-runtime/code-versions/exports/001'/call_id/'tree';export.mkdir(parents=True)
    for name in ('run.py','helper.py','resource.txt') if directory else ('run.py',):
        (export/name).write_bytes((code/name).read_bytes())
    body=dict(run_argv=[sys.executable],max_seconds=30,cwd=str(work),scope=scope,
        record_dir=str(root/'host-runtime/experiment-control/001/records'))
    interface=dict(version=1,executor='local_execution:command',config_record=body)
    adapter=local_experiments.LocalExecution(body,frozen_interface=interface)
    payload=e.LocalFileDescriptor(name=call_id,local_path=str(export/'run.py'),
        size_bytes=(export/'run.py').stat().st_size,media_type='application/octet-stream')
    e.Records(body).root.joinpath('calls',call_id).mkdir(parents=True)
    runtime_cwd=work/'experiments/sentinel' if directory else work
    runtime_cwd.mkdir(parents=True,exist_ok=True)
    options=dict(source_script=str(source),arguments=['explicit-argument'])
    if directory:options['experiment_directory']=str(runtime_cwd)
    return root,work,adapter,payload,version,export,source,runtime_cwd,options


def _local_code_launch_inputs(tmp_path,fixed):
    from scidiscovery.research_loop import experiment_client, local_experiments, native_execution
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    facts=experiment_client.load_client_facts(adapter.config)
    metadata=adapter.acceptance_metadata(payload,command_options=options,_code_version=version,client_facts=facts)
    accepted=dict(metadata,call_id=payload.name,scope=adapter.config['scope'],code_version=version,snapshot_files=[])
    config=dict(adapter.config,execution_interface=adapter.frozen_interface)
    public=tmp_path/'public'
    home=tmp_path/'native-home';home.mkdir();(home/'config.toml').write_text('')
    policy=dict(writable_roots=[],network_access=False,exclude_tmpdir_env_var=False,exclude_slash_tmp=False)
    value=dict(workspace=str(work),home=str(home),python=sys.executable,runtime_roots=list(dict.fromkeys((sys.base_prefix,sys.prefix))),
        native_attempt=dict(number=0),
        bwrap=dict(path='/usr/bin/bwrap'),native=dict(path='/usr/bin/true'),policy=policy,
        backend_configuration=e.proof(home/'config.toml'),
        backend_permission_profile=dict(configuration=native_execution._profile(policy,cwd,'/task-tmp',virtual=True)))
    if 'experiment_directory' in accepted:
        local_experiments.artifact_directory(config,payload.name).mkdir(parents=True)
    return value,config,accepted,public


def test_local_code_metadata_uses_fixed_payload_and_frozen_runtime_inputs(tmp_path,monkeypatch):
    from scidiscovery.research_loop import experiment_client, local_experiments
    root,work,adapter,payload,version,export,source,cwd,options=_fixed_local_code(tmp_path,monkeypatch)
    from test_mutable_run_inputs import _frozen_material
    logical=cwd/'data.txt'
    material,_,original=_frozen_material(root,work,logical,name='local-argv-data')
    reference=e.proof(original)
    outside,_,_=_frozen_material(root,work,work/'inputs/outside.txt',name='local-outside-data')
    job=c.read(root/'jobs/001.json');job['global_materials'].extend([material,outside])
    c.write(root/'jobs/001.json',job)
    (cwd/'scratch').mkdir();(cwd/'scratch/local.txt').write_text('runtime scratch')
    (cwd/'stdout.log').write_text('runtime log')
    for name in ('artifacts','.git','.agents','.codex','.aws'):
        (cwd/name).mkdir(exist_ok=True);(cwd/name/'local.txt').write_text('runtime state')
    source.unlink()
    load_facts=experiment_client.load_client_facts
    facts=load_facts(adapter.config)
    requested=[];input_reference=experiment_client._observe_input_reference
    def frozen_reference(config,path,*,facts):
        requested.append(Path(path));assert Path(path)==logical
        assert facts is owner_facts
        return input_reference(config,path,facts=facts)
    owner_facts=facts
    def no_inventory(*args,**kwargs):
        pytest.fail('A code version must not inventory its runtime directory')
    monkeypatch.setattr(experiment_client,'_observe_input_reference',frozen_reference)
    monkeypatch.setattr(experiment_client,'load_client_facts',no_inventory)
    monkeypatch.setattr(local_experiments.LocalExecution,'files',no_inventory)
    metadata=adapter.acceptance_metadata(payload,command_options=options,_code_version=version,client_facts=facts)
    assert requested==[logical]
    assert metadata['script']==e.proof(export/'run.py')
    assert metadata['source_script']==str(source)
    assert metadata['command']==[sys.executable,str(source),'explicit-argument']
    assert metadata['cwd']==metadata['experiment_directory']==str(cwd)
    assert metadata['snapshot_files']==[dict(reference,name='data.txt')]
    assert not {'code_version','entry','source_reference','source_input'}&metadata.keys()
    # Readonly scientific material cannot be relabeled as writable artifacts.
    excluded,_,_=_frozen_material(root,work,cwd/'artifacts/frozen.txt',name='local-output-data')
    job['global_materials'].append(excluded);c.write(root/'jobs/001.json',job)
    rejected_facts=load_facts(adapter.config)
    with pytest.raises(ValueError,match='artifacts overlap'):
        adapter.acceptance_metadata(payload,command_options=options,_code_version=version,client_facts=rejected_facts)


@pytest.mark.parametrize('draft',['missing','changed'])
@pytest.mark.parametrize('directory',[False,True],ids=['single-code-file','whole-code-directory'])
def test_local_code_native_argv_uses_whole_fixed_export_and_independent_cwd(tmp_path,monkeypatch,directory,draft):
    from scidiscovery.research_loop import code_versions, local_experiments, native_execution, shared_input_views, experiment_client
    fixed=_fixed_local_code(tmp_path,monkeypatch,directory=directory)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    source.unlink()
    if draft=='changed':source.write_text("raise AssertionError('mutable draft must not run')\n")
    (source.parent/'new-draft-sibling.py').write_text("raise AssertionError('draft sibling')\n")
    def no_draft_reference(*args,**kwargs):
        pytest.fail('A code-version launch must not resolve its mutable logical source')
    materialized=[]
    def fixed_export(instance,**kwargs):
        materialized.append((instance,{key:item for key,item in kwargs.items() if key!='context'}))
        return export
    monkeypatch.setattr(experiment_client,'_observe_input_reference',no_draft_reference)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/usr/bin/bwrap')
    monkeypatch.setattr(code_versions,'materialize',fixed_export)
    argv,host_cwd,finish=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert materialized==[(root,dict(step=1,call_id=payload.name,code_version=version))]
    bindings=[argv[index:index+3] for index,argument in enumerate(argv) if argument in ('--bind','--ro-bind')]
    code_bindings=[(index,row) for index,row in enumerate(bindings) if row[2]==str(work/'code')]
    last_code_index,last_code_binding=code_bindings[-1]
    assert last_code_binding==['--ro-bind',str(export),str(work/'code')]
    assert all(row[0]=='--ro-bind' for _,row in code_bindings)
    assert not any(Path(row[2]).is_relative_to(work/'code') for row in bindings[last_code_index+1:])
    assert not any(row[2]==str(source) for row in bindings)
    assert argv[-len(accepted['command']):]==[sys.executable,str(source),'explicit-argument']
    assert argv[argv.index('--chdir')+1]==str(cwd)
    assert host_cwd is finish is None
    assert (export/'run.py').read_text()=="print('fixed source')\n"
    assert not (export/'new-draft-sibling.py').exists()
    for name in ('.git','.agents','.codex','.aws'):
        assert (cwd/name).is_dir() and not (export/name).exists()
    if directory:
        assert ['--bind',str(local_experiments.artifact_directory(config,payload.name)),str(cwd/'artifacts')] in bindings
        assert (export/'helper.py').read_text()=="VALUE='fixed helper'\n"
        assert (export/'resource.txt').read_text()=='fixed resource'
        assert not (export/'artifacts').exists()


@pytest.mark.parametrize('directory',[False,True],ids=['single-code-file','whole-code-directory'])
def test_local_code_native_argv_masks_a_replaced_draft_entry_symlink(tmp_path,monkeypatch,directory):
    """The readonly code tree masks a mutable entry without reading its target."""
    from scidiscovery.research_loop import code_versions, native_execution, shared_input_views, experiment_client
    fixed=_fixed_local_code(tmp_path,monkeypatch,directory=directory)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    private=root/'private.txt';private.write_text('host private fixture')
    source.unlink();source.symlink_to(private)
    (source.parent/'new-draft-sibling.py').write_text("raise AssertionError('draft sibling')\n")
    materialized=[]
    def fixed_export(instance,**kwargs):
        materialized.append((instance,{key:item for key,item in kwargs.items() if key!='context'}))
        return export
    def forbidden(*args,**kwargs):
        pytest.fail('Fixed code argv must not read its mutable entry or start a process')
    monkeypatch.setattr(experiment_client,'_observe_input_reference',forbidden)
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/argv-only/bwrap')
    monkeypatch.setattr(code_versions,'materialize',fixed_export)
    argv,host_cwd,finish=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert materialized==[(root,dict(step=1,call_id=payload.name,code_version=version))]
    bindings=[argv[index:index+3] for index,argument in enumerate(argv) if argument in ('--bind','--ro-bind')]
    code_bindings=[(index,row) for index,row in enumerate(bindings) if row[2]==str(work/'code')]
    last_code_index,last_code_binding=code_bindings[-1]
    assert last_code_binding==['--ro-bind',str(export),str(work/'code')]
    assert all(row[0]=='--ro-bind' for _,row in code_bindings)
    assert not any(Path(row[2]).is_relative_to(work/'code') for row in bindings[last_code_index+1:])
    assert not any(row[2]==str(source) for row in bindings)
    assert argv[-len(accepted['command']):]==[sys.executable,str(source),'explicit-argument']
    assert argv[argv.index('--chdir')+1]==str(cwd) and host_cwd is finish is None
    assert source.is_symlink() and private.read_text()=='host private fixture'
    assert (export/'run.py').read_text()=="print('fixed source')\n"
    assert not (export/'new-draft-sibling.py').exists()


@pytest.mark.parametrize('changed',['logical-source','working-directory','code-pin','scientific-mount','artifact-alias'])
def test_local_code_native_argv_refuses_changed_bindings_before_launch(tmp_path,monkeypatch,changed):
    from scidiscovery.research_loop import code_versions, native_execution, shared_input_views
    fixed=_fixed_local_code(tmp_path,monkeypatch)
    root,work,adapter,payload,version,export,source,cwd,options=fixed
    value,config,accepted,public=_local_code_launch_inputs(tmp_path,fixed)
    monkeypatch.setattr(shared_input_views,'ensure_capability',lambda **kwargs:'/usr/bin/bwrap')
    monkeypatch.setattr(code_versions,'materialize',lambda *args,**kwargs:export)
    baseline=native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert str(export) in baseline[0]
    expected=e.IntegrityError
    def unavailable_export(*args,**kwargs):
        # The real mount owner discovers this alias after the fixed tree exists.
        if changed=='scientific-mount':return export
        if changed!='code-pin':pytest.fail('Invalid logical bindings must fail before code materialization')
        assert kwargs['code_version']==accepted['code_version']
        raise ValueError('Original code pin differs')
    monkeypatch.setattr(code_versions,'materialize',unavailable_export)
    if changed=='logical-source':
        accepted['source_script']=str(work/'code/other.py')
    elif changed=='working-directory':
        accepted['cwd']=str(work/'code');expected=e.UnsupportedExecution
    elif changed=='code-pin':
        accepted['code_version']=dict(version,commit='b'*40)
    elif changed=='scientific-mount':
        original=root/'frozen-data.txt';original.write_text('fixed scientific input')
        job=c.read(root/'jobs/001.json')
        job['global_materials'].append(dict(path='code/helper.py',source=str(original),size_bytes=original.stat().st_size))
        c.write(root/'jobs/001.json',job)
        expected=e.UnsupportedExecution
    elif changed=='artifact-alias':
        (cwd/'artifacts').rmdir();(cwd/'artifacts').symlink_to(work/'code');expected=e.UnsupportedExecution
    with pytest.raises(expected):
        native_execution._launch(value,config,accepted,accepted['command'],accepted['cwd'],public=public,root=root)
    assert (export/'run.py').read_text()=="print('fixed source')\n"
    assert all((cwd/name).is_dir() for name in ('.git','.agents','.codex','.aws'))
