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
        for label in ('Main','Competitor')],focus=dict(label='Main',action='improve'))
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


@pytest.mark.parametrize('bad',['missing','null','malformed','blank','main','ref','action','focus'])
@pytest.mark.parametrize('search_value',['valid','null','malformed'])
def test_real_seal_rejects_raw_comparison_before_publication(tmp_path,bad,search_value):
    root=prepared(tmp_path);review=comparison()
    if bad=='null':review=None
    elif bad=='malformed':review={'main_line':MAIN}
    elif bad=='blank':review['reason']=' \t\n'
    elif bad=='main':review['main_line']='h999.m1'
    elif bad=='ref':review['competitor']['evidence'][0]['source_step']=3
    elif bad=='action':review['decision']='continue'
    elif bad=='focus':review=comparison(action='continue_w')
    job,payload,record=raw_author(root,review=review,action='continue_w' if bad=='focus' else 'finish')
    if bad=='missing':payload.pop('portfolio_review')
    if search_value=='null':payload['search_review']=None
    elif search_value=='malformed':payload['search_review']={'dispositions':'bad'}
    rewrite(record,payload)
    with pytest.raises(OutputRepairRequired,match='portfolio_review'):
        c.seal_result(root,3)
    assert not (record/'result-identity.json').exists()
    assert not (record/'result.json').exists()
    assert not (root/'host-history/nodes/003.json').exists()


def test_single_line_can_honestly_declare_no_viable_competitor_or_work(tmp_path):
    root=prepared(tmp_path)
    review=comparison();review.update(competitor=None,no_competitor_reason='No feasible competing explanation under current inputs.')
    job,payload,record=raw_author(root,review=review)
    c.seal_result(root,3)
    assert c.validate(c.workspace(root,3))['portfolio_review']==review
    assert (record/'result-identity.json').exists()
    assert search.project(root,4)['order']['discriminating_work']==[]


@pytest.mark.parametrize('pointer',['/role','/direct_refs/0/path','/implementation_selection','/control_outcome','/scientific_result/missing','/summary/~2'])
def test_science_reference_cannot_access_control_or_invalid_pointer(tmp_path,pointer):
    root=prepared(tmp_path);job,_,_=raw_author(root,review=comparison())
    with pytest.raises(ValueError):search.validate_science_refs(root,job,[dict(source_step=2,pointer=pointer)])
    assert search.validate_science_refs(root,job,[dict(source_step=2,pointer='/scientific_result')])==[
        dict(source_step=2,pointer='/scientific_result')]


@pytest.mark.parametrize('boundary',['wait','no_search','no_completed_w'])
def test_legitimate_exemptions_allow_null_without_inventing_judgment(tmp_path,boundary):
    root=prepared(tmp_path,search_enabled=boundary!='no_search',completed=boundary!='no_completed_w')
    step=2 if boundary=='no_completed_w' else 3
    job,_,record=raw_author(root,step,parents=(1,) if step==2 else (1,2),
        action='wait' if boundary=='wait' else 'finish')
    c.seal_result(root,step)
    assert c.read(record/'science-output.json')['portfolio_review'] is None


@pytest.mark.parametrize('action',['audit','extract','formal_review'])
def test_intermediate_action_is_not_a_comparison_exemption(tmp_path,action):
    root=prepared(tmp_path);job,payload,_=raw_author(root,action=action)
    # The ordinary A request is distinct from an actual independent review job.
    with pytest.raises(OutputRepairRequired):
        search.require_portfolio_review(root,job,payload,interpretation=dict(focus=None))


def test_nullable_scientific_field_cannot_silently_erase_required_comparison(tmp_path):
    root=prepared(tmp_path);job,payload,record=raw_author(root,review={'reason':'incomplete'})
    assert c.scientific_field(job,payload,'portfolio_review') is None
    assert c.science_payload(record/'science-output.json','A',**c.response_options(job))['portfolio_review']==payload['portfolio_review']
    with pytest.raises(OutputRepairRequired):c.seal_result(root,3)


def test_final_context_only_uses_exact_pending_or_direct_w(tmp_path):
    root=prepared(tmp_path)
    _,_,record=raw_author(root,review=comparison());c.seal_result(root,3)
    job=c.build_job(root,4,'A',[1,3])
    final=dict(job,final_report=dict(source_cutoff=3),search=None)
    assert not search.assessment_context(root,final)['applicable']
    # Context-level identity is exercised against the original task inputs,
    # whose source W remains assessable despite the completed-cycle de-dup set.
    original=c.read(root/'jobs/003.json')
    context=search.assessment_context(root,dict(original,final_report=dict(source_cutoff=2),search=None))
    assert context['applicable'] and [row['step'] for row in context['completed_w']]==[2]
