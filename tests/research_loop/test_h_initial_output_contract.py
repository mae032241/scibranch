"""Initial H choices are disclosed, author corrected, and retain exact originals."""
import copy
import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, native, search
from scidiscovery.research_loop.scientific_intent import resolve_intent
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_decision_value_guidance import prepare
from test_history_handoff import finish
from test_result_schema import rendered_contract


def declaration(*, focus=None):
    return dict(commitments=[dict(label='Revisable explanation'),dict(label='Competing explanation')],
        focus=focus if focus is not None else dict(index=1,action='improve'))


@pytest.mark.parametrize('choice',[[], 'plan in artifact',dict(commitments=[dict(label='Explanation')],focus={'action':'improve'})])
def test_actual_seal_reports_role_path_and_shape_for_malformed_plan(tmp_path,choice):
    root=prepare(tmp_path,None,search_policy=search.DEFAULT_POLICY)
    with pytest.raises(OutputRepairRequired) as refused:
        finish(root,1,'H',[],action='experiment',search_choice=choice,
            files={'direction.json':'{"search_plan":{"commitments":[{"label":"Advice"}],"focus":null}}'})
    assert 'H output search_plan.focus' in refused.value.diagnostic
    assert 'top-level search_plan' in refused.value.diagnostic
    assert 'commitments' in refused.value.diagnostic and 'index:null requires screen' in refused.value.diagnostic
    assert not (execution.origin(root,1)/'result-identity.json').exists()


def test_explicit_breadth_choice_seals_and_admits_next_w(tmp_path):
    root=prepare(tmp_path,None,search_policy=search.DEFAULT_POLICY)
    finish(root,1,'H',[],action='experiment',search_choice=declaration(focus=dict(index=None,action='screen')))
    job=c.build_job(root,2,'W',[1])
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action='screen')
    assert len(job['search']['coverage_obligation'])==2
    assert search.launch_reason(root,job) is None


