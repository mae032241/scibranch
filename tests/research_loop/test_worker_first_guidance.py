"""V3 purpose contracts and actual fixed wire guidance remain bounded."""
import pytest

from scidiscovery.research_loop import backends,contracts as c,guidance
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_result_schema import rendered_contract
from test_scientific_guidance import prepare


def test_worker_first_literals_and_final_contract_are_versioned():
    current=guidance.ADVISORY_WORKER_FIRST
    assert guidance.DEFAULT is current
    assert c.ROLE_INSTRUCTIONS is current
    assert guidance.policy(current)==current
    assert 'Own research/methods' in current['W']
    assert '--research JSON' in current['W']
    assert '--mechanism' not in current['W']
    worker=dict(role='W',research_strategy_version=3,advisory_scope_version=1)
    fields=c.response_fields(worker,{})
    assert 'exploration_rounds' not in fields and 'research_memory' in fields
    assert 'cheap observations' in current['H']
    assert 'No material issue means no new constraints' in current['A']
    ordinary=dict(role='A',research_strategy_version=3,advisory_scope_version=1)
    assert c.result_schema('A',**c.response_options(ordinary))['properties']['next_action']['enum']==['continue_w']
    final=dict(ordinary,final_only=True,delivery_sources=dict(items=[{}]))
    assert 'reject all' in guidance.role_contract(final)
    assert c.response_options(final)['final_decline'] is True
    assert c.result_schema('A',**c.response_options(final))['properties']['next_action']['enum']==['deliver','finish']


def test_v3_consultation_requires_the_requesting_workers_concrete_question(tmp_path):
    value=dict(summary='Advice needed',scientific_result='A retained observation is unresolved',limitations=[],stage_intent='review')
    original=tmp_path/'science.json';c.write(original,value)
    with pytest.raises(OutputRepairRequired,match='consultation_question'):
        c.science_payload(original,'W',worker_consultation_question=True)
    c.write(original,dict(value,consultation_question='Would the conditional failure change candidate selection?'))
    assert c.science_payload(original,'W',worker_consultation_question=True)['consultation_question'].startswith('Would')
    c.write(original,value)
    assert c.science_payload(original,'W')['stage_intent']=='review'


def test_only_exact_v3_delivery_correction_accepts_the_frozen_legacy_binding():
    from scidiscovery.research_loop import delivery_review,history
    ref='w001.initial';source=dict(path='/instance/native-evidence/001/result-identity.json')
    origin=dict(goal_id='goal',source_step=1,source_role='W',mechanism_ref=ref)
    proof=dict(ordinal=1,origin_ref=origin,source_goal_id='goal')
    row=dict(mechanism_ref=ref,label='Retained direction',source_step=1,source_ref=source,source_role='W',
        minimal_test='Resolve the assigned gap',status='pending',**proof)
    breadth=dict(version=3,initial_calls=1,mechanisms=[row],baseline=[dict(mechanism_ref=ref,accepted_count=1)],
        grant=None,prior_unknown=False,outside_initial_owed=0,catalog=dict(prefix=dict(version=1,cutoff=2),max_ordinal=1))
    scope=dict(job_ref=dict(path='/instance/jobs/003.json'))
    legacy=dict(mechanism_ref=ref,source_step=1,source_ref=source,source_role='W',
        job_ref=scope['job_ref'],grant=None,catalog_binding=proof)
    correction=dict(role='W',step=3,research_strategy_version=3,search=dict(breadth=breadth),
        evidence_return=delivery_review.make('delivery_correction',2,1))
    assert history._check_call_binding(correction,legacy,scope)==legacy
    ordinary={key:value for key,value in correction.items() if key!='evidence_return'}
    with pytest.raises(ValueError):history._check_call_binding(ordinary,legacy,scope)
    with pytest.raises(ValueError):history._check_call_binding(dict(ordinary,purpose='delivery_correction'),legacy,scope)
    with pytest.raises(ValueError):history._check_call_binding(correction,dict(legacy,research_binding={}),scope)
    for version in (None,1,2):
        assert history._check_call_binding(dict(ordinary,research_strategy_version=version),legacy,scope)==legacy


def test_real_v3_final_correction_seals_without_loading_live_research(tmp_path,monkeypatch):
    from scidiscovery.research_loop import delivery_review,worker_research
    from test_delivery_review_guidance import correction_return,reviewed
    from test_history_handoff import finish
    root=prepare(tmp_path,None,research_strategy_version=3)
    finish(root,1,'W',[],stage_intent='decision')
    reviewed(root,2,1,0)
    finish(root,2,'A',[1],action='continue_w')
    correction=c.build_job(root,3,'W',[2],evidence_return=delivery_review.make('delivery_correction',2,1))
    task=c.read(c.workspace(root,3)/'science-task.json')
    assert 'Declare/revise stable mechanisms' not in task['question']
    assert 'frozen --mechanism N' in task['role_contract']
    assert not (correction.get('search') or {}).get('worker_research')
    def forbidden_live_load(*args,**kwargs):raise AssertionError('Correction has no live declaration permission')
    monkeypatch.setattr(worker_research,'load',forbidden_live_load)
    correction_return(root,correction)
    assert (root/'native-evidence/003/result-identity.json').is_file()
    final=reviewed(root,4,3,1)
    assert c.result_schema('A',**c.response_options(final))['properties']['next_action']['enum']==['deliver','finish']


def test_h_consultation_advice_names_the_existing_no_plan_return_action():
    contract=guidance.role_contract(dict(role='H',research_strategy_version=3,purpose='worker_consultation'))
    assert 'next_action=wait returns to W' in contract


