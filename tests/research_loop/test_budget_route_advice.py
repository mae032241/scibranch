"""Scientific ownership and local budget advice use the real native request."""
import json

import pytest

from scidiscovery.research_loop import backends, contracts as c, continuation, controller, guidance, native
from test_history_handoff import finish


def request_scene(tmp_path, role, *, advice=None, instruction=''):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain scientific requirements and use exact prior evidence.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launch'],max_tokens=1000,max_seconds=120,max_steps=6,
        model='fixture',reasoning_effort='high',search_policy=None,
        **(dict(role_guidance=advice) if advice is not None else {}))
    if role=='A':
        finish(root,1,'W',[],stage_intent='decision')
        step,parents=2,[1]
    else:
        step,parents=1,[]
    job=c.build_job(root,step,role,parents,instruction)
    work=c.workspace(root,step)
    return root,job,c.read(work/'science-task.json'),work


@pytest.mark.parametrize('role','HAW')
@pytest.mark.parametrize('backend_name,transport',[('codex','developer'),('protocol','prompt')])
def test_role_planning_uses_control_allowance_without_rewriting_science(tmp_path,role,transport,backend_name):
    root,job,task,work=request_scene(tmp_path,role)
    originals={path:path.read_bytes() for path in
        (root/'settings.json',root/'jobs'/f"{job['step']:03d}.json",work/'science-task.json')}
    status=dict(shared_seconds_remaining=9,shared_observed_tokens_remaining=17,
        node_budget=dict(max_observed_tokens=900000,max_seconds=120),
        exploration=dict(observed_tokens_remaining_upper_bound=17,
            active_seconds_available=9,token_usage_unknown=True))
    prompt,instruction,schema=native.render_scientific_request(job,task,{},backend_name=backend_name,
        instruction_transport=transport,budget_status=status)
    assert job['research_strategy_version']==3
    if role=='W':assert job['step']==1 and job['direct_refs']==[]
    assert c.settings(root)['role_guidance']==guidance.ADVISORY_WORKER_FIRST
    assert 'Goals remain.' in prompt
    assert ('work/budget-status.json: stale/partial/no grant; unknown!=0; input has cache. '
        'Targets soft; limits/permissions bind.') in prompt
    assert task['role_contract']==guidance.role_contract(job)
    if role=='W':
        # guidance.role_contract and contracts.response_instructions deliberately
        # carry v3 W authority once in stage_response, not a second role banner.
        assert task['stage_response']==c.response_instructions(job)['stage_response']
        assert job['native_role_instruction']==guidance.ADVISORY_WORKER_FIRST['W']
        assert 'No unknown-call replay' in instruction
    if role=='A':
        assert ('Complete the requested judgment; follow role_contract. Soft targets do not end research. '
            'Shared tokens<=0 or time<=0: retain originals/gaps; final-return admission and permissions hold.') in prompt
        assert 'delivery_selection' not in c.response_instructions(job)
        assert 'deliver eligible candidate or finish with gaps from originals' not in prompt
    else:
        assert 'Follow-up targets advise; no grant/required handoff.' in prompt
    if role=='W':
        assert prompt.count('Wait 10-30s on pending originals within deadlines.')==1
        assert 'decision=pre-delivery Final A. Advice returns W; no grant.' in task['stage_response']
        assert 'review=A advice' in task['stage_response']
    else:
        assert 'Wait 10-30s' not in prompt
    assert json.JSONDecoder().raw_decode(prompt.splitlines()[-1])[0]==status
    assert schema==c.result_schema(role,job['handoff_protocol'],'selection' in job,**c.response_options(job))
    assert instruction.startswith(job['native_role_instruction']+'\n')
    assert all(path.read_bytes()==value for path,value in originals.items())