def test_actual_seal_names_implementation_selection_and_exact_shape(tmp_path):
    root=prepare(tmp_path,None,search_policy=search.DEFAULT_POLICY)
    with pytest.raises(OutputRepairRequired) as refused:
        finish(root,1,'H',[],action='experiment',search_choice=declaration(),selection=['W999-I001'])
    assert refused.value.code=='implementation_unresolved'
    assert 'H output implementation_selection' in refused.value.diagnostic
    assert 'array of exact current/history input IDs' in refused.value.diagnostic
    assert 'null keeps only an already resolved selection' in refused.value.diagnostic
    assert not (execution.origin(root,1)/'result-identity.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('initial', ['artifact_only','null_focus','focused'])
def test_controller_h_repairs_missing_initial_choice_once_before_next_w(tmp_path,monkeypatch,initial):
    """The real controller, native transport, seal and finite repair reach W admission."""
    import test_default_cli_output_repair as transport
    injection='''
if role=='H':
 plan=dict(commitments=[dict(label='Revisable explanation'),dict(label='Competing explanation')],focus=dict(index=1,action='improve'))
 payload.update(next_action='experiment',route_reason='W owns the next scientific design and may revise these explanations.')
 payload.pop('search_plan',None)
 (work/'artifacts/scientific_direction.json').write_text(json.dumps(dict(search_plan=dict(plan,focus=None))))
 if INITIAL=='focused':payload['search_plan']=plan
 elif INITIAL=='null_focus':payload['search_plan']=dict(plan,focus=None)
 if resume:
  assert 'H output search_plan.focus' in prompt
  assert 'top-level search_plan' in prompt and 'commitments' in prompt
  assert 'index:null requires screen' in prompt
  assert 'W scientific task' not in prompt
  (work/'work/repair-prompt.txt').write_text(prompt)
  payload['search_plan']=plan
'''.replace('INITIAL',repr(initial))
    marker="if not (config['fault']=='completed-missing-output' and role=='W' and not resume):"
    monkeypatch.setattr(transport,'NATIVE',transport.NATIVE.replace(marker,injection+'\n'+marker))
    root,_,env=transport.prepare_cli(tmp_path,fault='incomplete_w')
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(key,env[key])
    original=native.execute_native
    class WorkerReached(BaseException):
        pass
    def execute(root,step,*args,**kwargs):
        job=c.read(root/'jobs'/f'{step:03d}.json')
        if job['role']=='W':
            assert search.launch_reason(root,job) is None
            raise WorkerReached
        return original(root,step,*args,**kwargs)
    monkeypatch.setattr(native,'execute_native',execute)
    with pytest.raises(WorkerReached):controller.run(root)
    origin=execution.origin(root,1);records=execution.records(root,1)
    assert len(records)==(1 if initial=='focused' else 2)
    first=c.read(origin/'science-output.json');accepted=c.validate(c.workspace(root,1))
    assert accepted['search_plan']==declaration()
    for field in ('summary','scientific_result','route_reason','limitations'):
        assert accepted[field]==first[field]
    if initial!='focused':
        if initial=='artifact_only':assert 'search_plan' not in first
        else:assert first['search_plan']['focus'] is None
        repaired=c.read(records[1]/'native-intent.json')
        assert repaired['continuation_kind']=='repair'
        assert repaired['output_repair']['code']=='science_task_action_required'
        assert repaired['budget_slice']['previous_tokens']==14
        assert repaired['task_ref']==c.read(origin/'native-intent.json')['task_ref']
        assert (c.workspace(root,1)/'work/launches').read_text()=='exec\nresume\n'
    worker=c.read(root/'jobs/002.json')
    assert worker['search']['focus']==dict(mechanism_ref='h001.m1',action='improve')
    assert worker['role']=='W'


@pytest.mark.parametrize('role,action',[('H','experiment'),('H','analyze'),('A','continue_w'),('A','revise_h')])
@pytest.mark.parametrize('choice',[None,{}])
def test_omitted_focus_retains_authorized_focus_and_remaining_allocation(role,action,choice):
    allocation=dict(mechanism_ref='h001.m1',planned_step=1)
    focus=dict(mechanism_ref='h001.m1',action='improve')
    state=dict(mechanisms={'h001.m1':dict(status='pending')},authorized={'h001.m1'},focus=focus,
        order=dict(remaining=[allocation],seen_w=set(),allocation=allocation))
    original=copy.deepcopy(state)
    payload=dict(next_action=action,**{'search_plan' if role=='H' else 'search_review':choice})
    result=resolve_intent(state,dict(role=role,step=3),payload)
    assert result['focus']==focus and result['allocation']==allocation and result['diagnostics']==[]
    assert state==original


def test_a_retained_focus_does_not_resurrect_consumed_allocation():
    allocation=dict(mechanism_ref='h001.m1',planned_step=1)
    focus=dict(mechanism_ref='h001.m1',action='improve')
    state=dict(mechanisms={'h001.m1':dict(status='pending')},authorized={'h001.m1'},focus=focus,
        order=dict(remaining=[],seen_w={'original-w'},allocation=None))
    result=resolve_intent(state,dict(role='A',step=3),dict(next_action='continue_w'),
        assessed=dict(identity='original-w',mechanism_ref='h001.m1',allocation=allocation,breadth=False))
    assert result['focus']==focus and result['allocation'] is None and result['unfinished_plan']==[]


def test_a_omitted_same_repair_retains_unconsumed_allocation():
    allocation=dict(mechanism_ref='h001.m1',planned_step=1)
    focus=dict(mechanism_ref='h001.m1',action='repair')
    state=dict(mechanisms={'h001.m1':dict(status='pending')},authorized={'h001.m1'},focus=focus,
        order=dict(remaining=[allocation],seen_w=set(),allocation=allocation))
    result=resolve_intent(state,dict(role='A',step=3),dict(next_action='continue_w'),
        assessed=dict(identity='original-w',mechanism_ref='h001.m1',allocation=allocation,breadth=False))
    assert result['focus']==focus and result['allocation']==allocation
    assert result['unfinished_plan']==[allocation] and result['seen_w']==set()


@pytest.mark.parametrize('authorized,status',[(False,'pending'),(True,'waived')])
def test_omission_does_not_reauthorize_ineligible_focus(authorized,status):
    state=dict(mechanisms={'h001.m1':dict(status=status)},authorized={'h001.m1'} if authorized else set(),
        focus=dict(mechanism_ref='h001.m1',action='improve'),
        order=dict(remaining=[],seen_w=set(),allocation=None))
    result=resolve_intent(state,dict(role='H',step=3),dict(next_action='experiment'))
    assert result['focus'] is None
    assert result['diagnostics'][0]['subject']=='search_plan.focus'


@pytest.mark.parametrize('role,action',[('H','analyze'),('A','revise_h')])
def test_advice_omission_retains_focus_through_real_seal_and_accepted_fold(tmp_path,role,action):
    root=prepare(tmp_path,None,search_policy=search.DEFAULT_POLICY)
    finish(root,1,'H',[],action='experiment',search_choice=declaration())
    original=c.read(execution.origin(root,1)/'result-identity.json')['control_outcome']
    _,_,record=finish(root,2,role,[1],action=action)
    accepted=c.read(record/'result-identity.json')['control_outcome']
    assert accepted['resolved_focus']==original['resolved_focus']
    assert accepted['resolved_allocation']==original['resolved_allocation']
    next_job=c.build_job(root,3,'W',[2])
    assert next_job['search']['focus']==original['resolved_focus']
    assert search.launch_reason(root,next_job) is None


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('role',['H','A'])
def test_conditional_choice_and_repair_rules_fit_rendered_budget(tmp_path,backend_name,role):
    from scidiscovery.research_loop import backends,decision,guidance
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    from scidiscovery.research_loop.scientific_intent import focus_diagnostic
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,search={},
        native_role_instruction=max((guidance.OPPORTUNITY[role],c.ROLE_INSTRUCTIONS[role]),key=lambda value:len(value.encode())))
    if role=='A':job['scientific_response_fields']=c.response_fields(job,{})
    task=c.response_instructions(job)
    if role=='A':
        assert 'breadth_response' not in task
        assert 'decision_request{decision:candidate|mechanism|compliance,question,change_criterion}' in task['role_contract']
        with pytest.raises(OutputRepairRequired) as refused:
            decision.require_request(job,dict(next_action='continue_w'))
        repair=refused.value.request()
        subject='decision_request'
    else:
        assert 'top-level search_plan' in task['breadth_response']
        row=focus_diagnostic(job)
        repair=OutputRepairRequired(row['code'],row['message']).request()
        subject=row['subject']
    backend=backends.selected(dict(native_backend=backend_name,native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),schema_transport='prompt' if backend_name=='codex' else None)
    size,prompt,_,_=rendered_contract(tmp_path,job,task,dict(output_repair=repair),backend,remaining={})
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072
    assert subject in prompt


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('role',['H','A'])
@pytest.mark.parametrize('branch',['ordinary','repair'])
def test_retained_prior_response_rules_fit_current_rendered_budget(tmp_path,backend_name,role,branch):
    """Current resumes retain old task bytes and append the current response rules."""
    from scidiscovery.research_loop import backends,guidance
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,search={},formal_review_version=1,
        scientific_response_fields=['portfolio_review','final_coverage'],
        native_role_instruction=max((guidance.OPPORTUNITY[role],c.ROLE_INSTRUCTIONS[role]),key=lambda value:len(value.encode())))
    if role=='A':job['scientific_response_fields']=c.response_fields(job,{})
    task=c.response_instructions(job)
    if role=='H':
        task['breadth_response']='search_plan:commitments[{label,existing_ref?}],focus?{index:1-based|null,action:screen|improve}; null=breadth; omissions retain.'
    else:
        assert 'breadth_response' not in task and 'portfolio_response' not in task
        task['role_contract']='Answer the concrete assessment question with a meaningful judgment;'
    frozen=copy.deepcopy(task)
    intent=dict(output_repair=OutputRepairRequired('science_task_action_required','D'*512).request()) if branch=='repair' else {}
    backend=backends.selected(dict(native_backend=backend_name,native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),schema_transport='prompt' if backend_name=='codex' else None)
    size,prompt,instruction,_=rendered_contract(tmp_path,job,task,intent,backend,
        remaining={} if branch=='repair' else None,
        budget_status=dict(shared_seconds_remaining=None,shared_observed_tokens_remaining=None))
    assert task==frozen
    assert 'Append task rules:' in prompt+instruction
    if role=='A':
        assert 'decision_request{decision:candidate|mechanism|compliance,question,change_criterion}' in prompt+instruction
        assert 'continue_w needs focus' not in prompt+instruction
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(role,backend_name,branch,size)