def test_v3_clue_guidance_preserves_scoped_claims_and_reflection_information_value():
    from scidiscovery.research_loop import protocol
    assert 'Scope negatives' in guidance.ADVISORY_WORKER_FIRST['W']
    assert 'separate candidates/lineage/results and adaptive/final evidence' in guidance.ADVISORY_WORKER_FIRST['W']
    worker=dict(role='W',research_strategy_version=3,advisory_scope_version=1)
    stage=c.response_instructions(worker)['stage_response']
    assert 'research_memory' in stage
    assert 'nonblank statement field+nonempty evidence' in stage
    assert 'limited_negative needs applicability' in stage
    ordinary=dict(role='A',research_strategy_version=3,advisory_scope_version=1)
    marked=dict(ordinary,supervision_request=dict(policy_version=4,review_kind='ordinary_reflection'))
    contract=guidance.role_contract(ordinary)
    assert 'ignored counterevidence/critical alternatives' in contract
    assert 'Distinguish productive deepening' in contract
    assert 'No material issue means no new constraints' in contract
    assert 'Return continue_w with decision_request=null' in contract
    assert guidance.role_contract(marked)==contract
    with pytest.raises(protocol.UnsupportedProtocol):
        protocol.require_current_behavior(dict(marked,research_strategy_version=2))


@pytest.mark.parametrize('version',[None,1,2])
def test_rebuilding_current_tasks_preserves_literals_and_refuses_old_strategy(tmp_path,monkeypatch,version):
    from scidiscovery.research_loop import protocol
    root=prepare(tmp_path,None,research_strategy_version=3)
    job=c.build_job(root,1,'W',[])
    paths=(root/'settings.json',root/'jobs/001.json',c.workspace(root,1)/'science-task.json')
    frozen={path:path.read_bytes() for path in paths}
    assert job['native_role_instruction']==guidance.ADVISORY_WORKER_FIRST['W']
    purpose_instruction=job['role_purpose_instruction']
    monkeypatch.setitem(guidance.ADVISORY_WORKER_FIRST,'W','Changed future guidance; preserve the original task.')
    monkeypatch.setattr(guidance,'role_contract',lambda job:'Changed future purpose; preserve the original task.')
    assert c.build_job(root,1,'W',[])==job
    assert c.read(c.workspace(root,1)/'science-task.json')['role_contract']==purpose_instruction
    with pytest.raises(protocol.UnsupportedProtocol):
        protocol.require_settings_format(dict(c.settings(root),research_strategy_version=version))
    assert all(path.read_bytes()==data for path,data in frozen.items())


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('role,scope,branch',[
    ('H','ordinary','ordinary'),('H','consultation','ordinary'),
    ('W','ordinary','ordinary'),('W','ordinary','fragment'),('W','ordinary','repair'),
    ('A','ordinary','ordinary'),('A','ordinary','repair'),('A','consultation','ordinary'),
    ('A','supervision','ordinary'),('A','reflection','ordinary'),('A','reflection','repair'),
    ('A','final','ordinary'),('E','ordinary','ordinary'),
    ('W','correction','ordinary'),('W','correction','repair'),
])
def test_worker_first_actual_fixed_wire_contract_fits(tmp_path,backend_name,role,scope,branch):
    job=dict(role=role,step=3,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,
        advisory_scope_version=1,worker_view_version=1,native_subagents=backend_name=='codex',
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST[role],formal_review_version=1)
    if role in {'H','W'}:job['search']={}
    if role=='W':
        job.update(worker_fragment_policy=dict(context='same_session',context_handoff_version=2),
            scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode())))
    if scope=='consultation':job['purpose']='worker_consultation'
    elif scope=='correction':
        from scidiscovery.research_loop import delivery_review
        job['evidence_return']=delivery_review.make('delivery_correction',2,1)
    elif scope in {'supervision','reflection'}:
        job['supervision_request']=dict(version=1,cutoff=dict(total_rounds=0,source_step=None,round_id=None),
            due_reasons=['fixture'],budget_fraction=None,automatic=True,final=False)
        if scope=='reflection':job['supervision_request'].update(review_kind='ordinary_reflection',reflection_scope='worker_question_clues_evidence')
    elif scope=='final':job.update(purpose='closeout',search=None,final_only=True,delivery_sources=dict(items=[{}]))
    job['scientific_response_fields']=c.response_fields(job,{})
    job['role_purpose_instruction']=guidance.role_contract(job)
    task=c.response_instructions(job)
    if scope=='correction':
        assert 'frozen --mechanism N' in task['role_contract']
        assert 'no new declarations' in task['role_contract']
        assert 'breadth_response' not in task and 'revise_h=H' not in task['stage_response']
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),
        schema_transport='prompt' if backend_name=='codex' else None)
    intent={};remaining=None
    if branch=='fragment':intent['continuation_kind']='fragment';remaining={}
    elif branch=='repair':
        repair_field='work_repair' if role=='W' else 'output_repair'
        intent[repair_field]=OutputRepairRequired('candidate_recommendation_missing','D'*512).request();remaining={}
    context=dict(remaining=remaining,final_return=scope=='final',
        budget_status=dict(shared_seconds_remaining=None,shared_observed_tokens_remaining=None),
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command') if role=='W' else None,
        managed_command=['experiment'] if role=='W' else None)
    size,_,_,_=rendered_contract(tmp_path,job,task,intent,backend,**context)
    if branch=='repair':
        empty=dict(intent,**{repair_field:dict(intent[repair_field],diagnostic='')})
        fixed_size,_,_,_=rendered_contract(tmp_path,job,task,empty,backend,**context)
        assert fixed_size+512<=c.MAX_FIXED_RESPONSE_BYTES,(role,scope,backend_name,fixed_size)
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(role,scope,backend_name,branch,size)