@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_a_delta_reading_advice_reaches_actual_backend_without_changing_frozen_inputs(tmp_path,backend_name):
    root,job,task,work=request_scene(tmp_path,'A')
    originals={path:path.read_bytes() for path in
        (root/'settings.json',root/'jobs/002.json',work/'science-task.json')}
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent']),
        schema_transport='prompt' if backend_name=='codex' else None)
    prompt,instruction,schema=native.render_scientific_request(job,task,{},backend_name=backend.name,
        instruction_transport=backend.instruction_transport)
    schema_path=work/'response.schema.json';c.write(schema_path,schema)
    packet=backend.request(prompt,schema=schema_path,profile={},work=work,response_schema=schema)
    delivered=packet.decode() if backend_name=='codex' else json.loads(packet)['prompt']
    assert ('Complete the requested judgment; follow role_contract. Soft targets do not end research. '
        'Shared tokens<=0 or time<=0: retain originals/gaps; final-return admission and permissions hold.') in delivered
    assert instruction.startswith(job['native_role_instruction']+'\n')
    assert job['native_role_instruction']==guidance.ADVISORY_WORKER_FIRST['A']
    assert task['role_contract']==guidance.role_contract(job)
    assert 'decision_request' in job['scientific_response_fields']
    assert set(schema['required'])=={'summary','scientific_result','limitations','next_action','route_reason'}
    assert schema['additionalProperties'] is True
    assert schema==c.result_schema('A',job['handoff_protocol'],**c.response_options(job))
    assert c.build_job(root,2,'A',[1])==job
    assert all(path.read_bytes()==value for path,value in originals.items())


@pytest.mark.parametrize('variant,role',[
    ('output_repair','A'),('output_repair','W'),('work_repair','A'),('work_repair','W'),
    ('output_only_repair','W'),('author','A'),('author','W'),('final','A'),
    ('closeout','A'),('final_report','A'),('final_only','A'),('formal_review','A'),
])
@pytest.mark.parametrize('backend_name,transport',[('codex','developer'),('protocol','prompt')])
def test_restricted_returns_do_not_expand_scientific_scope(tmp_path,variant,role,backend_name,transport):
    _,job,task,_=request_scene(tmp_path,role)
    intent={};options={}
    if variant=='output_repair':intent['output_repair']=dict(code='json',diagnostic='Return the existing result as JSON.')
    elif variant=='work_repair':intent['work_repair']=dict(code='missing_work',diagnostic='Complete only the original assignment.')
    elif variant=='output_only_repair':intent.update(continuation_kind='repair',output_repair=dict(
        code='missing_output',diagnostic='Return the missing scientific summary without rerunning completed work.'))
    elif variant=='author':options['author']=dict(facts='work/author-closeout-science.json',limits={})
    elif variant=='final':options['final_return']=True
    elif variant=='closeout':job['purpose']='closeout'
    elif variant=='final_report':job['final_report']={'source_cutoff':1}
    elif variant=='final_only':job['final_only']=True
    else:
        job['formal_review']={'subject':'original'}
        task['review_instruction']='Independently assess the exact original.'
    prompt,instruction,schema=native.render_scientific_request(job,task,intent,backend_name=backend_name,
        instruction_transport=transport,**options)
    for text in ('Goals remain.','Follow-up targets advise','Wait 10-30s'):
        assert text not in prompt
    if variant in {'output_repair','output_only_repair'}:
        assert 'No work/rerun/resubmit; W grants unchanged; no prior experiment implied:' in prompt
    if variant=='final':assert 'Protected FINAL: conclude now; no new role/audit/experiment' in prompt
    assert schema==c.result_schema(role,job['handoff_protocol'],**c.response_options(job))
    if variant=='formal_review':assert instruction==task['review_instruction']
    else:assert instruction.startswith(job['native_role_instruction']+'\n')


def test_custom_frozen_instruction_is_retained_with_launcher_level_budget_advice(tmp_path):
    custom={**guidance.DEFAULT,'H':'Retain all 25 required runs and the final eight unimproved audits. This finite stage permits exactly two calls.'}
    root,job,task,_=request_scene(tmp_path,'H',advice=custom)
    prompt,instruction,_=native.render_scientific_request(job,task,{},backend_name='codex',
        instruction_transport='prompt')
    assert instruction.startswith(custom['H']+'\n') and prompt.startswith(custom['H']+'\n')
    assert c.settings(root)['role_guidance']==custom
    assert 'Targets soft; limits/permissions bind.' in prompt
    assert 'Follow-up targets advise; no grant/required handoff.' in prompt


