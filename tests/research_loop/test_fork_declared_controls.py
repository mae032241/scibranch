"""Accepted adapter declarations retain opaque controls, never undeclared files."""
from pathlib import Path

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from scidiscovery.research_loop import (accepted_budget, contracts as c, controller,
    execution, experiments as e, fork_sources, history, originals)
from test_history_handoff import finish


@pytest.fixture
def declared_control(tmp_path, monkeypatch, remote_registry, request):
    """Real accepted external completion; no process, provider or solver runs."""
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal.md';goal.write_text('Retain exact declared adapter proof custody.')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,['never-launched'],max_tokens=10000,
        max_seconds=120,max_steps=8,model='fixture',reasoning_effort='high',
        search_policy=None,experiment_mode='external')
    job=c.build_job(root,1,'H',[]);record=execution.origin(root,1)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'))
    accepted_budget.accept_native(root,1,record,intent)
    scientific_terminal=getattr(request,'param',False)
    config=e.configure(root,1,'remote_execution:fixture',dict(include_result=scientific_terminal),
        c.workspace(root,1)/'work/config.json')
    opaque=root/'host-recovery/non-domain-name/custom.json'
    credential=root/'native-homes/001/credential'
    credential.parent.mkdir(parents=True);credential.write_bytes(b'never copy credentials')
    raw=e.encoded(dict(job=dict(path=str(credential)),arbitrary=dict(path=str(tmp_path/'outside'))))
    submit=DifferentExternalIdentity.submit

    def declared_submit(adapter, descriptor):
        publish=adapter.records.publish

        def publish_declared(call, filename, value):
            if filename=='completed.json':
                assert call==descriptor.name==adapter.accepted['call_id']
                opaque.parent.mkdir(parents=True,exist_ok=True)
                with opaque.open('xb') as stream:stream.write(raw)
                opaque.chmod(0o400)
                value=dict(value,proofs=[*value['proofs'],e.proof(opaque)])
            return publish(call,filename,value)

        # Declare the opaque control in the original completion publication.
        # Rewriting a published receipt is not an adapter terminal protocol.
        with monkeypatch.context() as publication:
            publication.setattr(adapter.records,'publish',publish_declared)
            return submit(adapter,descriptor)

    monkeypatch.setattr(DifferentExternalIdentity,'submit',declared_submit)
    payload=c.workspace(root,1)/'work/payload';payload.write_bytes(b'actual declared scientific output')
    result=e.dispatch(config,payload,new=True)
    assert result['status']=='known_success', result
    call=result['call_id'];directory=e.Records(config).directory(call)
    observed=c.read(directory/'execution-observation.json')
    completed=c.read(directory/'completed.json')
    assert completed==e.load(config).result(call)
    assert e.proof(opaque) in completed['proofs']
    assert any(proof['source']==e.proof(opaque) for proof in observed['proofs'])
    assert e.verify_observation(observed,lambda proof:Path(proof['path']),
        interface=config['execution_interface'])=='known_success'
    # The synthetic backend's stopped writers explicitly close their originals.
    for proof in [observed[name] for name in ('accepted','completed','submission')]+observed['proofs']+observed['outputs']+observed['scientific_outputs']:
        Path(proof['path']).chmod(0o400)
    (directory/'execution-observation.json').chmod(0o400)
    finish(root,1,'H',[],action='experiment',preserve_native_intent=True)
    selected=c.build_job(root,2,'W',[1])
    with controller._writer(root):pass
    seal=c.read(record/'result-identity.json')
    managed=c.read(root/seal['observations']['path'])['managed_calls'][0]
    return dict(root=root,selected=selected,opaque=opaque,raw=raw,credential=credential,
        managed=managed,observation=observed,call=call,scientific_terminal=scientific_terminal,
        terminal=directory/'remote-terminal.json')


