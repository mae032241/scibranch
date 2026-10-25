"""Verified permanent non-submission is retained, never a scientific observation.

Only the transport and gate dependencies are trusted stand-ins. Retirement,
archival, W sealing/assessment and A screening use their production validators.
No process, solver, actual authorization or independence qualification is claimed.
"""
import io
from pathlib import Path

import pytest

from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission, LocalFileDescriptor
from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.research_loop import (contracts as c, controller, execution, experiment_client,
    experiments as e, history, search, worker_delivery)
from scidiscovery.research_loop.output_repair import WorkRepairRequired
from scidiscovery.research_loop.protocol import stage_scope
from tcad_artifact.managed_execution import TCADManagedExecution
from execution_fixtures import execution_registry
from tests.operations.test_runtime_plugin_configuration import (
    _ManagedTCADBackend, _small_resolved_execution_package)
from test_breadth_science import prepare, h, worker, findings, a
from test_history_handoff import finish
from test_worker_delivery import output, tree


def test_retired_screen_call_seals_partial_and_cannot_be_reused_as_observed(tmp_path,execution_registry):
    root=prepare(tmp_path);h(root,labels=('P',))
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),
        search_slice=search.accept(root,job)))
    config=dict(transport='socket',socket_path=str(tmp_path/'unused.socket'),scope=stage_scope(root,job),
        record_dir=str(experiment_client.record_directory(root,2)),breadth=job['search']['breadth'],
        native_attempt=dict(number=0,execution_key=execution.identity(root,2)),admission=dict(version=1,mode='hard'))
    # Complete trusted-DI host binding, distinct from source discovery metadata.
    config['execution_interface']=dict(version=e.VERSION,executor='tcad_execution:tcad',
        code=[e.proof(Path(__file__).resolve())],observation_version=e.OBSERVATION_VERSION,
        terminal_evidence=TCADManagedExecution.terminal_evidence_kind,result_protocol=TCADManagedExecution.result_protocol,
        config_record=dict(config),
        capabilities=vars(TCADManagedExecution.lifecycle_capabilities()))
    source=c.workspace(root,2)/'work/package.json'
    source.write_bytes(canonical_json(_small_resolved_execution_package().model_dump(mode='json')))
    identity=e.proof(source)
    payload=LocalFileDescriptor(name='0001-retired',local_path=str(source),
        size_bytes=identity['size_bytes'],media_type='application/json')
    owner=record/'trusted-owner.json';c.write(owner,dict(call_id=payload.name,scope=config['scope']))
    admission=ExecutionAdmission(outcome='policy',policy_digest='3'*64,policy={},
        budget=dict(max_storage_bytes=2_000_000_000,wall_time_seconds=60),
        allowance=dict(max_storage_bytes=2_000_000_000,wall_time_seconds=60),
        outside_allowance='deny',budget_key='4'*64,reason='Trusted dependency fixture only')
    backend=_ManagedTCADBackend(tmp_path)
    wrapper=TCADManagedExecution(config,backend,gate=lambda phase,**bound:dict(receipt=e.proof(owner),authorization=admission),
        input_metadata=lambda item:(2,'application/json'),open_input=lambda item:io.BytesIO(b'{}'))
    config['execution_interface']['public_capabilities']=e._public_capabilities(wrapper)
    c.write(record/'experiment-config.json',config)
    (record/'experiment-config.json').chmod(0o400)
    prepared=wrapper.prepare(payload,preparation_profile='research_loop.v1',exchange_directory=wrapper.records.root)
    accepted=wrapper.records.accept(prepared,wrapper.acceptance_metadata(prepared),config['execution_interface'],mechanism='P')
    wrapper.dispatch_lease(accepted)
    assert e.retire_original(config,wrapper,payload.name)['status']=='not_submitted'
    changed=dict(config['breadth'],mechanisms=[dict(item,source_h_ref=dict(path='/wrong/h-seal.json'))
        for item in config['breadth']['mechanisms']])
    with pytest.raises(e.IntegrityError,match='Accepted scientific mechanism binding changed'):
        e.Records(dict(config,breadth=changed)).accepted(payload.name)
    directory=wrapper.records.directory(payload.name)
    assert 'submit' not in backend.events and not (directory/'completed.json').exists()
    assert wrapper.records.external(payload.name) is None
    originals={path:path.read_bytes() for path in (record/'native-intent.json',
        record/'experiment-config.json',directory/'accepted.json')}

    record,science=output(root,findings(blocked=('P',)))
    c.seal_result(root,2)
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    facts=worker_delivery.validate(root,job,record)
    assert facts['evidence']==[] and facts['unknown']==[]
    seal=c.read(record/'result-identity.json');observation=c.read(root/seal['observations']['path'])
    row,=observation['managed_calls']
    assert row['status']=='not_submitted' and row['completed'] is None and row['terminal'] is None
    assert row['retirement'] and history.managed_status(root,row)=='not_submitted'
    assert seal['control_outcome']['worker_delivery']['status']=='partial'
    assert seal['control_outcome']['worker_delivery']['reason']=='scientific_blocked'
    assert controller._route(root,2,'W',c.validate(c.workspace(root,2)),job,1,None)[0]=='A'
    with pytest.raises(WorkRepairRequired):
        worker_delivery.require_delivery(root,job,dict(science,mechanism_findings=findings('P')),record)

    # The next W traverses the predecessor-observation selection branch too.
    a(root,3,[1,2]);later,_=worker(root,tmp_path,4,[1,2,3])
    finish(root,4,'W',[1,2,3],mechanism_findings=findings(blocked=('P',)))
    later_facts=worker_delivery.validate(root,later,execution.record(root,4))
    assert later_facts['evidence']==[] and later_facts['unknown']==[]
    a(root,5,[1,4],action='finish',dispositions=[dict(mechanism_ref='h001.m1',status='screened',reason='An observed result would justify screening')])
    control=c.read(execution.record(root,5)/'result-identity.json')['control_outcome']
    assert any(item['code']=='screen_source_unavailable' for item in control['diagnostics'])
    state=search.project(root,6)
    assert state['mechanisms']['h001.m1']['status']=='pending' and state['opportunity_counts']=={'h001.m1':1}
    assert not state['opportunity_unknown']
    before=tree(root);c.validate(c.workspace(root,2));c.validate(c.workspace(root,4));assert tree(root)==before
    proof=root/row['retirement']['snapshot'];proof.chmod(0o600);proof.write_bytes(b'changed retirement original')
    with pytest.raises(history.ObserverIntegrityError):c.validate(c.workspace(root,2))