@pytest.mark.parametrize('unit,expected',[
    ('screen',('using fair controls','Reuse adequate evidence; report observations and obstacles.',
        'Calls alone do not establish screening.')),
    ('improve',('Sustained exploration of the focused scientific mechanism toward unmet goals',
        'adapt from observations/residuals','beyond a batch or weak result')),
    ('validate',('stop when resolved or blocked, without a new search',)),
    ('repair',('Repair the exact scientific observation','declared mechanism and original allowance',
        'Preserve checks/accepted evidence','reconcile unknown calls, never replay')),
])
def test_work_unit_task_retains_exact_requirements_with_decision_led_improvement(tmp_path,unit,expected):
    # Explicit work-unit recipes retain their scientific requirements.
    root,job,_,work=request_scene(tmp_path,'W',
        instruction='Keep five required comparisons and the exact validation split.')
    assert job['research_strategy_version']==3 and job['advisory_scope_version']==1
    assert job['instruction']=='Keep five required comparisons and the exact validation split.'
    assert c.read(root/'jobs'/f"{job['step']:03d}.json")['instruction']==job['instruction']
    # Quantitative scientific requirements remain binding even when ordinary
    # improvement can adapt methods after a suggested local comparison.
    job=dict(job,scientific_work_unit=guidance.WORK_UNITS[unit])
    task=c.science_view(job,root)
    c.write(work/'science-task.json',task)
    originals={path:path.read_bytes() for path in
        (root/'settings.json',root/'jobs'/f"{job['step']:03d}.json",work/'science-task.json')}
    prompt,instruction,schema=native.render_scientific_request(job,task,{},backend_name='protocol',
        instruction_transport='prompt')
    assert schema==c.result_schema('W',job['handoff_protocol'],**c.response_options(job))
    assert task['question'].startswith(job['instruction']+'\n')
    assert task['question'].endswith('\nWork unit: '+guidance.WORK_UNITS[unit])
    assert instruction.startswith(job['native_role_instruction']+'\n')
    assert 'Own research/methods' in instruction
    assert 'No unknown-call replay' in instruction
    assert 'Targets soft; limits/permissions bind.' in prompt
    assert 'Follow-up targets advise; no grant/required handoff.' in prompt
    for text in expected:assert text in task['question']
    assert all(path.read_bytes()==value for path,value in originals.items())


@pytest.mark.parametrize('transport',['prompt','developer'])
@pytest.mark.parametrize('waiting',['wake','managed','interface'])
def test_worker_handoff_distinguishes_final_scientific_response_from_pending_wait(tmp_path,transport,waiting):
    root,job,task,work=request_scene(tmp_path,'W')
    originals={path:path.read_bytes() for path in
        (root/'settings.json',root/'jobs/001.json',work/'science-task.json')}
    intent={};options={}
    if waiting=='wake':intent['execution_wake']=True
    elif waiting=='managed':job['managed_execution_wait']={}
    else:options=dict(execution_interface=dict(capabilities={},result_protocol={}),managed_command=['original-tool'])
    prompt,instruction,schema=native.render_scientific_request(job,task,intent,backend_name='codex',
        instruction_transport=transport,**options)
    assert prompt.count('handoff.action:finalize_response=JSON;return_control=wait.')==1
    assert 'poll pending originals' not in prompt
    assert prompt.count('Wait 10-30s on pending originals within deadlines.')==1
    assert 'Follow-up targets advise; no grant/required handoff.' in prompt
    assert 'Targets soft; limits/permissions bind.' in prompt
    assert instruction.startswith(job['native_role_instruction']+'\n')
    assert 'decision=pre-delivery Final A. Advice returns W; no grant.' in task['stage_response']
    assert schema==c.result_schema('W',job['handoff_protocol'],'selection' in job,**c.response_options(job))
    if waiting=='interface':
        managed=json.JSONDecoder().raw_decode(prompt.split('Execution: ',1)[1])[0]
        assert managed==dict(command=['original-tool'],lifecycle={},result_protocol={})
        assert 'Read capabilities/describe; exact IDs; capture freezes code/; other outputs/logs.' in prompt
    if waiting=='wake':
        assert 'Read work/execution-results.json; continue original W work; never replay accepted calls.' in prompt
        assert 'output repair' not in prompt and 'Author-only closeout' not in prompt
        assert 'Advice returns W; no grant.' in task['stage_response']
        assert intent==dict(execution_wake=True)
    # Rendering wake/wait advice cannot alter frozen permissions or the task.
    assert all(path.read_bytes()==value for path,value in originals.items())


