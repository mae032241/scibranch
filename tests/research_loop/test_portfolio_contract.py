"""Raw scientific comparison gates; fixtures assert mechanics, not science merit."""
import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, native, search
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, fixture_final_coverage


pytestmark = pytest.mark.process_e2e
MAIN, OTHER = 'h001.m1', 'h001.m2'


def comparison(*, action='finish', main=MAIN):
    return dict(main_line=main,main_line_reason='The retained observation supports this bounded line.',
        competitor=dict(mechanism_ref=OTHER,explanation='The other mechanism remains a feasible explanation.',
            evidence=[dict(source_step=1,pointer='/search_plan/commitments/1/minimal_test')]),
        no_competitor_reason=None,discriminating_work=None,
        no_work_reason='The current inputs do not permit another choice-changing comparison.',
        decision='close' if action in {'finish','deliver'} else 'reframe' if action=='revise_h' else 'continue',
        reason='Preserve the original scientific limitations when choosing this route.',deferral=None,work_updates=[])


def prepared(tmp_path,*,search_enabled=True,completed=True):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Compare scientific mechanisms under the same evidence.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],max_tokens=1_000_000,max_seconds=3600,
        max_steps=40,model='fixture',reasoning_effort='high',decision_contract_version=1,
        **({} if search_enabled else dict(search_policy=None)))
    plan=dict(scope='Two scientific explanations',commitments=[dict(label=label,
        minimal_test='A contradictory observation would reopen this explanation.',existing_ref=None)
        for label in ('Main','Competitor')],focus=dict(index=1,action='improve'))
    finish(root,1,'H',[],action='experiment',search_choice=plan if search_enabled else None)
    if completed:finish(root,2,'W',[1],text='A completed bounded contribution with unresolved alternatives.')
    return root


def raw_author(root,step=3,parents=(1,2),*,action='finish',review=None,search_review=None):
    path=root/'jobs'/f'{step:03d}.json'
    job=c.read(path) if path.exists() else c.build_job(root,step,'A',list(parents))
    payload=dict(summary='A comparison',scientific_result='The fixture cannot establish scientific effectiveness.',
        limitations=['Mechanical evidence only'],implementation_selection=None,candidate_recommendation=None,
        selection_explanation=None,progress_assessment=None,portfolio_review=review,
        search_review=search_review if search_review is not None else dict(dispositions=[],next_focus=None),
        next_action=action,route_reason='Choose the route from exact available observations.',
        decision_request=dict(decision='mechanism',question=f'Which explanation should change decision {step}?',
            change_criterion='An accurate contradiction would change the choice.')
            if action in {'continue_w','revise_h','extract'} else None)
    fixture_final_coverage(root,job,payload)
    record=execution.record(root,step)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(path)))
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=0,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    return job,payload,record


def rewrite(record,payload):
    c.write(record/'science-output.json',payload)
    outcome=c.read(record/'native-outcome.json')
    c.write(record/'native-outcome.json',dict(outcome,raw_ref=c.file_ref(record/'science-output.json')))


@pytest.mark.parametrize('review',[None,{},'A concise comparison',{'main_line':MAIN,'reason':'  '},
    {'main_line':'h999.m1','reason':'An unknown route'}])
def test_substantive_completed_worker_review_still_has_a_real_small_gate(tmp_path,review):
    root=prepared(tmp_path)
    _,_,record=raw_author(root,review=review)
    with pytest.raises(OutputRepairRequired,match='portfolio_review'):c.seal_result(root,3)
    assert not (record/'result-identity.json').exists()


def test_compact_comparison_and_three_key_final_coverage_preserve_breadth(tmp_path):
    root=prepared(tmp_path)
    review=dict(discriminating_work=None,main_line=MAIN,reason='The competitor has no decisive target evidence; its cheapest test is unavailable. Stop with this gap, revisiting when the target input arrives.')
    _,payload,record=raw_author(root,review=review)
    payload['final_coverage']=[dict(mechanism_ref=ref,status='unexplored',reason='No target evidence establishes this route; the target response remains unanswered.') for ref in (MAIN,OTHER)]
    rewrite(record,payload);c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['portfolio_review']==review
    state=search.project(root,4)
    assert state['order']['discriminating_work']==[]
    assert state['portfolio']['mainline_reviews'][0]['main_line']==MAIN
    assert len(state['portfolio']['seen_w'])==1


def test_missing_final_mechanism_cannot_be_published_as_complete_coverage(tmp_path):
    root=prepared(tmp_path)
    _,payload,record=raw_author(root,review=dict(discriminating_work=None,main_line=MAIN,reason='The second route remains unresolved at this bounded stop.'))
    payload['final_coverage']=[dict(mechanism_ref=MAIN,status='unexplored',reason='Target performance remains unmeasured.')]
    rewrite(record,payload)
    with pytest.raises(OutputRepairRequired,match='final_coverage'):c.seal_result(root,3)
    assert not (record/'result-identity.json').exists()


@pytest.mark.parametrize('pointer',['/role','/direct_refs/0/path','/implementation_selection','/control_outcome','/scientific_result/missing','/summary/~2'])
def test_science_reference_cannot_access_control_or_invalid_pointer(tmp_path,pointer):
    root=prepared(tmp_path);job,_,_=raw_author(root,review=comparison())
    with pytest.raises(ValueError):search.validate_science_refs(root,job,[dict(source_step=2,pointer=pointer)])
    assert search.validate_science_refs(root,job,[dict(source_step=2,pointer='/scientific_result')])==[
        dict(source_step=2,pointer='/scientific_result')]


