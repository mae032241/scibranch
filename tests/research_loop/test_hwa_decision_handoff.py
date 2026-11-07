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


@pytest.mark.process_e2e
@pytest.mark.parametrize(('fault','coverage'),[('nomination-extra','complete'),('completed-decision','partial')])
def test_real_cli_w_request_gets_bounded_a_answer_without_incomplete_loop(tmp_path,monkeypatch,execution_registry,fault,coverage):
    import test_default_cli_output_repair as transport
    request='Should the refined retained candidate be delivered within the original scientific goal limits?'
    injection='''
if role=='W':
 payload['route_reason']=REQUEST
elif role=='A':
 task=json.loads((work/'science-task.json').read_text())
 assignment=json.dumps(task)
 assert REQUEST in assignment
 assert 'inputs/parents/002/conclusion.json' in assignment
 assert 'assigned work remains incomplete for worker_decision' not in assignment
 assert ('Missing assigned observations: h001.m2.' in assignment)==PARTIAL
 assert ('Retained work has partial coverage' in assignment)==PARTIAL
 payload.update(next_action='finish',route_reason='The requested decision has a bounded answer within the retained evidence.',
  scientific_result='The retained fixture cannot establish scientific target suitability. The answer is bounded by the stated missing evidence.',
  limitations=['Synthetic assessment only; no scientific qualification or execution permission is asserted.'],
  candidate_recommendation=None)
 payload.pop('portfolio_review',None)
 payload.pop('final_coverage',None)
'''.replace('REQUEST',repr(request)).replace('PARTIAL',repr(coverage=='partial'))
    marker="if not (config['fault']=='completed-missing-output' and role=='W' and not resume):"
    monkeypatch.setattr(transport,'NATIVE',transport.NATIVE.replace(marker,injection+'\n'+marker))
    root,_,env=transport.prepare_cli(tmp_path,fault=fault)
    outcome=transport.cli(root,'run',env=env)
    assert outcome['status']=='finish',transport.nomination_diagnostic(root,outcome,fault=fault)
    worker=c.read(root/'jobs/002.json');judge=c.read(root/'jobs/003.json')
    origin,=execution.records(root,2)
    seal=c.read(origin/'result-identity.json')
    receipt=seal['control_outcome']['worker_delivery']
    assert receipt['status']==coverage
    assert receipt['reason']==('worker_decision' if coverage=='partial' else None)
    assert c.read(origin/'science-output.json')['route_reason']==request
    assert judge['role']=='A' and judge.get('purpose') is None and judge['evidence_return'] is None
    assert request in judge['instruction'] and 'inputs/parents/002/conclusion.json' in judge['instruction']
    assert worker.get('execution_authority') is None and 'formal_review' not in seal
    observed=c.read(root/seal['observations']['path'])
    assert len(observed['managed_calls'])==1
    answer=c.validate(c.workspace(root,3))
    assert answer['next_action']=='finish' and 'bounded' in answer['scientific_result']
    assert answer['limitations']==['Synthetic assessment only; no scientific qualification or execution permission is asserted.']
    assert not (root/'jobs/004.json').exists()
    originals={path:path.read_bytes() for path in (root/'jobs/002.json',root/'jobs/003.json',origin/'science-output.json',origin/'result-identity.json')}
    assert transport.cli(root,'run',env=env)['status']=='finish'
    assert all(path.read_bytes()==value for path,value in originals.items())
    assert len(execution.records(root,2))==1 and not (root/'jobs/004.json').exists()


