"""Small advisory projections retain soft targets, actual accounting and finite author time."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, budget_notice, contracts as c, controller, execution, native, protocol
from test_node_budget import instance, policy, target
from test_soft_node_budget import start_clock


def assert_a_notice_retains_advisory_scope(text):
    assert 'A: return advice with continue_w and decision_request=null.' in text
    assert 'W judges it; no direct work or finalization.' in text
    assert 'original permissions hold; unknown usage != 0.' in text
    assert 'conclude now' not in text


def assert_a_instruction_retains_advisory_scope(text):
    assert 'Return fallible advice with continue_w and decision_request=null' in text
    assert 'W judges the concern and chooses its response.' in text
    assert ('Shared exhaustion is handled by control admission and separate Final A; '
        'ordinary advice grants no new work or delivery.') in text
    assert 'without terminating this accepted node for a soft target' in text


@pytest.fixture
def advice(tmp_path, monkeypatch):
    def make(*, node=True, delivery=False, node_tokens=100, role='H',
            role_targets=None, thinking_target=None, max_tokens=1000, max_seconds=120,
            fragment_policy=None):
        public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Bounded scientific question.')
        root=tmp_path/'instance'
        reserve=(dict(closeout_a=dict(max_observed_tokens=10,max_seconds=1),inflight_tokens=5,
            cleanup_seconds=2,submit_seconds=0,confirm_seconds=0,adapter=None) if delivery else None)
        fragment_options=(dict(native_backend='codex',
            native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False),
            resume_policy=dict(
            max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=True,
            automatic_recovery=dict(enabled=False))) if fragment_policy is not None else {})
        launcher=['never-launch']
        if fragment_policy is not None:
            executable=tmp_path/'codex'
            executable.write_text('#!/bin/sh\nexit 97\n')
            executable.chmod(0o700)
            launcher=[str(executable)]
        controller.prepare(root,objective,public,launcher,max_tokens=max_tokens,max_seconds=max_seconds,max_steps=6,
            model='fixture',reasoning_effort='high',search_policy=None,node_budget_mode='soft',
            node_budget_policy=(role_targets or policy(tokens=node_tokens,seconds=30)) if node else None,delivery_policy=reserve,
            role_budget_policy=dict(version=1,min_w_share=.65,max_thinking_share=.35,
                thinking_node_target_tokens=thinking_target) if thinking_target is not None else None,
            worker_fragment_policy=fragment_policy,**fragment_options)
        job=c.build_job(root,1,role,[]);start_clock(root,'advisory fixture')
        anchor=budget.accept_node(root,job)
        intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),**(dict(budget_slice=anchor) if anchor else {}))
        accepted_budget.accept_native(root,1,execution.origin(root,1),intent)
        limits=budget.snapshot(root)
        if anchor:limits['advance_seconds']=anchor['advance_seconds']+5
        limits['seconds_remaining']=100.
        head=accepted_budget.read(root)
        shared=accepted_budget.usage_from_facts(head,
            job_rows=accepted_budget.load_usage_rows(head),current_native=dict(
                record=head['current_native']['record'],
                meter=dict(input_tokens=20,cached_input_tokens=19,output_tokens=5),
                metering=None,outcome_present=False,interrupted=False))
        shared.update(observed_tokens=25,accounted_tokens=25,unknown_steps=[])
        facts=dict(settings=c.settings(root),head=head,shared=shared,stamp=budget.clock_stamp(),
            task=intent['task_ref'],scope=protocol.stage_scope(root,job),
            final_return=None,closeout_preparation_seconds=None,
            native_attempt=dict(number=0,execution_key=intent['task_ref']))
        publications=[]
        original_publish=budget.publish_review
        def publish_review(*args,**kwargs):
            publications.append(True)
            return original_publish(*args,**kwargs)
        monkeypatch.setattr(budget,'publish_review',publish_review)
        for name in ('snapshot','node_admission','accept_node'):
            monkeypatch.setattr(budget,name,lambda *a,**kw:pytest.fail('Advice must use the supplied observation, never reenter admission'))
        path=c.workspace(root,1)/'work/budget-status.json'
        def publish(meter=None,*,observed=True,author_limits=None,context_observation=None):
            budget.write_node_hint(root,job,intent,limits,meter or dict(input_tokens=20,cached_input_tokens=19,output_tokens=5),
                facts=facts,observed=observed,author_limits=author_limits,context_observation=context_observation)
            assert path.stat().st_size<=4096
            return c.read(path)
        return root,job,intent,limits,shared,publications,publish
    return make


@pytest.mark.parametrize('charged,expected',[(100,900),(950,50)])
def test_soft_advice_uses_actual_shared_balance(advice,charged,expected):
    root,_,_,_,shared,published,publish=advice(delivery=True)
    frozen=c.settings(root)
    assert budget.delivery_reserve(frozen)==dict(tokens=0,seconds=0.,steps=0)
    settings=(root/'settings.json').read_bytes();shared.update(observed_tokens=charged,accounted_tokens=charged)
    hint=publish()
    assert hint['mode']=='soft' and hint['current_work_boundary']=='node_slice'
    assert hint['scope']==budget_notice.SCOPE
    assert hint['observed_tokens_lower_bound']==25  # Cached input is already included.
    assert hint['current_work_observed_tokens_remaining_upper_bound']==expected
    assert hint['current_work_active_seconds_available']==100
    assert hint['per_request_admission']=='unavailable' and hint['final_response_allowance']=='not_guaranteed'
    assert hint['inflight_usage_unknown'] and hint['reporting_delay_unmeasured']
    assert published==[True] and (root/'settings.json').read_bytes()==settings


def test_unreported_continuation_keeps_previous_consumption_and_unknown(advice):
    _,_,intent,_,shared,_,publish=advice()
    # This projection receives the prior consumption from an accepted anchor;
    # ownership/no-refund validation is independently covered by node tests.
    intent['budget_slice']['previous_tokens']=30
    shared.update(observed_tokens=30,accounted_tokens=30,unknown_steps=[1])
    hint=publish(dict(input_tokens=0,cached_input_tokens=0,output_tokens=0),observed=False)
    assert hint['observed_tokens_lower_bound'] == 30 and hint['unknown_usage'] is True
    assert hint['metering']=='inflight_unknown'
    assert hint['current_work_observed_tokens_remaining_upper_bound']==970
    assert hint['shared_observed_tokens_remaining_upper_bound']==970
    assert hint['shared_remaining_basis']=='configured_ceiling_minus_reported_usage'


def test_soft_target_remains_advisory_and_shared_balance_is_not_a_fresh_grant(advice):
    root,_,_,_,shared,published,publish=advice()
    frozen=c.settings(root)
    assert budget.delivery_reserve(frozen)==dict(tokens=0,seconds=0.,steps=0)
    shared.update(observed_tokens=250,accounted_tokens=250)
    hint=publish(dict(input_tokens=200,cached_input_tokens=190,output_tokens=50))
    assert hint['mode']=='soft' and hint['target_state']=='reached'
    assert hint['current_work_observed_tokens_remaining_upper_bound']==750
    assert published==[True]


def test_a_node_target_with_r15_shared_balance_does_not_end_research(advice):
    root,job,intent,limits,shared,_,publish=advice(role='A',node_tokens=300_000,
        max_tokens=5_000_000,max_seconds=10_800)
    shared.update(observed_tokens=3_145_974,accounted_tokens=3_145_974)
    limits['seconds_remaining']=9341.
    meter=dict(input_tokens=300_000,cached_input_tokens=0,output_tokens=1)
    hint=publish(meter)
    assert hint['target_state']=='reached'
    assert hint['shared_observed_tokens_remaining_upper_bound']==1_854_026
    assert hint['shared_active_seconds_remaining']==9341.
    assert set(hint['closeout_notices'])=={'token'}
    assert hint['closeout_notices']['token']['cause']=='node'
    assert_a_notice_retains_advisory_scope(hint['closeout_notices']['token']['text'])
    assert_a_instruction_retains_advisory_scope(hint['instruction'])
    assert budget.stop_node(root,job,intent,limits,meter) is None


def test_a_shared_token_exhaustion_preserves_advice_and_separate_final_authority(advice):
    _,_,_,_,shared,_,publish=advice(role='A')
    shared.update(observed_tokens=1000,accounted_tokens=1000)
    hint=publish()
    assert hint['shared_observed_tokens_remaining_upper_bound']==0
    assert hint['current_work_observed_tokens_remaining_upper_bound']==0
    assert hint['reserve_state']=='reached'
    text=hint['closeout_notices']['token']['text']
    assert hint['closeout_notices']['token']['cause']=='shared'
    assert_a_notice_retains_advisory_scope(text)
    assert_a_instruction_retains_advisory_scope(hint['instruction'])


def test_author_advice_uses_verified_attempt_allowance_not_reused_w_cap(advice):
    root,_,intent,_,shared,_,publish=advice(node_tokens=200)
    frozen=c.settings(root)
    assert budget.delivery_reserve(frozen)==dict(tokens=0,seconds=0.,steps=0)
    assert intent['budget_slice']['limits']['max_observed_tokens']==200
    intent.update(continuation_kind='author_closeout')
    intent['budget_slice'].update(previous_tokens=123,previous_seconds=2.)
    shared.update(observed_tokens=133,accounted_tokens=133,unknown_steps=[1])
    hint=publish(dict(input_tokens=7,cached_input_tokens=6,output_tokens=3),
        author_limits=dict(max_observed_tokens=20,max_seconds=8))
    assert hint['current_work_boundary']=='author_permit'
    assert hint['targets']==dict(max_observed_tokens=20,max_seconds=8)
    assert hint['observed_tokens_lower_bound']==10 and hint['active_seconds']==5
    assert hint['shared_observed_tokens_remaining_upper_bound']==867  # All reported prior usage remains consumed.
    assert hint['current_work_observed_tokens_remaining_upper_bound']==867
    assert hint['current_work_active_seconds_available']==3


def test_missing_author_allowance_is_unavailable_and_never_shows_original_w_cap(advice):
    _,_,intent,_,_,_,publish=advice()
    intent.update(continuation_kind='author_closeout')
    hint=publish()
    assert hint['current_work_boundary']=='unavailable' and hint['targets'] is None
    assert hint['current_work_observed_tokens_remaining_upper_bound'] is None
    assert hint['current_work_active_seconds_available'] is None


def test_author_balance_preserves_actual_shared_usage_past_soft_targets(advice):
    _,_,intent,_,shared,_,publish=advice()
    intent.update(continuation_kind='author_closeout')
    intent['budget_slice'].update(previous_tokens=95,previous_seconds=2.)
    shared.update(observed_tokens=105,accounted_tokens=105)
    hint=publish(dict(input_tokens=7,cached_input_tokens=6,output_tokens=3),
        author_limits=dict(max_observed_tokens=20,max_seconds=8))
    assert hint['current_work_observed_tokens_remaining_upper_bound']==895
    assert hint['target_state']=='within' and hint['targets']['max_observed_tokens']==20


def test_no_node_policy_does_not_invent_a_node_allowance(advice):
    _,_,_,_,_,_,publish=advice(node=False)
    hint=publish()
    assert hint['targets'] is None and hint['current_work_boundary']=='shared_only'
    assert hint['current_work_observed_tokens_remaining_upper_bound']==975


def test_one_large_report_reaches_soft_target_without_claiming_pre_request_cap(advice):
    root,job,intent,limits,shared,_,publish=advice()
    meter=dict(input_tokens=500,cached_input_tokens=490,output_tokens=5)
    shared.update(observed_tokens=505,accounted_tokens=505)
    hint=publish(meter)
    assert hint['target_state']=='reached' and hint['current_work_observed_tokens_remaining_upper_bound']==495
    assert hint['per_request_admission']=='unavailable'
    assert budget.stop_node(root,job,intent,limits,meter) is None


def test_current_publisher_adds_stable_notice_from_loaded_budget_risk(advice):
    _,_,_,_,shared,_,publish=advice()
    assert 'closeout_notices' not in publish(observed=False)
    target=publish(dict(input_tokens=100,cached_input_tokens=90,output_tokens=0))
    assert set(target['closeout_notices'])=={'token'}
    assert target['closeout_notices']['token']['cause']=='node'
    assert 'suggested W+A token targets' in target['closeout_notices']['token']['text']
    assert 'No reservation/grant' in target['closeout_notices']['token']['text']
    repeated=publish(dict(input_tokens=101,cached_input_tokens=90,output_tokens=0))
    assert repeated['closeout_notices']==target['closeout_notices']
    shared.update(observed_tokens=1000,accounted_tokens=1000,unknown_steps=[1])
    exhausted=publish(observed=False)
    assert exhausted['closeout_notices']['token']['cause']=='shared'
    assert 'Wrap up' in exhausted['closeout_notices']['token']['text']
    assert exhausted['unknown_usage'] is True
    assert exhausted['notification_channel']==dict(status='unavailable',reason='not_preflighted')


@pytest.mark.parametrize('tokens,state,alert',[(79,'within',False),(80,'near',True),(99,'near',True),(100,'reached',True)])
def test_node_risk_starts_at_eighty_percent_without_changing_soft_targets(advice,tokens,state,alert):
    root,job,intent,limits,_,_,publish=advice(role='E')
    meter=dict(input_tokens=tokens,output_tokens=0,cached_input_tokens=0)
    hint=publish(meter)
    assert hint['target_state']==state and hint['targets']==dict(max_observed_tokens=100,max_seconds=30)
    assert ('closeout_notices' in hint) is alert
    if alert:
        assert set(hint['closeout_notices'])=={'token'}
        assert hint['closeout_notices']['token']['cause']=='node' and 'Soft target' in hint['closeout_notices']['token']['text']
        assert budget_notice.published_notices(hint)==hint['closeout_notices']
    assert budget.stop_node(root,job,intent,limits,meter) is None


@pytest.mark.parametrize('elapsed,alert',[(23.99,False),(24.,True)])
def test_node_time_risk_uses_the_existing_eighty_percent_target(advice,elapsed,alert):
    _,_,intent,limits,_,_,publish=advice(role='E')
    limits['advance_seconds']=intent['budget_slice']['advance_seconds']+elapsed
    hint=publish()
    assert ('closeout_notices' in hint) is alert
    if alert:assert set(hint['closeout_notices'])=={'time'}
    assert hint['targets']==dict(max_observed_tokens=100,max_seconds=30)


@pytest.mark.parametrize('role,threshold,expected',[('H',340,{'W':300,'A':40})])
def test_followup_risk_uses_existing_role_suggestions_without_reserving_them(advice,role,threshold,expected):
    targets={key:dict(max_observed_tokens=value,max_seconds=30) for key,value in dict(H=100,W=300,A=200,E=100).items()}
    root,_,_,_,shared,_,publish=advice(role=role,role_targets=targets,thinking_target=40)
    frozen=(root/'settings.json').read_bytes()
    shared.update(observed_tokens=1000-threshold-1,accounted_tokens=1000-threshold-1)
    before=publish()
    assert before['target_state']=='within' and 'closeout_notices' not in before
    shared.update(observed_tokens=1000-threshold,accounted_tokens=1000-threshold)
    hint=publish()
    assert hint['target_state']=='within' and hint['closeout_notices']['token']['cause']=='shared'
    assert set(hint['closeout_notices'])=={'token'}
    assert hint['shared_observed_tokens_remaining_upper_bound']==threshold
    assert hint['current_work_observed_tokens_remaining_upper_bound']==threshold
    assert 'No reservation/grant' in hint['closeout_notices']['token']['text']
    for next_role,tokens in expected.items():assert f'{next_role}={tokens}' in hint['instruction']
    assert budget_notice.published_notices(hint)==hint['closeout_notices']
    assert (root/'settings.json').read_bytes()==frozen


@pytest.mark.parametrize('shared_seconds',[100.,11.999])
def test_ordinary_worker_ignores_legacy_soft_targets_and_shared_time_risk(advice,shared_seconds):
    root,_,intent,limits,shared,_,publish=advice(role='W')
    original=json.dumps(intent,sort_keys=True)
    limits['advance_seconds']=intent['budget_slice']['advance_seconds']+3000
    limits['seconds_remaining']=shared_seconds
    shared.update(observed_tokens=885,accounted_tokens=885)
    hint=publish(dict(input_tokens=200,cached_input_tokens=0,output_tokens=1))
    assert hint['targets'] is None and hint['target_kind']=='context_window_only'
    assert hint['current_work_boundary']=='shared_only' and hint['target_state']=='within'
    assert 'closeout_notices' not in hint
    assert 'suggested' not in hint['instruction'] and 'time target' not in hint['instruction']
    review=c.read(root/'host-runtime/experiment-control/001/review.json')
    assert review['pending'] is False and review['reason'] is None
    assert review['targets'] is None
    assert json.dumps(intent,sort_keys=True)==original  # Immutable accounting anchor remains intact.


@pytest.mark.parametrize('charged,alert',[(889,False),(899,False),(900,True),(1000,True)])
def test_ordinary_worker_global_token_reminder_uses_cumulative_configured_total(advice,charged,alert):
    root,_,_,_,shared,_,publish=advice(role='W')
    shared.update(observed_tokens=charged,accounted_tokens=charged)
    hint=publish(dict(input_tokens=200,cached_input_tokens=190,output_tokens=1))
    assert hint['targets'] is None and hint['target_state']=='within'
    assert hint['shared_observed_tokens_remaining_upper_bound']==1000-charged
    assert ('closeout_notices' in hint) is alert
    if alert:
        notice=hint['closeout_notices']['token']
        assert notice['cause']=='shared'
        assert set(hint['closeout_notices'])=={'token'}
        assert ('Shared token budget reached.' if charged==1000 else
            'cumulative tokens >=90% of configured total') in notice['text']
        assert 'suggested' not in notice['text']
        assert budget_notice.published_notices(hint)==hint['closeout_notices']
    review=c.read(root/'host-runtime/experiment-control/001/review.json')
    assert review['pending'] is (charged==1000)


def test_ordinary_worker_global_token_reminder_keeps_unknown_usage_as_lower_bound(advice):
    _,_,_,_,shared,_,publish=advice(role='W')
    shared.update(observed_tokens=900,accounted_tokens=900,unknown_steps=[1])
    hint=publish(dict(input_tokens=0,cached_input_tokens=0,output_tokens=0),observed=False)
    assert hint['shared_observed_tokens_remaining_upper_bound']==100
    assert hint['unknown_usage'] is True
    assert hint['closeout_notices']['token']['cause']=='shared'
    assert 'unknown usage != 0' in hint['closeout_notices']['token']['text']


def test_ordinary_worker_unlimited_global_tokens_have_no_near_total_notice(advice):
    _,_,_,_,shared,_,publish=advice(role='W',max_tokens=None)
    shared.update(observed_tokens=10000,accounted_tokens=10000)
    hint=publish(dict(input_tokens=200,cached_input_tokens=0,output_tokens=1))
    assert hint['shared_observed_tokens_remaining_upper_bound'] is None
    assert 'closeout_notices' not in hint


def test_ordinary_worker_shared_exhaustion_remains_pending(advice):
    root,_,_,_,shared,_,publish=advice(role='W')
    shared.update(observed_tokens=1000,accounted_tokens=1000)
    hint=publish()
    assert hint['closeout_notices']['token']['cause']=='shared'
    review=c.read(root/'host-runtime/experiment-control/001/review.json')
    assert review['pending'] is True and review['reason']=='reserve_reached'


@pytest.mark.parametrize('started,occupancy,state,reason',[
    (1,25,'within',None),(1,90,'near',None),
    (2,70,'within',None),(2,71,'reached','worker_context_review_due')])
def test_ordinary_worker_hint_uses_context_occupancy_after_legacy_targets(advice,started,occupancy,state,reason):
    root,_,intent,limits,_,_,publish=advice(role='W',fragment_policy=dict(
        context='same_session',max_context_windows=1,context_handoff_version=2))
    original=json.dumps(intent,sort_keys=True)
    limits['advance_seconds']=intent['budget_slice']['advance_seconds']+3000
    observation=dict(context_windows=dict(coverage='live',windows_started=started),
        context=dict(status='last_reported',total_tokens=occupancy,model_context_window=100,
            current_window=True,observed_at=123.))
    hint=publish(dict(input_tokens=200,cached_input_tokens=0,output_tokens=1),
        context_observation=observation)
    assert hint['targets'] is None and hint['target_kind']=='context_window_only'
    assert hint['target_state']==state
    assert hint['observed_tokens_lower_bound']==201 and hint['active_seconds']==3000
    assert set(hint.get('closeout_notices',{}))==({'progress'} if state!='within' else set())
    if state!='within':
        assert hint['closeout_notices']['progress']['text']==(
            budget_notice.WORKER_CONTEXT_90_TEXT if state=='near' else budget_notice.WORKER_CONTEXT_TEXT)
        assert budget_notice.published_notices(hint)==hint['closeout_notices']
    review=c.read(root/'host-runtime/experiment-control/001/review.json')
    assert review['pending'] is (reason is not None) and review['reason']==reason
    assert review['targets'] is None and json.dumps(intent,sort_keys=True)==original


@pytest.mark.parametrize('seconds,alert',[(12.,False),(11.999,True),(0.,True),(-1.,True)])
def test_shared_time_risk_is_strictly_below_ten_percent(advice,seconds,alert):
    _,_,_,limits,_,_,publish=advice(role='A')
    limits['seconds_remaining']=seconds
    hint=publish()
    assert hint['target_state']=='within' and hint['shared_active_seconds_remaining']==seconds
    assert hint['current_work_active_seconds_available']==max(0.,seconds)
    assert hint['reserve_state']==('reached' if seconds<=0 else 'within')
    assert ('closeout_notices' in hint) is alert
    assert_a_instruction_retains_advisory_scope(hint['instruction'])
    if alert:
        text=hint['closeout_notices']['time']['text']
        assert set(hint['closeout_notices'])=={'time'}
        assert hint['closeout_notices']['time']['cause']=='shared'
        assert_a_notice_retains_advisory_scope(text)
        assert budget_notice.published_notices(hint)==hint['closeout_notices']


@pytest.mark.parametrize('first_dimension',['token','time','both'])
def test_producer_keeps_token_and_time_risks_independent_through_resume(advice,first_dimension):
    root,_,_,limits,_,_,publish=advice(role='A')
    if first_dimension in {'time','both'}:limits['seconds_remaining']=11.
    meter=dict(input_tokens=80 if first_dimension in {'token','both'} else 25,cached_input_tokens=0,output_tokens=0)
    first=publish(meter)
    expected={'time','token'} if first_dimension=='both' else {first_dimension}
    assert set(first['closeout_notices'])==expected
    selected='time' if first_dimension=='both' else first_dimension
    work=c.workspace(root,1)
    assert budget_notice.hook_output(work,0)['hookSpecificOutput']['additionalContext']==first['closeout_notices'][selected]['text']
    if first_dimension!='both':assert budget_notice.hook_output(work,1) is None
    limits['seconds_remaining']=0.
    both=publish(dict(input_tokens=100,cached_input_tokens=0,output_tokens=0))
    assert set(both['closeout_notices'])=={'time','token'}
    remaining='token' if selected=='time' else 'time'
    output=budget_notice.hook_output(work,2)['hookSpecificOutput']['additionalContext']
    assert output==both['closeout_notices'][remaining]['text']
    assert_a_notice_retains_advisory_scope(output)
    for attempt in (0,1,2,999):assert budget_notice.hook_output(work,attempt) is None


@pytest.mark.parametrize('first_dimension',['time','token'])
def test_other_dimension_exhaustion_does_not_retrigger_advice_or_change_its_scope(advice,first_dimension):
    root,_,intent,limits,shared,_,publish=advice(role='A')
    meter=dict(input_tokens=25,cached_input_tokens=0,output_tokens=0)
    if first_dimension=='time':limits['seconds_remaining']=11.
    else:meter['input_tokens']=80
    first=publish(meter)
    assert set(first['closeout_notices'])=={first_dimension}
    work=c.workspace(root,1)
    assert_a_notice_retains_advisory_scope(budget_notice.hook_output(work,0)['hookSpecificOutput']['additionalContext'])

    if first_dimension=='time':limits['seconds_remaining']=0.
    else:shared.update(observed_tokens=1000,accounted_tokens=1000)
    exhausted=publish(meter)
    assert set(exhausted['closeout_notices'])=={first_dimension}
    assert exhausted['closeout_notices'][first_dimension]['cause']=='shared'
    assert budget_notice.hook_output(work,1) is None

    # Exhaustion in one dimension cannot invent a trigger in the other.
    # Its later node risk still returns advice within the same frozen A scope.
    if first_dimension=='time':meter['input_tokens']=80
    else:limits['advance_seconds']=intent['budget_slice']['advance_seconds']+24.
    both=publish(meter)
    remaining='token' if first_dimension=='time' else 'time'
    assert set(both['closeout_notices'])=={'time','token'}
    assert both['closeout_notices'][remaining]['cause']=='node'
    text=budget_notice.hook_output(work,2)['hookSpecificOutput']['additionalContext']
    assert text==both['closeout_notices'][remaining]['text']
    assert_a_notice_retains_advisory_scope(text)
    assert budget_notice.hook_output(work,3) is None


def test_republishing_lower_shared_balance_does_not_reset_consumption(advice):
    _,_,_,_,shared,_,publish=advice()
    shared.update(observed_tokens=900,accounted_tokens=900)
    first=publish(dict(input_tokens=80,cached_input_tokens=70,output_tokens=0))
    shared.update(observed_tokens=950,accounted_tokens=950)
    second=publish(dict(input_tokens=5,cached_input_tokens=0,output_tokens=0))
    assert first['shared_observed_tokens_remaining_upper_bound']==100
    assert second['shared_observed_tokens_remaining_upper_bound']==50
    assert second['current_work_observed_tokens_remaining_upper_bound']==50
    assert second['observed_tokens_lower_bound']==5  # Local reset is no shared refund.
    assert second['per_request_admission']=='unavailable'


def test_negative_node_time_is_rejected_without_replacing_the_last_hint(advice):
    root,_,intent,limits,_,_,publish=advice()
    publish()
    path=c.workspace(root,1)/'work/budget-status.json'
    original=path.read_bytes()
    limits['advance_seconds']=intent['budget_slice']['advance_seconds']-1
    with pytest.raises(ValueError,match='Node consumption cannot move backwards'):
        publish()
    assert path.read_bytes()==original


@pytest.mark.process_e2e
@pytest.mark.parametrize('role','HWAE')
def test_soft_native_stdin_has_budget_advice_and_prelaunch_hint(tmp_path,role):
    root=instance(tmp_path,limits=policy())
    launcher=tmp_path/'native.py'
    code=launcher.read_text().replace('args=sys.argv[1:];sys.stdin.read()',
        'args=sys.argv[1:];prompt=sys.stdin.read()')
    code=code.replace("task=json.loads((work/'science-task.json').read_text())",
        "(work/'work/prompt.txt').write_text(prompt)\n"
        "hint=json.loads((work/'work/budget-status.json').read_text())\n"
        "(work/'work/prelaunch-budget-hint.json').write_text(json.dumps(hint))\n"
        "assert hint['mode']=='soft' and hint['inflight_usage_unknown'] is True\n"
        "task=json.loads((work/'science-task.json').read_text())")
    # This transport fixture needs a complete W finding and ordinary A advice.
    code=code.replace("stage_intent='review'", "stage_intent='decision'")
    code=code.replace("next_action='finish'", "next_action='continue_w'")
    launcher.write_text(code)
    step=target(root,role);c.build_job(root,step,role,[1] if step==2 else [])
    previous=native.usage(root)
    reserve=budget.delivery_reserve(c.settings(root))
    start_clock(root,'soft native stdin fixture');native.execute_native(root,step)
    prompt=(c.workspace(root,step)/'work/prompt.txt').read_text()
    budgets=[json.JSONDecoder().raw_decode(line)[0] for line in prompt.splitlines() if line.startswith('{')]
    shared=next(value for value in budgets if 'shared_observed_tokens_remaining' in value)
    ordinary=shared['exploration']
    local=c.read(c.workspace(root,step)/'work/prelaunch-budget-hint.json')
    assert ordinary['observed_tokens_remaining_upper_bound']==100_000-previous['accounted_tokens']-reserve['tokens']
    assert 0<ordinary['active_seconds_available']<=120-reserve['seconds']
    assert shared['shared_observed_tokens_remaining']==ordinary['observed_tokens_remaining_upper_bound']
    if role=='W':
        assert 'node_budget' not in shared
        assert local['targets'] is None and local['target_kind']=='context_window_only'
    else:
        assert shared['node_budget']==c.read(execution.origin(root,step)/'native-intent.json')['budget_slice']['limits']
    assert local['shared_observed_tokens_remaining_upper_bound']==ordinary['observed_tokens_remaining_upper_bound']
    assert local['per_request_admission']=='unavailable' and local['final_response_allowance']=='not_guaranteed'
    assert local['inflight_usage_unknown'] and local['reporting_delay_unmeasured']
    assert ordinary['token_usage_unknown']  # This current native task has no outcome or usage before submission.
    assert 'unknown_usage' not in ordinary
    for text in ('limits/permissions bind.','work/budget-status.json','input has cache',
            'unknown!=0','no grant'):
        assert text in prompt
    if role=='H':
        assert 'Follow-up targets advise; no grant/required handoff.' in prompt
    if role=='W':
        assert 'Targets soft' not in prompt and 'Follow-up targets' not in prompt
    if role=='A':
        assert ('Complete the requested judgment; follow role_contract. Soft targets do not end research. '
            'Shared tokens<=0 or time<=0: retain originals/gaps; final-return admission and permissions hold.') in prompt
    assert c.read(execution.record(root,step)/'native-outcome.json')['stop_reason'] is None


def test_ordinary_worker_prompt_does_not_describe_node_soft_targets(advice):
    root,job,intent,_,_,_,_=advice(role='W')
    prompt,_,_=native.render_scientific_request(job,c.read(c.workspace(root,1)/'science-task.json'),intent,
        backend_name='codex',instruction_transport='developer')
    assert 'Targets soft' not in prompt and 'Follow-up targets' not in prompt
    assert 'limits/permissions bind' in prompt