@pytest.mark.parametrize('declared_control',[False,True],indirect=True)
def test_declared_opaque_control_survives_without_parent_proof(declared_control,tmp_path):
    row=declared_control;root=row['root']
    plan=fork_sources.inspect_source(root,2)
    assert plan['blocked_reasons']==[]
    path=row['opaque'].relative_to(root).as_posix()
    entry=next(item for item in plan['entries'] if item['path']==path)
    assert entry['visibility']=='host_proof'
    assert entry['origin_identity']==originals.local_identity(root)
    assert entry['size_bytes']==len(row['raw'])
    assert not any(item['path'].startswith('native-homes/') for item in plan['entries'])
    child=tmp_path/'child'
    fork_sources.copy_source(plan,child/originals.FORK_DIRECTORY)
    inherited=originals.node(child,1)
    terminal=next(item for item in plan['entries'] if item['path']==row['terminal'].relative_to(root).as_posix())
    assert terminal['visibility']==('historical_science' if row['scientific_terminal'] else 'host_proof')
    assert (inherited.storage_root/terminal['storage_path']).exists()==(not row['scientific_terminal'])
    science={Path(item['path']):e.proof(item['path']) for item in row['observation']['scientific_outputs']}
    assert science
    assert all(next(item for item in plan['entries'] if item['path']==str(path.relative_to(root)))['visibility']=='historical_science'
        for path in science)
    # Remove only the parent control, not scientific originals or all of root.
    row['opaque'].unlink()
    assert originals.resolve_ref(inherited,dict(path=path)).read_bytes()==row['raw']
    fork_sources.validate_manifest(child,full=True)
    history.validate_managed_original(inherited,originals.source_job(inherited),[row['managed']],controls_only=True)
    history.validate_managed_original(inherited,originals.source_job(inherited),[row['managed']])
    assert all(e.proof(path)==proof for path,proof in science.items())
    assert row['credential'].read_bytes()==b'never copy credentials'


def test_required_control_copy_cannot_fall_back_to_parent(declared_control,tmp_path):
    row=declared_control;root=row['root']
    plan=fork_sources.inspect_source(root,2)
    child=tmp_path/'child'
    fork_sources.copy_source(plan,child/originals.FORK_DIRECTORY)
    inherited=originals.node(child,1)
    path=row['opaque'].relative_to(root).as_posix()
    retained=originals.resolve_ref(inherited,dict(path=path))
    retained.unlink()
    assert row['opaque'].read_bytes()==row['raw']
    with pytest.raises((ValueError,OSError)):
        history.validate_managed_original(inherited,originals.source_job(inherited),
            [row['managed']],controls_only=True)


@pytest.fixture(params=[False,True],ids=['without-code-version','with-code-version'])
def tcad_dispatch_control(tmp_path,monkeypatch,request):
    """Unit control schema: no adapter, Git, native process or solver execution."""
    root=tmp_path/'parent';root.mkdir()
    identity=dict(goal_id='dispatch-control-unit',settings_ref=dict(path=str(root/'settings.json')))
    source=originals.NodeSource(identity,6,str(root),root)
    call='exact-dispatch-call'
    directory=root/'host-runtime/experiment-control/006/records/calls'/call
    directory.mkdir(parents=True)

    def publish(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        e.write(path,value);path.chmod(0o400)
        return e.proof(path)

    foreign=root/'native-homes/006/credential'
    foreign.parent.mkdir(parents=True);foreign.write_bytes(b'never follow envelope JSON')
    # Deliberately not a physical/pkg_*/job.json spelling. The accepted typed
    # declaration, never a basename or embedded JSON key, owns the control role.
    envelope=root/'host-recovery/opaque-dispatch.json'
    raw=e.encoded(dict(job=dict(path=str(foreign)),
        arbitrary=dict(path=str(tmp_path/'absent-foreign-original'))))
    envelope.parent.mkdir(parents=True);envelope.write_bytes(raw);envelope.chmod(0o400)
    script=e.proof(envelope)
    science=root/'project/nodes/006/artifacts/direct-output'
    science.parent.mkdir(parents=True);science.write_bytes(b'direct scientific original')
    scientific=e.proof(science)
    descriptor=dict(name=call,local_path=str(envelope),size_bytes=len(raw),media_type='application/json')
    preparation=publish(root/'host-recovery/preparation.json',dict(prepared=descriptor,payload=scientific))
    interface=dict(terminal_evidence='adapter_result')
    accepted=dict(call_id=call,scope=dict(step=6),script=script,prepared=descriptor,
        interface=interface,tcad=dict(prepared=descriptor,prepared_ref=preparation,payload=scientific))
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='code',
        selection='file',entry='implementation.json',logical_root='code')
    if request.param:accepted['code_version']=version
    accepted_ref=publish(directory/'accepted.json',accepted)
    completed_ref=publish(directory/'completed.json',dict(call_id=call,scope=accepted['scope'],
        status='known_success',proofs=[accepted_ref],outputs=[dict(scientific,name='scientific-output')]))
    submission_ref=publish(directory/'submission.json',dict(call_id=call,scope=accepted['scope'],
        external_run_id='exact-external-call'))
    output=dict(scientific,source=scientific,name='scientific-output',
        descriptor=dict(name='scientific-output',local_path=str(science),
            size_bytes=scientific['size_bytes'],media_type='application/octet-stream'))
    observation=dict(version=1,call_id=call,scope=accepted['scope'],interface=interface,
        status='known_success',external_run_id='exact-external-call',accepted=accepted_ref,
        completed=completed_ref,submission=submission_ref,proofs=[dict(accepted_ref,source=accepted_ref)],
        outputs=[output],scientific_outputs=[output],scientific_source=None)
    observed_ref=publish(directory/'execution-observation.json',observation)
    publish(root/'native-evidence/006/experiment-config.json',{})
    closure=fork_sources._Closure();code_calls=[]
    # Isolate only the independently tested Git-transfer boundary. Assert that
    # custody neither skips nor rewrites the exact source/step/call/version.
    monkeypatch.setattr(closure,'code',lambda owner,step,call_id,pin:
        code_calls.append((owner,step,call_id,pin)))
    closure.call(source,dict(accepted=accepted_ref,completed=completed_ref,
        observation=observed_ref,outputs=[scientific]))
    assert code_calls==([(source,6,call,version)] if request.param else [])
    assert e.verify_observation(observation,lambda proof:Path(proof['path']),verify_files=False)=='known_success'
    return dict(root=root,source=source,closure=closure,script=script,raw=raw,
        observation=observation,science=science,scientific=scientific,foreign=foreign,
        originals={Path(ref['path']):Path(ref['path']).read_bytes()
            for ref in (script,preparation,accepted_ref,completed_ref,submission_ref,observed_ref)})


