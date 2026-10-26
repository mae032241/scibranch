"""Operational search switches preserve prior scientific facts and consumption."""
import copy

import pytest

from scidiscovery.research_loop import configuration_updates as updates, contracts as c, lineage, native, research_config, search
from test_history_handoff import finish
from test_portfolio_contract import MAIN, OTHER, comparison, prepared, raw_author

pytestmark=pytest.mark.process_e2e


def test_search_off_then_on_preserves_exact_cycles_streak_work_and_usage(tmp_path):
    root=prepared(tmp_path)
    review=comparison(action='continue_w')
    review.update(discriminating_work=dict(existing_ref=None,mechanism_refs=[MAIN,OTHER],
        question='Which missing target observation would change the mechanism choice?',
        change_criterion='A contradiction changes the scientific choice.',next_action='extract'),no_work_reason=None,
        deferral=dict(reason='Synthetic A defers unavailable target input.',trigger='A new target observation arrives.',revisit='Next independent A assessment.'))
    _,_,record=raw_author(root,action='continue_w',review=review,
        search_review=dict(dispositions=[],next_focus=dict(mechanism_ref=MAIN,action='improve')))
    c.seal_result(root,3)
    before=search.project(root,4);spent=native.usage(root)['observed_tokens']
    assert len(before['portfolio']['seen_w'])==1 and search.portfolio_view(before,2)['mainline_streak']==1
    assert len(before['order']['discriminating_work'])==1
    original=research_config.to_document(root)
    off=copy.deepcopy(original);off['budget'].pop('breadth')
    updates.submit(root,off,expected_revision=0)
    job,_,_=finish(root,4,'A',[1,3],action='wait')
    assert c.settings(root,job).get('search_policy') is None
    assert 'search' not in job and 'portfolio_review' not in job['scientific_response_fields']
    assert 'final_coverage' not in job['scientific_response_fields']
    visible=c.science_view(job,root)['portfolio_assessment']
    assert visible['required_for_completed_w'] is False
    assert 'Historical navigation only' in visible['instruction']
    assert 'update each work_ref exactly once' not in visible['instruction']
    projected=lineage.project(root,5)['search_state']
    assert projected['portfolio']['seen_w']==before['portfolio']['seen_w']
    assert projected['portfolio']['mainline_reviews']==before['portfolio']['mainline_reviews']
    assert projected['order']['discriminating_work']==before['order']['discriminating_work']
    saved=c.read(root/'host-history/overview.json')['portfolio_metrics']
    assert saved['portfolio']['completed_cycle_count']==1 and saved['portfolio']['mainline_streak']==1
    assert len(saved['discriminating_work'])==1
    assert native.usage(root)['observed_tokens']==spent+10
    rows=[];page=job['history']['view']['indexes']['mechanisms']
    while page:
        body=c.read(c.workspace(root,4)/page);rows.extend(body['mechanisms']);page=body['next_page']
    assert rows and all(row['initial_calls'] is None and row['initial_calls_remaining'] is None for row in rows)
    on=research_config.to_document(root,updates.current(root));on['budget']['breadth']=copy.deepcopy(original['budget']['breadth'])
    updates.submit(root,on,expected_revision=1)
    next_job,_,_=finish(root,5,'A',[1,4],action='wait')
    assert next_job.get('search') is not None
    after=search.project(root,6)
    assert after['portfolio']['seen_w']==before['portfolio']['seen_w']
    assert after['portfolio']['mainline_reviews']==before['portfolio']['mainline_reviews']
    assert after['order']['discriminating_work']==before['order']['discriminating_work']
    assert after['opportunity_counts']==before['opportunity_counts']
    assert native.usage(root)['observed_tokens']==spent+20
