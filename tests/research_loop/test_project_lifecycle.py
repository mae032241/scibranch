"""Bounded project drafts and actual Local/native sandbox lifecycle.

H/W author session records are synthetic. The process case uses real Git,
installed native sandbox ELF and the actual _launch/provider output lifecycle;
it invokes no model, SSH transport or solver.
"""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (code_versions, contracts as c, controller,
    experiments as e, local_experiments, native_execution, shared_input_views,
    worker_execution_bridge as bridge)
from test_history_handoff import finish


_PAYLOAD = b'''from pathlib import Path
base=Path(__file__).parent.parent
assert Path.cwd()==base
assert (base/'config.txt').read_text()=='readonly base\\n'
assert not (base/'untracked.txt').exists()
for relative in ('config.txt','src/main.py','tools/helper.py','empty/new','undeclared'):
 try:(base/relative).write_text('forbidden')
 except OSError:pass
 else:raise AssertionError(relative)
(base/'build/temporary').write_text('scratch')
(base/'out/kept.txt').write_bytes(b'exact original call output\\n')
'''


def _native_sandbox():
    return Path(os.environ.get('SCID_TEST_NATIVE_SANDBOX',
        '/home/da/.nvm/versions/node/v25.2.1/lib/node_modules/@openai/codex/node_modules/'
        '@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex'))


def _base(tmp_path):
    base=tmp_path/'base'
    for name in ('src','tools','empty','data'):(base/name).mkdir(parents=True)
    (base/'src/main.py').write_bytes(_PAYLOAD)
    (base/'tools/helper.py').write_bytes(b'VALUE = 1\n');(base/'tools/helper.py').chmod(0o755)
    (base/'config.txt').write_bytes(b'readonly base\n')
    (base/'data/context.txt').write_bytes(b'second readonly context\n')
    git=shutil.which('git',path=os.defpath)
    assert git
    environment=dict(PATH=os.defpath,HOME=os.devnull,XDG_CONFIG_HOME=os.devnull,
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,LC_ALL='C',
        GIT_AUTHOR_NAME='Fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',
        GIT_COMMITTER_NAME='Fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for arguments in [('init','--quiet','--object-format=sha1','--template='),
            ('add','--','src/main.py','tools/helper.py','config.txt','data/context.txt'),
            ('commit','--quiet','-m','Exact project lifecycle fixture')]:
        subprocess.run([git,'-c','core.hooksPath='+os.devnull,*arguments],cwd=base,env=environment,
            check=True,capture_output=True,timeout=5)
    (base/'untracked.txt').write_bytes(b'never expose or copy\n')
    return dict(base_root=str(base),editable=['src','tools','empty'],scratch=['build'],outputs=['out'])


@pytest.fixture
def project_instance(tmp_path,monkeypatch,request):
    dummy=tmp_path/'codex'
    dummy.write_text('# Synthetic identity only; this fixture never executes this launcher.\n')
    dummy.chmod(0o700)
    launcher=[str(dummy)]
    if getattr(request,'param',None)=='native':
        native=_native_sandbox()
        if not native.is_absolute() or not native.is_file() or not os.access(native,os.X_OK):
            pytest.skip('Exact native sandbox ELF is unavailable; set SCID_TEST_NATIVE_SANDBOX')
        with native.open('rb') as stream:
            if stream.read(4)!=b'\x7fELF':pytest.skip('Exact native sandbox program is not ELF')
        launcher=[str(native)]
    project=_base(tmp_path)
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Retain one bounded synthetic Local observation.')
    root=tmp_path/'instance'
    options=dict(workspace='workspace-write',network=False)
    execution=dict(run_argv=[sys.executable,'-I'],max_seconds=15)
    controller.prepare(root,objective,public,launcher,max_tokens=1000,max_seconds=90,
        max_steps=8,model='fixture',reasoning_effort='high',search_policy=None,
        experiment_mode='local',execution_config=dict(executor='local_execution:command',configuration=execution),
        native_options=options,project=project)
    original_write=c.write

    def synthetic_session(path,value):
        if path.name=='native-outcome.json':value=dict(value,native_started=False)
        return original_write(path,value)

    monkeypatch.setattr(c,'write',synthetic_session)
    finish(root,1,'H',[],action='experiment')
    return root,project,options,execution


