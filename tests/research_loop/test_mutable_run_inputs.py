"""Selected W code binds Git versions before the first adapter effect."""
from copy import deepcopy
from pathlib import Path
import sys
import subprocess

import pytest

from execution_fixtures import execution_registry
from test_worker_fragments import fragment
from scidiscovery.operations.execution import AdapterCapability, LifecycleCapabilities, LocalFileDescriptor
from scidiscovery.research_loop import (code_versions, contracts as c, experiment_client,
    experiments as e, local_experiments, replay_check, shared_input_views, worker_execution_bridge)
from scidiscovery.research_loop.local_experiments import LocalExecution


def _configuration(root, job):
    work=c.workspace(root,job['step'])
    return e.configure(root,job['step'],'local_execution:command',
        dict(run_argv=[sys.executable],cwd=str(work)),work/'work/version-config.json')


def _prepare(config, source, *, directory=None, adapter=None):
    records=e.Records(config)
    payload=LocalFileDescriptor(name='input',local_path=str(source),size_bytes=source.stat().st_size,
        media_type='application/octet-stream')
    options=dict(source_script=str(source))
    if directory is not None:options['experiment_directory']=str(directory)
    if adapter is None:
        adapter=LocalExecution({key:value for key,value in config.items() if key!='execution_interface'},
            frozen_interface=config['execution_interface'])
    return e._prepare_descriptor(config,payload,adapter,records,
        [path.name for path in records.calls()],command_options=options,_source_script=str(source))


class _Adapter:
    def __init__(self, *, prepare=None, metadata=None):
        self.prepared=[];self.prepare_effect=prepare;self.metadata=metadata

    def prepare(self,payload,**options):
        self.prepared.append(payload)
        return self.prepare_effect(payload) if self.prepare_effect is not None else payload

    def acceptance_metadata(self,payload,**options):
        return dict(parent=dict(candidate_id=None),change_intent=None,**(self.metadata or {}))

    def capabilities(self):return ()


class _SubmissionSpy(_Adapter):
    def __init__(self,**options):
        super().__init__(**options);self.leased=[];self.submitted=[]

    def dispatch_lease(self,accepted):self.leased.append(accepted['call_id'])

    def submit(self,payload):
        self.submitted.append(payload)
        return payload.name,'running'

    def result(self,call_id):return dict(status='known_success',call_id=call_id)


@pytest.fixture
def fixed_code(monkeypatch, tmp_path):
    """Mock only the Git boundary; exercise the actual host record binding."""
    bindings={};contents={};exports={};cleaned=[]
    def capture(root,*,step,call_id,code_root,entry,selection,logical_root,**options):
        key=(Path(root),step,call_id)
        version=dict(version=1,object_format='sha1',commit='a'*40,tree_path='.',
            selection=selection,entry=entry,logical_root=logical_root)
        bindings[key]=deepcopy(version);contents[key]=(Path(code_root)/entry).read_bytes()
        return version
    def verify(root,*,step,call_id,code_version,**options):
        if bindings.get((Path(root),step,call_id))!=code_version:
            raise ValueError('Exact original code pin is unavailable')
    def materialize(root,*,step,call_id,code_version,**options):
        verify(root,step=step,call_id=call_id,code_version=code_version)
        key=(Path(root),step,call_id)
        tree=Path(root)/'host-runtime/code-versions/exports'/f'{step:03d}'/call_id/'tree'
        if tree.exists():
            if (tree/code_version['entry']).read_bytes()!=contents[key]:
                raise ValueError('Exact exported code bytes changed')
            return tree
        (tree/code_version['entry']).parent.mkdir(parents=True,exist_ok=True)
        (tree/code_version['entry']).write_bytes(contents[key]);exports[key]=tree
        return tree
    def cleanup(root,*,step,call_id,**options):
        key=(Path(root),step,call_id);cleaned.append(key)
        tree=exports.get(key)
        if tree is not None and tree.exists():
            (tree/bindings[key]['entry']).unlink();tree.rmdir()
    monkeypatch.setattr(code_versions,'capture',capture)
    monkeypatch.setattr(code_versions,'materialize',materialize)
    monkeypatch.setattr(code_versions,'verify',verify)
    monkeypatch.setattr(code_versions,'cleanup_export',cleanup)
    return dict(bindings=bindings,exports=exports,cleaned=cleaned,materialize=materialize)


def _code(root,job,raw=b'original opaque source'):
    source=c.workspace(root,job['step'])/'code/domain-authored-source'
    source.parent.mkdir(exist_ok=True);source.write_bytes(raw)
    return source


def _frozen_material(root, work, logical, raw=b'exact scientific data', *, name='data.dat'):
    """HEAD row2 and its independent frozen task slot; never an external G2 alias."""
    source=root/'controlled-test-inputs'/name
    source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(raw)
    row=dict(path=logical.relative_to(work).as_posix(),source=str(source),size_bytes=len(raw))
    c._bind_original(source,logical,root=root,size_bytes=len(raw),source_kind='mutable')
    assert set(row)=={'path','source','size_bytes'}
    return row,e.proof(logical),source


