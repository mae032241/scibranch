"""Live original-call effort reaches the existing advice and hook publishers."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, budget_notice, contracts as c, controller, protocol, search, supervision


def scene(tmp_path,mode):
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal.md';goal.write_text('Explore the public observations and retain uncertainties.')
    root=tmp_path/'instance'
    chosen=dict(supervision.WORKER_FIRST_POLICY,mode=mode,max_automatic_reviews=1)
    controller.prepare(root,goal,public,['never-launched'],native_backend='protocol',
        model='fixture',reasoning_effort='high',max_tokens=100_000,max_seconds=120,max_steps=6,
        supervision_policy=chosen)
    job=c.build_job(root,1,'W',[])
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','deterministic investment advice')
    anchor=budget.accept_node(root,job)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),**(dict(budget_slice=anchor) if anchor is not None else {}),
        search_slice=search.accept(root,job))
    limits=budget.snapshot(root)
    head=accepted_budget.read(root)
    assert head['search']['supervision']['policy']==chosen
    shared=accepted_budget.usage_from_facts(head,
        job_rows=accepted_budget.load_usage_rows(head),current_native=None)
    scope=protocol.stage_scope(root,job)
    facts=dict(settings=c.settings(root),head=head,shared=shared,stamp=budget.clock_stamp(),
        task=intent['task_ref'],scope=scope,final_return=None,closeout_preparation_seconds=None,
        native_attempt=dict(number=0,execution_key=intent['task_ref']))
    live=dict(version=1,scope=scope,total_calls=4,by_mechanism={'w001.first':4},
        direction='w001.first',consecutive_calls=4,predecessors=[],last_original_call_id='original-4')
    return root,job,intent,limits,facts,live


def publish(root,job,intent,limits,facts):
    budget.write_node_hint(root,job,intent,limits,
        dict(input_tokens=2,cached_input_tokens=1,output_tokens=1),facts=facts,observed=True)
    work=c.workspace(root,1)
    path=work/'work/budget-status.json'
    assert path.stat().st_size<=4096
    return work,c.read(path)


@pytest.mark.parametrize('mode',['off','notice','review'])
def test_live_call_modes_reach_real_publishers_without_scientific_rounds(tmp_path,mode):
    root,job,intent,limits,facts,live=scene(tmp_path,mode)
    facts['research_investment']=live
    work,hint=publish(root,job,intent,limits,facts)
    assert hint['research_investment']['total_calls']==4
    assert hint['research_investment']['coverage']=='live'
    assert hint['research_investment']['mode']==mode
    review=c.read(budget._review_directory(root,job)/'review.json')
    assert review['pending'] is (mode=='review')
    # The advice contract itself distinguishes a notification from a handoff.
    if mode=='off':
        assert hint['research_investment']['due'] is False
        assert 'direction_review' not in hint
        assert 'investment' not in hint.get('closeout_notices',{})
    elif mode=='notice':
        assert hint['research_investment']['due'] is True
        assert 'direction_review' not in hint
        assert hint['target_state']=='within'
        output=budget_notice.hook_output(work,0)
        assert output['hookSpecificOutput']['additionalContext']==budget_notice.WORKER_INVESTMENT_TEXT
        assert len(output['hookSpecificOutput']['additionalContext'].encode())<=200
        assert budget_notice.hook_output(work,1) is None
        assert budget_notice.hook_output(work,0) is None
    else:
        assert hint['direction_review']['reasons']==['consecutive_calls']
        assert hint['target_state']=='reached'
        output=budget_notice.hook_output(work,0)
        assert output['hookSpecificOutput']['additionalContext']==budget_notice.WORKER_DIRECTION_TEXT
    assert facts['head']['search']['supervision']['investment']['total_calls']==0
    assert live['total_calls']==4


def test_missing_live_projection_is_an_explicit_lower_bound_not_zero_progress(tmp_path):
    root,job,intent,limits,facts,live=scene(tmp_path,'notice')
    facts['research_investment']=None
    work,hint=publish(root,job,intent,limits,facts)
    assert hint['research_investment']['coverage']=='accepted_lower_bound'
    assert hint['research_investment']['due'] is None
    assert 'direction_review' not in hint
    assert budget_notice.hook_output(work,0) is None
