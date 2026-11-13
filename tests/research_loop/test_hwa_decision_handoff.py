"""Authored W requests survive coverage classification and the real CLI handoff."""
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import contracts as c, execution, worker_delivery
from execution_fixtures import execution_registry


def assessment_inputs(tmp_path, *, observed=(), blocked=(), intent='decision', unknown=False, capacity=False, correction=None):
    record=tmp_path/'record';record.mkdir()
    c.write(record/worker_delivery.FACT_NAME,dict(frozen='fixture evidence original'))
    obligations=[dict(mechanism_ref=ref,source_step=1,source_ref='inputs/parents/001/conclusion.json',source_role='H')
        for ref in ('h001.m1','h001.m2')]
    job=dict(role='W',search=dict(focus=dict(action='screen',mechanism_ref='h001.m1'),
        coverage_obligation=obligations))
    evidence=[dict(mechanism_ref=ref,accepted=dict(path=f'calls/call-{index}/accepted.json',snapshot=f'original-{index}.json'))
        for index,ref in enumerate(observed)]
    facts=dict(evidence=evidence,unknown=[dict(original='unresolved')] if unknown else [],
        prior_unknown=False,capacity=capacity)
    science=dict(stage_intent=intent,mechanism_findings=[dict(mechanism=ref,basis='blocked',finding='Missing required scientific input.')
        for ref in blocked])
    inventory=SimpleNamespace(job=job,record=record,attempt=0,delivery=facts,
        caller_correction=correction,file_correction=None)
    return job,science,record,inventory


@pytest.mark.parametrize('intent',['decision','review','revise_h','handoff'])
def test_authored_stage_end_does_not_make_observed_coverage_partial(tmp_path,intent):
    job,science,record,inventory=assessment_inputs(tmp_path,observed=('h001.m1','h001.m2'),intent=intent)
    result=worker_delivery.assessment(tmp_path,job,science,record,inventory=inventory)
    assert (result['status'],result['reason'],result['missing'],result['blocked'])==('complete',None,[],[])


@pytest.mark.parametrize(('options','status','reason','missing','blocked'),[
    (dict(observed=('h001.m1',)),'partial','worker_decision',['h001.m2'],[]),
    (dict(observed=('h001.m1',),blocked=('h001.m2',)),'partial','scientific_blocked',[],['h001.m2']),
    (dict(observed=('h001.m1','h001.m2'),unknown=True),'partial','original_execution_unknown',[],[]),
    (dict(observed=('h001.m1','h001.m2'),capacity=True),'partial','work_evidence_capacity',[],[]),
    (dict(observed=('h001.m1','h001.m2'),correction=dict(mechanism_ref='h001.m2',diagnostic='unaccepted request')),
        'incomplete',None,['h001.m2'],[]),
])
def test_authored_request_retains_actual_missing_blocked_unknown_and_repair(tmp_path,options,status,reason,missing,blocked):
    job,science,record,inventory=assessment_inputs(tmp_path,**options)
    result=worker_delivery.assessment(tmp_path,job,science,record,inventory=inventory)
    assert (result['status'],result['reason'],result['missing'],result['blocked'])==(status,reason,missing,blocked)