def _transformed_submission(config,payload):
    path=Path(config['record_dir']).parent/'transformed'/payload.name/'submission'
    path.parent.mkdir(parents=True);path.write_bytes(b'adapter transformed submission')
    return payload.model_copy(update=dict(local_path=str(path),size_bytes=path.stat().st_size))


def test_generic_prepare_receives_fixed_code_and_keeps_logical_identity(fragment, execution_registry, fixed_code):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    def effect(payload):
        assert payload.local_path!=str(source)
        assert Path(payload.local_path).read_bytes()==b'original opaque source'
        source.write_bytes(b'later author revision')
        assert Path(payload.local_path).read_bytes()==b'original opaque source'
        return payload
    adapter=_Adapter(prepare=effect)
    prepared,accepted=_prepare(config,source,adapter=adapter)
    assert len(adapter.prepared)==1 and accepted['source_script']==str(source)
    assert accepted['script']['path']==prepared.local_path
    assert accepted['code_version']['logical_root']=='code'
    assert accepted['snapshot_files']==[]
    assert not {'entry','source_reference','source_input'}&accepted.keys()
    assert e.verify_scientific_inputs(accepted,config=config)==accepted


@pytest.mark.parametrize('failure',['capture','export'])
def test_capture_and_export_refusal_precedes_prepare_and_cleans_only_owned_export(fragment, monkeypatch, execution_registry, fixed_code, failure):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job);adapter=_Adapter()
    if failure=='capture':
        def failed(*args,**options):raise ValueError('Ordinary source change or quota refusal')
        monkeypatch.setattr(code_versions,'capture',failed)
    else:
        def failed(*args,**options):
            fixed_code['materialize'](*args,**options)
            raise ValueError('Export validation failed')
        monkeypatch.setattr(code_versions,'materialize',failed)
    with pytest.raises(e.CommandOptionsError,match='before preparation'):_prepare(config,source,adapter=adapter)
    assert adapter.prepared==[] and e.Records(config).calls()==[]
    assert len(fixed_code['cleaned'])==(failure=='export')
    assert all(not path.exists() for path in fixed_code['exports'].values())


def test_arbitrary_prepare_failure_retains_exact_export(fragment, execution_registry, fixed_code):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    def uncertain(payload):raise RuntimeError('Preparation may already have external effects')
    adapter=_Adapter(prepare=uncertain)
    with pytest.raises(RuntimeError,match='external effects'):_prepare(config,source,adapter=adapter)
    assert len(adapter.prepared)==1 and e.Records(config).calls()==[]
    assert fixed_code['cleaned']==[]
    assert len(fixed_code['exports'])==1 and next(iter(fixed_code['exports'].values())).is_dir()


@pytest.mark.parametrize('callback',['acceptance_metadata','input_sources'])
def test_adapter_cannot_supply_or_mutate_the_host_code_version(fragment, execution_registry, fixed_code, callback):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    if callback=='acceptance_metadata':adapter=_Adapter(metadata=dict(code_version=dict(commit='b'*40)))
    else:
        class Mutator(_Adapter):
            def capabilities(self):return LocalExecution.capabilities()
            def input_sources(self,payload,metadata):
                metadata['code_version']['logical_root']='code/changed'
                return None
        adapter=Mutator()
    with pytest.raises(e.IntegrityError,match='host-owned code versions'):_prepare(config,source,adapter=adapter)
    assert len(adapter.prepared)==1 and e.Records(config).calls()==[]
    assert fixed_code['cleaned']==[]


def test_dedicated_code_scope_and_bound_data_overlap_are_explicit(fragment, tmp_path, execution_registry):
    root,job,_=fragment;config=_configuration(root,job);work=c.workspace(root,job['step'])
    directory=work/'experiments/example';directory.mkdir(parents=True)
    code_root=work/'code';source=code_root/'package/opaque-entry'
    source.parent.mkdir(parents=True);source.write_bytes(b'code')
    assert experiment_client.code_selection(config,source,experiment_directory=directory)==dict(
        code_root=code_root,entry='package/opaque-entry',selection='directory',logical_root='code')
    assert experiment_client.code_selection(config,source)==dict(
        code_root=source.parent,entry=source.name,selection='file',logical_root='code/package')
    outside=directory/'opaque-entry';outside.write_bytes(b'outside code scope')
    with pytest.raises(e.CallerError,match='code/'):
        experiment_client.code_selection(config,outside)
    with pytest.raises(e.CallerError,match='code/'):
        experiment_client.code_selection(config,outside,experiment_directory=directory)
    for invalid in (work/'experiments',code_root,source.parent):
        with pytest.raises(e.CallerError,match='experiments/<name>'):
            experiment_client.code_selection(config,source,experiment_directory=invalid)
    data=root/'controlled-test-inputs/overlap';data.parent.mkdir(parents=True);data.write_bytes(b'data')
    material=dict(path='code/input-data',source=str(data),size_bytes=data.stat().st_size)
    c.write(root/'jobs'/f'{job["step"]:03d}.json',dict(job,global_materials=[*job['global_materials'],material]))
    with pytest.raises(e.CallerError,match='overlaps a bound scientific input'):
        experiment_client.code_selection(config,source,experiment_directory=directory)


