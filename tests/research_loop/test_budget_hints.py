"""Small advisory projections retain soft targets, actual accounting and finite author time."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, budget_notice, contracts as c, controller, execution, native, protocol
from test_node_budget import instance, policy, target
from test_soft_node_budget import start_clock


def assert_a_risk_keeps_evidence_based_routing(text):
    assert 'end this assessment' in text and 'retain unmet goals' in text
    assert 'normal next_action' in text and 'evidence/shared balance' in text
    assert 'Soft targets do not end research' in text
    assert 'deliver' not in text and 'finish with gaps' not in text
    assert 'No H/W/E' not in text and 'conclude now' not in text


def assert_a_instruction_scopes_global_closeout(text):
    assert ('Finish the current assessment by evidence/shared balance; node targets do not end research. '
        'Existing productive work may continue with continue_w and decision_request=null.') in text
    assert ('At exhausted shared budgets, summarize existing results and gaps and use finish/deliver '
        'to request the protected final A. Ordinary A does not authorize adapter submission; '
        'no new H/W/E is authorized at exhaustion.') in text
    assert 'without terminating this accepted node for a soft target' in text


def assert_a_exhaustion_requires_protected_final(text):
    assert ('A: conclude now; deliver/finish requests protected Final A. '
        'No new H/W/E; permissions hold; unknown usage != 0.') in text
    assert 'eligible existing candidate' not in text and 'No H/W/E/extra FINAL' not in text


@pytest.fixture
def advice(tmp_path, monkeypatch):
    def make(*, mode='soft', node=True, delivery=False, node_tokens=100, role='H',
            role_targets=None, thinking_target=None, max_tokens=1000, max_seconds=120, strategy=3):
        public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Bounded scientific question.')
        root=tmp_path/'instance'
        reserve=(dict(closeout_a=dict(max_observed_tokens=10,max_seconds=1),inflight_tokens=5,
            cleanup_seconds=2,submit_seconds=0,confirm_seconds=0,adapter=None) if delivery else None)
        controller.prepare(root,objective,public,['never-launch'],max_tokens=max_tokens,max_seconds=max_seconds,max_steps=6,
            model='fixture',reasoning_effort='high',search_policy=None,node_budget_mode=mode,
            research_strategy_version=strategy,
            node_budget_policy=(role_targets or policy(tokens=node_tokens,seconds=30)) if node else None,delivery_policy=reserve,
            role_budget_policy=dict(version=1,min_w_share=.65,max_thinking_share=.35,
                thinking_node_target_tokens=thinking_target) if thinking_target is not None else None)
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
        def publish(meter=None,*,observed=True,author_limits=None):
            budget.write_node_hint(root,job,intent,limits,meter or dict(input_tokens=20,cached_input_tokens=19,output_tokens=5),
                facts=facts,observed=observed,author_limits=author_limits)
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
    root,_,_,_,shared,published,publish=advice(mode='soft')
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
    assert_a_risk_keeps_evidence_based_routing(hint['closeout_notices']['token']['text'])
    assert_a_instruction_scopes_global_closeout(hint['instruction'])
    assert budget.stop_node(root,job,intent,limits,meter) is None


def test_a_shared_token_exhaustion_requires_immediate_conclusion(advice):
    _,_,_,_,shared,_,publish=advice(role='A')
    shared.update(observed_tokens=1000,accounted_tokens=1000)
    hint=publish()
    assert hint['shared_observed_tokens_remaining_upper_bound']==0
    assert hint['current_work_observed_tokens_remaining_upper_bound']==0
    assert hint['reserve_state']=='reached'
    text=hint['closeout_notices']['token']['text']
    assert hint['closeout_notices']['token']['cause']=='shared'
    assert 'Shared token budget reached' in text and 'conclude now' in text
    assert_a_exhaustion_requires_protected_final(text)


@pytest.mark.parametrize('mode',['soft'])
def test_author_advice_uses_verified_attempt_allowance_not_reused_w_cap(advice,mode):
    root,_,intent,_,shared,_,publish=advice(mode=mode,node_tokens=200)
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


@pytest.mark.parametrize('mode,prior,expected,state',[('soft',85,905,'within'),('soft',95,895,'within')])
def test_author_balance_preserves_actual_shared_usage_past_soft_targets(advice,mode,prior,expected,state):
    _,_,intent,_,shared,_,publish=advice(mode=mode)
    intent.update(continuation_kind='author_closeout')
    intent['budget_slice'].update(previous_tokens=prior,previous_seconds=2.)
    shared.update(observed_tokens=prior+10,accounted_tokens=prior+10)
    hint=publish(dict(input_tokens=7,cached_input_tokens=6,output_tokens=3),
        author_limits=dict(max_observed_tokens=20,max_seconds=8))
    assert hint['current_work_observed_tokens_remaining_upper_bound']==expected
    assert hint['target_state']==state and hint['targets']['max_observed_tokens']==20


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


@pytest.mark.parametrize('role,threshold,expected',[('H',340,{'W':300,'A':40}),('W',40,{'A':40})])
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


@pytest.mark.parametrize('seconds,alert',[(12.,False),(11.999,True),(0.,True),(-1.,True)])
def test_shared_time_risk_is_strictly_below_ten_percent(advice,seconds,alert):
    _,_,_,limits,_,_,publish=advice(role='A')
    limits['seconds_remaining']=seconds
    hint=publish()
    assert hint['target_state']=='within' and hint['shared_active_seconds_remaining']==seconds
    assert hint['current_work_active_seconds_available']==max(0.,seconds)
    assert hint['reserve_state']==('reached' if seconds<=0 else 'within')
    assert ('closeout_notices' in hint) is alert
    assert_a_instruction_scopes_global_closeout(hint['instruction'])
    if alert:
        text=hint['closeout_notices']['time']['text']
        assert set(hint['closeout_notices'])=={'time'}
        assert hint['closeout_notices']['time']['cause']=='shared'
        if seconds<=0:
            assert_a_exhaustion_requires_protected_final(text)
        else:
            assert_a_risk_keeps_evidence_based_routing(text)
        assert ('Shared time expired' in text) is (seconds<=0)
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
    if remaining=='time':assert 'Shared time expired' in output
    for attempt in (0,1,2,999):assert budget_notice.hook_output(work,attempt) is None


def test_later_token_notice_concludes_when_shared_time_expired_after_its_notice(advice):
    root,_,_,limits,_,_,publish=advice(role='A')
    limits['seconds_remaining']=11.
    first=publish()
    assert set(first['closeout_notices'])=={'time'}
    work=c.workspace(root,1)
    assert_a_risk_keeps_evidence_based_routing(budget_notice.hook_output(work,0)['hookSpecificOutput']['additionalContext'])
    limits['seconds_remaining']=0.
    expired=publish()
    assert set(expired['closeout_notices'])=={'time'}  # Time expiry does not invent a token-risk trigger.
    assert budget_notice.hook_output(work,1) is None
    both=publish(dict(input_tokens=80,cached_input_tokens=0,output_tokens=0))
    assert set(both['closeout_notices'])=={'time','token'}
    text=budget_notice.hook_output(work,2)['hookSpecificOutput']['additionalContext']
    assert text==both['closeout_notices']['token']['text'] and both['closeout_notices']['token']['cause']=='shared'
    assert 'Token budget risk' in text and 'Shared budget exhausted' in text
    assert 'Shared token budget reached' not in text
    assert_a_exhaustion_requires_protected_final(text)
    assert budget_notice.hook_output(work,3) is None


def test_later_time_notice_concludes_when_shared_tokens_exhausted_after_their_notice(advice):
    root,_,intent,limits,shared,_,publish=advice(role='A')
    meter=dict(input_tokens=80,cached_input_tokens=0,output_tokens=0)
    first=publish(meter)
    assert set(first['closeout_notices'])=={'token'}
    work=c.workspace(root,1)
    assert_a_risk_keeps_evidence_based_routing(budget_notice.hook_output(work,0)['hookSpecificOutput']['additionalContext'])
    shared.update(observed_tokens=1000,accounted_tokens=1000)
    exhausted=publish(meter)
    assert set(exhausted['closeout_notices'])=={'token'}  # Token exhaustion does not invent a time-risk trigger.
    assert budget_notice.hook_output(work,1) is None
    limits['advance_seconds']=intent['budget_slice']['advance_seconds']+24.
    both=publish(meter)
    assert set(both['closeout_notices'])=={'time','token'}
    text=budget_notice.hook_output(work,2)['hookSpecificOutput']['additionalContext']
    assert text==both['closeout_notices']['time']['text'] and both['closeout_notices']['time']['cause']=='shared'
    assert 'Time budget risk' in text and 'Shared budget exhausted' in text
    assert 'Shared time expired' not in text and 'Shared time budget reached' not in text
    assert_a_exhaustion_requires_protected_final(text)
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


def test_explicit_legacy_a_exhaustion_retains_direct_candidate_or_gaps_contract(advice):
    _,job,_,_,shared,_,publish=advice(role='A',strategy=None)
    assert job.get('research_strategy_version') is None
    shared.update(observed_tokens=1000,accounted_tokens=1000)
    hint=publish()
    assert hint['closeout_notices']['token']['cause']=='shared'
    assert hint['closeout_notices']['token']['text']==(
        'Shared token budget reached. A: conclude now; deliver an eligible existing candidate or finish with gaps. '
        'No H/W/E/extra FINAL; permissions hold; unknown usage != 0.')
    assert ('If shared tokens remaining<=0 or shared time remaining<=0, ordinary A must conclude now: '
        'summarize existing results and deliver an eligible existing candidate or finish with gaps, '
        'with no new H/W/E or extra FINAL; permissions hold.') in hint['instruction']


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
    launcher.write_text(code)
    step=target(root,role);job=c.build_job(root,step,role,[1] if step==2 else [])
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
    assert shared['node_budget']==c.read(execution.origin(root,step)/'native-intent.json')['budget_slice']['limits']
    assert local['shared_observed_tokens_remaining_upper_bound']==ordinary['observed_tokens_remaining_upper_bound']
    assert local['per_request_admission']=='unavailable' and local['final_response_allowance']=='not_guaranteed'
    assert local['inflight_usage_unknown'] and local['reporting_delay_unmeasured']
    assert ordinary['token_usage_unknown']  # This current native task has no outcome or usage before submission.
    assert 'unknown_usage' not in ordinary
    for text in ('Targets soft; limits/permissions bind.','work/budget-status.json','input has cache',
            'unknown!=0','no grant','no wrappers/hashes'):
        assert text in prompt
    if role in 'HW':
        assert 'Follow-up targets advise; no grant/required handoff.' in prompt
    if role=='A':
        if job.get('research_strategy_version') in {1,2,3}:
            assert ('Complete the requested judgment; follow role_contract. Soft targets do not end research. '
                'Shared tokens<=0 or time<=0: retain originals/gaps; final-return admission and permissions hold.') in prompt
        else:
            assert ('Complete the requested judgment; bounded further work needs decision_request. '
                'Soft targets do not end research. Shared tokens<=0 or time<=0: deliver eligible candidate '
                'or finish with gaps from originals; no new H/W/E/extra FINAL; permissions hold.') in prompt
    assert c.read(execution.record(root,step)/'native-outcome.json')['stop_reason'] is None
