"""Frozen W-first reflection opportunities use facts, never progress guesses."""
import copy

import pytest

from scidiscovery.research_loop import budget_notice, supervision


POLICY=supervision.AUTOMATIC_REFLECTION_POLICY


def worker_review(state, fraction):
    return supervision.worker_review(dict(role='W'), {}, summary=state,
        budget_fraction=fraction, selected_policy=POLICY)


def investment(number, mechanism='w001.question'):
    return dict(original_call_id=f'original-{number}',mechanism_ref=mechanism)


def test_new_policy_is_explicit_and_historical_policies_keep_their_bytes():
    assert supervision.DEFAULT_POLICY==dict(version=1,consecutive_rounds=4,
        min_budget_fraction=.10,budget_checkpoints=[.30,.65],max_automatic_reviews=2)
    assert supervision.WORKER_FIRST_POLICY==dict(version=2,mode='off',consecutive_calls=4,
        min_budget_fraction=.10,budget_checkpoints=[],max_automatic_reviews=0)
    assert supervision.policy(POLICY)==dict(version=3,mode='review',consecutive_calls=4,
        min_budget_fraction=.20,budget_checkpoints=[.30,.65],max_automatic_reviews=2)
    old=supervision.empty(supervision.WORKER_FIRST_POLICY)
    for number in range(4):old=supervision.add_investment(old,investment(number))
    assert not supervision.due(old,.9)['automatic_allowed']
    with pytest.raises(ValueError,match='cannot reset accepted counters'):
        supervision.due(old,.9,policy=POLICY)


def test_first_long_worker_has_a_consumption_opportunity_with_no_completed_calls():
    state=supervision.empty(POLICY)
    original=copy.deepcopy(state)
    assert worker_review(state,.299) is None
    review=worker_review(state,.30)
    assert review==dict(review_kind='ordinary_reflection',reasons=['budget_checkpoint:0.3'],
        cutoff=dict(total_rounds=0,source_step=None,round_id=None),budget_fraction=.30)
    assert state==original
    assert state['investment']['total_calls']==state['total_rounds']==0
    notice=budget_notice.worker_direction_notice(review)
    assert notice['text']==budget_notice.WORKER_REFLECTION_TEXT
    assert 'independent A' in notice['text'] and 'No progress verdict' in notice['text']
    assert len(notice['text'].encode())<=budget_notice.MAX_NOTICE_BYTES


def test_distinct_original_investment_opens_opportunity_and_repeated_calls_do_not_add_effort():
    state=supervision.empty(POLICY)
    for number in range(4):state=supervision.add_investment(state,investment(number))
    unchanged=supervision.add_investment(state,investment(3))
    assert unchanged==state
    assert worker_review(state,.1) is None
    assert worker_review(state,None) is None
    review=worker_review(state,.2)
    assert review['reasons']==['consecutive_calls']
    frozen=supervision.request(state,budget_fraction=.2,automatic=True)
    assert frozen['investment_cutoff']==4
    assert frozen['review_kind']=='ordinary_reflection'
    assert frozen['reflection_scope']=='worker_question_clues_evidence'
    state=supervision.cover(state,frozen,source_step=2,completion_fraction=.25)
    assert state['automatic_reviews']==1
    assert state['investment']['covered_calls']==4
    assert worker_review(state,.25) is None
    # Same observations can be republished without scheduling another A.
    assert supervision.observe(state,.25)==state


def test_spacing_review_ceiling_and_a_consumption_remain_in_original_total():
    state=supervision.empty(POLICY)
    first=supervision.request(state,budget_fraction=.3,automatic=True)
    # A's own usage moves the shared cut forward and consumes crossed points.
    state=supervision.cover(state,first,source_step=2,completion_fraction=.4)
    for number in range(4):state=supervision.add_investment(state,investment(number))
    assert supervision.due(state,.59)['cooldown']
    assert worker_review(state,.59) is None
    assert worker_review(state,.60)['reasons']==['consecutive_calls']
    second=supervision.request(state,budget_fraction=.60,automatic=True)
    state=supervision.cover(state,second,source_step=4,completion_fraction=.66)
    assert state['automatic_reviews']==2
    assert state['covered_checkpoints']==[.3,.65]
    for number in range(4,8):state=supervision.add_investment(state,investment(number))
    assert supervision.due(state,.9)['due']
    assert worker_review(state,.9) is None
    assert state['max_budget_fraction']==.66  # Pure projections do not mutate it.


def test_unknown_consumption_does_not_reset_or_fabricate_a_second_opportunity():
    state=supervision.empty(POLICY)
    for number in range(4):state=supervision.add_investment(state,investment(number))
    assert worker_review(state,None) is None
    assert worker_review(state,.2) is not None
    first=supervision.request(state,budget_fraction=.2,automatic=True)
    state=supervision.cover(state,first,source_step=2,completion_fraction=None)
    for number in range(4,8):state=supervision.add_investment(state,investment(number))
    assert supervision.due(state,None)['cooldown']
    assert worker_review(state,None) is None
    assert state['investment']['total_calls']==8 and state['automatic_reviews']==1


def test_final_scope_is_distinct_and_ordinary_cap_does_not_authorize_final_review():
    state=supervision.empty(POLICY)
    frozen=supervision.request(state,budget_fraction=.9,final=True)
    assert frozen['review_kind']=='final_assessment'
    assert frozen['final'] and not frozen['automatic']
    assert frozen['reflection_scope']=='worker_question_clues_evidence'
    assert supervision.request(supervision.empty(supervision.WORKER_FIRST_POLICY))==dict(
        investment_cutoff=0,version=1,cutoff=dict(total_rounds=0,source_step=None,round_id=None),
        due_reasons=[],budget_fraction=None,automatic=False,final=False)


def test_public_projection_explains_mechanical_trigger_without_private_originals():
    state=supervision.empty(POLICY)
    state=supervision.add_investment(state,investment(1))
    view=supervision.project(state,budget_fraction=.3)
    assert view['review_kind']=='ordinary_reflection'
    assert view['trigger_scope']=='consumption_and_original_call_investment'
    assert view['automatic_allowed'] and view['max_automatic_reviews']==2
    assert view['investment']['total_calls']==1
    assert 'originals' not in view['investment'] and 'lineage' not in view['investment']
    assert 'never scientific progress verdicts' in view['scope']
