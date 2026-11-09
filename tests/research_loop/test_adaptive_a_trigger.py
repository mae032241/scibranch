"""Opt-in review admission consumes originals, never a scientific stall score."""
import copy

import pytest

from scidiscovery.research_loop import accepted_prefix, accepted_search, contracts, controller, lineage, supervision


POLICY=supervision.ADAPTIVE_REFLECTION_POLICY


def facts(tokens=0,seconds=0,*,token_limit=10000,seconds_limit=1000,tokens_complete=True,seconds_complete=True):
    return dict(tokens=tokens,seconds=seconds,limits=dict(tokens=token_limit,seconds=seconds_limit),
        complete=dict(tokens=tokens_complete,seconds=seconds_complete))


def receipt(state, number, *, complete=True):
    return supervision.add_investment(state,dict(original_call_id=f'original-{number}',
        mechanism_ref='w001.question',completed_receipt=complete))


JOB_REF=dict(path='/original/jobs/002.json',size_bytes=123)


def freeze(state, **kwargs):
    kwargs.setdefault('budget_facts',facts(1100,110))
    return supervision.request(dict(supervision=state,last_w=1),**kwargs)


def charge(state, request, *, cost, budget_facts=None):
    budget_facts=request.get('budget_scope') if budget_facts is None else budget_facts
    scoped=dict(cost,job_ref=JOB_REF,task_ref=JOB_REF,step=2,role='A') if cost is not None else None
    return supervision.charge_automatic(state,request,cost=scoped,budget_facts=budget_facts,
        job=dict(role='A',step=2,supervision_request=request),job_ref=JOB_REF)


def settle(state, at, *, tokens=10,seconds=1,source_step=2):
    request=freeze(state,automatic=True,budget_facts=at)
    state=charge(state,request,cost=dict(tokens=tokens,seconds=seconds,
        complete=dict(tokens=True,seconds=True)),budget_facts=at)
    return supervision.cover(state,request,source_step=source_step,budget_facts=at),request


def test_third_low_cost_automatic_a_is_admitted_without_changing_frozen_policies():
    state=supervision.empty(POLICY)
    for index in range(3):
        state=receipt(state,index)
        current=facts(tokens=(index+1)*1100,seconds=(index+1)*110)
        assert supervision.due(state,budget_facts=current)['automatic_allowed']
        state,request=settle(state,current,source_step=2*(index+1))
        assert request['automatic'] and request['review_kind']=='ordinary_reflection'
    assert state['automatic_reviews']==3
    assert state['adaptive']['automatic_cost']==dict(tokens=30,seconds=3.)
    assert POLICY['max_automatic_reviews'] is None
    assert supervision.AUTOMATIC_REFLECTION_POLICY['version']==3
    assert supervision.AUTOMATIC_REFLECTION_POLICY['max_automatic_reviews']==2
    assert supervision.WORKER_FIRST_POLICY['mode']=='off'


def test_unchanged_inputs_and_consumption_do_not_repeat_a_but_quiet_spending_can():
    state=receipt(supervision.empty(POLICY),1)
    state,_=settle(state,facts(1100,110))
    for current in (facts(1100,110),facts(1500,150)):
        status=supervision.due(state,budget_facts=current)
        assert not status['automatic_allowed'] and not status['fresh_input']
    status=supervision.due(state,budget_facts=facts(2200,220))
    assert status['automatic_allowed']
    assert status['reasons']==['operational_no_new_experiment']
    assert not status['fresh_input']  # A new operational reason, no invented evidence.


def test_receipt_identity_and_counts_alone_are_not_fresh_completed_input():
    state=supervision.empty(POLICY)
    for index in range(5):state=receipt(state,index,complete=False)
    status=supervision.due(state,budget_facts=facts(0,0))
    assert not status['fresh_input'] and not status['automatic_allowed']
    assert state['adaptive']['receipts']==0
    state=receipt(state,5)
    assert state['adaptive']['receipts']==1
    assert receipt(state,5)==state
    assert supervision.due(state,budget_facts=facts(1100,110))['fresh_input']


def test_question_change_is_complete_owner_comparison_without_text_hash_or_truncation():
    original='x'*5000+' original'
    prior=dict(candidate_memory=dict(question=original))
    assert not accepted_prefix._authored_question_changed(prior,dict(question=original))
    assert accepted_prefix._authored_question_changed(prior,dict(question='x'*5000+' revised'))
    state=supervision.authored_question_revision(supervision.empty(POLICY),True)
    assert state['adaptive']['question_revision']==1 and 'question' not in state['adaptive']
    assert supervision.due(state,budget_facts=facts(1100,110))['automatic_allowed']