def test_new_local_code_is_fixed_before_metadata_and_prepare_without_draft_inventory(fragment, monkeypatch, execution_registry, fixed_code):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    directory=c.workspace(root,job['step'])/'experiments/example';directory.mkdir(parents=True)
    (directory/'runtime-state').write_bytes(b'not a declared scientific input')
    metadata_method=LocalExecution.acceptance_metadata;prepare_method=LocalExecution.prepare;seen=[]
    def forbidden(*args,**options):pytest.fail('Local fixed-code preparation inventoried a mutable draft directory')
    monkeypatch.setattr(LocalExecution,'files',forbidden)
    def metadata(self,payload,**options):
        key=(root,job['step'],payload.name)
        assert options['_code_version']==fixed_code['bindings'][key]
        assert payload.local_path!=str(source)
        assert Path(payload.local_path).read_bytes()==b'original opaque source'
        assert options['command_options']['source_script']==str(source)
        seen.append(('metadata',payload));source.unlink()
        result=metadata_method(self,payload,**options)
        assert not {'code_version','entry','source_reference','source_input'}&result.keys()
        return result
    def prepare(self,payload,**options):
        assert seen==[('metadata',payload)]
        assert not source.exists()
        seen.append(('prepare',payload))
        return prepare_method(self,payload,**options)
    monkeypatch.setattr(LocalExecution,'acceptance_metadata',metadata)
    monkeypatch.setattr(LocalExecution,'prepare',prepare)
    prepared,accepted=_prepare(config,source,directory=directory)
    assert seen==[('metadata',prepared),('prepare',prepared)]
    assert accepted['script']['path']==prepared.local_path
    assert accepted['source_script']==accepted['command'][accepted['script_index']]==str(source)
    assert accepted['cwd']==str(directory) and accepted['snapshot_files']==[]
    assert set(accepted['input_sources']['view'])=={'job','native_intent','configuration','prepared','files'}
    assert accepted['input_sources']['view']['files']==[]
    assert not {'entry','source_reference','source_input'}&accepted.keys()
    assert e.verify_scientific_inputs(accepted,config=config)==accepted


@pytest.mark.parametrize('callback',['acceptance_metadata','input_sources'])
def test_local_callbacks_cannot_supply_or_mutate_the_host_code_version(fragment, monkeypatch, execution_registry, fixed_code, callback):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    if callback=='acceptance_metadata':
        method=LocalExecution.acceptance_metadata
        def metadata(self,payload,**options):
            return dict(method(self,payload,**options),code_version=options['_code_version'])
        monkeypatch.setattr(LocalExecution,'acceptance_metadata',metadata)
    else:
        def input_sources(self,payload,metadata):
            metadata['code_version']['commit']='b'*40
            return None
        monkeypatch.setattr(LocalExecution,'input_sources',input_sources)
    with pytest.raises(e.IntegrityError,match='host-owned code versions'):_prepare(config,source)
    assert e.Records(config).calls()==[] and len(fixed_code['cleaned'])==1


def test_registered_inputs_and_host_compiled_packages_keep_head_retained_slots(fragment, monkeypatch, execution_registry):
    root,job,_=fragment;config=_configuration(root,job);work=c.workspace(root,job['step'])
    logical=work/'inputs/global/registered'
    material,reference,original=_frozen_material(root,work,logical,b'global-original',name='registered')
    c.write(root/'jobs'/f'{job["step"]:03d}.json',dict(job,global_materials=[*job['global_materials'],material]))
    package=Path(config['record_dir']).parent/'implementations/I001/package.json'
    package.parent.mkdir(parents=True);package.write_bytes(b'host-owned compiled bytes')
    def forbidden(*args,**options):pytest.fail('A bound scientific input entered Git capture')
    monkeypatch.setattr(code_versions,'capture',forbidden)
    for selected,logical_source in ((logical,logical),(package,work/'artifacts/client.json')):
        records=e.Records(config);adapter=_Adapter();identity=e.proof(selected)
        payload=LocalFileDescriptor(name='direct',local_path=str(selected),size_bytes=identity['size_bytes'],media_type='application/octet-stream')
        _,accepted=e._prepare_descriptor(config,payload,adapter,records,[path.name for path in records.calls()],_source_script=str(logical_source))
        retained=records.directory(accepted['call_id'])/'source.py'
        assert accepted['script']==c.file_ref(retained) and retained.read_bytes()==selected.read_bytes()
        assert set(accepted['script'])=={'path','size_bytes'}
        assert 'code_version' not in accepted and e.verify_scientific_inputs(accepted)==accepted
        assert accepted['script']['path']!=str(selected)
    assert experiment_client.input_reference(config,logical)==reference
    assert original.read_bytes()==b'global-original'


