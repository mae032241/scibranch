"""Science delivery structure, exact frozen observations and read-only validation."""
import os

import pytest

from scidiscovery.research_loop import (contracts as c, execution, native,
    worker_delivery, checkpoint, continuation, controller, budget, runtime)
from scidiscovery.research_loop.output_repair import OutputRepairRequired,WorkRepairRequired
from execution_fixtures import execution_registry
from test_breadth_science import prepare,h,worker,run,findings


def scene(tmp_path):
    root=prepare(tmp_path);h(root,labels=('P','Q'))
    job,config=worker(root,tmp_path,2,[1])
    return root,job,config


def output(root,rows,*,intent='review'):
    record=execution.record(root,2)
    job=c.read(root/'jobs/002.json')
    gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='Fixture has no scientific candidate suitability judgment; retained originals remain available.')
        for row in job.get('candidate_nomination_mechanisms',[]) if row['status']!='waived']
    payload=dict(summary='Independent shallow comparison',scientific_result='These observations retain scientific limitations.',
        limitations=['Mechanical completion alone does not establish screening'],implementation_selection=None,
        mechanism_findings=rows,stage_intent=intent,
        candidate_nominations=dict(representatives=[],extras=[],gaps=gaps) if gaps else None)
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json')))
    return record,payload


def tree(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_schema_valid_missing_mechanism_cannot_direct_seal_or_fake_stage_end(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,payload=output(root,findings('P'))
    assert c.science_payload(record/'science-output.json','W')==payload
    with pytest.raises(WorkRepairRequired,match='h001.m2'):c.seal_result(root,2)
    assert not (record/'result-identity.json').exists()
    checkpoint.end_stage(root,job,record,'worker_resume_node_exhausted')
    before=tree(root)
    with pytest.raises(WorkRepairRequired):c.seal_result(root,2)
    assert tree(root)==before
    assert len(execution.records(root,2))==1 and len(config['breadth']['mechanisms'])==2


def test_later_same_owner_q_call_cannot_wash_original_attempt_delivery(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,payload=output(root,findings('P','Q'))
    with pytest.raises(WorkRepairRequired):c.seal_result(root,2)
    original=(record/worker_delivery.FACT_NAME).read_bytes()
    request=continuation.output_request(root,2,record,verify_task=False)
    assert request['code']=='worker_delivery_incomplete'
    run(root,config,'Q')
    result=worker_delivery.assessment(root,job,payload,record)
    assert result['status']=='incomplete' and result['missing']==['h001.m2']
    assert (record/worker_delivery.FACT_NAME).read_bytes()==original
    before=tree(root)
    assert continuation.output_request(root,2,record,verify_task=False)==request
    assert tree(root)==before
    with pytest.raises(WorkRepairRequired):c.seal_result(root,2)


@pytest.mark.parametrize('rows',[None,[],[dict(mechanism='P',basis='observed',finding='   ')],
    [dict(mechanism='P',basis='observed',finding='A limited observation'),dict(mechanism='P',basis='observed',finding='Repeated label')]])
def test_missing_blank_and_duplicate_scientific_findings_need_same_worker(tmp_path,execution_registry,rows):
    root,job,config=scene(tmp_path);run(root,config,'P');run(root,config,'Q')
    record,_=output(root,rows)
    with pytest.raises(WorkRepairRequired):c.seal_result(root,2)
    assert not (record/'result-identity.json').exists()
    assert len(execution.records(root,2))==1


@pytest.mark.parametrize('rows',['Scientific narrative instead of a table',{'finding':'Partial explanation'},
    [dict(mechanism='P',basis='scientific uncertainty',finding='No classified claim')]])
def test_unparseable_explicit_findings_retain_science_and_go_to_a_without_screening_or_rework(tmp_path,execution_registry,rows):
    root,job,config=scene(tmp_path);run(root,config,'P');run(root,config,'Q')
    record,payload=output(root,rows);original=(record/'science-output.json').read_bytes()
    c.seal_result(root,2)
    seal=c.read(record/'result-identity.json');receipt=seal['control_outcome']['worker_delivery']
    assert receipt['status']=='partial' and receipt['reason']=='scientific_findings_unresolved'
    assert dict(code='scientific_field_unresolved',subject='mechanism_findings') in seal['control_outcome']['diagnostics']
    assert (record/'science-output.json').read_bytes()==original
    assert c.validate(c.workspace(root,2))['mechanism_findings']==rows
    route=controller._route(root,2,'W',c.validate(c.workspace(root,2)),job,1,None)
    assert route[0]=='A' and 'scientific_findings_unresolved' in route[2]
    from scidiscovery.research_loop import search
    assert {row['status'] for row in search.project(root,3)['mechanisms'].values()}=={'pending'}
    assert search._screen_refs(root,job,seal,search.project(root,3))==set()
    assert len(execution.records(root,2))==1 and len(experiments_calls(config))==2


@pytest.mark.parametrize('intent,rows,reason',[
    ('review',findings('P',blocked=('Q',)),'scientific_blocked'),
    ('decision',findings('P'),'worker_decision'),
])
def test_blocked_or_explicit_scientific_decision_is_partial_and_never_screened(tmp_path,execution_registry,intent,rows,reason):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,_=output(root,rows,intent=intent)
    c.seal_result(root,2)
    seal=c.read(record/'result-identity.json')
    assert seal['control_outcome']['worker_delivery']['status']=='partial'
    assert seal['control_outcome']['worker_delivery']['reason']==reason
    route=controller._route(root,2,'W',c.validate(c.workspace(root,2)),job,1,None)
    assert route[0]=='A' and reason in route[2] and 'partial' in route[2]
    from scidiscovery.research_loop import search
    assert {row['status'] for row in search.project(root,3)['mechanisms'].values()}=={'pending'}
    before=tree(root);c.validate(c.workspace(root,2));assert tree(root)==before


def test_complete_findings_use_original_failed_observation_and_validate_never_rebuilds(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P',failed=True);run(root,config,'Q')
    record,_=output(root,findings('P','Q'));c.seal_result(root,2)
    result=c.read(record/'result-identity.json')['control_outcome']['worker_delivery']
    assert result['status']=='complete' and not result['missing']
    before=tree(root);c.validate(c.workspace(root,2));assert tree(root)==before
    facts=record/worker_delivery.FACT_NAME;facts.unlink()
    before=tree(root)
    with pytest.raises((ValueError,OSError)):c.validate(c.workspace(root,2))
    assert tree(root)==before and not facts.exists()


def test_unknown_native_and_invalid_json_keep_original_unsealed(tmp_path,execution_registry):
    root,job,config=scene(tmp_path);run(root,config,'P')
    record,_=output(root,findings('P',blocked=('Q',)))
    outcome=c.read(record/'native-outcome.json');outcome['usage']['unknown_steps']=[2]
    outcome['usage']['blocking_unknown_steps']=[2]
    c.write(record/'native-outcome.json',outcome)
    with pytest.raises(ValueError):c.seal_result(root,2)
    assert not (record/'result-identity.json').exists() and not (record/worker_delivery.FACT_NAME).exists()
    outcome['usage']['unknown_steps']=[]
    outcome['usage']['blocking_unknown_steps']=[]
    (record/'science-output.json').write_text('not JSON')
    outcome['raw_ref']=c.file_ref(record/'science-output.json');c.write(record/'native-outcome.json',outcome)
    with pytest.raises(OutputRepairRequired) as caught:c.seal_result(root,2)
    assert not isinstance(caught.value,WorkRepairRequired)
    assert not (record/'result-identity.json').exists()


def stopped_native(root,record):
    """Exact known native originals without an optional node-budget slice."""
    identity=execution.identity(root,2)
    for index,name in enumerate(('runner-process.json','native-process.json')):
        c.write(record/name,dict(task_ref=identity,boot_id=budget.clock_stamp()['boot_id'],
            pid=os.getpid()+10000+index,start_ticks=1,pgid=1,sid=1,ppid=1))
    outcome=c.read(record/'native-outcome.json')
    outcome.update(cleanup='observed_group_cleaned',execution_processes={
        name:c.record_ref(record/name) for name in ('runner-process.json','native-process.json')})
    c.write(record/'native-outcome.json',outcome)


def test_direct_seal_never_captures_while_original_writer_is_live(tmp_path,execution_registry,monkeypatch):
    root,job,config=scene(tmp_path);run(root,config,'P');run(root,config,'Q')
    record,_=output(root,findings('P','Q'));stopped_native(root,record)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    before=tree(root)
    with pytest.raises(ValueError,match='Original native writer still owns'):c.seal_result(root,2)
    assert tree(root)==before
    assert not (record/'checkpoint.json').exists() and not (record/worker_delivery.FACT_NAME).exists()


def test_no_resume_or_node_budget_retains_partial_known_original_without_replacement(tmp_path,execution_registry,monkeypatch):
    root,job,config=scene(tmp_path);run(root,config,'P')
    assert c.settings(root).get('resume_policy') is None and budget.node_limits(root,job) is None
    record,_=output(root,findings('P'));stopped_native(root,record)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    assert 'budget_slice' not in c.read(record/'native-outcome.json')
    # Replacement eligibility still needs its qualified slice; a partial exit
    # accepts no replacement and uses the exact original turn usage instead.
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_usage_unknown'):
        continuation._eligible_original(root,2,[record],successful=True)
    continuation.seal_or_repair(root,2)
    receipt=c.read(record/'result-identity.json')['control_outcome']['worker_delivery']
    assert receipt['status']=='partial' and receipt['reason']=='worker_resume_unsupported'
    ending=checkpoint.stage_end(root,job,record)
    assert ending['work_exit']['kind']=='resume_policy'
    assert len(execution.records(root,2))==1 and len(experiments_calls(config))==1
    before=tree(root);c.validate(c.workspace(root,2));assert tree(root)==before


def experiments_calls(config):
    from scidiscovery.research_loop import experiments
    return experiments.Records(config).calls()