@pytest.mark.parametrize('boundary',['wait','no_search','no_completed_w'])
def test_no_duplicate_comparison_declaration_is_required(tmp_path,boundary):
    root=prepared(tmp_path,search_enabled=boundary!='no_search',completed=boundary!='no_completed_w')
    step=2 if boundary=='no_completed_w' else 3
    _,payload,record=raw_author(root,step,parents=(1,) if step==2 else (1,2),
        action='wait' if boundary=='wait' else 'finish')
    payload.pop('portfolio_review');rewrite(record,payload)
    c.seal_result(root,step)
    assert (record/'result-identity.json').exists()


def test_active_work_updates_are_sparse_and_explicit_execute_keeps_real_focus_gate():
    context=dict(applicable=True,mechanisms={MAIN:{},OTHER:{}},final=False,
        discriminating_work=[dict(work_ref='A003-D001',status='deferred',mechanism_refs=[OTHER],next_action='continue_w')])
    job=dict(role='A');payload=dict(next_action='continue_w',portfolio_review=dict(discriminating_work=None,main_line=MAIN,
        reason='Keep the main line while testing its viable competitor.',
        work_updates=[dict(work_ref='A003-D001',action='execute',reason='The discriminating target input is now available.')]))
    # A main line may differ from the next exact discriminating W focus.
    assert search.require_portfolio_review(None,job,payload,context=context,
        interpretation=dict(focus=dict(mechanism_ref=OTHER,action='improve')))==payload['portfolio_review']
    payload['portfolio_review'].pop('work_updates')
    assert search.require_portfolio_review(None,job,payload,context=context)==payload['portfolio_review']
    payload['portfolio_review']['work_updates']=[dict(work_ref='A003-D001',action='execute',reason='Test its prediction.')]
    with pytest.raises(OutputRepairRequired,match='actual W focus'):
        search.require_portfolio_review(None,job,payload,context=context,
            interpretation=dict(focus=dict(mechanism_ref=MAIN,action='improve')))


def test_sparse_update_preserves_omitted_work_original_and_response():
    import copy
    from scidiscovery.research_loop.scientific_intent import resolve_discriminating_work
    first=dict(work_ref='A003-D001',status='deferred',source_a=3,declaration_pointer='/portfolio_review/discriminating_work',
        mechanism_refs=[MAIN],next_action='extract',updates=[],executions=[])
    second=dict(copy.deepcopy(first),work_ref='A004-D001',source_a=4,mechanism_refs=[OTHER])
    state=dict(order=dict(discriminating_work=[first,second]));before=copy.deepcopy(state)
    payload=dict(next_action='extract',portfolio_review=dict(main_line=MAIN,reason='Keep both scientific commitments visible.',
        discriminating_work=None,work_updates=[dict(work_ref=first['work_ref'],action='execute',reason='Gather the exact missing observation.')]))
    context=dict(applicable=True,mechanisms={MAIN:{},OTHER:{}},final=False,discriminating_work=[first,second])
    search.require_portfolio_review(None,dict(role='A'),payload,context=context)
    rows=resolve_discriminating_work(state,dict(role='A',step=5),payload)
    assert rows[0]['status']=='executed' and rows[0]['executions']==[dict(source_a=5,response=None)]
    assert rows[1]==before['order']['discriminating_work'][1] and state==before


@pytest.mark.parametrize('action',['defer','withdraw'])
def test_work_reason_is_free_text_without_null_slots_or_deep_evidence_table(action):
    context=dict(applicable=True,mechanisms={MAIN:{}},final=False,
        discriminating_work=[dict(work_ref='A003-D001',status='deferred',mechanism_refs=[MAIN],next_action='extract')])
    payload=dict(next_action='finish',portfolio_review=dict(discriminating_work=None,main_line=MAIN,reason='Bounded scientific comparison and unmet conditions.',
        work_updates=[dict(work_ref='A003-D001',action=action,reason='Exact retained W evidence changes feasibility; revisit only when missing conditions are supplied.')]))
    search.require_portfolio_review(None,dict(role='A'),payload,context=context)


def test_completed_worker_review_must_explicitly_declare_no_new_work_or_a_real_work(tmp_path):
    root=prepared(tmp_path)
    _,payload,record=raw_author(root,review=dict(main_line=MAIN,reason='No feasible additional comparison exists under the missing target input.'))
    with pytest.raises(OutputRepairRequired,match='discriminating_work'):c.seal_result(root,3)
    assert not (record/'result-identity.json').exists()
    payload['portfolio_review']['discriminating_work']=None
    rewrite(record,payload);c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['portfolio_review']['discriminating_work'] is None


def test_minimal_new_discriminating_work_is_accepted_without_extra_null_fields():
    context=dict(applicable=True,mechanisms={MAIN:{},OTHER:{}},final=False,discriminating_work=[])
    work=dict(mechanism_refs=[OTHER],question='Which target observation would distinguish the competitor? Revisit when the missing target input arrives.',next_action='extract')
    payload=dict(next_action='continue_w',portfolio_review=dict(main_line=MAIN,
        reason='Keep the current main line; defer the cheapest competitor test until its target input arrives.',discriminating_work=work))
    assert search.require_portfolio_review(None,dict(role='A'),payload,context=context)==payload['portfolio_review']