def test_accepted_control_survives_deleted_export_but_consumption_requires_exact_version(fragment, execution_registry, fixed_code):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    _,accepted=_prepare(config,source,adapter=_Adapter());records=e.Records(config)
    saved=(records.directory(accepted['call_id'])/'accepted.json').read_bytes()
    code_versions.cleanup_export(root,step=job['step'],call_id=accepted['call_id']);source.unlink()
    assert records.accepted(accepted['call_id'])==accepted
    assert e.verify_scientific_inputs(accepted,config=config)==accepted
    fixed_code['bindings'].clear()
    assert records.accepted(accepted['call_id'])==accepted
    with pytest.raises(e.IntegrityError,match='code version original'):
        e.verify_scientific_inputs(accepted,config=config)
    assert (records.directory(accepted['call_id'])/'accepted.json').read_bytes()==saved


@pytest.mark.parametrize('prepared_kind',['transformed','export'])
@pytest.mark.parametrize('drift',['changed','removed'])
def test_pre_submit_refuses_drifted_prepared_descriptor(fragment,monkeypatch,execution_registry,fixed_code,prepared_kind,drift):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    adapter=_SubmissionSpy(prepare=(lambda payload:_transformed_submission(config,payload))
        if prepared_kind=='transformed' else None)
    prepared,accepted=_prepare(config,source,adapter=adapter);records=e.Records(config)
    accepted_path=records.directory(accepted['call_id'])/'accepted.json';saved=accepted_path.read_bytes()
    path=Path(prepared.local_path)
    if drift=='changed':
        replacement=path.with_name(path.name+'.replacement')
        replacement.write_bytes(path.read_bytes());replacement.replace(path)
    else:path.unlink()
    # Permanent code/data verification and control queries still do not need
    # the temporary prepared file. Only the actual submission consumes it.
    assert e.verify_scientific_inputs(accepted,config=config)==accepted
    assert records.accepted(accepted['call_id'])==accepted
    monkeypatch.setattr(e,'record_observation',lambda *args,**options:None)
    with pytest.raises((e.IntegrityError,OSError)):
        e._submit_descriptor(config,adapter,LifecycleCapabilities(query=False),prepared,accepted)
    assert adapter.leased==[] and adapter.submitted==[]
    assert records.external(accepted['call_id']) is None
    assert accepted_path.read_bytes()==saved


def test_pre_submit_accepts_unchanged_transformed_descriptor_once(fragment,monkeypatch,execution_registry,fixed_code):
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    adapter=_SubmissionSpy(prepare=lambda payload:_transformed_submission(config,payload))
    prepared,accepted=_prepare(config,source,adapter=adapter)
    assert prepared.local_path!=adapter.prepared[0].local_path
    assert Path(prepared.local_path).read_bytes()!=Path(adapter.prepared[0].local_path).read_bytes()
    monkeypatch.setattr(e,'record_observation',lambda *args,**options:None)
    result=e._submit_descriptor(config,adapter,LifecycleCapabilities(query=False),prepared,accepted)
    assert result==dict(status='known_success',call_id=accepted['call_id'])
    assert adapter.leased==[accepted['call_id']] and adapter.submitted==[prepared]
    assert e.Records(config).external(accepted['call_id'])==accepted['call_id']


