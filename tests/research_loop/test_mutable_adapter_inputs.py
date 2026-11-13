"""G1 source metadata and exact consumption; no process or Git is executed."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cov_execution import experiments as cov_execution
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop.protocol import CURRENT
from scidiscovery.research_loop import (code_versions, contracts as c, execution, execution_inputs as inputs,
    execution_authority as authority, experiments as e, local_experiments as local, material_references,
    originals, qualification as q)


def _saved(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(e.encoded(value))
    return e.proof(path)


def _replace(path, raw):
    replacement = path.with_name(path.name + '.replacement')
    replacement.write_bytes(raw)
    replacement.replace(path)


def _code_control(tmp_path, *, directory=False):
    root=tmp_path/'instance';work=c.workspace(root,1)
    _saved(root/'jobs/001.json',dict(step=1,role='W',worker_view_version=1))
    config=dict(record_dir=str(root/'host-runtime/experiment-control/001/records'),
        scope=dict(step=1,role='W'),execution_interface=dict(
            executor='local_execution:command' if directory else 'fixture:command',
            public_capabilities=e._public_capabilities(local.LocalExecution) if directory else []))
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',
        selection='directory' if directory else 'file',entry='package/main' if directory else 'main',logical_root='code')
    accepted=dict(call_id='exact-call',scope=config['scope'],parent=dict(candidate_id=None),
        source_script=str(work/'code'/version['entry']),code_version=version,snapshot_files=[],
        script=dict(path=str(root/'deleted-export/main'),size_bytes=1,file_stat=[1,2,1,3]))
    path=Path(config['record_dir'])/'calls/exact-call/accepted.json'
    _saved(path,accepted)
    return root,config,accepted,path


def test_code_control_read_needs_no_draft_export_or_git_content(tmp_path, monkeypatch):
    _,config,accepted,path=_code_control(tmp_path)
    def forbidden(*args,**options):pytest.fail('Control query consumed scientific code')
    monkeypatch.setattr(code_versions,'verify',forbidden)
    monkeypatch.setattr(code_versions,'materialize',forbidden)
    saved=path.read_bytes()
    assert e.Records(config).accepted(accepted['call_id'])==accepted
    assert path.read_bytes()==saved


def test_directory_code_control_keeps_runtime_cwd_separate_from_code_root(tmp_path, monkeypatch):
    root,config,accepted,path=_code_control(tmp_path,directory=True)
    directory=c.workspace(root,1)/'experiments/example'
    accepted.update(experiment_directory=str(directory),cwd=str(directory))
    _saved(path,accepted);saved=path.read_bytes()
    def forbidden(*args,**options):pytest.fail('Control query consumed scientific code')
    monkeypatch.setattr(code_versions,'verify',forbidden)
    monkeypatch.setattr(code_versions,'materialize',forbidden)
    assert e.Records(config).accepted(accepted['call_id'])==accepted
    assert path.read_bytes()==saved


@pytest.mark.parametrize('change',['experiment_as_code','nested_code_root','capability_removed','source_in_cwd'])
def test_directory_code_control_rejects_changed_code_scope(tmp_path,change):
    root,config,accepted,path=_code_control(tmp_path,directory=True)
    work=c.workspace(root,1)
    directory=work/'experiments/example'
    accepted.update(experiment_directory=str(directory),cwd=str(directory));_saved(path,accepted)
    assert e.Records(config).accepted(accepted['call_id'])==accepted
    if change in {'experiment_as_code','nested_code_root'}:
        accepted['code_version']['logical_root']='experiments/example' if change=='experiment_as_code' else 'code/package'
        accepted['source_script']=str(work/accepted['code_version']['logical_root']/accepted['code_version']['entry'])
    elif change=='capability_removed':config['execution_interface']['public_capabilities']=[]
    else:accepted['source_script']=str(work/'experiments/example'/accepted['code_version']['entry'])
    _saved(path,accepted)
    with pytest.raises(e.IntegrityError,match='code version'):
        e.Records(config).accepted(accepted['call_id'])


@pytest.mark.parametrize('change',['logical_source','code_root','legacy_entry','role'])
def test_code_control_rejects_changed_host_logical_binding(tmp_path,change):
    _,config,accepted,path=_code_control(tmp_path)
    assert e.Records(config).accepted(accepted['call_id'])==accepted
    if change=='logical_source':accepted['source_script']+='.different'
    elif change=='code_root':accepted['code_version']['logical_root']='work'
    elif change=='legacy_entry':accepted['entry']='main'
    else:
        config['scope']['role']='A';accepted['scope']=config['scope']
    _saved(path,accepted)
    with pytest.raises(e.IntegrityError,match='code version'):
        e.Records(config).accepted(accepted['call_id'])


def test_code_consumption_requires_configuration_and_checks_git_plus_data(tmp_path,monkeypatch):
    root,config,accepted,_=_code_control(tmp_path)
    from test_mutable_run_inputs import _frozen_material
    data=c.workspace(root,1)/'inputs/measurements'
    material,reference,_=_frozen_material(root,c.workspace(root,1),data,name='measurements')
    job=c.read(root/'jobs/001.json');job['global_materials']=[material];_saved(root/'jobs/001.json',job)
    accepted['snapshot_files']=[dict(reference,name='measurements')]
    seen=[]
    def verify(instance,*,step,call_id,code_version,**options):
        seen.append((instance,step,call_id,deepcopy(code_version)))
        assert (instance,step,call_id)==(root,1,accepted['call_id'])
    monkeypatch.setattr(code_versions,'verify',verify)
    with pytest.raises(e.IntegrityError,match='exact W configuration'):
        e.verify_scientific_inputs(accepted)
    assert seen==[]
    assert e.verify_scientific_inputs(accepted,config=config)==accepted
    _replace(data,b'changed data')
    with pytest.raises(e.IntegrityError,match='scientific input original changed'):
        e.verify_scientific_inputs(accepted,config=config)
    assert len(seen)==2


def _source_call(tmp_path, monkeypatch, *, executor='local_execution:command'):
    """Synthetic source/control originals; no model, backend or native launch."""
    root=tmp_path/'instance';call='source-call';work=c.workspace(root,1)
    _saved(root/'settings.json',dict(skills=None,goal_id='source-fixture'))
    _saved(root/'settings-identity.json',dict(path='settings.json'))
    job=root/'jobs/001.json';_saved(job,dict(step=1,role='W',worker_view_version=1))
    scope=dict(handoff_protocol=CURRENT,step=1,role='W',job_ref=c.record_ref(job));attempt=dict(number=0)
    body=dict(scope=scope,native_attempt=attempt,max_seconds=10,
        record_dir=str(root/'host-runtime/experiment-control/001/records'))
    interface=dict(version=e.VERSION,executor=executor,terminal_evidence='local_process',
        projection_contract='scidiscovery.local-projections.v1',config_record=body)
    config=dict(body,execution_interface=interface)
    version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',
        selection='file',entry='main.py',logical_root='code')
    draft=work/'code/main.py';draft.parent.mkdir(parents=True);draft.write_bytes(b'fixed code')
    export=root/'host-runtime/code-versions/exports/001'/call/'tree/main.py'
    export.parent.mkdir(parents=True);export.write_bytes(draft.read_bytes())
    script=e.proof(export)
    accepted=dict(call_id=call,scope=scope,native_attempt=attempt,interface=interface,
        parent=dict(candidate_id=None),source_script=str(draft),script=script,code_version=version,
        prepared=LocalFileDescriptor(name=call,local_path=str(export),size_bytes=script['size_bytes'],
            media_type='application/octet-stream').model_dump(mode='json'),snapshot_files=[])
    directory=Path(body['record_dir'])/'calls'/call
    _saved(directory/'accepted.json',accepted)
    _saved(directory/'process.json',dict(pid=1,start_ticks=1,pgid=1,sid=1,task_ref=c.record_ref(directory/'accepted.json')))
    _saved(directory/'terminal.json',dict(call_id=call,scope=scope,returncode=0,
        cleanup_confirmed=True,transport_complete=True))
    for name in ('stdout.raw','stderr.raw'):(directory/name).write_bytes(b'')
    _saved(directory/'submission.json',dict(call_id=call,scope=scope,external_run_id=call))
    completed=dict(call_id=call,scope=scope,status='known_success',proofs=[e.proof(directory/name)
        for name in ('accepted.json','process.json','stdout.raw','stderr.raw','terminal.json')])
    _saved(directory/'completed.json',completed)
    def registration(executor):
        assert executor==interface['executor']
        callback=(cov_execution.CovExecution.scientific_source if executor=='cov_execution:experiment'
            else local.LocalExecution.scientific_source)
        return SimpleNamespace(scientific_source=callback,scientific_outputs=None),'command'
    monkeypatch.setattr(e,'registration',registration)
    return dict(root=root,config=config,accepted=accepted,directory=directory,draft=draft,export=export,completed=completed)


def _source_observation(value, *, source=None, version=True, outputs=()):
    accepted=value['accepted'];directory=value['directory'];completed=value['completed']
    retain=lambda item:dict(deepcopy(item),source=deepcopy(item))
    result=dict(version=e.OBSERVATION_VERSION,call_id=accepted['call_id'],scope=accepted['scope'],
        mechanism_binding=accepted.get('mechanism_binding'),interface=accepted['interface'],
        external_run_id=accepted['call_id'],status=completed['status'],
        accepted=e.proof(directory/'accepted.json'),completed=e.proof(directory/'completed.json'),
        submission=e.proof(directory/'submission.json'),proofs=[retain(item) for item in completed['proofs']],
        outputs=[dict(retain(e.proof(item.local_path)),name=item.name,descriptor=item.model_dump(mode='json')) for item in outputs],
        scientific_source=None if source is None else retain(source),scientific_outputs=[],
        public_result=dict(status=completed['status'],call_id=accepted['call_id']))
    if accepted['interface']['terminal_evidence']=='local_process':result['native_terminal']=e.proof(directory/'terminal.json')
    if version:result['source_code_version']=deepcopy(value.get('compiler_version',accepted.get('code_version')))
    return result


def _forbid_source_consumption(monkeypatch):
    def forbidden(*args,**options):pytest.fail('Source metadata consumed or exported scientific code/data')
    for name in ('verify','materialize','capture'):monkeypatch.setattr(code_versions,name,forbidden)
    for name in ('verify_compilation','compiled_payload','resolve_native_original','_producer'):
        monkeypatch.setattr(inputs,name,forbidden)
    monkeypatch.setattr(authority,'accepted_input_subject',forbidden)


@pytest.mark.parametrize('executor',['local_execution:command','cov_execution:experiment'])
def test_direct_source_projection_uses_seven_code_fields_after_export_and_draft_removal(tmp_path,monkeypatch,executor):
    value=_source_call(tmp_path,monkeypatch,executor=executor);_forbid_source_consumption(monkeypatch)
    value['draft'].unlink();value['export'].unlink();saved=deepcopy(value['accepted'])
    assert e._source_projection(value['config'],value['accepted'])==(None,value['accepted']['code_version'])
    assert set(value['accepted']['code_version'])=={'version','object_format','commit','tree_path','selection','entry','logical_root'}
    assert value['accepted']==saved


def test_source_projection_preserves_real_alternate_file_and_legacy_callback(tmp_path,monkeypatch):
    value=_source_call(tmp_path,monkeypatch);alternate=tmp_path/'alternate.py';alternate.write_bytes(b'alternate exact source')
    original=e.proof(alternate);seen=[]
    def callback(accepted,*,implementation=None):seen.append(implementation);return original
    monkeypatch.setattr(e,'registration',lambda executor:(SimpleNamespace(scientific_source=callback),'command'))
    _forbid_source_consumption(monkeypatch)
    assert e._source_projection(value['config'],value['accepted'])==(original,value['accepted']['code_version'])
    legacy=deepcopy(value['accepted']);legacy.pop('code_version')
    assert e._source_projection(value['config'],legacy)==(original,None)
    assert seen==[None,None]
    _replace(alternate,b'changed source')
    with pytest.raises(e.IntegrityError,match='retained original'):
        e._source_projection(value['config'],value['accepted'])


def test_local_code_completion_keeps_controls_and_omits_exact_export_proof(tmp_path,monkeypatch):
    value=_source_call(tmp_path,monkeypatch);adapter=object.__new__(local.LocalExecution);adapter.config=value['config'];adapter.records=e.Records(value['config'])
    _forbid_source_consumption(monkeypatch)
    # An already saved completion is returned as-is; derive the proof list afresh.
    (value['directory']/'completed.json').unlink()
    result=adapter.completion(value['directory'],value['accepted'],0,True,True)
    assert result['proofs']==value['completed']['proofs']
    assert value['accepted']['script'] not in result['proofs']
    alternate=tmp_path/'prepared.json';alternate.write_bytes(b'actual prepared control')
    accepted=deepcopy(value['accepted']);accepted['script']=e.proof(alternate)
    accepted['prepared'].update(local_path=str(alternate),size_bytes=accepted['script']['size_bytes'])
    assert adapter.completion(value['directory'],accepted,0,True,True)['proofs'][-1]==accepted['script']


@pytest.mark.parametrize('change',['extra_field','bad_commit','different_commit','temporary_source'])
def test_observation_rejects_malformed_or_mixed_code_projection(tmp_path,monkeypatch,change):
    value=_source_call(tmp_path,monkeypatch);observation=_source_observation(value)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    if change=='extra_field':observation['source_code_version']['call_id']='invented-owner'
    elif change=='bad_commit':observation['source_code_version']['commit']='not-a-commit'
    elif change=='different_commit':observation['source_code_version']['commit']='b'*40
    else:observation['scientific_source']=dict(value['accepted']['script'],source=value['accepted']['script'])
    _forbid_source_consumption(monkeypatch)
    with pytest.raises(e.IntegrityError,match='code|code version'):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


def test_control_observation_omits_only_declared_science_after_draft_export_and_data_removal(tmp_path,monkeypatch):
    value=_source_call(tmp_path,monkeypatch);data=tmp_path/'observed.dat';data.write_bytes(b'exact output')
    descriptor=LocalFileDescriptor(name='observed.dat',local_path=str(data),size_bytes=data.stat().st_size,
        media_type='application/octet-stream')
    # A retained Cov-style source proof is still an original fact. Its exact
    # export role is established by accepted metadata, without opening it.
    value['completed']['proofs'].extend([value['accepted']['script'],e.proof(data)])
    _saved(value['directory']/'completed.json',value['completed'])
    observation=_source_observation(value,outputs=[descriptor])
    value['draft'].unlink();value['export'].unlink();data.unlink();_forbid_source_consumption(monkeypatch)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    with pytest.raises((OSError,ValueError)):
        e.verify_observation(observation,lambda item:Path(item['path']))


@pytest.mark.parametrize('alias_as_science',[False,True])
def test_control_observation_never_omits_process_proof(tmp_path,monkeypatch,alias_as_science):
    value=_source_call(tmp_path,monkeypatch);process=value['directory']/'process.json'
    descriptors=([LocalFileDescriptor(name='claimed-output.json',local_path=str(process),size_bytes=process.stat().st_size,
        media_type='application/json')] if alias_as_science else [])
    observation=_source_observation(value,outputs=descriptors);_forbid_source_consumption(monkeypatch)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    _replace(process,b'changed process control')
    with pytest.raises(e.IntegrityError,match='original changed'):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


def test_observation_control_read_does_not_replace_missing_pin_consumption_check(tmp_path,monkeypatch):
    value=_source_call(tmp_path,monkeypatch);observation=_source_observation(value);seen=[]
    def unavailable(instance,*,step,call_id,code_version,**options):
        seen.append((instance,step,call_id,deepcopy(code_version)))
        raise ValueError('Exact original code pin is unavailable')
    monkeypatch.setattr(code_versions,'verify',unavailable)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    with pytest.raises(e.IntegrityError,match='code version original changed'):
        e.verify_scientific_inputs(value['accepted'],config=value['config'])
    assert seen==[(value['root'],1,value['accepted']['call_id'],value['accepted']['code_version'])]


def _compiler_source_call(tmp_path,monkeypatch,*,mixed=None,input_slots=False):
    """Already accepted controls with deliberately absent scientific packages."""
    root=tmp_path/'compiler-instance';call='compiler-consumer';identity='d'*32
    _saved(root/'settings.json',dict(skills=None,goal_id='compiler-source-fixture'))
    _saved(root/'settings-identity.json',dict(path='settings.json'))
    step=2 if input_slots else 1;requester=step+1;frozen=[];frozen_refs=[];slots=[];input_seal=None
    if input_slots:
        first=dict(step=1,role='W',worker_view_version=1,material_refs=[],selected_inputs=[])
        _saved(root/'jobs/001.json',first)
        input_material=dict(path='inputs/parents/001/artifacts/data.dat',
            source=str(c.workspace(root,1)/'artifacts/data.dat'),size_bytes=10)
        other=deepcopy(input_material);other['path']=other['path'].replace('data.dat','more.dat')
        input_seal=root/'native-evidence/001/result-identity.json'
        input_value=dict(job_ref=c.record_ref(root/'jobs/001.json'),
            raw_ref=_saved(root/'native-evidence/001/science-output.json',dict(fixture='sealed raw output')),
            materials=[input_material,other])
        _saved(input_seal,input_value)
        input_sources={1:dict(owner=originals.node(root,1),job=first,seal=input_value)}
        frozen_refs=material_references.references([input_material],sources=input_sources)
        original=dict(instance_id='compiler-source-fixture',producer_step=1,producer_seal=q._ref(root,input_seal),
            material_path=input_material['path'],size_bytes=input_material['size_bytes'])
        frozen=[input_material]
        slots=[dict(semantic_name=name,alias=input_material['path'],size_bytes=input_material['size_bytes'],
            media_type='application/octet-stream',original=deepcopy(original)) for name in ('slot-a','slot-b')]
    assert all(set(row)=={'path','source','size_bytes'} for row in frozen)
    producer_job=root/'jobs'/f'{step:03d}.json'
    _saved(producer_job,dict(step=step,role='W',worker_view_version=1,material_refs=frozen_refs,selected_inputs=[]))
    producer_scope=dict(step=step,role='W',job_ref=c.record_ref(producer_job));attempt=dict(number=0)
    declaration=dict(key='implementation_authoring',kind='authoring',size_bytes=1)
    producer_interface=dict(public_capabilities=[declaration])
    producer_config=dict(scope=producer_scope,native_attempt=attempt,worker_view_version=1,
        record_dir=str(root/'host-runtime/experiment-control'/f'{step:03d}'/'records'),execution_interface=producer_interface)
    producer_projection=root/'host-runtime/experiment-control'/f'{step:03d}'/'interfaces/000.json';_saved(producer_projection,producer_config)
    producer_intent=root/'native-evidence'/f'{step:03d}'/'native-intent.json';_saved(producer_intent,dict(task_ref=c.record_ref(producer_job)))
    directory=root/'native-evidence'/f'{step:03d}'/'compiled-implementations'/identity
    capability=directory/'capability.json';_saved(capability,dict(scientific_contract='retained capability'))
    capability_ref=q._ref(root,capability)
    version=dict(version=1,object_format='sha1',commit='c'*40,tree_path='.',selection='file',entry='implementation.py',logical_root='code')
    package=root/'host-runtime/experiment-control'/f'{step:03d}'/'implementations/I001/package.json'
    output=dict(path=str(package),size_bytes=1,file_stat=[1,2,1,3])
    material=dict(path=f'inputs/parents/{step:03d}/artifacts/implementation.json',
        source=str(c.workspace(root,step)/'artifacts/implementation.json'),size_bytes=1)
    client=package.parent/'client.json';_saved(client,dict(client=dict(worker_implementation_version=1,implementation_ref='I001'),
        public_path=material['source'],package=output))
    compiler=dict(version=1,job=q._ref(root,producer_job),configuration=q._ref(root,producer_projection),
        native_intent=q._ref(root,producer_intent),implementation=producer_interface,contract=declaration,
        capability=capability_ref,capability_source=deepcopy(capability_ref),code_version=version,inputs=slots,output=output)
    if mixed is not None:compiler[mixed]=dict(path='forbidden-author-copy',size_bytes=1,file_stat=[1,2,1,3])
    assert all(set(compiler[key])=={'path','size_bytes'} for key in ('job','configuration','native_intent','capability'))
    compiler_path=directory/'compiler.json';_saved(compiler_path,compiler)
    sealed=dict(material=material,compiler=q._ref(root,compiler_path),client_binding=q._ref(root,client))
    seal=root/'native-evidence'/f'{step:03d}'/'result-identity.json';_saved(seal,dict(job_ref=c.record_ref(producer_job),
        raw_ref=_saved(root/'native-evidence'/f'{step:03d}'/'science-output.json',dict(fixture='sealed raw output')),
        materials=[material],compiled_implementations=[sealed]))
    job=root/'jobs'/f'{requester:03d}.json'
    producer_sources={step:dict(owner=originals.node(root,step),job=c.read(producer_job),seal=c.read(seal))}
    _saved(job,dict(step=requester,role='W',worker_view_version=1,selected_inputs=[],
        material_refs=material_references.references([material],sources=producer_sources)))
    scope=dict(step=requester,role='W',job_ref=c.record_ref(job))
    body=dict(scope=scope,native_attempt=attempt,max_seconds=10,
        record_dir=str(root/'host-runtime/experiment-control'/f'{requester:03d}'/'records'))
    interface=dict(version=e.VERSION,executor='fixture:compiler',terminal_evidence='adapter_result',
        projection_contract='fixture.compiler-source.v1',config_record=body)
    config=dict(body,execution_interface=interface)
    native=root/'native-evidence'/f'{requester:03d}';_saved(native/'experiment-config.json',config)
    intent=native/'native-intent.json';_saved(intent,dict(task_ref=c.record_ref(job)))
    subject=dict(producer_step=step,producer_seal=q._ref(root,seal),material=material)
    request=dict(kind='execution_authorization',options=list(authority.OPTIONS),call_id=call,
        job=q._ref(root,job),native_intent=q._ref(root,intent),subject=subject,
        review=dict(path='formal-review/qualified.json',size_bytes=1),payload=output,payload_alias=output,target={})
    request_id=authority.security.request_digest(request)
    request['nonce_hash']='fixture-original-nonce';request_directory=root/'execution-authority/requests'/request_id
    request_path=request_directory/'request.json';_saved(request_path,request);request_ref=q._ref(root,request_path)
    secret=b's'*32
    decision=dict(request=request_ref,nonce_hash=request['nonce_hash'],selected_option=authority.OPTIONS[0])
    decision['receipt_hmac']=authority.security.receipt_hmac(secret,authority.security.canonical(decision))
    decision_path=request_directory/'decision.json';_saved(decision_path,decision)
    binding=dict(scope=scope,native_attempt=attempt,interface=interface,subject=subject,review=request['review'],payload_alias=output)
    allocation=root/'execution-authority/calls'/call/'allocation.json'
    _saved(allocation,dict(call_id=call,binding=binding,native_intent=q._ref(root,intent)))
    authorization=dict(scope=scope,native_attempt=attempt,interface=interface,call_id=call,target={},payload_alias=output,
        request=request_ref,allocation=q._ref(root,allocation),decision=q._ref(root,decision_path))
    authorization['receipt_hmac']=authority.security.receipt_hmac(secret,authority.security.canonical(authorization))
    authorization_path=request_directory/'authorization.json';_saved(authorization_path,authorization)
    prepared_path=root/'tcad-prepared'/call/'prepared.json';_saved(prepared_path,dict(original_prepared_control=True))
    script=e.proof(prepared_path)
    accepted=dict(call_id=call,scope=scope,native_attempt=attempt,interface=interface,parent=dict(candidate_id=None),
        prepared=LocalFileDescriptor(name=call,local_path=str(prepared_path),size_bytes=script['size_bytes'],
            media_type='application/json').model_dump(mode='json'),script=script,execution_target={},
        execution_authority=e.proof(authorization_path),tcad=dict(prepared_ref=script,authority=e.proof(authorization_path)))
    call_directory=Path(body['record_dir'])/'calls'/call;_saved(call_directory/'accepted.json',accepted)
    _saved(call_directory/'submission.json',dict(call_id=call,scope=scope,external_run_id=call))
    completed=dict(call_id=call,scope=scope,status='known_success',proofs=[e.proof(call_directory/'accepted.json'),script])
    _saved(call_directory/'completed.json',completed)
    # The fixture supplies an already configured admission key; no request,
    # qualification, allocation or execution permission is created by the test.
    _saved(root/'execution-authority/host-key-source.json',dict(fixture_admission_boundary=True))
    monkeypatch.setattr(authority,'host_secret',lambda instance:secret)
    seen=[]
    def source(accepted,*,implementation=None):seen.append(implementation);return implementation
    monkeypatch.setattr(e,'registration',lambda executor:(SimpleNamespace(scientific_source=source),'compiler'))
    return dict(root=root,config=config,accepted=accepted,directory=call_directory,completed=completed,
        compiler_version=version,compiler_path=compiler_path,compiler=compiler,seen=seen,prepared=prepared_path,
        authorization=authorization_path,request=request_path,allocation=allocation,native_config=native/'experiment-config.json',
        compiler_configuration=producer_projection,package=package,input_seal=input_seal)


def test_compiler_source_projection_keeps_original_uuid_without_author_or_scientific_consumption(tmp_path,monkeypatch):
    value=_compiler_source_call(tmp_path,monkeypatch);_forbid_source_consumption(monkeypatch)
    source,version=e._source_projection(value['config'],value['accepted'])
    assert source is None and version==value['compiler_version'] and value['seen']==[None]
    assert value['compiler_path'].parent.name=='d'*32 and set(version)==set(value['compiler_version'])
    assert not value['package'].exists() and not (value['compiler_path'].parent/'author.py').exists()
    observation=_source_observation(value)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'


@pytest.mark.parametrize('control',['request','compiler_configuration','prepared'])
def test_compiler_control_observation_rejects_corrupt_mandatory_originals(tmp_path,monkeypatch,control):
    value=_compiler_source_call(tmp_path,monkeypatch);observation=_source_observation(value)
    _forbid_source_consumption(monkeypatch)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    _replace(value[control],b'changed mandatory control')
    with pytest.raises((e.IntegrityError,q.IntegrityError,ValueError)):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


@pytest.mark.parametrize('control',['allocation','compiler_configuration','input_seal'])
def test_compiler_control_role_overrides_scientific_descriptor_membership(tmp_path,monkeypatch,control):
    value=_compiler_source_call(tmp_path,monkeypatch,input_slots=control=='input_seal');path=value[control];original=e.proof(path)
    value['completed']['proofs'].append(original);_saved(value['directory']/'completed.json',value['completed'])
    descriptor=LocalFileDescriptor(name='claimed-data.json',local_path=str(path),size_bytes=original['size_bytes'],
        media_type='application/json')
    observation=_source_observation(value,outputs=[descriptor]);_forbid_source_consumption(monkeypatch)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    _replace(path,path.read_bytes())
    with pytest.raises(e.IntegrityError,match='original changed'):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


@pytest.mark.parametrize('mixed',['author','author_origin','author_input'])
def test_compiler_source_projection_rejects_mixed_author_and_code_metadata(tmp_path,monkeypatch,mixed):
    pristine=_compiler_source_call(tmp_path/'pristine',monkeypatch)
    _forbid_source_consumption(monkeypatch)
    assert e._source_projection(pristine['config'],pristine['accepted'])==(None,pristine['compiler_version'])
    assert e.verify_observation(_source_observation(pristine),lambda item:Path(item['path']),verify_files=False)=='known_success'
    value=_compiler_source_call(tmp_path/'mixed',monkeypatch,mixed=mixed)
    with pytest.raises(e.IntegrityError,match='producer binding'):
        e._source_projection(value['config'],value['accepted'])


@pytest.mark.parametrize('change',['alias','slot_size','duplicate_slot'])
def test_compiler_source_rejects_equal_length_input_control_changes(tmp_path,monkeypatch,change):
    value=_compiler_source_call(tmp_path,monkeypatch,input_slots=True);observation=_source_observation(value)
    _forbid_source_consumption(monkeypatch)
    assert e._source_projection(value['config'],value['accepted'])==(None,value['compiler_version'])
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    compiler=deepcopy(value['compiler']);slot=compiler['inputs'][0]
    if change=='alias':slot['alias']=slot['alias'][:-1]+'x'
    elif change=='original_path':slot['original']['material_path']=slot['original']['material_path'][:-1]+'x'
    elif change=='original_size':slot['original']['size_bytes']+=1
    elif change=='slot_size':slot['size_bytes']+=1
    elif change=='instance':slot['original']['instance_id']=slot['original']['instance_id'][:-1]+'x'
    elif change=='producer_step':slot['original']['producer_step']=0
    elif change=='duplicate_slot':compiler['inputs'][1]['semantic_name']=slot['semantic_name']
    else:slot['media_type']=10**(len(json.dumps(slot['media_type']))-1)
    changed=e.encoded(compiler)
    assert len(changed)==value['compiler_path'].stat().st_size
    _replace(value['compiler_path'],changed)
    with pytest.raises(e.IntegrityError,match='compiler input'):
        e._source_projection(value['config'],value['accepted'])
    with pytest.raises(e.IntegrityError,match='compiler input'):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


@pytest.mark.parametrize('change',['job_ref','duplicate_material','conflicting_material'])
def test_compiler_source_rejects_equal_length_input_seal_control_changes(tmp_path,monkeypatch,change):
    value=_compiler_source_call(tmp_path,monkeypatch,input_slots=True);observation=_source_observation(value)
    _forbid_source_consumption(monkeypatch)
    assert e._source_projection(value['config'],value['accepted'])==(None,value['compiler_version'])
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    seal=json.loads(value['input_seal'].read_bytes())
    if change=='job_ref':seal['job_ref']['path']=seal['job_ref']['path'].replace('/jobs/001.json','/jobs/009.json')
    else:
        seal['materials'][1]['path']=seal['materials'][0]['path']
        if change=='conflicting_material':
            seal['materials'][1]['size_bytes']+=1
    changed=e.encoded(seal)
    assert len(changed)==value['input_seal'].stat().st_size
    _replace(value['input_seal'],changed)
    # A changed sealed job reference is now refused while loading the producer seal.
    error,match=(ValueError,'exact original job') if change=='job_ref' else (e.IntegrityError,'compiler input')
    with pytest.raises(error,match=match):
        e._source_projection(value['config'],value['accepted'])
    with pytest.raises(error,match=match):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


def test_control_observation_keeps_native_manifest_mandatory(tmp_path,monkeypatch):
    value=_compiler_source_call(tmp_path,monkeypatch);manifest=tmp_path/'native-manifest.json'
    _saved(manifest,dict(terminal_state='succeeded'))
    descriptor=LocalFileDescriptor(name='tcad_manifest',local_path=str(manifest),size_bytes=manifest.stat().st_size,
        media_type='application/json')
    value['completed']['proofs'].append(e.proof(manifest));_saved(value['directory']/'completed.json',value['completed'])
    observation=_source_observation(value,outputs=[descriptor]);_forbid_source_consumption(monkeypatch)
    assert e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)=='known_success'
    _replace(manifest,b'changed native manifest')
    with pytest.raises(e.IntegrityError,match='original changed'):
        e.verify_observation(observation,lambda item:Path(item['path']),verify_files=False)


def test_preparation_authority_keeps_qualification_refs_and_bound_acceptance_at_head_shape(tmp_path,monkeypatch):
    from scidiscovery.research_loop import budget,preparation_recovery as recovery,storage_policy
    value=_compiler_source_call(tmp_path,monkeypatch);root=value['root'];config=value['config']
    accepted=value['accepted'];permit=c.read(value['authorization']);request=c.read(value['request'])
    reservation=q._save(root,root/'reservation.json',dict(fixture='retained resource ownership'))
    accepted['execution_reservation']=reservation;_saved(value['directory']/'accepted.json',accepted)
    prepare=root/'execution-authority/calls'/accepted['call_id']/'prepare-intent.json'
    _saved(prepare,dict(call_id=accepted['call_id'],authority=accepted['execution_authority'],reservation=reservation))
    refs=dict(authority=accepted['execution_authority'],**{key:permit[key] for key in ('allocation','request','decision')},
        prepare_intent=e.proof(prepare),reservation=reservation)
    binding=dict(call_id=accepted['call_id'],scope=config['scope'],native_attempt=config['native_attempt'],
        interface=config['execution_interface'],authority=refs['authority'],request=refs['request'],decision=refs['decision'])
    bound=dict(accepted=q._ref(root,value['directory']/'accepted.json'),prepared=accepted['prepared'])
    row=dict(ref=reservation,binding=binding,bound=bound,released=False,actual={})
    # Ledger storage and the accepted native-attempt chain locator are injected;
    # reservation lookup, signatures and every authority/native-intent/acceptance
    # comparison still use production readers over the retained originals.
    monkeypatch.setattr(budget,'_events',lambda selected,**options:([],None,({}, {'a'*64:row})))
    def forbidden(*args,**options):pytest.fail('Preparation inspection rewrote consumed budget ownership')
    monkeypatch.setattr(recovery.execution,'record',
        lambda selected,step,attempt=None:Path(selected)/'native-evidence'/f'{step:03d}')
    monkeypatch.setattr(budget,'_append',forbidden)
    snapshot=dict(call_id=accepted['call_id'],scope=config['scope'],native_attempt=config['native_attempt'],
        interface=config['execution_interface'],prepared=accepted['prepared'],records=config['record_dir'],
        snapshot='.prepared-original-authority',authority_originals=refs)
    assert set(refs['authority'])=={'path','size_bytes','file_stat'}
    assert set(request['native_intent'])==set(bound['accepted'])=={'path','size_bytes'}
    assert set(e.proof(root/request['native_intent']['path']))=={'path','size_bytes','file_stat'}
    originals={path:path.read_bytes() for path in (value['authorization'],value['request'],value['allocation'],
        prepare,value['directory']/'accepted.json',root/'reservation.json')}
    saved_row=deepcopy(row)
    for _ in range(2):
        recovery._authority(root,snapshot,config,accepted,storage_policy.Deadline(10),directory=value['directory'])
    assert row==saved_row and all(path.read_bytes()==raw for path,raw in originals.items())
    row['bound']['accepted']=dict(bound['accepted'],path=bound['accepted']['path']+'.other')
    with pytest.raises(recovery.PreparationError,match='reservation already binds another acceptance'):
        recovery._authority(root,snapshot,config,accepted,storage_policy.Deadline(10),directory=value['directory'])
    assert all(path.read_bytes()==raw for path,raw in originals.items())
