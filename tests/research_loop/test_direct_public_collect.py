"""Local originals use the public root already bound before backend output."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from execution_fixtures import execution_registry
from test_cov_lineage import cov_completed, wrapper
from test_mutable_run_inputs import fixed_code, terminal_code
from test_worker_fragments import fragment
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import (contracts as c, experiments as e,
    execution, local_experiments, native, runtime, shared_input_views)
from scidiscovery.research_loop.local_experiments import LocalExecution


def inode(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino


def replace_equal_bytes(path):
    """Preserve size and mtime, changing only the original entity."""
    import os
    path = Path(path)
    saved = path.stat()
    replacement = path.with_name(path.name + '.replacement')
    replacement.write_bytes(path.read_bytes())
    os.utime(replacement, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    replacement.chmod(saved.st_mode & 0o777)
    replacement.replace(path)


def stopped_attempt(root, job, record, monkeypatch):
    """Inject physical stop facts while retaining the real stop validator."""
    identity = execution.identity(root, job['step'], 0)
    receipts = {}
    for number, name in enumerate(('runner-process.json', 'native-process.json', 'bridge-process.json'), 1):
        pid = 900000000 + number
        receipt = dict(pid=pid, start_ticks=1, pgid=pid, sid=pid,
            boot_id='batch-a-physical-state-fixture', task_ref=identity)
        c.write(record / name, receipt)
        receipts[name] = receipt
    c.write(record / 'bridge-outcome.json',
        dict(process=receipts['bridge-process.json'], cleanup='observed_group_cleaned'))
    previous = c.read(record / 'native-outcome.json')
    c.write(record / 'native-outcome.json', dict(previous, native_started=True,
        cleanup='observed_group_cleaned', usage=native.usage(root),
        execution_processes={name: c.record_ref(record / name)
            for name in ('runner-process.json', 'native-process.json')}))
    live = set()
    def process_matches(receipt, expected):
        assert receipt['task_ref'] == expected
        return receipt['pid'] in live
    monkeypatch.setattr(runtime, 'process_matches', process_matches)
    return live, receipts


@pytest.fixture
def observed_local(terminal_code):
    """Use the real preparation, completion and observation producers."""
    def create(**options):
        row = terminal_code(**options)
        row['observation'] = e.record_observation(
            row['config'], row['adapter'], row['accepted']['call_id'])
        return row
    return create


def test_cov_collection_reuses_receipt_model_original(cov_completed):
    row=cov_completed()
    adapter,completed=row['adapter'],row['completed']
    model=row['model'];call=row['accepted']['call_id']
    saved=e.proof(model)
    assert completed['status']=='known_success' and completed['model']==saved
    assert completed['model'] in completed['proofs']
    first=adapter.collect_original(call)
    second=adapter.collect_original(call)
    assert first==second
    selected=adapter.scientific_outputs(first,projection_directory=row['directory'])
    assert len(selected)==1 and selected[0].name=='model.pkl'
    assert selected[0].local_path==str(model) and e.proof(model)==saved
    assert not (row['directory']/'model.pkl').exists()
    assert not (row['directory']/'observed-originals').exists()
    assert model.stat().st_mode&0o777==0o400
    replace_equal_bytes(model)
    with pytest.raises(e.IntegrityError,match='proof changed'):
        adapter.collect_original(call)


def test_tcad_collection_reuses_package_and_output_originals(tmp_path,monkeypatch):
    import sys
    from tcad_artifact import managed_execution as tcad
    from tcad_artifact.command_adapter import CommandAdapterConfig,CommandTCADExecutorAdapter
    from tcad_artifact.execution_adapter import TCADExecutorAdapter
    from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission
    from scidiscovery.plugin_runtime.collection import CollectionContext
    from scidiscovery.research_loop.protocol import CURRENT

    fixture=json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/tcad_analysis_package.json').read_text())
    for field in ('source_tree_sha256','project_sha256','declarations_sha256'):
        fixture['project']['preflight_attestation'].pop(field,None)
    package=tcad.ExecutionPackage.model_validate_json(json.dumps({key:fixture[key]
        for key in ('project','review','capability')}),strict=True)
    raw=json.dumps(package.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,
        allow_nan=False,separators=(',',':')).encode()
    source=tmp_path/'external-package.json';source.write_bytes(raw)
    call='tcad-fixture-call'
    payload=LocalFileDescriptor(name=call,local_path=str(source),size_bytes=len(raw),media_type='application/json')
    scope=dict(handoff_protocol=CURRENT,step=1,role='W',mechanism_ref='h001.m1',
        job_ref=dict(path=str(tmp_path/'jobs/001.json')),execution_key=dict(path=str(tmp_path/'jobs/001.json')))
    command_config=CommandAdapterConfig(executable=sys.executable)
    command_path=tmp_path/'command.json';e.write(command_path,command_config.model_dump(mode='json'))
    config=dict(transport='command',command_config_path=str(command_path),record_dir=str(tmp_path/'records'),
        scope=scope,max_seconds=65,worker_view_version=1,admission=dict(version=1,mode='hard'))
    config['execution_interface']=dict(version=e.VERSION,executor='tcad_execution:tcad',
        observation_version=e.OBSERVATION_VERSION,terminal_evidence='adapter_result',result_protocol=None,
        config_record=dict(path=str(tmp_path/'frozen-configuration.json')),
        capabilities=vars(tcad.TCADManagedExecution.lifecycle_capabilities()))
    transport=CommandTCADExecutorAdapter(command_config,local_result_root=tmp_path/'private-command-results')
    transport_calls=[]
    def response(method,arguments):
        transport_calls.append(method)
        if method=='capabilities':
            assert arguments=={}
            return dict(capabilities=[package.capability.model_dump(mode='json')])
        assert method=='prepare'
        marker=Path(arguments['execution_exchange'])/'submission.json'
        e.write(marker,dict(request_id='original-preparation-request'))
        return dict(submission=LocalFileDescriptor(name='prepared-marker',local_path=str(marker),
            size_bytes=marker.stat().st_size,media_type='application/json').model_dump(mode='json'))
    monkeypatch.setattr(transport,'_call',response)
    authority=tmp_path/'authority.json'
    e.write(authority,dict(call_id=call,scope=scope,package=e.proof(source)))
    admission=ExecutionAdmission(outcome='policy',policy_digest='0'*64,policy=dict(fixture=True),
        budget=dict(wall_time_seconds=65),allowance={},outside_allowance='deny',budget_key='1'*64,
        reason='Fixture transport has no solver launch')
    gate_calls=[]
    def gate(phase,**request):
        assert request['scope']==scope and request['call_id']==call and request['payload']==payload
        if request['prior_receipt'] is not None:assert request['prior_receipt']==e.proof(authority)
        gate_calls.append(phase)
        return dict(receipt=e.proof(authority),authorization=admission,context=CollectionContext.for_seconds(10))
    managed=tcad.TCADManagedExecution(config,transport,gate=gate)
    socket_config={key:value for key,value in config.items() if key!='command_config_path'}
    socket_config.update(transport='socket',socket_path=str(tmp_path/'unused.sock'),record_dir=str(tmp_path/'socket-records'))
    socket_transport=TCADExecutorAdapter(tmp_path/'unused.sock')
    socket_calls=[]
    def socket_response(method,arguments):
        socket_calls.append((method,arguments))
        raise AssertionError('Unsupported new socket preparation reached the transport')
    monkeypatch.setattr(socket_transport,'_call',socket_response)
    socket=tcad.TCADManagedExecution(socket_config,socket_transport,gate=gate)
    with pytest.raises(e.UnsupportedExecution,match='socket.*first-write'):
        socket.prepare(payload,preparation_profile='research_loop.v1',exchange_directory=socket.records.root)
    assert gate_calls==[] and socket_calls==[] and socket.records.calls()==[]
    assert not socket._stage(call).exists()
    original_package=e.proof(source);package_mode=source.stat().st_mode
    prepared=managed.prepare(payload,preparation_profile='research_loop.v1',exchange_directory=managed.records.root)
    assert managed.prepare(payload,preparation_profile='research_loop.v1',exchange_directory=managed.records.root)==prepared
    assert transport_calls==['capabilities','prepare'] and gate_calls==['prepare']
    binding=managed._preparation(prepared)
    assert binding['retained_payload']['local_path']==str(source)
    assert binding['retained_payload']['file_stat']==original_package['file_stat']
    assert e.proof(source)==original_package and source.stat().st_mode==package_mode
    assert not (managed._stage(call)/'package.json').exists()

    prepared_proof=e.proof(prepared.local_path)
    metadata=managed.acceptance_metadata(prepared)
    accepted=managed.records.accept(prepared,metadata,config['execution_interface'],expected_calls=[])
    directory=managed.records.directory(call)
    assert accepted['script']==prepared_proof and accepted['tcad']==metadata['tcad']
    assert e.proof(prepared.local_path)==prepared_proof
    assert not (directory/'source.py').exists()
    assert not (directory/'tcad-dispatch.json').exists()
    assert managed.records.external(call) is None
    external='original-native-run'
    managed._binding(call,prepared,external)
    managed.records.bind_external(call,external,'succeeded')
    external_outputs=tmp_path/'external-outputs';external_outputs.mkdir()
    science=external_outputs/'science.dat';science.write_bytes(b'native solver data fixture\n')
    log=external_outputs/'solver.log';log.write_bytes(b'native diagnostic fixture\n')
    manifest=external_outputs/'manifest.json'
    e.write(manifest,dict(started_at=None,completed_at='2026-10-25T00:00:00Z',terminal_state='succeeded',
        exit_code=0,error='',outputs=[dict(name='science',relative_path='results/science.dat',
            size_bytes=science.stat().st_size,media_type='application/octet-stream',output_class='solver_native')]))
    descriptors=tuple(LocalFileDescriptor(name=name,local_path=str(path),size_bytes=path.stat().st_size,media_type=media)
        for name,path,media in (('science',science,'application/octet-stream'),
            ('tcad_log',log,'text/plain'),('tcad_manifest',manifest,'application/json')))
    saved={item.name:e.proof(item.local_path) for item in descriptors}
    modes={item.name:Path(item.local_path).stat().st_mode for item in descriptors}
    # Native stop and transport descriptors are the only injected boundary;
    # inventory, terminal binding, completion and collection remain real.
    managed._retain(call,descriptors,dict(state='succeeded',call_id=call,external_run_id=external),
        CollectionContext.for_seconds(10))
    completed=managed.result(call)
    first=managed.collect_original(call);second=managed.collect_original(call)
    assert completed['status']=='known_success' and first==second==descriptors
    assert gate_calls==['prepare','collect'] and transport_calls==['capabilities','prepare']
    assert not (directory/'tcad-originals').exists()
    for item in first:
        assert e.proof(item.local_path)==saved[item.name]
        assert Path(item.local_path).stat().st_mode==modes[item.name]

    base=managed.records.root/'scientific-output-views'
    scoped=base/'calls'/call;scoped.mkdir(parents=True)
    projected=managed.scientific_outputs(first,projection_directory=scoped)
    facts=next(item for item in projected if item.name=='tcad_execution_facts.json')
    assert Path(facts.local_path)==base/'tcad/v3'/call/'tcad_execution_facts.json'
    facts_proof=e.proof(facts.local_path)
    assert managed.scientific_outputs(second,projection_directory=scoped)==projected
    assert e.proof(facts.local_path)==facts_proof
    with pytest.raises(e.IntegrityError,match='terminal state'):
        managed._collection(call,'failed')
    replace_equal_bytes(source)
    with pytest.raises(e.IntegrityError,match='original proof changed'):
        managed._preparation(prepared)
    assert managed.status_details(call)['state']=='succeeded'
    replace_equal_bytes(science)
    with pytest.raises(e.IntegrityError,match='original proof changed'):
        managed.collect_original(call)


def test_command_tcad_first_writes_node_science_and_keeps_controls_private(tmp_path,monkeypatch):
    import io
    import tarfile
    from tcad_artifact import managed_execution as tcad
    from tcad_artifact.command_adapter import CommandTCADExecutorAdapter
    from tcad_artifact.ssh_transport import SSHRemoteClient,SSHTCADTransport,SSHTCADTransportConfig
    from scidiscovery.plugin_runtime.collection import CollectionContext
    from scidiscovery.research_loop.local_experiments import artifact_directory
    from scidiscovery.research_loop.protocol import CURRENT

    call='node-call';run='run_'+'a'*32
    records=tmp_path/'records';directory=records/'calls'/call;directory.mkdir(parents=True)
    scope=dict(handoff_protocol=CURRENT,step=1,role='W',mechanism_ref='h001.m1',
        job_ref=dict(path=str(tmp_path/'jobs/001.json')),execution_key=dict(path=str(tmp_path/'jobs/001.json')))
    config=dict(record_dir=str(records),scope=scope,max_seconds=65,transport='command')
    prepared=tmp_path/'prepared.json';e.write(prepared,dict(request_id='original-request'))
    owner=e.Records(config)
    e.write(directory/'accepted.json',dict(call_id=call,scope=scope,parent=dict(candidate_id=None),
        prepared=LocalFileDescriptor(name=call,local_path=str(prepared),size_bytes=prepared.stat().st_size,
            media_type='application/json').model_dump(mode='json')))
    owner.bind_external(call,run,'succeeded')
    node=artifact_directory(config,call)
    private=tmp_path/'executor-results';private.mkdir()
    ssh=tmp_path/'ssh-placeholder';ssh.touch()
    remote=SSHRemoteClient(SSHTCADTransportConfig(ssh_executable=str(ssh),destination='test@host',
        remote_helper='/runner',remote_config='/config.json',remote_exchange_root='/exchange',
        max_transfer_bytes=1024*1024),diagnostic_root=private)
    science=b'actual native data\n';log=b'declared scientific native log\n';control=b'private transport capture\n'
    manifest=e.encoded(dict(started_at=None,completed_at='2026-10-25T00:00:00Z',terminal_state='succeeded',
        exit_code=0,error='',outputs=[
            dict(name='science',relative_path='result/science.dat',size_bytes=len(science),
                media_type='application/octet-stream',output_class='solver_native'),
            dict(name='transport',relative_path='.capture/stdout.log',size_bytes=len(control),
                media_type='text/plain',output_class='transport_derived')]))
    bodies={'/remote/science':science,'/remote/log':log,'/remote/transport':control,'/remote/manifest':manifest}
    rows=[dict(name=name,local_path=path,size_bytes=len(bodies[path]),media_type=media)
        for name,path,media in (('science','/remote/science','application/octet-stream'),
            ('tcad_log','/remote/log','text/plain'),('transport','/remote/transport','text/plain'),
            ('tcad_manifest','/remote/manifest','application/json'))]
    downloads=[];spools=[]
    def transfer(operation,payload,_body,*,download_to=None,download_limit=0):
        destination=Path(download_to)
        if operation=='get':
            assert payload['local_path']=='/remote/manifest'
            assert destination.parent==private/'runs'/run
            destination.write_bytes(bodies[payload['local_path']])
            downloads.append(payload['local_path'])
            result=dict(size_bytes=destination.stat().st_size)
        else:
            assert operation=='get_archive' and destination.parent==private
            assert not destination.is_relative_to(node)
            spools.append(destination)
            with tarfile.open(destination,mode='w',format=tarfile.USTAR_FORMAT) as archive:
                entries=e.encoded(payload['files']);entry=tarfile.TarInfo('manifest.json');entry.size=len(entries)
                archive.addfile(entry,io.BytesIO(entries))
                for index,record in enumerate(payload['files']):
                    raw=bodies[record['local_path']];entry=tarfile.TarInfo(f'files/{index}');entry.size=len(raw)
                    archive.addfile(entry,io.BytesIO(raw));downloads.append(record['local_path'])
            result=dict(files=len(payload['files']))
        assert destination.stat().st_size<=download_limit
        return dict(schema_version=2,operation=operation,ok=True,payload=result),b''
    monkeypatch.setattr(remote,'_call',transfer)
    receiver=SSHTCADTransport(remote,local_result_root=private)
    def rpc(method,arguments):
        assert method=='tcad_collect' and arguments==dict(run_id=run)
        return dict(outputs=rows)
    monkeypatch.setattr(receiver,'_rpc',rpc)
    adapter=object.__new__(CommandTCADExecutorAdapter);adapter.local_result_root=private
    requests=[]
    def command(operation,payload):
        assert operation=='collect' and payload==dict(run_id=run,local_result_root=str(node))
        requests.append(payload)
        return receiver.handle(operation,payload)
    monkeypatch.setattr(adapter,'_call',command)
    descriptors=adapter.collect(run,output_directory=node)
    by_name={item.name:item for item in descriptors}
    assert Path(by_name['science'].local_path)==node/'science'
    assert Path(by_name['tcad_log'].local_path)==node/'tcad_log'
    assert Path(by_name['transport'].local_path)==private/'runs'/run/'transport'
    assert Path(by_name['tcad_manifest'].local_path)==private/'runs'/run/'tcad_manifest'
    assert downloads==['/remote/manifest','/remote/science','/remote/log','/remote/transport']
    assert len(spools)==1 and not spools[0].exists()
    assert not (private/'runs'/run/'science').exists() and not (private/'runs'/run/'tcad_log').exists()
    assert not (node/'transport').exists() and not (node/'tcad_manifest').exists()
    saved={item.name:e.proof(item.local_path) for item in descriptors}
    managed=object.__new__(tcad.TCADManagedExecution);managed.config=config;managed.records=owner
    managed.operation_context=None
    managed._retain(call,descriptors,dict(state='succeeded'),CollectionContext.for_seconds(10))
    assert e.proof(node/'science')==saved['science'] and (node/'science').stat().st_mode&0o777==0o400
    assert e.proof(node/'tcad_log')==saved['tcad_log']
    projection=records/'scientific-output-views'/'calls'/call;projection.mkdir(parents=True)
    selected=managed.scientific_outputs(descriptors,projection_directory=projection,output_directory=node)
    assert {item.name for item in selected}=={'science','tcad_log','tcad_execution_facts.json'}
    assert all(Path(item.local_path).is_relative_to(node) for item in selected)
    bindings=records/'scientific-output-views'/'tcad/v3'/call/'private-scientific-path-bindings.json'
    assert bindings.is_file() and not (node/bindings.name).exists()
    facts=e.proof(node/'tcad_execution_facts.json')
    assert managed.scientific_outputs(descriptors,projection_directory=projection,output_directory=node)==selected
    assert e.proof(node/'tcad_execution_facts.json')==facts and len(requests)==1


def test_collect_returns_the_declared_actual_public_original(fragment, terminal_code):
    root, job, _ = fragment
    work = c.workspace(root, job['step'])
    public = shared_input_views.dynamic_root(root, work, 'retained-experiments')
    bound_inode = inode(public)
    mounts = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    assert any(mounts[index:index + 3] == ['--ro-bind', str(public), str(public)]
        for index in range(0, len(mounts), 3))

    row = terminal_code()
    config, adapter, accepted = row['config'], row['adapter'], row['accepted']
    call_id = accepted['call_id']
    assert row['output'].parent == public / call_id / 'outputs'
    assert local_experiments.artifact_directory(config, call_id) == row['output'].parent
    assert inode(public) == bound_inode

    # Exercise the exact Local output-target guard while the prepared export
    # still exists. The public root is already a readonly Worker mount.
    prepared = LocalFileDescriptor.model_validate(accepted['prepared'])
    protected = LocalExecution(dict(adapter.config, cwd=str(public)),
        frozen_interface=config['execution_interface'])
    with pytest.raises(ValueError, match='readonly|read-only|output'):
        protected.acceptance_metadata(prepared,
            command_options=dict(source_script=accepted['source_script']),
            _code_version=accepted['code_version'])
    with pytest.raises(ValueError, match='readonly|read-only|output'):
        adapter.acceptance_metadata(prepared,
            command_options=dict(source_script=accepted['source_script'],
                experiment_directory=str(public)),
            _code_version=accepted['code_version'])

    observed = e.record_observation(config, adapter, call_id)
    saved = {item['name']: item for item in observed['scientific_outputs']}
    first = e._collected_view(config, call_id, observed, adapter)
    second = e._collected_view(config, call_id, observed, adapter)
    assert first == second and first['status'] == 'collected'
    assert {item['name'] for item in first['outputs']} == set(saved)
    for item in first['outputs']:
        original = saved[item['name']]
        assert item['path'] == original['path'] == original['source']['path']
        assert Path(item['path']).is_relative_to(public / call_id / 'outputs')
        assert e.proof(item['path']) == {key: original[key]
            for key in ('path', 'size_bytes', 'file_stat')}
    assert not (row['directory'] / 'outputs').exists()
    assert not (row['directory'] / 'observed-originals').exists()
    assert inode(public) == bound_inode

    # Corrupt only the selected mapping of a producer-created observation.
    # Equal-looking private bytes do not declare a public publication location.
    private = row['directory'] / 'private-result.txt'
    private.write_bytes(row['output'].read_bytes())
    tampered = deepcopy(observed)
    selected = next(item for item in tampered['scientific_outputs'] if item['name'] == 'result.txt')
    selected.update(e.proof(private), source=e.proof(private))
    selected['descriptor']['local_path'] = str(private)
    with pytest.raises((e.IntegrityError, e.UnsupportedExecution), match='public|original|projection'):
        e._collected_view(config, call_id, tampered, adapter)