from contextlib import nullcontext
from copy import deepcopy
from scidiscovery.research_loop import budget, qualification as q


@pytest.fixture
def retirement_control(tmp_path,monkeypatch):
    """Real control refs/retirement validation; only ledger storage is a stand-in."""
    root=tmp_path/'retirement-controls';root.mkdir()
    settings=dict(goal_id='source-fixture')
    monkeypatch.setattr(c,'settings',lambda selected:settings)
    monkeypatch.setattr(budget,'_ledger_writer',lambda selected:nullcontext())
    def create(*,authority_stat=False):
        call_id='0001-source-fixture';scope=dict(step=1,role='W')
        contract=dict(e.RETIREMENT_CONTRACT,backend_protocol=dict(fixture='never submitted'))
        capability=dict(key=e.RETIREMENT_KEY,kind='execution_recovery',schema_id=e.RETIREMENT_SCHEMA,
            payload_schema_version=1,media_type='application/json',size_bytes=len(e.encoded(contract)))
        interface=dict(executor='fixture:source_only',public_capabilities=[capability])
        authority_path=root/'authority.json';c.write(authority_path,dict(fixture='original control'))
        authority=(e.proof(authority_path) if authority_stat else
            dict(q._ref(root,authority_path),path=str(authority_path)))
        source=root/'source.py';source.write_bytes(b'never submitted source')
        prepared=LocalFileDescriptor(name=call_id,local_path=str(source),size_bytes=source.stat().st_size,
            media_type='application/octet-stream').model_dump(mode='json')
        reservation=q._save(root,root/'reservation.json',dict(fixture='held resources'))
        dispatch=root/'dispatch.json'
        accepted=dict(call_id=call_id,scope=scope,native_attempt=dict(number=0),interface=interface,
            script=c.file_ref(source),prepared=prepared,execution_authority=authority,
            execution_reservation=reservation,submission_intent_path=str(dispatch))
        accepted_path=root/'accepted.json';c.write(accepted_path,accepted)
        c.write(dispatch,dict(call_id=call_id,scope=scope,prepared=prepared,authority=authority))
        native=root/'native-fence.json';c.write(native,dict(status='permanent_not_submitted'))
        value=dict(version=2,kind='permanent_not_submitted',call_id=call_id,scope=scope,
            native_attempt=accepted['native_attempt'],interface=interface,accepted=e.proof(accepted_path),
            prepared=prepared,authority=authority,capability=capability,contract=contract,
            native_originals=[e.proof(native)],dispatch_intent=e.proof(dispatch))
        assertion=root/'assertion.json'
        def publish():
            c.write(assertion,dict(version=1,kind='permanent_not_submitted',
                **{key:value[key] for key in ('call_id','scope','accepted','prepared','authority','native_originals')}))
            value['assertion']=e.proof(assertion)
            c.write(root/'retirement.json',value)
            return q._ref(root,root/'retirement.json')
        retirement=publish()
        assert e.verify_retirement(value,lambda item:Path(item['path']))['accepted']==e.proof(accepted_path)
        row=dict(ref=reservation,owner=dict(fixture='one owner'),binding={key:accepted[key] for key in (
            'call_id','scope','native_attempt','interface')},actual={},released=False,
            bound=dict(accepted=q._ref(root,accepted_path),prepared=prepared))
        row['binding']['authority']=authority
        events=[]
        monkeypatch.setattr(budget,'_events',lambda selected,settings,**options:(events,None,({}, {'a'*64:row})))
        def append(selected,settings,previous,parent,**fields):
            assert fields['kind']=='resource_release'
            events.append(fields);row['released']=True
        monkeypatch.setattr(budget,'_append',append)
        return dict(root=root,value=value,row=row,events=events,reservation=reservation,
            retirement=retirement,accepted=accepted,accepted_path=accepted_path,
            authority_path=authority_path,publish=publish)
    return create