@pytest.mark.process_e2e
def test_first_project_worker_seeds_only_tracked_editable_bytes_and_preserves_reentry(project_instance):
    root,project,_,_=project_instance
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2);draft=work/'project'
    assert (draft/'src/main.py').read_bytes()==_PAYLOAD
    assert (draft/'tools/helper.py').read_bytes()==b'VALUE = 1\n'
    assert stat.S_IMODE((draft/'tools/helper.py').stat().st_mode)==0o755
    assert (draft/'empty').is_dir()
    for name in ('config.txt','data','untracked.txt','.git'):assert not (draft/name).exists()
    (draft/'src/main.py').write_bytes(b'current uncommitted draft\n')
    Path(project['base_root']).rename(Path(project['base_root']).with_name('unavailable-base'))
    assert c.build_job(root,2,'W',[1])==job
    assert (draft/'src/main.py').read_bytes()==b'current uncommitted draft\n'


@pytest.mark.process_e2e
def test_project_seed_shares_code_quota_and_does_not_publish_partial_draft(project_instance,monkeypatch):
    root,_,_,_=project_instance
    monkeypatch.setitem(code_versions.DEFAULT_LIMITS,'max_files',1)
    with pytest.raises(ValueError):c.build_job(root,2,'W',[1])
    assert not (c.workspace(root,2)/'project').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('preexisting',[False,True])
@pytest.mark.parametrize('copy_failure',[False,True])
def test_project_seed_releases_only_new_original_export_on_success_and_failure(tmp_path,monkeypatch,preexisting,copy_failure):
    from scidiscovery.research_loop import storage_policy as storage
    root=tmp_path/'instance';root.mkdir()
    source=root/'source-project'
    for name in ('src','empty'):(source/name).mkdir(parents=True)
    (source/'src/main.py').write_bytes(b'exact selected original\n')
    version=code_versions.capture(root,step=1,call_id='parent',code_root=source,
        entry='src/main.py',selection='directory',logical_root='project',roots=['src','empty'])
    project=dict(base_root=str(tmp_path/'absent-base'),editable=['src','empty'],scratch=[],outputs=[],
        base_identity=dict(object_format='sha1',commit='a'*40))
    binding=dict(source='selected_commit',kind='run',step=1,call_id='parent',code_version=version)
    monkeypatch.setattr(c,'_selected_project_code',lambda *args:(root,binding))
    exact=code_versions._private(root,1,'parent',create=False,context=storage.Deadline(10)).export
    if preexisting:code_versions.materialize(root,step=1,call_id='parent',code_version=version)
    work=root/'successor-work';work.mkdir()
    if copy_failure:
        original_stage=c.tempfile.mkdtemp

        def stage(*args,**kwargs):
            path=original_stage(*args,**kwargs)

            def fail_copy(*args,**kwargs):
                raise OSError('fixture copy failure after materialize')

            monkeypatch.setattr(code_versions,'_read_file',fail_copy)
            return path

        monkeypatch.setattr(c.tempfile,'mkdtemp',stage)
    try:
        if copy_failure:
            with pytest.raises(OSError,match='fixture copy failure'):
                c._seed_project_code(root,dict(project=project),work,context=storage.Deadline(10))
            assert not (work/'project').exists()
        else:
            assert c._seed_project_code(root,dict(project=project),work,context=storage.Deadline(10))==binding
            assert (work/'project/src/main.py').read_bytes()==b'exact selected original\n'
        assert exact.exists() is preexisting
    finally:code_versions.cleanup_export(root,step=1,call_id='parent')