@pytest.fixture
def terminal_code(fragment,monkeypatch,execution_registry,fixed_code):
    """Synthetic stopped originals only; Git and every launch entry stay mocked."""
    root,job,_=fragment;config=_configuration(root,job)
    def forbidden(*args,**options):pytest.fail('Terminal source fixture reached a process launch')
    monkeypatch.setattr(LocalExecution,'submit',forbidden)
    monkeypatch.setattr(LocalExecution,'_execute',forbidden)
    monkeypatch.setattr(worker_execution_bridge,'local_process_launch',forbidden)
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    def create(*,replay=False,returncode=0):
        source=_code(root,job);events=[]
        runtime=c.workspace(root,job['step'])/'experiments/terminal';runtime.mkdir(parents=True,exist_ok=True)
        adapter=LocalExecution({key:value for key,value in config.items() if key!='execution_interface'},
            frozen_interface=config['execution_interface'])
        if replay:
            capability=AdapterCapability(replay_check.KEY,'engineering','fixture.replay.v1',1,
                'application/json',e.encoded(dict(check_scope=dict(fixture='source only'),
                    max_seconds=5,feedback_seconds=.01)),{})
            adapter.capabilities=lambda:(*LocalExecution.capabilities(),capability)
            def check(call_id,**options):
                directory=e.Records(config).directory(call_id)
                assert (directory/'completed.json').exists()
                assert not (directory/'execution-observation.json').exists()
                assert Path(e.Records(config).accepted(call_id)['script']['path']).exists()
                events.append('replay')
                return dict(status='sample_replay_ok',note='Fixture stopped',cleanup_confirmed=True)
            adapter.replay_check=check
        _,accepted=_prepare(config,source,directory=runtime,adapter=adapter)
        call_id=accepted['call_id'];records=e.Records(config);directory=records.directory(call_id)
        owner=dict(call_id=call_id,scope=accepted['scope'])
        c.write(directory/'process.json',dict(owner,task_ref=dict(path=str(directory/'accepted.json')),
            pid=999999999,start_ticks=1,pgid=999999999,sid=999999999))
        (directory/'stdout.raw').write_bytes(b'fixture stdout\n');(directory/'stderr.raw').write_bytes(b'')
        c.write(directory/'terminal.json',dict(owner,returncode=returncode,cleanup_confirmed=True,
            transport_complete=True,wall_seconds=.01))
        output=local_experiments.artifact_directory(config,call_id)/'result.txt'
        output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(b'retained scientific output')
        completed=adapter.completion(directory,accepted,returncode,True,True)
        assert completed['outputs']==[dict(e.proof(directory/'outputs/result.txt'),name='result.txt')]
        assert completed['outputs'][0]['path']!=str(output)
        c.write(directory/'completed.json',completed)
        records.bind_external(call_id,call_id,'succeeded' if returncode==0 else 'failed')
        public=adapter.public_result
        def project(value):
            assert Path(accepted['script']['path']).exists()
            events.append('projection')
            return public(value)
        adapter.public_result=project
        return dict(root=root,job=job,config=config,adapter=adapter,accepted=accepted,
            directory=directory,output=output,events=events,key=(root,job['step'],call_id))
    return create


@pytest.mark.parametrize('returncode',[0,2])
def test_terminal_export_cleanup_follows_saved_projection_and_replay(terminal_code,fixed_code,monkeypatch,returncode):
    row=terminal_code(replay=True,returncode=returncode);call_id=row['accepted']['call_id']
    config=row['config'];directory=row['directory'];cleanup=code_versions.cleanup_export
    def checked_cleanup(root,*,step,call_id,**options):
        observed=c.read(directory/'execution-observation.json')
        assert e.verify_observation(observed,lambda item:Path(item['path']))==observed['status']
        assert replay_check.cleanup_confirmed(config,call_id,observed)
        assert observed['source_code_version']==row['accepted']['code_version']
        assert observed['scientific_source'] is None and observed['scientific_outputs']
        assert row['events'][-1]=='projection'
        row['events'].append('cleanup')
        cleanup(root,step=step,call_id=call_id,**options)
    monkeypatch.setattr(code_versions,'cleanup_export',checked_cleanup)
    observed=e.record_observation(config,row['adapter'],call_id)
    assert not fixed_code['exports'][row['key']].exists()
    assert row['events'][-1]=='cleanup'
    assert row['events'].count('replay')==(returncode==0)
    originals={name:(directory/name).read_bytes() for name in (
        'accepted.json','completed.json','submission.json','terminal.json','execution-observation.json')}
    # Returning saved control facts and collected outputs never consumes the export.
    adapter=LocalExecution(config)
    assert adapter.status_details(call_id)['state']==('succeeded' if returncode==0 else 'failed')
    assert adapter.result(call_id)['status']==observed['status']
    assert adapter.collect_original(call_id)
    monkeypatch.setattr(code_versions,'cleanup_export',cleanup)
    assert e.record_observation(config,adapter,call_id,verify_files=False)==observed
    assert e.scientific_outputs(config,observed)
    assert row['output'].read_bytes()==b'retained scientific output'
    assert all((directory/name).read_bytes()==raw for name,raw in originals.items())


@pytest.mark.parametrize('damage',['unconfirmed','missing','binding','corrupt'])
def test_terminal_cleanup_retains_exports_without_exact_replay_cleanup(terminal_code,fixed_code,monkeypatch,damage):
    row=terminal_code(replay=True);config=row['config'];directory=row['directory'];call_id=row['accepted']['call_id']
    observed=e.record_observation(config,row['adapter'],call_id)
    assert replay_check.cleanup_confirmed(config,call_id,observed)
    fixed_code['materialize'](row['root'],step=row['job']['step'],call_id=call_id,
        code_version=row['accepted']['code_version']);fixed_code['cleaned'].clear()
    outcome=directory/replay_check.OUTCOME
    if damage=='unconfirmed':
        value=c.read(outcome);value['result']['cleanup_confirmed']=False;c.write(outcome,value)
    elif damage=='missing':outcome.unlink()
    elif damage=='binding':
        intent=directory/replay_check.INTENT;value=c.read(intent);value['check']={'wrong':'check'};c.write(intent,value)
        value=c.read(outcome);value['intent_ref']=e.proof(intent);c.write(outcome,value)
    else:
        replacement=outcome.with_name('corrupt-outcome.json')
        replacement.write_bytes(b'{malformed')
        replacement.replace(outcome)
    assert not replay_check.cleanup_confirmed(config,call_id,observed)
    def forbidden(*args,**options):pytest.fail('An existing engineering intent was replayed')
    monkeypatch.setattr(row['adapter'],'replay_check',forbidden)
    replay_check.complete(config,row['adapter'],call_id,observed)
    monkeypatch.setattr(replay_check,'complete',forbidden)
    assert e.record_observation(config,LocalExecution(config),call_id,verify_files=False)==observed
    assert fixed_code['exports'][row['key']].exists() and fixed_code['cleaned']==[]


