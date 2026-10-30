"""Small advisory projections retain soft targets, actual accounting and finite author time."""
import json

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native
from test_node_budget import instance, policy, target
from test_soft_node_budget import start_clock


@pytest.fixture
def advice(tmp_path, monkeypatch):
    def make(*, mode='soft', node=True, delivery=False, node_tokens=100):
        public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Bounded scientific question.')
        root=tmp_path/'instance'
        reserve=(dict(closeout_a=dict(max_observed_tokens=10,max_seconds=1),inflight_tokens=5,
            cleanup_seconds=2,submit_seconds=0,confirm_seconds=0,adapter=None) if delivery else None)
        controller.prepare(root,objective,public,['never-launch'],max_tokens=1000,max_seconds=120,max_steps=6,
            model='fixture',reasoning_effort='high',search_policy=None,node_budget_mode=mode,
            node_budget_policy=policy(tokens=node_tokens,seconds=30) if node else None,delivery_policy=reserve)
        job=c.build_job(root,1,'H',[]);start_clock(root,'advisory fixture')
        anchor=budget.accept_node(root,job)
        intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),**(dict(budget_slice=anchor) if anchor else {}))
        c.write(execution.origin(root,1)/'native-intent.json',intent)
        limits=budget.snapshot(root)
        if anchor:limits['advance_seconds']=anchor['advance_seconds']+5
        limits['seconds_remaining']=100.
        shared=dict(observed_tokens=25,accounted_tokens=25,unknown_steps=[])
        monkeypatch.setattr(native,'usage',lambda *_:dict(shared))
        publications=[]
        monkeypatch.setattr(budget,'publish_review',lambda *a,**kw:publications.append(True))
        for name in ('snapshot','node_admission','accept_node'):
            monkeypatch.setattr(budget,name,lambda *a,**kw:pytest.fail('Advice must use the supplied observation, never reenter admission'))
        path=c.workspace(root,1)/'work/budget-status.json'
        def publish(meter=None,*,observed=True,author_limits=None):
            budget.write_node_hint(root,job,intent,limits,meter or dict(input_tokens=20,cached_input_tokens=19,output_tokens=5),
                observed=observed,author_limits=author_limits)
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
    assert hint['observed_tokens_lower_bound']==25  # Cached input is already included.
    assert hint['current_work_observed_tokens_remaining_upper_bound']==expected
    assert hint['current_work_active_seconds_available']==100
    assert hint['per_request_admission']=='unavailable' and hint['final_response_allowance']=='not_guaranteed'
    assert hint['inflight_usage_unknown'] and hint['reporting_delay_unmeasured']
    assert published==[True] and (root/'settings.json').read_bytes()==settings


def test_unreported_continuation_keeps_previous_consumption_and_unknown(advice,monkeypatch):
    _,_,intent,_,shared,_,publish=advice()
    # This projection receives the prior consumption from an accepted anchor;
    # ownership/no-refund validation is independently covered by node tests.
    intent['budget_slice']['previous_tokens']=30
    monkeypatch.setattr(budget,'_node_duration',lambda *a:5.)
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


@pytest.mark.parametrize('mode',['soft'])
def test_author_advice_uses_verified_attempt_allowance_not_reused_w_cap(advice,monkeypatch,mode):
    root,_,intent,_,shared,_,publish=advice(mode=mode,node_tokens=200)
    frozen=c.settings(root)
    assert budget.delivery_reserve(frozen)==dict(tokens=0,seconds=0.,steps=0)
    assert intent['budget_slice']['limits']['max_observed_tokens']==200
    intent.update(continuation_kind='author_closeout')
    intent['budget_slice'].update(previous_tokens=123,previous_seconds=2.)
    shared.update(observed_tokens=133,accounted_tokens=133,unknown_steps=[1])
    monkeypatch.setattr(budget,'_node_duration',lambda *a:7.)
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
def test_author_balance_preserves_actual_shared_usage_past_soft_targets(advice,monkeypatch,mode,prior,expected,state):
    _,_,intent,_,shared,_,publish=advice(mode=mode)
    intent.update(continuation_kind='author_closeout')
    intent['budget_slice'].update(previous_tokens=prior,previous_seconds=2.)
    shared.update(observed_tokens=prior+10,accounted_tokens=prior+10)
    monkeypatch.setattr(budget,'_node_duration',lambda *a:7.)
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
    assert shared['node_budget']==c.read(execution.origin(root,step)/'native-intent.json')['budget_slice']['limits']
    assert local['shared_observed_tokens_remaining_upper_bound']==ordinary['observed_tokens_remaining_upper_bound']
    assert local['per_request_admission']=='unavailable' and local['final_response_allowance']=='not_guaranteed'
    assert local['inflight_usage_unknown'] and local['reporting_delay_unmeasured']
    assert ordinary['token_usage_unknown']  # This current native task has no outcome or usage before submission.
    assert 'unknown_usage' not in ordinary
    for text in ('Soft targets','work/budget-status.json','cached once',
            'unknown tokens!=0','no grant','no control wrappers/hashes'):
        assert text in prompt
    assert c.read(execution.record(root,step)/'native-outcome.json')['stop_reason'] is None