@pytest.mark.process_e2e
def test_real_cli_missing_a_decision_request_repairs_same_original_once(tmp_path,monkeypatch,execution_registry):
    import test_default_cli_output_repair as transport
    request='Does the refined retained candidate justify delivery within the original goal limits?'
    injection='''
if role=='W':
 payload['route_reason']=REQUEST
elif role=='A':
 task_bytes=(work/'science-task.json').read_bytes()
 task=json.loads(task_bytes)
 assert REQUEST in json.dumps(task)
 assert 'inputs/parents/002/conclusion.json' in json.dumps(task)
 saved=work/'work/assessment-task-before-repair.json'
 payload.update(next_action='continue_w',route_reason='The retained observation does not establish scientific target suitability.',
  scientific_result='The answer is bounded by missing independent target-condition evidence.',
  limitations=['Synthetic assessment only; target suitability is unqualified and no new execution is authorized.'],
  candidate_recommendation=None)
 payload.pop('portfolio_review',None)
 payload.pop('final_coverage',None)
 if not resume:
  assert not saved.exists()
  saved.write_bytes(task_bytes)
  payload.pop('decision_request',None)
 else:
  assert saved.read_bytes()==task_bytes
  assert 'decision_request' in prompt and 'change_criterion' in prompt
  payload.update(next_action='finish',decision_request=dict(decision='candidate',
   question='Would an independent target-condition observation contradict the retained candidate?',
   change_criterion='A controlled contradiction would change the candidate assessment.'))
'''.replace('REQUEST',repr(request))
    marker="if not (config['fault']=='completed-missing-output' and role=='W' and not resume):"
    monkeypatch.setattr(transport,'NATIVE',transport.NATIVE.replace(marker,injection+'\n'+marker))
    root,_,env=transport.prepare_cli(tmp_path,fault='nomination-extra')
    outcome=transport.cli(root,'run',env=env)
    assert outcome['status']=='finish' and outcome['completed_steps']==3,outcome
    judge=c.read(root/'jobs/003.json')
    assert judge['role']=='A' and judge.get('purpose') is None and judge.get('author_correction') is None
    assert request in judge['instruction']
    assert 'inputs/parents/002/conclusion.json' in judge['instruction']
    origin,repaired=execution.records(root,3)
    assert repaired==origin/'continuations/001'
    first=c.read(origin/'science-output.json');answer=c.read(repaired/'science-output.json')
    assert first['next_action']=='continue_w' and 'decision_request' not in first
    assert answer['next_action']=='finish' and answer['decision_request']['decision']=='candidate'
    assert answer['decision_request']['question'] and answer['decision_request']['change_criterion']
    for field in ('summary','scientific_result','limitations','route_reason'):
        assert answer[field]==first[field]
    repair=c.read(repaired/'native-intent.json');original=c.read(origin/'native-intent.json')
    assert repair['continuation_kind']=='repair'
    assert repair['output_repair']['code']=='a_decision_request_incomplete'
    assert repair.get('work_repair') is None and repair.get('automatic_recovery') is None
    assert repair['task_ref']==original['task_ref']==c.record_ref(root/'jobs/003.json')
    assert repair.get('native_profile')==original.get('native_profile')
    assert repair['budget_slice']['previous_tokens']==14
    assert (c.workspace(root,3)/'work/launches').read_text()=='exec\nresume\n'
    assert c.validate(c.workspace(root,3))['next_action']=='finish'
    worker,=execution.records(root,2)
    worker_seal=c.read(worker/'result-identity.json')
    assert worker_seal['control_outcome']['worker_delivery']['status']=='complete'
    assert c.read(worker/'science-output.json')['route_reason']==request
    observation=c.read(root/worker_seal['observations']['path'])
    assert len(observation['managed_calls'])==1 and observation['managed_calls'][0]['status']=='known_success'
    assert (c.workspace(root,2)/'work/launches').read_text()=='exec\n'
    assert not (root/'jobs/004.json').exists()
    originals={path:path.read_bytes() for path in (root/'jobs/002.json',root/'jobs/003.json',
        worker/'science-output.json',worker/'result-identity.json',origin/'science-output.json',repaired/'science-output.json')}
    assert transport.cli(root,'run',env=env)['status']=='finish'
    assert all(path.read_bytes()==value for path,value in originals.items())
    assert execution.records(root,3)==[origin,repaired] and execution.records(root,2)==[worker]
    assert not (root/'jobs/004.json').exists()
