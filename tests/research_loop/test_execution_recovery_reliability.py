"""Recovery boundaries: no replay, no false absence, no in-flight sealing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_nonlocal_execution_observation import case
from test_mutable_run_inputs import fixed_code, fragment, live_fragment, terminal_code
from scidiscovery.research_loop import contracts as c, controller, execution, experiments as e
from scidiscovery.research_loop import execution_recovery, execution_resume, runtime
from scidiscovery.research_loop import candidate_audit, worker_execution_bridge as bridge


def test_pending_capture_contention_never_publishes_missing_evidence(tmp_path, monkeypatch):
    root=tmp_path.resolve()
    job=dict(role='W',step=1,execution_authority=True)
    monkeypatch.setattr(execution_resume,'_capture_pending',lambda *args: (_ for _ in ()).throw(BlockingIOError()))
    with pytest.raises(BlockingIOError):execution_resume.capture_pending(root,job)
    assert not list(root.glob('**/pending-execution-set.json'))


def test_sealed_accepted_unknown_remains_a_global_blocker(tmp_path, monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    root=tmp_path.resolve()
    unknown=dict(source_step=2,call_id='original',status='unknown',origin_identity='branch',recovery_ref=None)
    class Head(dict):
        prefix=dict(branch='branch')
    monkeypatch.setattr(accepted_budget,'read',lambda _:Head(search=dict(current_facts={'search/call/002/original':unknown})))
    latest=[None]
    monkeypatch.setattr(execution_recovery,'latest',lambda *_,**__:latest[0])
    assert execution_recovery.pending(root)
    assert execution_recovery.pending(root,unknown_only=True)
    assert not execution_recovery.pending(root,ignore=(2,'original'))
    # Only an appended exact-call terminal fact supersedes the frozen unknown.
    latest[0]=dict(version=2,source_step=2,call_id='original',phase='terminal',row=dict(call_id='original',status='known_success'))
    assert not execution_recovery.pending(root)


@pytest.mark.parametrize('kind',[ValueError,RuntimeError])
def test_cli_exception_text_is_private(tmp_path,monkeypatch,remote_registry,capsys,kind):
    root,_,config,_=case(tmp_path,monkeypatch)
    secret='/private/control/token=opaque-nonhex-secret'
    def broken(*_,**__):raise kind(secret)
    monkeypatch.setattr(e,'load',broken)
    code=e.main(['--config',str(c.workspace(root,2)/'work/experiment-config.json'),'capabilities'])
    public=capsys.readouterr()
    assert code in {2,3} and secret not in public.out+public.err
    assert json.loads(public.out)['status'] in {'unknown','unresolved'}
    diagnostics=list((Path(config['record_dir']).parent/'execution-diagnostics').glob('*.json'))
    assert len(diagnostics)==1
    diagnostic=c.read(diagnostics[0])
    # Host-private diagnostics keep the full original exception.
    assert diagnostic['error']==kind.__name__ and diagnostic['note'].endswith(f'{kind.__name__}: {secret}\n')


@pytest.mark.parametrize('owner_live,fenced,settles',[(False,False,True),(True,False,False),(False,True,False)])
def test_audit_preflight_recovery_requires_exclusive_owner_and_no_fence(tmp_path,monkeypatch,remote_registry,owner_live,fenced,settles):
    root,_,_,_=case(tmp_path,monkeypatch)
    operation='c'*64
    owner=runtime.process_identity(os.getpid(),operation)
    if not owner_live:owner['start_ticks']+=1
    intent=dict(operation_id=operation,candidate_id='selected',decision=dict(step=2),fence_protocol=1,owner=owner)
    directory=candidate_audit._directory(root,2)
    candidate_audit._save(directory/'intent.json',intent)
    if fenced:candidate_audit._save(directory/'dispatch.json',dict(original='possible Popen with no process receipt'))
    monkeypatch.setattr(candidate_audit,'validate_intent',lambda *_:None)
    with pytest.raises(RuntimeError,match='writer'):candidate_audit._settle_preflight(root,intent)
    with controller._writer(root):
        assert candidate_audit._settle_preflight(root,intent) is settles
    assert (directory/'outcome.json').exists() is settles


@pytest.mark.process_e2e
def test_bridge_process_isolates_globals_reaps_real_descendants_and_never_replays(tmp_path,monkeypatch,remote_registry,capsys):
    root,_,config,_=case(tmp_path,monkeypatch)
    work=c.workspace(root,2);record=execution.origin(root,2);slot=work/bridge.SUBMISSION_DIRECTORY
    marker=tmp_path/'real-descendants.json';counter=tmp_path/'dispatch-count'
    monkeypatch.setenv('SCID_BRIDGE_PRIVATE_ENV','parent')
    source=('import json,os,pathlib,subprocess,sys,time;'
        'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True);'
        f'pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]));time.sleep(60)')
    def blocked_main(_):
        assert os.environ['SCID_BRIDGE_PRIVATE_ENV']=='bridge'
        counter.write_text(counter.read_text()+'x' if counter.exists() else 'x')
        subprocess.Popen([sys.executable,'-c',source],start_new_session=True)
        time.sleep(60)
    monkeypatch.setattr(e,'main',blocked_main)
    monkeypatch.setattr(bridge,'_bridge_deadline',lambda *_:time.clock_gettime(time.CLOCK_BOOTTIME)+1.)
    request=dict(submission_ref='S001',arguments=['capabilities'])
    identities=[]
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *_:None,
            environment={'SCID_BRIDGE_PRIVATE_ENV':'bridge'}):
        c.write(slot/'submit.json',request)
        until=time.monotonic()+5
        while not marker.exists() and time.monotonic()<until:time.sleep(.02)
        assert marker.exists()
        identities=[runtime.process_identity(pid,'fixture') for pid in json.loads(marker.read_text())]
        with pytest.raises(BlockingIOError):bridge.stopped(record,execution.identity(root,2))
        print('parent stdout stays live')
        assert os.environ['SCID_BRIDGE_PRIVATE_ENV']=='parent'
    assert 'parent stdout stays live' in capsys.readouterr().out
    assert all(not runtime.process_matches(row,'fixture') for row in identities)
    bridge.stopped(record,execution.identity(root,2))
    assert counter.read_text()=='x'
    # A durable claim interrupted in flight is unknown on the same request;
    # restarting the receiver may not enter e.main again.
    with bridge.serve(slot,work/'work/experiment-config.json',work,launch=lambda *_:None):
        until=time.monotonic()+5
        while not (slot/'response.json').exists() and time.monotonic()<until:time.sleep(.02)
        assert c.read(slot/'response.json')['result']['status']=='unknown'
    assert counter.read_text()=='x'


def test_tcad_retire_found_binds_original_without_cancel(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from tcad_artifact.managed_execution import TCADManagedExecution
    from scidiscovery.operations.execution import LocalFileDescriptor
    directory=tmp_path/'records/calls/original';directory.mkdir(parents=True)
    c.write(directory/'accepted.json',dict(original=True))
    payload=LocalFileDescriptor(name='original',local_path=str(directory/'accepted.json'),
        **{key:e.proof(directory/'accepted.json')[key] for key in ('size_bytes',)},media_type='application/json')
    wrapper=TCADManagedExecution.__new__(TCADManagedExecution)
    wrapper.config=dict(record_dir=str(tmp_path/'records'));wrapper.target=dict(original=True)
    bindings=[]
    wrapper.records=SimpleNamespace(root=tmp_path/'records',directory=lambda _:directory,
        lease=lambda _:nullcontext(),bind_external=lambda *args:bindings.append(args))
    wrapper._bound=lambda _:(dict(scope={},tcad=dict(authority={},payload=payload.model_dump(mode='json'))),payload)
    wrapper._check=lambda *_,**__:None
    wrapper._binding=lambda *args:None
    wrapper.adapter=SimpleNamespace(validate_submission_protocol=lambda:dict(original=True),
        retire_submission_details=lambda _:dict(found=True,run_id='same-job',state='running'))
    wrapper.cancel_original=lambda _:pytest.fail('retire may not cancel the existing job')
    monkeypatch.setattr(e,'retirement',lambda *_:None)
    with wrapper.retire_original('original') as result:
        assert result['status']=='running' and result['external_run_id']=='same-job'
    assert bindings==[('original','same-job','running')]


def test_owner_run_settles_audit_preflight_before_admission(tmp_path,monkeypatch,remote_registry):
    from scidiscovery.research_loop import fork_runtime
    root,_,_,_=case(tmp_path,monkeypatch)
    calls=[]
    def settle(actual_root):
        controller.require_writer(actual_root)
        calls.append('settled')
    monkeypatch.setattr(candidate_audit,'settle_preflights',settle)
    def stop_after_recovery(_):
        assert calls==['settled']
        raise RuntimeError('next original control phase')
    monkeypatch.setattr(fork_runtime,'bootstrap',stop_after_recovery)
    with controller._writer(root),pytest.raises(RuntimeError,match='next original control phase'):
        controller._run(root)


def test_transferred_controller_descriptor_retains_exact_pid_writer_scope(tmp_path,monkeypatch,remote_registry):
    root,_,_,_=case(tmp_path,monkeypatch)
    descriptor=os.open(root/'host-run.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        with pytest.raises(RuntimeError,match='writer'):controller.require_writer(root)
        with controller._local_writer(root,descriptor=descriptor):
            controller.require_writer(root)
        os.fstat(descriptor)  # The detached owner still owns and closes this fd.
        with pytest.raises(RuntimeError,match='writer'):controller.require_writer(root)
    finally:os.close(descriptor)


@pytest.fixture
def recovery_metadata(tmp_path,monkeypatch):
    """Only control records; code, prepared bytes and scientific outputs are absent."""
    from scidiscovery.research_loop import history,qualification as q,code_versions
    root=tmp_path.resolve();step=2;call='original'
    job=dict(step=step,role='W',worker_view_version=1)
    c.write(root/'jobs/002.json',job)
    scope=dict(step=step,role='W',job_ref=dict(path=str(root/'jobs/002.json')))
    interface=dict(executor='local_execution:command')
    attempt=dict(number=0);alias=dict(path='opaque-original-payload',size_bytes=19)
    pending=dict(path='pending.json');seal_ref=dict(path='native-evidence/002/result-identity.json')
    def save(name,value):
        path=root/name;c.write(path,value)
        return dict(path=name,size_bytes=path.stat().st_size)
    allocation=save('allocation.json',dict(call_id=call,binding=dict(scope=scope,
        native_attempt=attempt,interface=interface,payload_alias=alias)))
    request=save('request.json',dict(call_id=call,job=dict(path='jobs/002.json'),target='fixture'))
    decision=save('decision.json',dict(request=request))
    admission=save('admission.json',dict(allocation=allocation,pending_set=pending))
    authority=save('authority.json',dict(allocation=allocation,request=request,decision=decision,
        scope=scope,native_attempt=attempt,interface=interface,payload_alias=alias,target='fixture'))
    authority['path']=str(root/authority['path'])
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',
        selection='directory',entry='main.py',logical_root='code')
    prepared=dict(name=call,local_path=str(root/'host-runtime/code-versions/exports/002/original/tree/main.py'),size_bytes=19)
    accepted=dict(call_id=call,scope=scope,native_attempt=attempt,interface=interface,
        source_script=str(c.workspace(root,step)/'code/main.py'),code_version=version,
        script=dict(path=prepared['local_path'],size_bytes=19,file_stat=[1,2,19,3]),
        prepared=prepared,execution_authority=authority,execution_target='fixture')
    records=root/'host-runtime/experiment-control/002/records'
    accepted_path=records/'calls'/call/'accepted.json';c.write(accepted_path,accepted)
    row=dict(call_id=call,scope=scope,status='unknown',
        accepted=dict(snapshot=accepted_path.relative_to(root).as_posix()))
    value=dict(version=2,source_step=step,call_id=call,scope=scope,seal_ref=seal_ref,
        pending_set=pending,allocation=allocation,request=request,decision=decision,admission=admission,
        origin='sealed_preparation_unknown',phase='accepted',previous=None,row=row)
    seal=dict(pending_executions=pending)
    validations=[]
    def managed(*args,**options):
        validations.append(options)
        assert options.get('verify_files') is False
    monkeypatch.setattr(history,'_validate_managed',managed)
    monkeypatch.setattr(execution_resume,'verify_pending_set',lambda *args,**options:dict(disposition='possibly_prepared'))
    def forbidden(*args,**options):pytest.fail('Metadata recovery consumed a code pin, export or new execution')
    for name in ('capture','verify','materialize','cleanup_export'):monkeypatch.setattr(code_versions,name,forbidden)
    ref=dict(path=f'{execution_recovery.DIRECTORY}/002/{call}/000001.json')
    return dict(root=root,job=job,accepted=accepted,accepted_path=accepted_path,ref=ref,value=value,
        sources={step:(job,None,seal,seal_ref)},seal=seal,validations=validations)


def test_git_recovery_metadata_survives_missing_export_draft_and_science(recovery_metadata):
    row=recovery_metadata;chains={}
    assert not Path(row['accepted']['prepared']['local_path']).exists()
    assert not Path(row['accepted']['source_script']).exists()
    execution_recovery._validate_event(row['root'],row['ref'],row['value'],chains,sources=row['sources'])
    assert chains[(2,'original')]['value']==row['value']
    assert row['validations']==[dict(context=None,verify_files=False)]


def _project_recovery_metadata(row):
    project=dict(base_root=str(row['root']/'deleted-base'),editable=['src','tools'],scratch=['build'],
        outputs=['out'],base_identity=dict(object_format='sha1',commit='b'*40))
    row['job']['project']=project
    c.write(row['root']/'jobs/002.json',row['job'])
    accepted=row['accepted'];accepted['base_identity']=dict(project['base_identity'])
    accepted['code_version'].update(logical_root='project',entry='src/main.py')
    accepted['source_script']=str(c.workspace(row['root'],2)/'project/src/main.py')
    accepted['script']['path']=str(row['root']/'host-runtime/code-versions/exports/002/original/tree/src/main.py')
    accepted['prepared']['local_path']=accepted['script']['path']
    c.write(row['accepted_path'],accepted)
    return row


def test_project_recovery_metadata_uses_original_job_without_base_or_code_reads(recovery_metadata,monkeypatch):
    from scidiscovery.research_loop import project_layout
    row=_project_recovery_metadata(recovery_metadata)
    monkeypatch.setattr(project_layout,'files',lambda *args,**kwargs:pytest.fail('Recovery reopened current project base'))
    chains={}
    execution_recovery._validate_event(row['root'],row['ref'],row['value'],chains,sources=row['sources'])
    assert chains[(2,'original')]['value']==row['value']
    assert not Path(row['job']['project']['base_root']).exists()


@pytest.mark.parametrize('damage',['base_identity','missing_identity','entry','source','file_selection','job_identity'])
def test_project_recovery_metadata_rejects_changed_original_project(recovery_metadata,damage):
    from scidiscovery.research_loop import history
    row=_project_recovery_metadata(recovery_metadata);accepted=row['accepted']
    if damage=='base_identity':accepted['base_identity']['commit']='c'*40
    elif damage=='missing_identity':accepted.pop('base_identity')
    elif damage=='entry':accepted['code_version']['entry']='data/not-editable.py'
    elif damage=='source':accepted['source_script']=row['job']['project']['base_root']+'/src/main.py'
    elif damage=='file_selection':accepted['code_version']['selection']='file'
    else:row['job']['project']['base_identity']['commit']='d'*40
    c.write(row['accepted_path'],accepted)
    with pytest.raises(history.ObserverIntegrityError):
        execution_recovery._validate_event(row['root'],row['ref'],row['value'],{},sources=row['sources'])


@pytest.mark.parametrize('damage',['schema','source','size','fingerprint','prepared'])
def test_git_recovery_metadata_rejects_changed_original_bindings(recovery_metadata,damage):
    from scidiscovery.research_loop import history
    row=recovery_metadata;accepted=row['accepted']
    if damage=='schema':accepted['code_version']['extra']='unapproved'
    elif damage=='source':accepted['source_script']=str(row['root']/'current-draft.py')
    elif damage=='size':accepted['script']['size_bytes']+=1
    elif damage=='fingerprint':accepted['script']['file_stat'][2]+=1
    else:accepted['prepared']['local_path']=str(row['root']/'other-prepared')
    c.write(row['accepted_path'],accepted)
    with pytest.raises(history.ObserverIntegrityError,match='exact original allocation'):
        execution_recovery._validate_event(row['root'],row['ref'],row['value'],{},sources=row['sources'])


@pytest.mark.parametrize('kind',['legacy','direct_packet','git_packet'])
def test_recovery_metadata_keeps_legacy_and_prepared_packet_identity_without_bytes(recovery_metadata,kind):
    row=recovery_metadata;accepted=row['accepted']
    if kind!='git_packet':accepted.pop('code_version')
    if kind=='legacy':
        accepted['script']=dict(path=str(row['accepted_path'].with_name('source.py')),size_bytes=19)
        accepted['prepared']['local_path']=str(row['root']/'missing-original-payload')
    else:
        accepted['prepared']['local_path']=str(row['root']/'missing-prepared-packet')
        accepted['script']['path']=accepted['prepared']['local_path']
    c.write(row['accepted_path'],accepted)
    execution_recovery._validate_event(row['root'],row['ref'],row['value'],{},sources=row['sources'])


def test_recovery_source_validation_uses_existing_controls_only_without_raw(recovery_metadata,monkeypatch):
    from scidiscovery.research_loop import history,originals
    row=recovery_metadata;source=object();checked=[]
    seal_path=row['root']/'native-evidence/002/result-identity.json';c.write(seal_path,row['seal'])
    monkeypatch.setattr(originals,'node',lambda root,step:source)
    monkeypatch.setattr(originals,'source_job',lambda selected:row['job'])
    monkeypatch.setattr(originals,'source_record',lambda selected,name:seal_path)
    monkeypatch.setattr(history,'source_seal_ref',lambda selected:row['value']['seal_ref'])
    def validate(selected,**options):
        assert selected is source and options==dict(controls_only=True);checked.append(selected)
    monkeypatch.setattr(c,'validate_original',validate)
    monkeypatch.setattr(history,'_source',lambda *args:pytest.fail('Recovery reopened raw science'))
    execution_recovery._validate_event(row['root'],row['ref'],row['value'],{})
    assert checked==[source]


def test_inherited_git_recovery_uses_logical_original_without_export_or_repository(recovery_metadata,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import history
    row=recovery_metadata;logical=row['root']/'absent-original-instance';accepted=row['accepted']
    scope=row['value']['scope'];scope['job_ref']['path']=str(logical/'jobs/002.json')
    accepted['source_script']=str(c.workspace(logical,2)/'code/main.py')
    accepted['script']['path']=str(logical/'host-runtime/code-versions/exports/002/original/tree/main.py')
    accepted['prepared']['local_path']=accepted['script']['path']
    accepted['execution_authority']['path']=str(logical/'authority.json')
    c.write(row['accepted_path'],accepted)
    for name in ('allocation.json','authority.json'):
        path=row['root']/name;value=c.read(path)
        if name=='allocation.json':value['binding']['scope']=scope
        else:value['scope']=scope
        c.write(path,value)
    source=SimpleNamespace(original_root=str(logical),logical_workspace=c.workspace(logical,2))
    def source_path(selected,proof):
        assert selected is source
        path=Path(proof['path']);relative=path.relative_to(logical) if path.is_absolute() else path
        return row['root']/relative
    monkeypatch.setattr(execution_resume,'_source_path',source_path)
    checked=[]
    def validate(selected,job,rows,**options):
        assert selected is source and options==dict(controls_only=True);checked.append(rows)
    monkeypatch.setattr(history,'validate_managed_original',validate)
    execution_recovery._validate_event(row['root'],row['ref'],row['value'],{},sources=row['sources'],source=source)
    assert checked==[[row['value']['row']]] and not logical.exists()


def _terminal_recovery_subject(row,monkeypatch,*,existing):
    """A saved local observation and a sealed unknown row; no provider is loaded."""
    from scidiscovery.research_loop import history,code_versions
    config=row['config'];call=row['accepted']['call_id'];root=row['root'];step=row['job']['step']
    observed=e.record_observation(config,row['adapter'],call)
    tree=code_versions.materialize(root,step=step,call_id=call,code_version=row['accepted']['code_version'])
    # Use the current producer over the fixture's real accepted call controls.
    # Only the older scientific source seal below is synthetic component data.
    current=c.accept_recovery_call(root,row['job'],call)
    old=dict(call_id=call,scope=row['accepted']['scope'],status='unknown',accepted=current['accepted'])
    old_path=root/'sealed-unknown-observation.json';c.write(old_path,dict(managed_calls=[old]))
    seal=dict(observations=dict(path=old_path.relative_to(root).as_posix()))
    seal_ref=dict(path='original-seal.json')
    monkeypatch.setattr(history,'_source',lambda *args:(row['job'],None,seal,seal_ref))
    monkeypatch.setattr(execution_recovery,'validate',lambda *args,**options:[])
    monkeypatch.setattr(e,'load',lambda *args,**options:pytest.fail('Terminal recovery loaded an execution provider'))
    recovery_path=root/execution_recovery.DIRECTORY/f'{step:03d}'/(call+'.json')
    if existing:c.write(recovery_path,dict(source_step=step,seal_ref=seal_ref,old_row=old,row=current))
    return observed,tree,recovery_path


@pytest.fixture
def terminal_create_publication(monkeypatch):
    """Cleanup component only; spy on unrelated budget/index publication.

    The live owner and call controls are real fixture originals. Its sealed
    scientific source is synthetic; this does not test a complete lifecycle or
    grant a scientific verdict, launch permission, new call or restored budget.
    """
    published=[]
    def publish(root,state,step,call_id,ref,value):
        assert state.current_job['step']==step
        assert value['source_step']==step and value['row']['call_id']==call_id
        assert value['row']['status'] in {'known_success','known_failed'}
        assert value['old_row']['status']=='unknown'
        assert value['old_row']['accepted']==value['row']['accepted']
        assert (Path(root)/ref['path']).read_bytes()
        published.append((ref,dict(value)))
    monkeypatch.setattr(execution_recovery,'_publish_fact',publish)
    return published


def test_original_unknown_recovery_retains_export_without_repeating_submission(terminal_code,fixed_code,monkeypatch):
    row=terminal_code();_,tree,_=_terminal_recovery_subject(row,monkeypatch,existing=False)
    (row['directory']/'execution-observation.json').unlink();fixed_code['cleaned'].clear()
    monkeypatch.setattr(e,'load',lambda *args,**options:object())
    monkeypatch.setattr(e,'record_observation',lambda *args,**options:None)
    result=execution_recovery.create(row['root'],row['job']['step'])
    assert result['status']=='execution_recovery_pending' and result['unresolved']==1
    assert tree.exists() and fixed_code['cleaned']==[]


@pytest.mark.parametrize('phase,saved_observation,removed',[
    ('terminal',True,True),('terminal',False,False),('not_submitted',True,False)])
def test_recorded_resume_only_cleans_saved_original_terminal_export(terminal_code,fixed_code,monkeypatch,
        phase,saved_observation,removed):
    from scidiscovery.research_loop import budget,execution_authority as authority
    row=terminal_code();_,tree,recovery_path=_terminal_recovery_subject(row,monkeypatch,existing=True)
    root=row['root'];step=row['job']['step'];call=row['accepted']['call_id']
    c.write(root/'execution-authority/calls'/call/'allocation.json',dict(call_id=call,
        binding=dict(native_attempt=row['config']['native_attempt'],
            interface=row['config']['execution_interface'],scope=row['config']['scope'])))
    allocation=execution_resume.q._ref(root,root/'execution-authority/calls'/call/'allocation.json')
    monkeypatch.setattr(execution_resume,'verify_pending_set',lambda *args,**options:dict(
        allocations=[dict(allocation=allocation,disposition='possibly_prepared')]))
    monkeypatch.setattr(authority,'_host_context',lambda config:(root,row['job'],None))
    monkeypatch.setattr(execution_recovery,'latest',
        lambda root_,step_,call_id:dict(phase=phase) if (step_,call_id)==(step,call) else None)
    def forbidden(*args,**options):pytest.fail('Recorded terminal resume changed execution or resource authority')
    monkeypatch.setattr(execution_resume,'_RecoveryAdmission',forbidden)
    for name in ('snapshot','execution_available','execution_accounting','authorization_revision',
            'execution_blocked','transition','reserve_execution','release_execution'):
        monkeypatch.setattr(budget,name,forbidden)
    if not saved_observation:(row['directory']/'execution-observation.json').unlink()
    controls=[recovery_path,*(row['directory']/name for name in
        ('accepted.json','completed.json','submission.json','terminal.json','execution-observation.json')),
        root/'settings.json',root/'host-started.json']
    ledger=root/'control-ledger'
    if ledger.exists():controls.extend(ledger.iterdir())
    originals={path:path.read_bytes() for path in controls if path.exists()}
    operations=root/'execution-authority/calls'/call/'recovery-operations'
    assert not operations.exists()
    fixed_code['cleaned'].clear()
    result=execution_resume.resume(root,step,call,max_seconds=10,authorization_note='Exact recorded original only')
    assert result['status']=='execution_resume_recorded'
    assert tree.exists() is not removed
    assert fixed_code['cleaned']==([row['key']] if removed else [])
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert not operations.exists()
    if ledger.exists():assert set(ledger.iterdir())=={path for path in originals if path.parent==ledger}


@pytest.mark.parametrize('existing',[False,True])
def test_original_terminal_recovery_retries_exact_export_cleanup(terminal_code,fixed_code,monkeypatch,terminal_create_publication,existing):
    row=terminal_code();observed,tree,path=_terminal_recovery_subject(row,monkeypatch,existing=existing)
    originals={name:(row['directory']/name).read_bytes() for name in
        ('accepted.json','completed.json','submission.json','terminal.json','execution-observation.json')}
    fixed_code['cleaned'].clear()
    assert execution_recovery.create(row['root'],row['job']['step'])['status']=='execution_recovered'
    assert not tree.exists() and fixed_code['cleaned']==[row['key']]
    saved=path.read_bytes()
    assert execution_recovery.create(row['root'],row['job']['step'])['status']=='execution_recovered'
    assert path.read_bytes()==saved
    assert all((row['directory']/name).read_bytes()==raw for name,raw in originals.items())
    assert terminal_create_publication
    assert all(value['row']['call_id']==row['accepted']['call_id'] for _,value in terminal_create_publication)


def test_original_terminal_recovery_cleanup_failure_keeps_fact_and_retries(terminal_code,fixed_code,monkeypatch,terminal_create_publication):
    from scidiscovery.research_loop import code_versions
    row=terminal_code();_,tree,path=_terminal_recovery_subject(row,monkeypatch,existing=True)
    before=path.read_bytes();cleanup=code_versions.cleanup_export
    def denied(*args,**options):raise OSError('fixture cleanup denied')
    monkeypatch.setattr(code_versions,'cleanup_export',denied)
    assert execution_recovery.create(row['root'],row['job']['step'])['status']=='execution_recovered'
    assert tree.exists() and path.read_bytes()==before
    diagnostic,=(Path(row['config']['record_dir']).parent/'execution-diagnostics').iterdir()
    note=c.read(diagnostic)['note']
    assert str(tree) in note and row['accepted']['call_id'] in note and 'fixture cleanup denied' in note
    monkeypatch.setattr(code_versions,'cleanup_export',cleanup)
    assert execution_recovery.create(row['root'],row['job']['step'])['status']=='execution_recovered'
    assert not tree.exists() and path.read_bytes()==before


def test_original_terminal_recovery_retains_export_without_replay_stop(terminal_code,fixed_code,monkeypatch,terminal_create_publication):
    from scidiscovery.research_loop import replay_check
    row=terminal_code(replay=True);_,tree,_=_terminal_recovery_subject(row,monkeypatch,existing=True)
    outcome=row['directory']/replay_check.OUTCOME;value=c.read(outcome)
    value['result']['cleanup_confirmed']=False;c.write(outcome,value);fixed_code['cleaned'].clear()
    assert execution_recovery.create(row['root'],row['job']['step'])['status']=='execution_recovered'
    assert tree.exists() and fixed_code['cleaned']==[]