def test_frozen_cut_cannot_cover_newer_original_receipts_or_question_revisions():
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True,budget_facts=facts(1100,110))
    state=receipt(state,2)
    state=supervision.authored_question_revision(state,True)
    state=supervision.cover(state,request,source_step=2,budget_facts=facts(1200,120))
    status=supervision.due(state,budget_facts=facts(2300,230))
    assert status['fresh_input'] and status['automatic_allowed']
    assert state['adaptive']['covered_receipts']==1 and state['adaptive']['receipts']==2


def test_request_validation_and_final_and_consultation_scope_remain_distinct():
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True,budget_facts=facts(1100,110))
    assert contracts.supervision_binding(request,step=2,research_strategy_version=3)==request
    final=freeze(state,final=True,budget_facts=facts(1100,110))
    assert final['review_kind']=='final_assessment' and not final['automatic']
    assert charge(state,final,cost=None)==state
    summary=accepted_search.empty();summary.update(supervision=state,last_w=1)
    route=('A',[1],'W consultation',dict(role='W',step=1),None,1,None,None)
    selected,binding=controller._supervision_route(1,dict(role='W'),route,
        facts=dict(summary=summary,budget_fraction=.11,policy=POLICY,budget_facts=facts(1100,110)),
        source_job=dict(role='W'))
    assert selected==route and binding is None


def test_restart_and_inherited_summary_retain_costs_cutoffs_and_unknowns():
    state=receipt(supervision.empty(POLICY),1)
    state,_=settle(state,facts(1100,110),tokens=200,seconds=20)
    request=freeze(state,automatic=True,budget_facts=facts(2200,220))
    state=charge(state,request,
        cost=dict(tokens=50,seconds=None,complete=dict(tokens=False,seconds=False)))
    inherited=copy.deepcopy(dict(node_count=8,supervision=state))
    restored=supervision.observe(inherited)
    assert restored==state
    assert restored['adaptive']['automatic_cost']['tokens']==250
    assert not supervision.due(restored,budget_facts=facts(3300,330))['automatic_allowed']
    with pytest.raises(ValueError,match='cannot reset accepted counters'):
        supervision.observe(inherited,selected_policy=supervision.AUTOMATIC_REFLECTION_POLICY)


def test_positive_cooldown_is_required_and_optional_emergency_cap_is_explicit():
    with pytest.raises(ValueError):supervision.policy(dict(POLICY,min_budget_fraction=0))
    chosen=dict(POLICY,max_automatic_reviews=1)
    state,_=settle(receipt(supervision.empty(chosen),1),facts(1100,110))
    state=receipt(state,2)
    assert supervision.due(state,budget_facts=facts(2200,220))['suppression_reason']=='emergency_review_count_cap'


def test_rejected_optional_memory_does_not_repeatedly_increment_question_frontier():
    prior=dict(candidate_memory=dict(question='Retained author question',candidates=[]))
    job=dict(role='W',step=2,research_strategy_version=3)
    payload=dict(research_memory=dict(question='Rejected changed question',clues=[dict(
        observed_fact='An authored claim',evidence=[dict(ref='missing-original',relation='supports',note='Unresolved')])]))
    for _ in range(2):
        memory=lineage.retained_research_memory(prior,job,payload,node_path='nodes/original/002',
            memory_facts=dict(candidates={},references={}))
        assert memory['question']==prior['candidate_memory']['question']
        assert not accepted_prefix._authored_question_changed(prior,memory)


@pytest.mark.parametrize('mutation', ['missing_policy','missing_reflection','wrong_source','wrong_budget','wrong_frontier','old_policy','float_policy'])
def test_public_v4_bindings_reject_mixed_or_changed_scopes(mutation):
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True)
    job=dict(role='A',step=2,supervision_request=request)
    bad=copy.deepcopy(request)
    if mutation=='float_policy':bad['policy_version']=4.0
    elif mutation=='missing_policy':bad.pop('policy_version')
    elif mutation=='missing_reflection':bad.pop('review_kind')
    elif mutation=='wrong_source':bad['source_w']=0
    elif mutation=='wrong_budget':bad['budget_scope']['limits']['tokens']=99
    elif mutation=='wrong_frontier':bad['adaptive_cutoff']['receipts']=99
    if mutation in {'missing_policy','missing_reflection','wrong_source','float_policy'}:
        with pytest.raises(ValueError):contracts.supervision_binding(bad,step=2,research_strategy_version=3,supervision_policy_version=4)
    else:
        with pytest.raises(ValueError):supervision.validate_adaptive_request(
            supervision.empty(supervision.AUTOMATIC_REFLECTION_POLICY) if mutation=='old_policy' else state,
            bad,job=job,budget_facts=facts(1200,120),source_w=1)