def test_unsupported_replay_cleanup_requires_no_orphan_outcome(terminal_code,fixed_code):
    row=terminal_code();config=row['config'];directory=row['directory'];call_id=row['accepted']['call_id']
    observed=e.record_observation(config,row['adapter'],call_id)
    assert replay_check.cleanup_confirmed(config,call_id,observed)
    fixed_code['materialize'](row['root'],step=row['job']['step'],call_id=call_id,
        code_version=row['accepted']['code_version']);fixed_code['cleaned'].clear()
    c.write(directory/replay_check.OUTCOME,dict(result=dict(cleanup_confirmed=True)))
    assert not replay_check.cleanup_confirmed(config,call_id,observed)
    e._cleanup_terminal_code_export(config,call_id,observed)
    assert fixed_code['exports'][row['key']].exists() and fixed_code['cleaned']==[]


def test_terminal_cleanup_failure_is_private_and_preserves_originals(terminal_code,fixed_code,monkeypatch):
    row=terminal_code();config=row['config'];directory=row['directory'];call_id=row['accepted']['call_id']
    observed=e.record_observation(config,row['adapter'],call_id)
    assert not fixed_code['exports'][row['key']].exists()
    tree=fixed_code['materialize'](row['root'],step=row['job']['step'],call_id=call_id,
        code_version=row['accepted']['code_version'])
    originals={name:(directory/name).read_bytes() for name in (
        'accepted.json','completed.json','submission.json','terminal.json','execution-observation.json')}
    cleanup=code_versions.cleanup_export
    def fail(*args,**options):raise OSError('fixture deletion denied')
    monkeypatch.setattr(code_versions,'cleanup_export',fail)
    assert e.record_observation(config,LocalExecution(config),call_id,verify_files=False)==observed
    assert tree.exists() and all((directory/name).read_bytes()==raw for name,raw in originals.items())
    diagnostic,=(Path(config['record_dir']).parent/'execution-diagnostics').iterdir()
    note=c.read(diagnostic)['note']
    assert str(tree) in note and call_id in note and 'OSError: fixture deletion denied' in note
    assert 'fixture deletion denied' not in str(observed)
    monkeypatch.setattr(code_versions,'cleanup_export',cleanup)
    other=terminal_code();other_id=other['accepted']['call_id']
    assert e.record_observation(config,other['adapter'],other_id)['status']=='known_success'
    assert tree.exists() and not fixed_code['exports'][other['key']].exists()
    assert e.progress(config)['counts']['known_success']==2


@pytest.mark.parametrize('inventory',['global_materials','history'])
def test_head_input_rows_without_fingerprints_bind_frozen_call_slots(fragment,execution_registry,inventory):
    root,job,_=fragment;config=_configuration(root,job);work=c.workspace(root,job['step'])
    logical=work/'experiments/input-slots/data.dat'
    material,reference,source=_frozen_material(root,work,logical,name='row-'+inventory)
    changed=dict(job)
    if inventory=='global_materials':changed['global_materials']=[material]
    else:changed['history']=dict(files=[material],execution_recovery_materials=[])
    c.write(root/'jobs'/f'{job["step"]:03d}.json',changed)
    assert experiment_client.input_reference(config,logical)==reference
    assert experiment_client.input_reference(config,source)==e.proof(source)
    assert reference['path']==str(logical) and reference['file_stat']!=e.proof(source)['file_stat']
    saved=deepcopy(material)
    replacement=logical.with_name('same-size-replacement')
    replacement.write_bytes(logical.read_bytes());replacement.replace(logical)
    # HEAD stores no global inode: the call acquires the current ordinary slot's
    # fingerprint only after the frozen resolver and bound size succeed.
    assert experiment_client.input_reference(config,logical)==e.proof(logical)
    assert material==saved and set(material)=={'path','source','size_bytes'}