@pytest.mark.parametrize('wake,allowed',[(None,False),(False,False),(True,True)])
def test_output_only_wake_requires_independent_frozen_opt_in(tmp_path,monkeypatch,wake,allowed):
    # continuation.wake_allowed / validate_wake_policy retain restricted-wake
    # coverage: output-only JSON repair permission alone cannot authorize wake.
    # execution.validate_records likewise checks this gate independently from
    # output repair. An explicitly permitted wake resumes ordinary W scope; it
    # does not turn the repair itself into permission for new scientific work.
    limits=dict(output_only=True,output_repair=True)
    if wake is not None:limits['execution_wake']=wake
    original=dict(limits)
    monkeypatch.setattr(continuation,'validate_launcher',lambda root,step:limits)
    assert continuation.wake_allowed(limits) is allowed
    if allowed:
        assert continuation.validate_wake_policy(tmp_path,1)==limits
    else:
        with pytest.raises(continuation.ResumeUnavailable,match='worker_execution_wake_unauthorized'):
            continuation.validate_wake_policy(tmp_path,1)
    assert limits==original


@pytest.mark.parametrize('role','HWAE')
@pytest.mark.parametrize('repair',[False,True])
@pytest.mark.parametrize('backend',['codex','protocol'])
def test_default_fixed_contract_retains_room_for_full_repair_diagnostic(role,repair,backend):
    # Include the applicable breadth/portfolio/final-coverage rules, rather
    # than measuring a small no-search task. Goal/data, concrete command and
    # budget observations are variable inputs outside this fixed contract.
    instruction=guidance.ADVISORY_WORKER_FIRST[role]
    job=dict(role=role,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,
        native_role_instruction=instruction,formal_review_version=1,
        scientific_response_fields=['portfolio_review','final_coverage'] if role=='A' else [])
    if role in 'HA':job['search']={}
    if role=='A':job.update(portfolio_context={},final_coverage_mechanisms=[{}])
    if role=='W':job['worker_fragment_policy']=dict(version=2,context='new_context')
    rules=c.response_instructions(job)
    intent=dict(output_repair=dict(code='output_layout_invalid',diagnostic='x'*512)) if repair else {}
    options={}
    if role=='W':options=dict(execution_interface=dict(capabilities={},result_protocol={}),managed_command=[])
    prompt,instruction,_=native.render_scientific_request(job,rules,intent,backend_name=backend,
        instruction_transport='developer',**options)
    # The developer transport keeps the instruction separate. Prompt transport
    # embeds those same bytes and must not count them a second time.
    embedded,_,_=native.render_scientific_request(job,rules,intent,backend_name=backend,
        instruction_transport='prompt',**options)
    assert embedded==instruction+'\n'+prompt
    fixed_bytes=len((instruction+'\n'+prompt).encode())+len(
        json.dumps(rules,ensure_ascii=False,separators=(',',':')).encode())
    total=fixed_bytes+(0 if repair else 512)
    assert total<=c.MAX_FIXED_RESPONSE_BYTES,f'{role} repair={repair}: {total} fixed bytes including diagnostic'