def test_tcad_dispatch_envelope_survives_parent_deletion_opaquely(tcad_dispatch_control,tmp_path):
    import shutil
    from scidiscovery import archive_io
    row=tcad_dispatch_control;closure=row['closure'];source=row['source']
    relative=Path(row['script']['path']).relative_to(row['root']).as_posix()
    entry=closure.entry(source.origin_identity,relative)
    assert entry['visibility']=='host_proof' and entry['size_bytes']==len(row['raw'])
    assert entry['origin_identity']==source.origin_identity
    assert not any(item['path'].startswith('native-homes/') for item in closure.entries.values())
    science_path=row['science'].relative_to(row['root']).as_posix()
    science_entry=closure.entry(source.origin_identity,science_path)
    assert science_entry['visibility']=='historical_science'
    assert fork_sources._key(source.origin_identity,science_path,science_entry) not in closure.paths
    store=tmp_path/'retained'/originals.FORK_DIRECTORY
    # Archival unit custody only: this is not child publication or admission.
    for key,path in closure.paths.items():
        item=closure.entries[key]
        archive_io.copy_original(Path(path),store/item['storage_path'],dict(item,kind='file'))
    frozen=originals._snapshot_source(store,originals._snapshot(dict(
        entries=list(closure.entries.values()),origins=closure.origins)),source.origin_identity,6)
    assert all(path.read_bytes()==raw for path,raw in row['originals'].items())
    assert e.proof(row['science'])==row['scientific']
    assert row['foreign'].read_bytes()==b'never follow envelope JSON'
    shutil.rmtree(row['root'])
    assert originals.resolve_ref(frozen,row['script']).read_bytes()==row['raw']
    assert not (store/science_entry['storage_path']).exists()

    def control_original(proof):
        reference={key:proof[key] for key in ('path','size_bytes','file_stat') if key in proof}
        return originals.resolve_ref(frozen,reference).read_bytes()

    assert e.verify_observation(row['observation'],control_original,verify_files=False)=='known_success'
    for path,raw in row['originals'].items():
        assert originals.resolve_ref(frozen,dict(path=str(path))).read_bytes()==raw
    originals.resolve_ref(frozen,row['script']).unlink()
    with pytest.raises((ValueError,OSError)):
        e.verify_observation(row['observation'],control_original,verify_files=False)


def test_missing_declared_tcad_dispatch_control_refuses_collection(tcad_dispatch_control):
    row=tcad_dispatch_control
    Path(row['script']['path']).unlink()
    closure=fork_sources._Closure()
    # Do not let the Git branch mask the missing envelope in this counterfactual.
    closure.code=lambda *args:None
    with pytest.raises((ValueError,OSError)):
        closure.control(row['source'],row['observation']['accepted'])