def test_host_preparation_snapshot_and_existing_reader_accept_owner_execution_refs(fragment,execution_registry):
    from scidiscovery.research_loop import preparation_recovery as recovery,qualification as q,storage_policy
    root,job,_=fragment;config=_configuration(root,job);records=Path(config['record_dir'])
    directory=records/'.prepared-g1-owned-reference';directory.mkdir(parents=True)
    source=c.workspace(root,job['step'])/'work/preparation-source.py';source.write_bytes(b'not submitted')
    descriptor=LocalFileDescriptor(name='0001-owned-preparation',local_path=str(source),size_bytes=source.stat().st_size,
        media_type='application/octet-stream')
    accepted=dict(call_id=descriptor.name,scope=config['scope'],native_attempt=config['native_attempt'],
        interface=config['execution_interface'],prepared=descriptor.model_dump(mode='json'),snapshot_files=[],
        script=e.proof(source),source_script=str(source),parent=dict(candidate_id=None))
    reference=recovery.bind_snapshot(config,directory,descriptor,accepted,context=storage_policy.Deadline(10))
    assert set(reference)=={'path','size_bytes','file_stat'}
    rows=recovery.retained_preparations(root,records)
    assert rows==[dict(call_id=descriptor.name,snapshot_token='g1-owned-reference',binding=reference,
        resolved=False,status='unknown',snapshot=str(directory))]
    value=c.read(Path(reference['path']))
    assert all(set(value[key])=={'path','size_bytes','file_stat'} for key in (
        'job','configuration','projection','native_intent'))
    assert set(q._ref(root,root/'jobs'/f'{job["step"]:03d}.json'))=={'path','size_bytes'}
    assert not (records/'calls'/descriptor.name).exists()


@pytest.mark.parametrize('prepared_kind',['export','transformed-control'])
def test_generic_git_preparation_reader_preserves_published_calls_without_code_copy(
        fragment,execution_registry,fixed_code,monkeypatch,prepared_kind):
    from scidiscovery.research_loop import preparation_recovery as recovery
    root,job,_=fragment;config=_configuration(root,job);source=_code(root,job)
    adapter=_Adapter(prepare=(lambda payload:_transformed_submission(config,payload))
        if prepared_kind=='transformed-control' else None)
    _,accepted=_prepare(config,source,adapter=adapter);records=e.Records(config)
    directory=records.directory(accepted['call_id']);binding=Path(accepted['host_preparation']['path'])
    originals={path:path.read_bytes() for path in (directory/'accepted.json',binding)}
    assert not (directory/'source.py').exists() and not (directory/'inputs').exists()
    code_versions.cleanup_export(root,step=job['step'],call_id=accepted['call_id'])
    assert not fixed_code['exports'][(root,job['step'],accepted['call_id'])].exists()
    rows=recovery.retained_preparations(root,records.root)
    assert len(rows)==1 and rows[0]['resolved'] is True and rows[0]['status']=='published'
    assert e._pending_dispatch(config,root) is None
    if prepared_kind=='transformed-control':
        packet=Path(accepted['script']['path'])
        assert packet.exists() and packet==Path(accepted['prepared']['local_path'])
        replacement=packet.with_name('replacement-control');replacement.write_bytes(packet.read_bytes());replacement.replace(packet)
        with pytest.raises(recovery.PreparationError,match='code lost its host preparation binding'):
            e._pending_dispatch(config,root)
        assert len(records.calls())==1 and len(adapter.prepared)==1
    else:
        successor=_Adapter();_,second=_prepare(config,source,adapter=successor)
        assert second['call_id']!=accepted['call_id'] and len(successor.prepared)==1
        assert len(records.calls())==2 and all(row['resolved'] for row in recovery.retained_preparations(root,records.root))
    assert all(path.read_bytes()==raw for path,raw in originals.items())


