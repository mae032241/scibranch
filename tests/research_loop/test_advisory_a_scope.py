"""Ordinary A returns fallible advice; final and legacy scopes stay separate."""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, correction, delivery_review, guidance, supervision
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_worker_e_consultation import QUESTION, instance, next_job, publish
from test_adaptive_a_budget import _prepare_publication_instance


@pytest.mark.parametrize('action',['revise_h','extract','finish','deliver','audit','formal_review','wait'])
def test_actual_ordinary_a_seal_rejects_every_direct_work_or_terminal_route(tmp_path,action):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[]);publish(root,worker,stage_intent='handoff')
    assessor=c.build_job(root,2,'A',[1])
    with pytest.raises(OutputRepairRequired):publish(root,assessor,next_action=action)
    assert not (root/'native-evidence/002/result-identity.json').exists()
    assert not (root/'jobs/003.json').exists()


def test_advice_has_no_foreground_task_or_material_selection_authority(tmp_path):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    publish(root,worker,stage_intent='handoff',files={'candidate.py':'original candidate'})
    assessor=c.build_job(root,2,'A',[1])
    retained=copy.deepcopy(assessor['direct_materials'])
    answer=publish(root,assessor,next_action='continue_w',decision_request=None,
        route_reason='BAN BROAD SEARCH; test deep bagging first.',implementation_selection=[],
        search_review=dict(next_focus=dict(mechanism_ref='fake',action='improve')),
        progress_assessment=dict(verdict='no_progress'),
        scientific_result='No material reasoning issue was found; the advice imposes no new constraints.')
    route=controller._route(root,2,'A',answer,assessor,None,1)
    assert route.role=='W' and 'BAN BROAD' not in route.instruction
    assert 'focus' not in route.instruction and 'original objective' in route.instruction
    successor=next_job(root,route,3)
    assert all(row in successor['direct_materials'] for row in retained)
    assert correction.project(root)['counters']==dict(no_progress=0,unknown=0)
    assert c.response_options(assessor)['advisory_assessment']
    assert c.result_schema('A',**c.response_options(assessor))['properties']['next_action']['enum']==['continue_w']


def test_explicit_operational_review_merges_exact_w_consultation_and_charges_once(tmp_path):
    chosen=dict(supervision.DEFAULT_POLICY,require_fresh_input=False)
    root=_prepare_publication_instance(tmp_path,supervision_policy=chosen)
    worker=c.build_job(root,1,'W',[])
    request=publish(root,worker,tokens=1100,stage_intent='review',consultation_question=QUESTION)
    route=controller._route(root,1,'W',request,worker,None,None)
    facts=controller._supervision_facts(root,c.settings(root))
    selected,frozen=controller._supervision_route(1,request,route,facts=facts,source_job=worker)
    assert selected==route and frozen['automatic']
    assert selected.evidence_return==dict(role='W',step=1)
    assessor=next_job(root,selected,2,supervision_request=frozen)
    assert assessor['purpose']=='worker_consultation' and assessor['supervision_request']==frozen
    assert 'supervision_result' in assessor['scientific_response_fields']
    response=publish(root,assessor,tokens=30,supervision_result='continue_existing')
    head=accepted_budget.read(root);state=copy.deepcopy(head['search']['supervision'])
    assert state['automatic_reviews']==1 and state['last_covered_cutoff']['assessment_step']==2
    assert state['adaptive']['automatic_cost']['tokens']==30
    assert state['adaptive']['covered_question_revision']==frozen['adaptive_cutoff']['question_revision']
    assert c.build_job(root,2,'A',list(selected.parents),instruction=selected.instruction,evidence_return=selected.evidence_return,supervision_request=frozen)==assessor
    assert accepted_budget.read(root)['search']['supervision']==state
    resumed=controller._route(root,2,'A',response,assessor,None,1)
    assert resumed.role=='W' and 'original objective' in resumed.instruction
    assert controller._supervision_route(2,response,resumed,facts=controller._supervision_facts(root,c.settings(root)),source_job=assessor)[0]==resumed


def test_separate_current_final_contract_keeps_its_actions(tmp_path):
    final=dict(role='A',step=2,research_strategy_version=3,advisory_scope_version=1,
        evidence_return=delivery_review.make('delivery_review',1,0))
    assert not guidance.advisory_assessment(final)
    assert c.result_schema('A',**c.response_options(final))['properties']['next_action']['enum']==['deliver','finish','continue_w']
    assert not guidance.advisory_assessment(dict(final,evidence_return=None,formal_review=dict(version=1)))


