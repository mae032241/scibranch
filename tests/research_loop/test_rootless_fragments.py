"""Actual launcher boundaries; no model, real or synthetic model service."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import backends,budget,checkpoint,contracts as c,continuation,controller,execution,native,profiles,runtime


@pytest.fixture
def launcher():
    repo=Path(__file__).resolve().parents[2]
    from cov_execution import launcher
    module=launcher
    return module


@pytest.mark.process_e2e
@pytest.mark.parametrize('action',['submit','--help','-h','help','status'])
def test_native_exp_warning_preserves_public_command_and_exit_status(launcher,tmp_path,action):
    executable=tmp_path/'task python'
    executable.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\nexit 7\n')
    executable.chmod(0o755)
    wrapper=tmp_path/'exp'
    wrapper.write_text(launcher.exp_wrapper(dict(task_python=str(executable),scid_python='unused'),tmp_path))
    wrapper.chmod(0o755)
    result=subprocess.run([str(wrapper),action,'--run-id','13'],text=True,capture_output=True)
    assert result.returncode==7
    assert result.stdout.splitlines()==['-B','-I','/opt/exp/exp_cli.py',action,'--run-id','13']
    if action=='status':
        assert result.stderr==''
    else:
        assert 'A next_action=deliver' in result.stderr
        assert 'does not resolve archived historical originals' in result.stderr


def policies():
    return dict(node_budget_policy={role:dict(max_observed_tokens=10000,max_seconds=60) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=False),
        worker_fragment_policy=dict(context='new_context'))


@pytest.fixture
def accepted(tmp_path,monkeypatch,request):
    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Bounded native transport fixture; no model.')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,[sys.executable],max_tokens=100000,max_seconds=120,max_steps=8,
        model='no-model',reasoning_effort='high',search_policy=None,**dict(policies(),
            worker_fragment_policy=dict(context='same_session' if getattr(request,'param',None) else 'new_context'),
            resume_policy=dict(policies()['resume_policy'],output_repair=getattr(request,'param',None)=='repair')))
    job=c.build_job(root,1,'W',[]);record=execution.record(root,1)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/001.json'),budget_slice=budget.accept_node(root,job)))
    binding=execution.identity(root,1)
    c.write(record/'runner-process.json',runtime.process_identity(os.getppid(),binding))
    c.write(record/'native-process.json',runtime.process_identity(os.getpid(),binding))
    home=execution.home(root,1);home.mkdir(parents=True)
    monkeypatch.setenv('THIN_HWA_JOB_MANIFEST',str(root/'jobs/001.json'))
    monkeypatch.setenv('THIN_HWA_ROLE','W');monkeypatch.setenv('THIN_HWA_NATIVE_HOME',str(home))
    return root,job,record


def arguments(root,record):
    settings=c.settings(root);job=c.read(root/'jobs/001.json');backend=backends.selected(settings)
    return backend.launch(profiles.for_job(settings,job),c.workspace(root,1),record/'result.pending.json',
        execution.origin(root,1)/'science-result.schema.json',job.get('native_role_instruction',c.ROLE_INSTRUCTIONS['W']))[len(backend.launcher):]


def spec(root):return dict(scene_root=str(root),model='no-model',reasoning_effort='high')


@pytest.mark.parametrize('operator,expected',[({},15),({'cov_engineering_check_reserve_seconds':0},0)])
def test_cov_launcher_reserve_reaches_real_frozen_configuration(launcher,accepted,execution_registry,operator,expected):
    """Host-only prepare/freeze: no launcher, namespace, experiment or model starts."""
    from scidiscovery.research_loop import experiments as e
    from cov_execution import lineage as cov_lineage
    root,job,record=accepted
    output=root/'host-runtime/lineage-001.json'
    cov_lineage.configure(root,job['step'],[sys.executable],root/'runs',output,
        engineering_check_reserve_seconds=launcher.engineering_reserve(operator))
    config=c.read(output)
    assert config['max_seconds']==65
    if expected:
        assert config['engineering_check_reserve_seconds']==15
    else:
        assert 'engineering_check_reserve_seconds' not in config
    adapter=e.load(config,path=output)
    check=next(item for item in adapter.capabilities() if item.key=='isolated_replay')
    assert check.public_summary['requested_reserve_seconds']==expected
    assert check.public_summary['total_call_seconds']==65
    assert c.read(record/'experiment-config.json')==config
    assert not (c.workspace(root,job['step'])/'work/lineage/calls').exists()
    wrapper=launcher.exp_wrapper(dict(operator,task_python='/task/python',scid_python='/host/python'),c.workspace(root,job['step']))
    assert launcher.engineering_notice(operator) in wrapper
    if expected:
        assert 'Experiment at most 50s; check at most 10s; feedback 2s.' in wrapper
    else:
        assert 'No reserved check window' in wrapper


def test_cov_launcher_rejects_unusable_reserve_and_keeps_unsupported_channel_neutral(launcher):
    for value in (True,False,-1,2,65,66,float('inf'),float('nan')):
        with pytest.raises(ValueError,match='cov_check_reserve_requires'):
            launcher.engineering_reserve(dict(cov_engineering_check_reserve_seconds=value))
    notice=launcher.engineering_notice(dict(cov_receipt_channel='backend_file'))
    assert 'no isolated check capability; no partition is promised' in notice
    assert 'Experiment at most' not in notice


def next_context(root,job,first,monkeypatch):
    payload=dict(summary='Original contribution',scientific_result='The target remains unresolved.',limitations=['No model.'],
        implementation_selection=None,mechanism_findings=None,stage_intent='continue')
    c.write(first/'science-output.json',payload)
    meter=dict(input_tokens=10,cached_input_tokens=0,output_tokens=0);c.write(first/'usage.json',meter)
    intent=c.read(first/'native-intent.json')
    c.write(first/'native-outcome.json',dict(returncode=0,stop_reason=None,cleanup='observed_group_cleaned',
        raw_sha256=c.digest(first/'science-output.json'),usage=native.usage(root),
        budget_slice=dict(budget.node_report(root,job,intent,meter),usage_observed=True)))
    checkpoint.capture(root,job,first)
    record=first/'continuations/001'
    home=root/'native-homes/001/context-001';home.mkdir(parents=True)
    c.write(record/'native-intent.json',dict(step=1,attempt=1,continuation_kind='fragment',context_mode='new_context',
        native_home=str(home),task_sha256=c.digest(root/'jobs/001.json'),previous=execution.proof(first),
        root_intent_sha256=c.digest(first/'native-intent.json'),budget_slice=budget.accept_continuation(root,job,[first]),
        fragment_checkpoint=dict(path=(first/'checkpoint.json').relative_to(root).as_posix(),sha256=c.digest(first/'checkpoint.json'))))
    binding=execution.identity(root,1,1)
    c.write(record/'runner-process.json',runtime.process_identity(os.getppid(),binding))
    c.write(record/'native-process.json',runtime.process_identity(os.getpid(),binding))
    monkeypatch.setenv('THIN_HWA_NATIVE_HOME',str(home))
    return record


def test_exact_new_context_uses_frozen_origin_schema(launcher,accepted,monkeypatch):
    root,job,first=accepted;record=next_context(root,job,first,monkeypatch)
    result=launcher.native_args(spec(root),c.workspace(root,1),arguments(root,record))
    assert result[result.index('--output-last-message')+1]==str(record/'result.pending.json')
    assert result[result.index('--output-schema')+1]==str(first/'science-result.schema.json')
    assert '--sandbox' not in result
    assert '--ignore-user-config' not in result
    assert not any(value.startswith('sandbox_workspace_write=') for value in result)
    assert 'project_doc_max_bytes=0' in result and 'features.multi_agent=false' in result


@pytest.mark.parametrize('damage',['unknown','instructions','developer','network','web','subagents','workspace','duplicate','missing','ignore_config'])
def test_current_backend_permission_overrides_are_exact(launcher,accepted,damage):
    root,job,record=accepted;args=arguments(root,record)
    if damage=='unknown':args[-1:-1]=['-c','unknown_setting=true']
    elif damage=='duplicate':args[-1:-1]=['-c','project_doc_max_bytes=0']
    elif damage=='missing':
        index=args.index('project_doc_max_bytes=0');del args[index-1:index+1]
    elif damage=='ignore_config':args.remove('--ignore-user-config')
    elif damage=='workspace':args[args.index('--sandbox')+1]='danger-full-access'
    else:
        key,value={'instructions':('project_doc_max_bytes','project_doc_max_bytes=1'),
            'developer':('developer_instructions','developer_instructions="Changed role instruction"'),
            'network':('sandbox_workspace_write','sandbox_workspace_write={"network_access"=true}'),
            'web':('web_search','web_search="live"'),'subagents':('features.multi_agent','features.multi_agent=true')}[damage]
        index=next(i for i,item in enumerate(args) if item.startswith(key+'='));args[index]=value
    before=(record/'native-intent.json').read_bytes()
    with pytest.raises(ValueError,match='native_config_override_rejected|complete_role_profile_config_required|unexpected_legacy_sandbox'):
        launcher.native_args(spec(root),c.workspace(root,1),args)
    assert (record/'native-intent.json').read_bytes()==before


@pytest.mark.parametrize('change',[dict(network=True),dict(web='live'),dict(subagents=True),dict(workspace='read-only')])
def test_cov_namespace_rejects_unsupported_frozen_native_permissions(launcher,accepted,change):
    root,job,record=accepted
    settings=c.settings(root);settings['native_options'].update(change)
    c.write(root/'settings.json',settings);c.write(root/'settings-identity.json',dict(sha256=c.digest(root/'settings.json')))
    args=arguments(root,record)
    with pytest.raises(ValueError,match='native_permissions_not_supported_by_cov_launcher'):
        launcher.native_args(spec(root),c.workspace(root,1),args)


@pytest.mark.parametrize('damage',['old_attempt','wrong_name','arbitrary_subdir','wrong_home','wrong_role','wrong_manifest','unknown_other_pid','terminal'])
def test_nonoriginal_dispatch_rejected_before_launcher_writes(launcher,accepted,monkeypatch,damage):
    root,job,first=accepted;record=next_context(root,job,first,monkeypatch);args=arguments(root,record)
    output=args.index('--output-last-message')+1
    if damage=='old_attempt':args[output]=str(first/'result.pending.json')
    elif damage=='wrong_name':args[output]=str(record/'native-intent.json')
    elif damage=='arbitrary_subdir':args[output]=str(first/'other/result.pending.json')
    elif damage=='wrong_home':monkeypatch.setenv('THIN_HWA_NATIVE_HOME',str(root/'native-homes/001'))
    elif damage=='wrong_role':monkeypatch.setenv('THIN_HWA_ROLE','A')
    elif damage=='wrong_manifest':monkeypatch.setenv('THIN_HWA_JOB_MANIFEST',str(root/'jobs/002.json'))
    elif damage=='unknown_other_pid':
        value=c.read(record/'native-process.json');value['pid']=os.getppid();c.write(record/'native-process.json',value)
    else:c.write(record/'native-outcome.json',dict(returncode=1))
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):launcher.native_args(spec(root),c.workspace(root,1),args)
    assert {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before


THREAD='11111111-1111-4111-8111-111111111111'


def next_session(root,job,first,monkeypatch,*,repair=False):
    home=execution.home(root,1);session=home/'sessions/current.jsonl';session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=THREAD,cwd=str(c.workspace(root,1)))))+'\n')
    (first/'native.jsonl').write_text(json.dumps(dict(type='thread.started',thread_id=THREAD))+'\n')
    payload=dict(summary='Existing work',scientific_result='Unresolved target',limitations=['No model.'],
        implementation_selection=None,mechanism_findings=None,stage_intent='continue')
    if repair:payload.pop('scientific_result')
    c.write(first/'science-output.json',payload)
    meter=dict(input_tokens=10,cached_input_tokens=0,output_tokens=0);c.write(first/'usage.json',meter)
    original=c.read(first/'native-intent.json')
    c.write(first/'native-outcome.json',dict(returncode=0,stop_reason=None,cleanup='observed_group_cleaned',
        thread_id=THREAD,raw_sha256=c.digest(first/'science-output.json'),usage=native.usage(root),
        budget_slice=dict(budget.node_report(root,job,original,meter),usage_observed=True)))
    if not repair:checkpoint.capture(root,job,first)
    reference=continuation.session(root,1,first)
    record=first/'continuations/001'
    value=dict(step=1,attempt=1,continuation_kind='repair' if repair else 'fragment',context_mode='same_session',
        task_sha256=c.digest(root/'jobs/001.json'),previous=execution.proof(first),session=reference,
        root_intent_sha256=c.digest(first/'native-intent.json'),budget_slice=budget.accept_continuation(root,job,[first]))
    if repair:value['output_repair']=continuation.output_request(root,1,first,source_attempt=0)
    else:value['fragment_checkpoint']=dict(path=(first/'checkpoint.json').relative_to(root).as_posix(),sha256=c.digest(first/'checkpoint.json'))
    c.write(record/'native-intent.json',value)
    binding=execution.identity(root,1,1)
    c.write(record/'runner-process.json',runtime.process_identity(os.getppid(),binding))
    c.write(record/'native-process.json',runtime.process_identity(os.getpid(),binding))
    monkeypatch.setenv('THIN_HWA_NATIVE_HOME',str(home))
    return record


def resume_arguments(root,record):
    settings=c.settings(root);job=c.read(root/'jobs/001.json');backend=backends.selected(settings)
    args,_=backend.resume([*backend.launcher,*arguments(root,record)],profiles.for_job(settings,job),c.workspace(root,1),
        record/'result.pending.json',execution.origin(root,1)/'science-result.schema.json',dict(thread_id=THREAD))
    # Custom launchers receive the exact retained schema from the namespace.
    args[-2:-2]=['--output-schema',str(execution.origin(root,1)/'science-result.schema.json')]
    return args[len(backend.launcher):]


@pytest.mark.parametrize('accepted',['fragment','repair'],indirect=True)
@pytest.mark.parametrize('explicit_schema',[False,True])
def test_exact_accepted_same_session_command(launcher,accepted,monkeypatch,request,explicit_schema):
    root,job,first=accepted
    record=next_session(root,job,first,monkeypatch,repair=request.node.callspec.params['accepted']=='repair')
    args=resume_arguments(root,record)
    if not explicit_schema:
        index=args.index('--output-schema');del args[index:index+2]
    result=launcher.native_args(spec(root),c.workspace(root,1),args)
    assert result.count('resume')==1 and result[-2:]==[THREAD,'-']
    assert result.count('--output-schema')==1 and '--sandbox' not in result
    assert result[result.index('--output-last-message')+1]==str(record/'result.pending.json')
    assert execution.home(root,1,1)==execution.home(root,1,0)


@pytest.mark.parametrize('accepted',['fragment'],indirect=True)
@pytest.mark.parametrize('damage',['last','wrong_thread','wrong_home','header','schema','schema_transport','missing_transport','fresh_shape',
    'old_output','unknown_pid','terminal','root_intent'])
def test_resumed_dispatch_rejects_identity_changes_without_writes(launcher,accepted,monkeypatch,damage):
    root,job,first=accepted;record=next_session(root,job,first,monkeypatch);args=resume_arguments(root,record)
    if damage=='last':args[-2]='--last'
    elif damage=='wrong_thread':args[-2]='22222222-2222-4222-8222-222222222222'
    elif damage=='wrong_home':monkeypatch.setenv('THIN_HWA_NATIVE_HOME',str(root/'other-home'))
    elif damage=='header':
        session=execution.home(root,1)/'sessions/current.jsonl'
        session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=THREAD,cwd=str(c.workspace(root,1)),changed=True)))+'\n')
    elif damage=='schema':args[args.index('--output-schema')+1]=str(record/'science-result.schema.json')
    elif damage in {'schema_transport','missing_transport'}:
        at=args.index('--output-schema');del args[at:at+2]
        settings=c.settings(root)
        if damage=='schema_transport':settings['native_capabilities']['response_schema_transport']='native_flag'
        else:settings['native_capabilities'].pop('response_schema_transport')
        c.write(root/'settings.json',settings);c.write(root/'settings-identity.json',dict(sha256=c.digest(root/'settings.json')))
    elif damage=='fresh_shape':args=arguments(root,record)
    elif damage=='old_output':args[args.index('--output-last-message')+1]=str(first/'result.pending.json')
    elif damage=='unknown_pid':
        value=c.read(record/'native-process.json');value['pid']=os.getppid();c.write(record/'native-process.json',value)
    elif damage=='terminal':c.write(record/'native-outcome.json',dict(returncode=0))
    else:
        value=c.read(record/'native-intent.json');value['root_intent_sha256']='0'*64;c.write(record/'native-intent.json',value)
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):launcher.native_args(spec(root),c.workspace(root,1),args)
    assert {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
