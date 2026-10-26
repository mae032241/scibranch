"""Derived A work retains questions across response, deferral and exact fork cut."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, fork_runtime, fork_service, native, search
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from scidiscovery.research_loop.scientific_intent import resolve_intent
from test_history_handoff import finish
from test_portfolio_contract import MAIN, comparison, prepared, raw_author, rewrite


pytestmark=pytest.mark.process_e2e
WORK='A003-D001'


def proposed_work():
    return dict(existing_ref=None,mechanism_refs=[MAIN],question='Which existing observation distinguishes the explanations?',
        change_criterion='An exact contradiction changes the selected explanation.',next_action='extract')


def update(action='defer'):
    return dict(work_ref=WORK,action=action,reason='This question remains relevant to the scientific choice.',
        trigger='The missing observation becomes available.' if action=='defer' else None,
        revisit='At the next substantive assessment.' if action=='defer' else None,
        evidence=[dict(source_step=2,pointer='/scientific_result')] if action=='withdraw' else [])


def declared(tmp_path):
    root=prepared(tmp_path);review=comparison(action='continue_w')
    review.update(discriminating_work=proposed_work(),no_work_reason=None,
        deferral=dict(reason='The required observation is currently missing.',trigger='The observation becomes available.',
            revisit='At the next assessment.'))
    job,payload,record=raw_author(root,action='continue_w',review=review,
        search_review=dict(dispositions=[],next_focus=dict(mechanism_ref=MAIN,action='improve')))
    c.seal_result(root,3)
    return root


def work_rows(root,step):
    return search.project(root,step)['order']['discriminating_work']


def test_declared_question_is_separate_from_initial_opportunities_and_visible(tmp_path):
    root=declared(tmp_path);state=search.project(root,4);work=state['order']['discriminating_work'][0]
    assert work['work_ref']==WORK and work['source_a']==3 and work['status']=='deferred'
    assert work['declaration_pointer']=='/portfolio_review/discriminating_work'
    assert all('mechanism_ref' in row and 'work_ref' not in row for row in state['order']['remaining'])
    before=copy.deepcopy(state['opportunity_counts'])
    job=c.build_job(root,4,'A',[1,2,3])
    public=c.science_view(job,root)['portfolio_assessment']
    assert public['active_work_count']==1 and public['work_index']
    page=c.read(c.workspace(root,4)/public['work_index'])
    assert page['discriminating_work'][0]['work_ref']==WORK
    assert page['discriminating_work'][0]['question']==proposed_work()['question']
    assert search.project(root,4)['opportunity_counts']==before


@pytest.mark.parametrize('bad',['omitted','duplicate','unknown','trigger','revisit','withdraw_evidence','execute_route'])
def test_real_seal_rejects_lost_or_unbound_active_work(tmp_path,bad):
    root=declared(tmp_path);review=comparison();review['work_updates']=[update()]
    if bad=='omitted':review['work_updates']=[]
    elif bad=='duplicate':review['work_updates']*=2
    elif bad=='unknown':review['work_updates'][0]['work_ref']='A099-D001'
    elif bad in {'trigger','revisit'}:review['work_updates'][0][bad]=None
    elif bad=='withdraw_evidence':review['work_updates']=[dict(update('withdraw'),evidence=[])]
    elif bad=='execute_route':review.update(discriminating_work=dict(existing_ref=WORK),no_work_reason=None,work_updates=[update('execute')])
    _,_,record=raw_author(root,4,(1,2,3),review=review)
    with pytest.raises(OutputRepairRequired,match='portfolio_review'):c.seal_result(root,4)
    assert not (record/'result-identity.json').exists()
    assert work_rows(root,4)[0]['status']=='deferred'


def test_each_deferral_refreshes_and_withdrawal_requires_authored_evidence(tmp_path):
    root=declared(tmp_path)
    review=comparison();review['work_updates']=[update()]
    raw_author(root,4,(1,2,3),review=review);c.seal_result(root,4)
    row=work_rows(root,5)[0]
    assert row['status']=='deferred' and row['updates'][-1]['source_a']==4
    assert row['updates'][-1]['trigger']==update()['trigger']
    review=comparison();review['work_updates']=[update('withdraw')]
    raw_author(root,5,(1,2,4),review=review);c.seal_result(root,5)
    row=work_rows(root,6)[0]
    assert row['status']=='withdrawn' and row['updates'][-1]['evidence']==update('withdraw')['evidence']
    job=c.build_job(root,6,'A',[1,2,5])
    assert c.science_view(job,root)['portfolio_assessment']['active_work_count']==0


def test_exact_response_is_linked_without_resolving_the_scientific_question(tmp_path):
    root=declared(tmp_path)
    review=comparison(action='extract');review.update(discriminating_work=dict(existing_ref=WORK),
        no_work_reason=None,work_updates=[update('execute')])
    _,payload,record=raw_author(root,4,(1,2,3),action='extract',review=review)
    payload['decision_request']=dict(decision='mechanism',question=proposed_work()['question'],
        change_criterion=proposed_work()['change_criterion']);rewrite(record,payload)
    c.seal_result(root,4)
    assert work_rows(root,5)[0]['executions']==[dict(source_a=4,response=None)]
    c.build_job(root,5,'E',[4],evidence_return=dict(step=4,role='A'))
    finish(root,5,'E',[4],text='The original observation remains inconclusive.')
    row=work_rows(root,6)[0]
    assert row['status']=='executed'
    assert row['executions']==[dict(source_a=4,response=dict(source_step=5,role='E',pointer='/scientific_result'))]
    job=c.build_job(root,6,'A',[1,2,5])
    assert c.science_view(job,root)['portfolio_assessment']['active_work_count']==1


def test_unresolved_selection_does_not_publish_work_updates(tmp_path):
    root=declared(tmp_path);before=work_rows(root,4)
    review=comparison();review['work_updates']=[update('withdraw')]
    _,payload,record=raw_author(root,4,(1,2,3),review=review)
    payload['implementation_selection']=['W999-I001'];rewrite(record,payload)
    c.seal_result(root,4)
    assert c.read(record/'result-identity.json')['control_outcome']['selection_status']=='unresolved'
    assert work_rows(root,5)==before


def test_wait_preserves_active_question_without_new_judgment(tmp_path):
    root=declared(tmp_path);before=work_rows(root,4)
    raw_author(root,4,(1,2,3),action='wait');c.seal_result(root,4)
    assert work_rows(root,5)==before


def test_fork_preserves_original_work_and_excludes_withdrawal_after_cut(tmp_path):
    root=declared(tmp_path)
    raw_author(root,4,(1,2,3),action='wait');c.seal_result(root,4)
    review=comparison();review['work_updates']=[update('withdraw')]
    raw_author(root,5,(1,2,4),review=review);c.seal_result(root,5)
    assert work_rows(root,6)[0]['status']=='withdrawn'
    with controller._writer(root):pass
    child=tmp_path/'child';limits=dict(max_tokens=10000,max_seconds=60,max_steps=8)
    preview=fork_service.preview(root,4,child,budget=limits)
    fork_service.create(root,4,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='retain-discriminating-question')
    job=fork_runtime.build_selected_job(child,4)
    row=work_rows(child,4)[0]
    assert row['work_ref']==WORK and row['source_a']==3 and row['status']=='deferred'
    inherited=search.portfolio_view(search.project(child,4),2)
    parent=search.portfolio_view(search.project(root,6),2)
    assert inherited['completed_cycle_count']==parent['completed_cycle_count']==1
    assert inherited['mainline_streak']==1 and parent['mainline_streak']==2
    assert inherited['mainline_source']['source_step']==3 and parent['mainline_source']['source_step']==5
    assert native.usage(child)['observed_tokens']==0
    assert native.usage(root)['observed_tokens']>0
    assert c.settings(child)['max_tokens']==limits['max_tokens']
    assert search.validate_science_refs(child,job,[dict(source_step=3,pointer='/portfolio_review/discriminating_work/question')])
    with pytest.raises(ValueError):search.validate_science_refs(child,job,[dict(source_step=5,pointer='/scientific_result')])