@pytest.mark.parametrize('authority_stat',[False,True])
def test_retirement_releases_real_control_reference_shape_once(retirement_control,authority_stat):
    row=retirement_control(authority_stat=authority_stat)
    assert set(row['row']['bound']['accepted'])=={'path','size_bytes'}
    assert set(row['value']['authority'])==({'path','size_bytes','file_stat'} if authority_stat else {'path','size_bytes'})
    for _ in range(2):
        assert budget.release_execution(row['root'],reservation=row['reservation'],
            retirement=row['retirement'])==row['reservation']
    assert row['row']['released'] and len(row['events'])==1


@pytest.mark.parametrize('damage',['accepted','authority','unknown','actual'])
def test_retirement_release_preserves_resources_without_exact_proof(retirement_control,damage):
    row=retirement_control(authority_stat=damage=='authority')
    value=row['value'];root=row['root'];retirement=row['retirement']
    # The fixture first verifies the complete legal original with the real reader.
    assert e.verify_retirement(value,lambda item:Path(item['path']),verify_files=False)
    if damage=='accepted':
        other=root/'other-accepted.json';c.write(other,row['accepted'])
        value['accepted']=e.proof(other);retirement=row['publish']()
        assert e.verify_retirement(value,lambda item:Path(item['path']),verify_files=False)
    elif damage=='authority':
        path=row['authority_path'];replacement=path.with_name('replacement.json')
        replacement.write_bytes(path.read_bytes());replacement.replace(path)
    elif damage=='unknown':
        retirement=q._save(root,root/'unknown.json',dict(status='unknown',found=False))
    else:row['row']['actual']={'wall_time_seconds':1}
    with pytest.raises((ValueError,KeyError)):
        budget.release_execution(root,reservation=row['reservation'],retirement=retirement)
    assert not row['row']['released'] and row['events']==[]
    assert row['row']['actual']==({'wall_time_seconds':1} if damage=='actual' else {})


@pytest.mark.parametrize('key',['accepted','assertion'])
def test_retirement_saved_execution_fingerprint_rejects_equal_byte_replacement(retirement_control,key):
    row=retirement_control();value=row['value']
    assert set(value[key])=={'path','size_bytes','file_stat'}
    assert e.verify_retirement(value,lambda item:Path(item['path']))
    path=Path(value[key]['path']);replacement=path.with_name(path.name+'.replacement')
    replacement.write_bytes(path.read_bytes());replacement.replace(path)
    with pytest.raises(e.IntegrityError,match='Retirement original changed'):
        e.verify_retirement(value,lambda item:Path(item['path']))
    assert not row['row']['released'] and row['events']==[]
