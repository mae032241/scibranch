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
def test_completed_worker_assessment_does_not_require_global_comparison(tmp_path,review):
    root=prepared(tmp_path)
    _,payload,record=raw_author(root,review=review)
    payload.pop('final_coverage',None)
    rewrite(record,payload);c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['portfolio_review']==review
    state=search.project(root,4)
    assert state['focus']==dict(mechanism_ref=MAIN,action='improve')
    assert state['order']['discriminating_work']==[]
    assert state['portfolio']['mainline_reviews']==[]


@pytest.mark.parametrize('coverage',[None,[],[dict(mechanism_ref=MAIN,status='unexplored',reason='Missing target evidence')]])
def test_final_assessment_preserves_optional_partial_coverage_without_inventory_gate(tmp_path,coverage):
    root=prepared(tmp_path)
    _,payload,record=raw_author(root,review=None)
    payload['final_coverage']=coverage
    rewrite(record,payload);c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['final_coverage']==coverage
    assert (record/'result-identity.json').exists()


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
    payload.pop('portfolio_review');payload.pop('final_coverage',None);rewrite(record,payload)
    c.seal_result(root,step)
    assert (record/'result-identity.json').exists()


@pytest.mark.parametrize('action',['continue_w','revise_h','extract','finish'])
def test_optional_a_global_planning_cannot_change_retained_w_intent(tmp_path,action):
    from scidiscovery.research_loop import accepted_budget
    root=prepared(tmp_path)
    before=accepted_budget.read(root)['search']
    advice=dict(main_line=OTHER,reason='Optional authored opinion',
        discriminating_work=dict(mechanism_refs=[OTHER],question='An optional comparison',next_action=action),
        work_updates=[dict(work_ref='A999-D001',action='execute',reason='No such retained work')])
    _,payload,record=raw_author(root,action=action,review=advice,search_review=dict(
        dispositions=[dict(mechanism_ref=MAIN,status='waived',reason='Optional authored advice')],
        next_focus=dict(mechanism_ref=OTHER,action='repair')))
    payload['final_coverage']=[dict(mechanism_ref=MAIN,status='ruled_out',reason='Optional partial account')]
    rewrite(record,payload);c.seal_result(root,3)
    after=accepted_budget.read(root)['search']
    assert after['focus']==before['focus'] and after['allocation']==before['allocation']
    for ref,item in before['table']['mechanisms'].items():
        assert {key:after['table']['mechanisms'][ref][key] for key in ('status','source_step','source_ref','accepted_calls')}=={
            key:item[key] for key in ('status','source_step','source_ref','accepted_calls')}
    assert after['table']['remaining']==before['table']['remaining']
    assert after['extra_grant'] is None and after['work_count']==0
    assert after['last_science']==before['last_science']
    assert after['portfolio']['main_line'] is None
    assert c.validate(c.workspace(root,3))['portfolio_review']==advice


def test_unconsumed_optional_advice_needs_no_identity_resolution_or_repair():
    payload=dict(portfolio_review=dict(competitor=dict(evidence=[dict(source_step=999,pointer='/missing')])))
    assert search.optional_reference_diagnostics(None,dict(role='A'),payload)==[]
    assert search.require_portfolio_review(None,dict(role='A'),payload)==payload['portfolio_review']