def test_legacy_local_completion_freezes_first_observation_with_outputs_and_logs(
        fragment,execution_registry,monkeypatch):
    root,job,_=fragment;config=_configuration(root,job);records=e.Records(config)
    runtime=c.workspace(root,job['step'])/'experiments/legacy-terminal';runtime.mkdir(parents=True)
    source=runtime/'run.py';source.write_bytes(b'inert legacy source; never executed')
    payload=LocalFileDescriptor(name='0001-legacy-terminal',local_path=str(source),
        size_bytes=source.stat().st_size,media_type='application/octet-stream')
    adapter=LocalExecution({key:value for key,value in config.items() if key!='execution_interface'},
        frozen_interface=config['execution_interface'])
    def forbidden(*args,**options):pytest.fail('Legacy terminal observation reached a process launch')
    monkeypatch.setattr(LocalExecution,'submit',forbidden)
    monkeypatch.setattr(LocalExecution,'_execute',forbidden)
    monkeypatch.setattr(worker_execution_bridge,'local_process_launch',forbidden)
    for name in ('Popen','run','call'):monkeypatch.setattr(subprocess,name,forbidden)
    metadata=adapter.acceptance_metadata(payload,command_options=dict(experiment_directory=str(runtime)))
    metadata['snapshot_files']=[{key:item[key] for key in ('name','path','size_bytes')}
        for item in metadata['snapshot_files']]
    accepted=records.accept(payload,metadata,config['execution_interface'],_local_preparation=True)
    directory=records.directory(payload.name);owner=dict(call_id=payload.name,scope=accepted['scope'])
    c.write(directory/'process.json',dict(owner,task_ref=dict(path=str(directory/'accepted.json')),
        pid=999999999,start_ticks=1,pgid=999999999,sid=999999999))
    (directory/'stdout.raw').write_bytes(b'legacy stdout\n');(directory/'stderr.raw').write_bytes(b'legacy stderr\n')
    c.write(directory/'terminal.json',dict(owner,returncode=0,cleanup_confirmed=True,
        transport_complete=True,wall_seconds=.01))
    artifact=directory/'inputs/artifacts/result.txt';artifact.parent.mkdir();artifact.write_bytes(b'legacy retained result')
    completed=adapter.completion(directory,accepted,0,True,True)
    # Serialize the historical path/size contract from real completion originals.
    completed['proofs']=[{key:item[key] for key in ('path','size_bytes')} for item in completed['proofs']]
    completed['outputs']=[{key:item[key] for key in ('name','path','size_bytes')} for item in completed['outputs']]
    records.publish(payload.name,'completed.json',completed);records.bind_external(payload.name,payload.name,'succeeded')
    originals={path:path.read_bytes() for path in (directory/'accepted.json',directory/'completed.json',
        directory/'process.json',directory/'terminal.json',directory/'stdout.raw',directory/'stderr.raw')}
    assert 'code_version' not in accepted and all(set(item)=={'path','size_bytes'} for item in completed['proofs'])
    assert not (directory/'execution-observation.json').exists()
    assert adapter.status_details(payload.name)['state']=='succeeded' and adapter.result(payload.name)==completed
    observed=e.record_observation(config,adapter,payload.name)
    assert observed['status']=='known_success' and observed['scientific_source']['source']==accepted['script']
    outputs={item['name']:Path(item['path']).read_bytes() for item in observed['scientific_outputs']}
    assert outputs=={'result.txt':b'legacy retained result','execution-logs/stdout.txt':b'legacy stdout\n',
        'execution-logs/stderr.txt':b'legacy stderr\n'}
    assert e.verify_observation(observed,lambda item:Path(item['path']))=='known_success'
    saved=(directory/'execution-observation.json').read_bytes()
    assert e.record_observation(config,adapter,payload.name)==observed
    assert (directory/'execution-observation.json').read_bytes()==saved
    assert all(path.read_bytes()==raw for path,raw in originals.items())


def test_legacy_partial_discard_retry_reuses_saved_reference_shapes_and_bytes(fragment,execution_registry):
    from scidiscovery.research_loop import preparation_recovery as recovery,storage_policy
    root,job,_=fragment;config=_configuration(root,job);records=Path(config['record_dir'])
    snapshot=records/'.prepared-legacy-discard';snapshot.mkdir(parents=True)
    source=c.workspace(root,job['step'])/'work/legacy-discard.py';source.write_bytes(b'not submitted')
    descriptor=LocalFileDescriptor(name='0001-legacy-discard',local_path=str(source),
        size_bytes=source.stat().st_size,media_type='application/octet-stream')
    accepted=dict(call_id=descriptor.name,scope=config['scope'],native_attempt=config['native_attempt'],
        interface=config['execution_interface'],prepared=descriptor.model_dump(mode='json'),snapshot_files=[],
        script=e.proof(source),source_script=str(source),parent=dict(candidate_id=None))
    reference=recovery.bind_snapshot(config,snapshot,descriptor,accepted,context=storage_policy.Deadline(10))
    binding=Path(reference['path']);value=c.read(binding)
    legacy={key:reference[key] for key in ('path','size_bytes')}
    intent=binding.with_name('legacy-discard.discard.intent.json')
    outcome=binding.with_name('legacy-discard.discard.outcome.json')
    c.write(intent,dict(version=1,kind='discard_partial_snapshot',binding=legacy,
        directory_identity=value['directory_identity'],reason='Original completed cleanup'))
    c.write(outcome,dict(version=1,kind='partial_snapshot_discarded',binding=legacy,intent=c.file_ref(intent)))
    snapshot.rmdir()  # The old cleanup finished; only its exact retained receipts remain.
    originals={path:path.read_bytes() for path in (binding,intent,outcome)}
    for supplied in (reference,legacy):
        result=recovery.discard_owned_snapshot(config,supplied,reason='Retry must retain the original reason',
            context=storage_policy.Deadline(10))
        assert result==dict(status='partial_discarded',call_id=descriptor.name,snapshot_token='legacy-discard',
            binding=legacy,external_preparation_state='unknown')
    assert not snapshot.exists() and not (records/'calls'/descriptor.name).exists()
    assert all(path.read_bytes()==raw for path,raw in originals.items())
