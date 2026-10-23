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