@pytest.mark.process_e2e
@pytest.mark.parametrize('project_instance',['native'],indirect=True)
def test_project_local_original_call_launches_real_sandbox_and_seals_same_outputs(project_instance,tmp_path,monkeypatch):
    from scidiscovery.research_loop import fork_runtime, fork_service
    from scidiscovery.research_loop.storage_policy import Deadline
    root,project,options,execution=project_instance
    native=_native_sandbox()
    shared_input_views.ensure_capability()
    home=tmp_path/'isolated-home';home.mkdir();(home/'.codex').mkdir()
    temporary=tmp_path/'isolated-tmp';temporary.mkdir()
    monkeypatch.setenv('HOME',str(home));monkeypatch.setenv('CODEX_HOME',str(home/'.codex'))
    monkeypatch.setenv('TMPDIR',str(temporary));monkeypatch.setenv('PYTHONDONTWRITEBYTECODE','1')
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2);record=c.host_record(work)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    config=e.configure(root,2,'local_execution:command',execution,work/'work/execution.json')
    _,launch=native_execution.prepare(root,2,record,work,home,
        [str(native),'exec','--sandbox','workspace-write','synthetic identity; never a model request'],
        standard=True,native_options=c.settings(root,job)['native_options'])

    def trusted_launch(body,accepted,command,cwd):
        assert body=={key:value for key,value in config.items() if key!='execution_interface'}
        assert e.Records(config).accepted(accepted['call_id'])==accepted
        return launch(config,accepted,command,cwd)

    token=bridge._LOCAL_LAUNCH.set(trusted_launch)
    try:result=e.dispatch(config,Path(project['base_root'])/'src/main.py')
    finally:bridge._LOCAL_LAUNCH.reset(token)
    assert result['status']=='known_success',result
    call=result['call_id'];accepted=e.Records(config).accepted(call)
    assert accepted['cwd']==project['base_root']
    assert accepted['code_version']['logical_root']=='project'
    original=local_experiments.artifact_directory(config,call)/'project/out/kept.txt'
    assert original.read_bytes()==b'exact original call output\n'
    assert stat.S_IMODE(original.stat().st_mode)==0o400
    completed=c.read(e.Records(config).directory(call)/'completed.json')
    assert [row['path'] for row in completed['outputs']]==[str(original)]
    assert completed['outputs'][0]['file_stat']==e.proof(original)['file_stat']
    assert not (Path(project['base_root'])/'build').exists()
    assert (Path(project['base_root'])/'config.txt').read_bytes()==b'readonly base\n'
    finish(root,2,'W',[1])
    seal=c.read(record/'result-identity.json')
    expected=f'inputs/parents/002/artifacts/experiments/{call}/artifacts/project/out/kept.txt'
    assert any(row['path']==expected for row in seal['materials'])
    assert original.read_bytes()==b'exact original call output\n'
    (work/'project/src/main.py').write_bytes(b'current uncommitted fork draft B\n')
    with controller._writer(root):pass
    child=tmp_path/'fork-child';allowance=dict(max_tokens=500,max_seconds=30,max_steps=3)
    preview=fork_service.preview(root,2,child,budget=allowance)
    assert preview['status']=='fork_preview_ready',preview
    fork_service.create(root,2,child,budget=allowance,expected_preview_id=preview['preview_id'],
        operation_id='project-current-draft-and-original-code')
    assert c.settings(child)['project']==c.settings(root)['project']
    fork_runtime.build_selected_job(child,2)
    assert (c.workspace(child,2)/'project/src/main.py').read_bytes()==b'current uncommitted fork draft B\n'
    assert not (c.workspace(child,2)/'project/config.txt').exists()
    export=code_versions.materialize(child,step=2,call_id=call,code_version=accepted['code_version'])
    assert (export/'src/main.py').read_bytes()==_PAYLOAD
    code_versions.cleanup_export(child,step=2,call_id=call)
    material=next(row for row in seal['materials'] if row['path']==expected)
    index=next(number for number,row in enumerate(sorted(seal['materials'],key=lambda row:row['path']),1)
        if row['path']==material['path'])
    finish(root,3,'A',[2],action='continue_w',selection=[f'W002-I{index:03d}'])
    parent_export=code_versions._private(root,2,call,create=False,context=Deadline(10)).export
    assert not parent_export.exists()
    successor=c.build_job(root,4,'W',[3])
    assert successor['project_code_seed']['source']=='selected_commit'
    assert successor['project_code_seed']['call_id']==call
    assert successor['project_code_seed']['code_version']==accepted['code_version']
    assert (c.workspace(root,4)/'project/src/main.py').read_bytes()==_PAYLOAD
    assert not parent_export.exists()
