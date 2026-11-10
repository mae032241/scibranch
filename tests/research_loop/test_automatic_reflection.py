"""Current reflection opportunities preserve exact originals and budget scope."""
import copy

import pytest

from scidiscovery.research_loop import budget_notice, supervision
from current_strategy_fixtures import budget_facts, current_policy


def worker_review(state, fraction):
    return supervision.worker_review(dict(role='W'), {}, summary=state,
        budget_fraction=fraction, selected_policy=state['policy'], budget_facts=budget_facts(fraction))


def investment(number, mechanism='w001.question'):
    return dict(original_call_id=f'original-{number}', mechanism_ref=mechanism, completed_receipt=True)


def test_default_requires_new_review_input_and_preserves_declaration():
    declared=current_policy()
    before=copy.deepcopy(declared)
    state=supervision.empty(declared)
    assert worker_review(state,.3) is None
    assert supervision.due(state,.3,budget_facts=budget_facts(.3))['suppression_reason']=='no_new_review_input'
    assert declared==before and declared['version']==4 and declared['require_fresh_input'] is True


def test_explicit_operational_opt_out_uses_known_consumption_without_inventing_rounds():
    state=supervision.empty(current_policy(require_fresh_input=False))
    original=copy.deepcopy(state)
    assert worker_review(state,.099) is None
    review=worker_review(state,.1)
    assert review==dict(review_kind='ordinary_reflection', reasons=['operational_no_new_experiment'],
        cutoff=dict(total_rounds=0,source_step=None,round_id=None),budget_fraction=.1)
    assert state==original and state['investment']['total_calls']==0
    notice=budget_notice.worker_direction_notice(review)
    assert notice['text']==budget_notice.WORKER_REFLECTION_TEXT
    assert len(notice['text'].encode())<=budget_notice.MAX_NOTICE_BYTES


def test_exact_original_receipt_counts_once_and_frozen_review_covers_only_its_input():
    state=supervision.empty()
    for number in range(4):state=supervision.add_investment(state,investment(number))
    assert supervision.add_investment(state,investment(3))==state
    assert worker_review(state,None) is None
    assert worker_review(state,.1)['reasons']==['changed_authored_question_or_original_receipt']
    frozen=supervision.request(state,budget_fraction=.1,automatic=True,budget_facts=budget_facts(.1))
    assert frozen['policy_version']==4 and frozen['investment_cutoff']==4
    newer=supervision.add_investment(state,investment(4))
    covered=supervision.cover(newer,frozen,source_step=2,completion_fraction=.15,budget_facts=budget_facts(.15))
    assert covered['investment']['covered_calls']==4 and covered['investment']['total_calls']==5
    assert covered['adaptive']['covered_receipts']==4 and covered['adaptive']['receipts']==5
    assert worker_review(covered,.2) is None
    with pytest.raises(ValueError,match='changed its declared identity'):
        supervision.add_investment(state,investment(3,'different-question'))


def test_unknown_consumption_cannot_open_an_automatic_opportunity():
    state=supervision.add_investment(supervision.empty(),investment(1))
    original=copy.deepcopy(state)
    assert worker_review(state,None) is None
    status=supervision.due(state,None,budget_facts=budget_facts(None))
    assert status['suppression_reason']=='shared_consumption_unknown_reconcile_original_usage'
    assert state==original


def test_final_request_keeps_separate_authority_and_exact_budget_scope():
    state=supervision.add_investment(supervision.empty(),investment(1))
    facts=budget_facts(.9)
    frozen=supervision.request(state,budget_fraction=.9,final=True,budget_facts=facts)
    assert frozen['review_kind']=='final_assessment' and frozen['final'] and not frozen['automatic']
    assert frozen['budget_scope']==facts and frozen['policy_version']==4
    assert frozen['reflection_scope']=='worker_question_clues_evidence'


def test_public_projection_omits_private_originals_and_lineage():
    state=supervision.add_investment(supervision.empty(),investment(1))
    view=supervision.project(state,budget_fraction=.3,budget_facts=budget_facts(.3))
    assert view['review_kind']=='ordinary_reflection' and view['automatic_allowed']
    assert view['investment']['total_calls']==1
    assert 'originals' not in view['investment'] and 'lineage' not in view['investment']
    assert 'never scientific progress verdicts' in view['scope']
