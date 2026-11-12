"""Model-visible scientific authority without editing original routes or schemas."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, guidance, native, protocol
from test_history_handoff import finish

RECIPE = 'Run algorithm Z on an exact 148-cell perturbation grid and fixed seed matrix; reconsider the global robustness gap.'
FAKE = r'''#!/usr/bin/env python
import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE'];step=int(work.name)
job=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
task=json.loads((work/'science-task.json').read_text())
value=next(s.split('=',1)[1] for s in args if s.startswith('developer_instructions='))
instruction=job['native_role_instruction']
if role=='A':
 assert not job.get('formal_review')
 instruction+='\n'+task['scientific_judgment']
assert json.loads(value)==instruction
assert prompt.startswith(instruction+'\n')
assert 'Leave further scientific verification requests to A.' not in prompt
(work/'work/prompt.txt').write_text(prompt)
(work/'work/developer-instructions.txt').write_text(json.loads(value))
p=dict(summary='Scientific contribution',scientific_result='Original scientific observation and scope',limitations=['Fake transport only'],implementation_selection=None)
if role=='H':p.update(search_plan=None,next_action='analyze',route_reason='Compare the original mechanism using comparator C')
if role=='A':p.update(search_review=None,candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,next_action='continue_w' if step==2 else 'finish',route_reason='Run algorithm Z on an exact 148-cell perturbation grid and fixed seed matrix; reconsider the global robustness gap.' if step==2 else 'Global judgment with limits')
if role=='W':
 assigned=(job.get('search') or {}).get('coverage_obligation',[])
 labels={row['mechanism_ref']:row['label'] for row in ((job.get('search') or {}).get('breadth') or {}).get('mechanisms',[])}
 p.update(stage_intent='review',mechanism_findings=[dict(mechanism=labels[row['mechanism_ref']],basis='blocked',
  finding='This transport fixture has no managed scientific experiment or observation.') for row in assigned] if assigned else None)
 task=json.loads((work/'science-task.json').read_text());ctx=task['decision_context']
 assert task['question']!=ctx['original_instruction']
 assert ctx['original_instruction']==json.loads((work/ctx['source_original']).read_text())['route_reason']
 assert 'comparator C' in (work/'inputs/parents/001/conclusion.json').read_text()
 assert 'Keep original data separation' in (work/'inputs/global/objective.md').read_text()
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(p))
'''


def prepare(tmp_path, *, marker=1, correction_policy=None, search_policy=None, real=False):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Keep original data separation and compare the mechanism.')
    launcher=tmp_path/'codex';launcher.write_text(FAKE);launcher.chmod(0o700)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(launcher)] if real else ['fixture-never-launched'],
        max_tokens=100000,max_seconds=120,max_steps=12,model='fake',reasoning_effort='high',
        search_policy=search_policy,correction_policy=correction_policy,handoff_authority_version=marker)
    return root


def a_source(root, *, action='continue_w'):
    finish(root,1,'H',[],action='analyze',text='Original H scope and comparator C constraint')
    c.build_job(root,2,'A',[1],instruction='Exact scientific question')
    job=c.read(root/'jobs/002.json');record=c.host_record(c.workspace(root,2))
    payload=dict(summary='Global contribution and gap',scientific_result='A scientific direction, not an implementation mandate',
        limitations=['Original H constraints remain'],implementation_selection=None,search_review=None,
        candidate_recommendation=None,progress_assessment=None,decision_request=None,portfolio_review=None,
        next_action=action,route_reason=RECIPE)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json')))
    accepted_budget.accept_native(root,2,record,c.read(record/'native-intent.json'))
    c.write(record/'science-output.json',payload);c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    return job,c.seal_result(root,2)


@pytest.mark.process_e2e
def test_real_codex_transport_uses_frozen_role_and_source_tagged_a_direction(tmp_path):
    root=prepare(tmp_path,real=True)
    result=controller.run(root)
    assert result['status']=='finish' and result['completed_steps']==4,result
    job=c.read(root/'jobs/003.json');view=c.read(c.workspace(root,3)/'science-task.json')
    assert job['instruction']==RECIPE and view['question']!=RECIPE
    assert view['decision_context']['original_instruction']==RECIPE
    assert view['decision_context']['source_role']=='A' and view['decision_context']['source_action']=='continue_w'
    for step in range(1,5):
        task=c.read(root/'jobs'/f'{step:03d}.json');work=c.workspace(root,step)
        expected=task['native_role_instruction']
        if task['role']=='A':
            assert c.read(work/'science-task.json')['scientific_judgment']==c.AUTHOR_EVIDENCE_GUIDANCE
            expected+='\n'+c.AUTHOR_EVIDENCE_GUIDANCE
        assert (work/'work/developer-instructions.txt').read_text()==expected
        assert (work/'work/prompt.txt').read_text().startswith(expected+'\n')
    saved={path:path.read_bytes() for path in (root/'jobs').glob('*.json')}
    assert controller.run(root)['status']=='finish'
    assert all(path.read_bytes()==original for path,original in saved.items())


@pytest.mark.parametrize('same_instruction',[True,False])
def test_ordinary_a_advice_does_not_replace_an_explicit_host_question(tmp_path,same_instruction):
    root=prepare(tmp_path);assessor,result=a_source(root)
    assert guidance.advisory_assessment(assessor)
    assert assessor['research_strategy_version']==3 and assessor['advisory_scope_version']==1
    route=controller._route(root,2,'A',result,assessor,1,None)
    assert route.role=='W' and RECIPE not in route.instruction
    instruction=RECIPE if same_instruction else 'Exact host constraint: comparator C is mandatory'
    job=c.build_job(root,3,'W',list(route.parents),instruction=instruction)
    view=c.science_view(job,root)
    # Even identical authored advice is not a binding W task proposal.
    assert 'decision_context' not in job and job['instruction']==instruction
    assert view['question'].startswith(instruction+'\n') and view['question'].count(instruction)==1
    assert view['question'].count('Own research and adapt in this W session.')==1
    assert 'inputs/parents/002/conclusion.json' in [row['path'] for row in job['direct_refs']]


def test_w_e_question_and_return_keep_the_exact_requester_original(tmp_path):
    root=prepare(tmp_path)
    finish(root,1,'H',[],action='experiment',text='Original H scope and comparator C constraint')
    worker,_,record=finish(root,2,'W',[1],stage_intent='extract',consultation_question=RECIPE)
    original=(record/'science-output.json').read_bytes()
    request=c.validate(c.workspace(root,2))
    route=controller._route(root,2,'W',request,worker,1,None)
    e=c.build_job(root,3,route.role,list(route.parents),instruction=route.instruction,evidence_return=route.evidence_return)
    ev=c.science_view(e,root)
    assert RECIPE in ev['question'] and 'decision_context' not in ev
    assert e['evidence_return']==dict(role='W',step=2)
    finish(root,3,'E',route.parents)
    returned=controller._route(root,3,'E',c.validate(c.workspace(root,3)),e,route.current_h,route.current_w)
    resumed=c.build_job(root,4,returned.role,list(returned.parents),instruction=returned.instruction)
    assert resumed['role']=='W' and 'original objective' in c.science_view(resumed,root)['question']
    assert {'inputs/parents/002/conclusion.json','inputs/parents/003/conclusion.json'} <= {row['path'] for row in resumed['direct_refs']}
    assert (record/'science-output.json').read_bytes()==original


def test_current_prepared_job_literal_stays_frozen_when_future_default_changes(tmp_path,monkeypatch):
    root=prepare(tmp_path,marker=None);a_source(root)
    job=c.build_job(root,3,'W',[1,2],instruction=RECIPE)
    # A null compatibility argument still prepares the current marker, frozen as 1.
    assert c.settings(root)['handoff_authority_version']==job['handoff_authority_version']==1
    literal=job['native_role_instruction']
    question=c.science_view(job,root)['question']
    assert job['instruction']==RECIPE and question.startswith(RECIPE+'\n')
    assert question.count(RECIPE)==1
    paths=[root/'settings.json',root/'settings-identity.json',root/'jobs/003.json',c.workspace(root,3)/'science-task.json',c.host_record(c.workspace(root,3))/'science-result.schema.json']
    original={path:path.read_bytes() for path in paths}
    monkeypatch.setitem(guidance.DEFAULT,'W','Changed advice for future settings')
    assert c.build_job(root,3,'W',[1,2],instruction=RECIPE)==job
    assert job['native_role_instruction']==literal
    assert c.science_view(job,root)['question']==question and 'decision_context' not in job
    assert all(path.read_bytes()==data for path,data in original.items())
    assert not (c.host_record(c.workspace(root,3))/'native-intent.json').exists()


def test_missing_advisory_scope_refuses_rebuild_without_changing_originals(tmp_path,monkeypatch):
    root=prepare(tmp_path);a_source(root)
    job=c.build_job(root,3,'W',[1,2],instruction=RECIPE)
    # The current scientific behavior gate requires the frozen advisory scope.
    # Missing optional authority metadata is not an unsupported scientific contract.
    job.pop('advisory_scope_version')
    c.write(root/'jobs/003.json',job)
    paths=[root/'jobs/003.json',c.workspace(root,3)/'science-task.json',c.host_record(c.workspace(root,3))/'science-result.schema.json']
    original={path:path.read_bytes() for path in paths}
    monkeypatch.setitem(guidance.DEFAULT,'W','Changed advice for future settings')
    with pytest.raises(protocol.UnsupportedProtocol,match='Scientific job scientific behavior'):
        c.build_job(root,3,'W',[1,2],instruction=RECIPE)
    assert all(path.read_bytes()==data for path,data in original.items())
    assert not (c.host_record(c.workspace(root,3))/'native-intent.json').exists()
    assert not (root/'jobs/004.json').exists()


def test_final_a_retains_one_bounded_correction_and_then_finish(tmp_path):
    from scidiscovery.research_loop import delivery_review, search
    root=prepare(tmp_path,search_policy=search.DEFAULT_POLICY)
    plan=dict(scope='Original mechanism scope',commitments=[dict(label='M',minimal_test='Comparator C',existing_ref=None)],focus=dict(index=1,action='improve'))
    finish(root,1,'H',[],action='experiment',search_choice=plan)
    worker,_,_=finish(root,2,'W',[1],stage_intent='decision')
    route=controller._route(root,2,'W',c.validate(c.workspace(root,2)),worker,1,None)
    final=c.build_job(root,3,route.role,list(route.parents),instruction=route.instruction,evidence_return=route.evidence_return)
    assert delivery_review.scope(final)=='delivery_review' and not guidance.advisory_assessment(final)
    assert c.result_schema('A',**c.response_options(final))['properties']['next_action']['enum']==['deliver','finish','continue_w']
    finish(root,3,'A',route.parents,action='continue_w')
    gap=c.validate(c.workspace(root,3))
    assert gap['decision_request'] is not None
    correction_route=controller._route(root,3,'A',gap,final,1,2)
    corrected=c.build_job(root,4,correction_route.role,list(correction_route.parents),
        instruction=correction_route.instruction,evidence_return=correction_route.evidence_return)
    assert delivery_review.scope(corrected)=='delivery_correction'
    assert corrected['search']['focus']['mechanism_ref']=='h001.m1'
    finish(root,4,'W',correction_route.parents,stage_intent='decision')
    reassess_route=controller._route(root,4,'W',c.validate(c.workspace(root,4)),corrected,1,2)
    reassessment=c.build_job(root,5,reassess_route.role,list(reassess_route.parents),
        instruction=reassess_route.instruction,evidence_return=reassess_route.evidence_return)
    assert reassessment['evidence_return']['corrections_used']==1
    assert c.result_schema('A',**c.response_options(reassessment))['properties']['next_action']['enum']==['deliver','finish']
    finish(root,5,'A',reassess_route.parents,action='finish')
    assert c.validate(c.workspace(root,5))['next_action']=='finish'