@pytest.mark.parametrize('role,intent',[('H','revise_h'),('E','extract')])
def test_h_e_consultation_returns_w_with_pending_cut_and_real_useful_live_interval(tmp_path,role,intent):
    from scidiscovery.research_loop import protocol, search
    chosen=dict(supervision.DEFAULT_POLICY,require_fresh_input=False)
    root=_prepare_publication_instance(tmp_path,supervision_policy=chosen)
    worker=c.build_job(root,1,'W',[])
    request=publish(root,worker,tokens=1100,stage_intent=intent,consultation_question=QUESTION)
    route=controller._route(root,1,'W',request,worker,None,None)
    selected,frozen=controller._supervision_route(1,request,route,
        facts=controller._supervision_facts(root,c.settings(root)),source_job=worker)
    assert selected==route and frozen is None
    consultant=next_job(root,route,2)
    advice=publish(root,consultant,**(dict(next_action='wait',search_plan=None) if role=='H' else {}))
    returned=controller._route(root,2,role,advice,consultant,route.current_h,route.current_w)
    successor=next_job(root,returned,3)
    head=accepted_budget.read(root);before=copy.deepcopy(head['search']['supervision'])
    assert before['last_covered_cutoff'] is None and before['automatic_reviews']==0
    anchor=budget.accept_node(root,successor)
    task=c.record_ref(root/'jobs/003.json');current=dict(task_ref=task,search_slice=search.accept(root,successor),
        **(dict(budget_slice=anchor) if anchor is not None else {}))
    limits=budget.snapshot(root)
    shared=accepted_budget.usage_from_facts(head,job_rows=accepted_budget.load_usage_rows(head),current_native=None)
    facts=dict(settings=c.settings(root),head=head,shared=shared,stamp=budget.clock_stamp(),task=task,
        scope=protocol.stage_scope(root,successor),native_attempt=dict(number=0,execution_key=task))
    budget.publish_review(root,successor,current,limits,dict(input_tokens=0,output_tokens=0),facts=facts,observed=True)
    review=c.read(budget._review_directory(root,successor)/'review.json')
    assert not review['pending'] and 'direction_review' not in review
    # A substantial ordinary interval, not a tiny delta, releases the old due
    # opportunity. The accepted scientific review frontier is never advanced.
    budget.publish_review(root,successor,current,limits,dict(input_tokens=1000,output_tokens=0),facts=facts,observed=True)
    assert c.read(budget._review_directory(root,successor)/'review.json')['reason']=='worker_direction_review_due'
    assert accepted_budget.read(root)['search']['supervision']==before


def test_actual_marked_final_assessment_keeps_finish_and_one_correction_scope(tmp_path):
    root=instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    request=publish(root,worker,stage_intent='decision')
    route=controller._route(root,1,'W',request,worker,None,None)
    final=next_job(root,route,2)
    assert delivery_review.scope(final)=='delivery_review'
    answer=publish(root,final,next_action='finish',candidate_recommendation=None)
    assert answer['next_action']=='finish' and c.validate(c.workspace(root,2))['role']=='A'


@pytest.mark.parametrize('role',['W','A','E'])
@pytest.mark.parametrize('repair',[False,True])
def test_real_prepared_advisory_contract_wire_fits_existing_three_kib(tmp_path,role,repair):
    from scidiscovery.research_loop import backends
    from test_context_90_70_handoff import context_scene
    from test_result_schema import rendered_contract
    root,worker,_,_,_=context_scene(tmp_path)
    if role=='W':job=worker
    else:
        request=publish(root,worker,stage_intent='extract' if role=='E' else 'handoff',
            consultation_question=QUESTION)
        job=(next_job(root,controller._route(root,1,'W',request,worker,None,None),2)
            if role=='E' else c.build_job(root,2,'A',[1]))
    measured=dict(job,scientific_work_unit=job.get('scientific_work_unit',''))
    task=c.response_instructions(job);intent={}
    if repair:intent['output_repair']=OutputRepairRequired('fixture','D'*512).request()
    if role=='W':intent['worker_continuation']=dict(version=1,requester_step=1,advice_step=2,
        mode='resume_requested',fallback_reason=None)
    backend=backends.selected(c.settings(root,job),schema_transport='prompt')
    size,_,_,_=rendered_contract(tmp_path,measured,task,intent,backend,
        remaining={} if repair else None,
        budget_status=dict(shared_seconds_remaining=0,shared_observed_tokens_remaining=0))
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(role,repair,size)


def test_real_child_usage_unknown_is_incomplete_statistics_without_new_pause(tmp_path):
    from pathlib import Path
    from unittest.mock import patch
    root=instance(tmp_path,node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=60) for role in 'HWAE'})
    worker=c.build_job(root,1,'W',[]);write=c.write
    def main_thread(path,value):
        if Path(path).name=='native-outcome.json':
            value=dict(value,metering=dict(value['metering'],usage_scope='native_main_thread',
                subagent_usage_coverage='unknown'))
        return write(path,value)
    with patch.object(c,'write',main_thread):
        response=publish(root,worker,stage_intent='handoff')
    head=accepted_budget.read(root)
    assert head.row('job/001')['complete'] is False
    assert head['usage']['input_tokens']==30 and head['usage']['unknown_count']==0
    route=controller._route(root,1,'W',response,worker,None,None)
    successor=next_job(root,route,2)
    assert budget.node_admission(root,successor) is None
