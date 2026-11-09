"""Same-session consultation transport binds original and successor separately."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import backends, continuation, contracts as c, native
from scidiscovery.research_loop import native_app_server


PROFILE=dict(model='explicit-worker',reasoning_effort='high',service_tier='priority')


def original(tmp_path):
    root=tmp_path/'instance';home=tmp_path/'native-home'
    record=root/'native-evidence/001';record.mkdir(parents=True,exist_ok=True)
    advisor=root/'native-evidence/002';advisor.mkdir(parents=True,exist_ok=True)
    source=c.workspace(root,1);target=c.workspace(root,3)
    source.mkdir(parents=True,exist_ok=True);target.mkdir(parents=True,exist_ok=True)
    (root/'jobs').mkdir(exist_ok=True)
    for step,role in [(1,'W'),(2,'A')]:
        path=root/'jobs'/f'{step:03d}.json'
        if not path.exists():c.write(path,dict(step=step,role=role))
    transcript=home/'sessions/original.jsonl';transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b'opaque original native transcript\n')
    stat=transcript.stat()
    session=dict(source='native_metadata_hooks',thread_id='original-worker',home=str(home),
        workspace=str(source),path=str(transcript),identity=[stat.st_dev,stat.st_ino],offset=stat.st_size)
    raw=record/'science-output.json';raw.write_text('{}')
    raw_ref=c.file_ref(raw)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/001.json'),native_profile=PROFILE))
    state=dict(coverage='live',windows_started=3,reason=None,
        cursor=dict(thread_id='original-worker',compactions=2,
            terminal_tokens=dict(input_tokens=40,cached_input_tokens=2,output_tokens=8)))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,terminal_observed=True,
        cleanup='observed_group_cleaned',thread_id='original-worker',native_backend='codex',raw_ref=raw_ref,
        native_thread_event=dict(type='thread.started',thread_id='original-worker'),
        native_identity=dict(home=str(home),session=session),metering=dict(context_windows=state)))
    c.write(record/'result-identity.json',dict(job_ref=c.record_ref(root/'jobs/001.json'),raw_ref=raw_ref))
    c.write(advisor/'result-identity.json',dict(job_ref=c.record_ref(root/'jobs/002.json')))
    binding=dict(version=1,requester_step=1,advice_step=2,
        requester_job=c.record_ref(root/'jobs/001.json'),advice_job=c.record_ref(root/'jobs/002.json'),
        requester_seal=c.record_ref(record/'result-identity.json'),
        advice_seal=c.record_ref(advisor/'result-identity.json'),
        requester_native=c.record_ref(record/'native-intent.json'))
    job=dict(step=3,role='W',worker_continuity_version=1,worker_continuation=binding,
        direct_refs=[dict(path=f'inputs/parents/{step:03d}/conclusion.json') for step in (1,2)])
    settings=dict(native_backend='codex',native_launcher_argv=['codex'],
        native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False),
        resume_policy=dict(output_only=True,native_home=str(home)))
    return root,job,settings,session,state


def test_supported_backend_resumes_original_thread_with_validated_successor_workspace(tmp_path):
    root,job,settings,session,state=original(tmp_path)
    backend=backends.selected(settings)
    plan=continuation.consultation_launch(root,job,settings=settings,backend=backend,profile=PROFILE)
    assert plan['worker_continuation']['mode']=='resume_requested'
    assert plan['session']==session and plan['worker_context_state']==state
    work=c.workspace(root,3)
    args=backend.launch(PROFILE,work,root/'pending.json',root/'schema.json','Current W instructions')
    args,_=backend.resume(args,PROFILE,work,root/'pending.json',root/'schema.json',plan['session'])
    declaration=native_app_server.invocation(args)
    assert declaration['resumed']=='original-worker' and declaration['cwd']==str(work)
    assert declaration['model']==PROFILE['model']
    meter=backend.meter(Path(session['home']),work,session=plan['session'],context_state=state)
    observation=backend.snapshot_observation(meter)
    assert observation['context_windows']['windows_started']==3
    assert observation['baseline_tokens']==state['cursor']['terminal_tokens']
    assert Path(session['path']).read_bytes()==b'opaque original native transcript\n'


@pytest.mark.parametrize('change,reason',[
    ('protocol','consultation_resume_unsupported_backend'),
    ('home','consultation_resume_shared_home_unavailable'),
    ('profile','consultation_resume_profile_changed'),
    ('missing_session','consultation_resume_session_unavailable'),
    ('inherited','consultation_resume_inherited_source'),
    ('rotation','consultation_resume_context_rotation_required'),
])
def test_unsupported_or_unavailable_resume_explicitly_uses_new_context(tmp_path,change,reason):
    root,job,settings,session,state=original(tmp_path)
    profile=PROFILE
    if change=='protocol':settings=dict(settings,native_backend='protocol')
    if change=='home':settings=dict(settings,resume_policy={})
    if change=='profile':profile=dict(PROFILE,model='another-explicit-model')
    if change=='inherited':job['worker_continuation']['requester_job']=dict(path='/retained-original/jobs/001.json')
    if change=='rotation':
        path=root/'native-evidence/001/result-identity.json';seal=c.read(path)
        seal['control_outcome']=dict(stage_end_reason='worker_context_review_due');c.write(path,seal)
    if change=='missing_session':
        path=root/'native-evidence/001/native-outcome.json';outcome=c.read(path)
        outcome['native_identity'].pop('session');c.write(path,outcome)
    plan=continuation.consultation_launch(root,job,settings=settings,
        backend=backends.selected(settings),profile=profile)
    assert plan['context_mode']=='new_context' and 'session' not in plan
    assert plan['worker_continuation']==dict(version=1,requester_step=1,advice_step=2,
        mode='new_context',fallback_reason=reason)


@pytest.mark.parametrize('change',['thread','workspace','inode','cancelled'])
def test_changed_original_or_cancelled_source_does_not_launch_fresh_replay(tmp_path,change):
    root,job,settings,session,state=original(tmp_path)
    path=root/'native-evidence/001/native-outcome.json';outcome=c.read(path)
    if change=='cancelled':outcome['stop_reason']='cancelled'
    else:
        binding=outcome['native_identity']['session']
        if change=='thread':binding['thread_id']='foreign-worker'
        if change=='workspace':binding['workspace']=str(c.workspace(root,2))
        if change=='inode':binding['identity']=[0,0]
    c.write(path,outcome)
    with pytest.raises(ValueError):
        continuation.consultation_launch(root,job,settings=settings,
            backend=backends.selected(settings),profile=PROFILE)


@pytest.mark.parametrize('invalid',[None,'cancelled','raw_binding'])
def test_provider_change_retains_known_source_and_never_uses_target_session_validation(
        tmp_path,monkeypatch,invalid):
    root,job,settings,session,state=original(tmp_path)
    path=root/'native-evidence/001/native-outcome.json';outcome=c.read(path)
    outcome['native_backend']='protocol'
    if invalid=='cancelled':outcome['stop_reason']='cancelled'
    if invalid=='raw_binding':outcome['raw_ref']=dict(outcome['raw_ref'],size_bytes=999)
    c.write(path,outcome);retained=path.read_bytes()
    backend=backends.selected(settings)
    def forbidden(*args,**kwargs):raise AssertionError('Target provider reinterpreted the source original')
    monkeypatch.setattr(backends.Codex,'terminal_reconciliation_candidate',staticmethod(forbidden))
    monkeypatch.setattr(backend,'validate_resume_session',forbidden)
    if invalid:
        with pytest.raises(continuation.ResumeUnavailable):
            continuation.consultation_launch(root,job,settings=settings,backend=backend,profile=PROFILE)
    else:
        plan=continuation.consultation_launch(root,job,settings=settings,backend=backend,profile=PROFILE)
        assert plan['context_mode']=='new_context' and 'session' not in plan
        assert plan['worker_continuation']['fallback_reason']=='consultation_resume_backend_changed'
    assert path.read_bytes()==retained


def test_legal_provider_revision_binds_only_successor_and_uses_explicit_fresh_context(tmp_path,monkeypatch):
    from scidiscovery.research_loop import configuration_updates as updates, controller
    from test_history_handoff import finish
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain W evidence across ordinary advice.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],native_backend='protocol',
        max_tokens=10000,max_seconds=120,max_steps=8,search_policy=None,
        model=PROFILE['model'],reasoning_effort=PROFILE['reasoning_effort'],service_tier=PROFILE['service_tier'],
        experiment_mode='external')
    original_write=c.write
    def source_receipts(path,value):
        if Path(path)==root/'native-evidence/001/native-intent.json':
            value=dict(value,native_profile=PROFILE)
        if Path(path)==root/'native-evidence/001/native-outcome.json':
            value=dict(value,native_backend='protocol',terminal_observed=True,cleanup='observed_group_cleaned')
        if Path(path)==root/'native-evidence/002/science-output.json':
            value=dict(value,scientific_result='No material reasoning issue is identified in this synthetic advice; W judges its response.',
                decision_request=None)
        return original_write(path,value)
    with monkeypatch.context() as authored:
        authored.setattr(c,'write',source_receipts)
        first,_,_=finish(root,1,'W',[],stage_intent='handoff')
        second,_,_=finish(root,2,'A',[1],action='continue_w')
    originals=[root/'settings.json',root/'jobs/001.json',root/'jobs/002.json']
    originals.extend(root/'native-evidence'/f'{step:03d}'/name for step in (1,2)
        for name in ('native-intent.json','native-outcome.json','science-output.json','result-identity.json'))
    frozen={path:path.read_bytes() for path in originals}
    binary=tmp_path/'codex';binary.write_text('#!/bin/sh\nexit 99\n');binary.chmod(0o755)
    state=updates.status(root);document=state['configuration']
    document['agent'].update(backend='codex',launcher=[str(binary)])
    document['permissions']=dict(workspace='workspace-write',network=False,web='disabled',subagents=False)
    saved=updates.submit(root,document,expected_revision=state['revision'])
    revision,selected,_=updates.bind_new_job_facts(root,3,base=c.settings(root))
    assert saved['status']=='configuration_saved' and revision['revision']==1
    assert updates.for_job(root,first)['native_backend']=='protocol'
    assert updates.for_job(root,second)['native_backend']=='protocol'
    assert selected['native_backend']=='codex'
    assert backends.selected(selected).recovery_capability('consultation_resume')['status']=='supported'
    job=dict(step=3,role='W',worker_continuity_version=1,configuration_revision=revision,
        direct_refs=[dict(path=f'inputs/parents/{step:03d}/conclusion.json') for step in (1,2)])
    facts={step:dict(job=source,seal=c.read(root/'native-evidence'/f'{step:03d}'/'result-identity.json'),
        payload=c.read(root/'native-evidence'/f'{step:03d}'/'science-output.json'))
        for step,source in [(1,first),(2,second)]}
    job['worker_continuation']=continuation.consultation_binding(root,job,parents=facts)
    plan=continuation.consultation_launch(root,job,settings=selected,
        backend=backends.selected(selected),profile=PROFILE)
    assert plan['context_mode']=='new_context' and 'session' not in plan
    assert plan['worker_continuation']['fallback_reason']=='consultation_resume_backend_changed'
    assert all(path.read_bytes()==data for path,data in frozen.items())


@pytest.mark.parametrize('resumed,verified,expected',[
    (True,True,'same_session'),(True,False,'resume_unconfirmed'),(False,False,'resume_unconfirmed')])
def test_resume_request_is_not_reported_as_observed_continuity(resumed,verified,expected):
    planned=dict(version=1,requester_step=1,advice_step=2,mode='resume_requested',fallback_reason=None)
    assert continuation.consultation_outcome(planned,resumed=resumed,verified=verified)['mode']==expected
    assert planned['mode']=='resume_requested'


@pytest.mark.parametrize('purpose,role,action,expected',[
    ('worker_consultation','H','wait',True),('worker_consultation','A','wait',True),
    (None,'A','continue_w',True),(None,'A','finish',False),('closeout','A','continue_w',False)])
def test_only_ordinary_advice_binds_the_exact_requester(tmp_path,purpose,role,action,expected):
    root,job,settings,session,state=original(tmp_path)
    consultant=dict(step=2,role=role,purpose=purpose,
        direct_refs=[dict(path='inputs/parents/001/conclusion.json')])
    if purpose=='worker_consultation':consultant['evidence_return']=dict(role='W',step=1)
    facts={1:dict(job=dict(step=1,role='W'),seal=c.read(root/'native-evidence/001/result-identity.json')),
        2:dict(job=consultant,payload=dict(next_action=action),
            seal=c.read(root/'native-evidence/002/result-identity.json'))}
    bound=continuation.consultation_binding(root,job,parents=facts)
    assert (bound is not None)==expected
    if expected:assert bound==job['worker_continuation']
    assert continuation.consultation_binding(root,dict(job,worker_continuity_version=None),parents=facts) is None


def test_prompt_distinguishes_requested_resume_and_explicit_fallback(tmp_path):
    # Rendering is pure; no provider invocation or model defaults are needed.
    job=dict(role='W',handoff_protocol=c.HANDOFF_PROTOCOL,scientific_response_fields=[],
        native_role_instruction='Own the current W question.',research_strategy_version=3)
    for mode,text in [('resume_requested','Same W session requested'),('new_context','New native context')]:
        intent=dict(worker_continuation=dict(version=1,requester_step=1,advice_step=2,
            mode=mode,fallback_reason=None))
        prompt,_,_=native.render_scientific_request(job,{},intent,
            backend_name='protocol',instruction_transport='prompt')
        assert text in prompt


def test_runner_consumes_accepted_plan_without_reopening_source_originals(tmp_path,monkeypatch):
    root,job,settings,session,state=original(tmp_path)
    backend=backends.selected(settings)
    plan=continuation.consultation_launch(root,job,settings=settings,backend=backend,profile=PROFILE)
    def forbidden(*args,**kwargs):raise AssertionError('Consumer reopened immutable source originals')
    monkeypatch.setattr(c,'read',forbidden)
    monkeypatch.setattr(c,'regular',forbidden)
    monkeypatch.setattr(c,'record_ref',forbidden)
    consumed=continuation.consultation_intent(job,plan,settings=settings,backend=backend)
    assert consumed['session']==session and consumed['worker_context_state']==state


@pytest.mark.parametrize('final',[False,True])
def test_new_reflection_request_requires_complete_investment_binding(final):
    request=dict(version=1,cutoff=dict(total_rounds=0,source_step=None,round_id=None),
        due_reasons=[],budget_fraction=.3,automatic=not final,final=final,investment_cutoff=4,
        review_kind='final_assessment' if final else 'ordinary_reflection',
        reflection_scope='worker_question_clues_evidence')
    assert c.supervision_binding(request,step=2,research_strategy_version=3)==request
    incomplete=dict(request);incomplete.pop('investment_cutoff')
    with pytest.raises(ValueError):c.supervision_binding(incomplete,step=2,research_strategy_version=3)
    legacy={key:value for key,value in request.items()
        if key not in {'investment_cutoff','review_kind','reflection_scope'}}
    assert c.supervision_binding(legacy,step=2,research_strategy_version=3)==legacy


@pytest.mark.parametrize('backend_name',['codex','protocol'])
@pytest.mark.parametrize('mode',['resume_requested','new_context'])
@pytest.mark.parametrize('branch',['ordinary','fragment','repair'])
def test_actual_continuation_wire_contract_including_repair_allowance_fits(
        tmp_path,backend_name,mode,branch):
    from scidiscovery.research_loop import guidance
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    from test_result_schema import rendered_contract
    job=dict(role='W',step=3,handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,
        native_role_instruction=guidance.WORKER_FIRST['W'],formal_review_version=1,search={},
        worker_fragment_policy=dict(context='same_session'),
        scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode())))
    job['scientific_response_fields']=c.response_fields(job,{})
    job['role_purpose_instruction']=guidance.role_contract(job)
    task=c.response_instructions(job)
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent'],
        native_options={} if backend_name=='codex' else None),
        schema_transport='prompt' if backend_name=='codex' else None)
    intent=dict(worker_continuation=dict(version=1,requester_step=1,advice_step=2,
        mode=mode,fallback_reason=None if mode=='resume_requested' else 'consultation_resume_unsupported_backend'))
    remaining=None
    if branch=='fragment':intent['continuation_kind']='fragment';remaining={}
    if branch=='repair':
        intent['work_repair']=OutputRepairRequired('candidate_recommendation_missing','D'*512).request()
        remaining={}
    context=dict(remaining=remaining,
        budget_status=dict(shared_seconds_remaining=None,shared_observed_tokens_remaining=None),
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command'),
        managed_command=['experiment'])
    size,_,_,_=rendered_contract(tmp_path,job,task,intent,backend,**context)
    if branch=='repair':
        empty=dict(intent,work_repair=dict(intent['work_repair'],diagnostic=''))
        fixed,_,_,_=rendered_contract(tmp_path,job,task,empty,backend,**context)
        assert fixed+512<=c.MAX_FIXED_RESPONSE_BYTES,(backend_name,mode,branch,fixed)
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(backend_name,mode,branch,size)
