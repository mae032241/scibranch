"""Actual A cost and coverage gates are backend-neutral admission facts."""
import copy

import pytest

from scidiscovery.research_loop import accepted_search, budget, contracts as c, supervision
from test_adaptive_a_trigger import JOB_REF, POLICY, charge, facts, freeze, receipt, settle
from test_worker_first_investment import scene, publish


def test_automatic_a_cost_ceiling_checks_tokens_and_time_separately():
    state=receipt(supervision.empty(POLICY),1)
    state,_=settle(state,facts(1100,110),tokens=10,seconds=251)
    state=receipt(state,2)
    status=supervision.due(state,budget_facts=facts(2200,400))
    assert status['suppression_reason']=='automatic_review_budget_exhausted'
    assert not status['automatic_allowed'] and state['adaptive']['automatic_cost']['tokens']==10
    token_state=receipt(supervision.empty(POLICY),1)
    token_state,_=settle(token_state,facts(1100,110),tokens=2500,seconds=1)
    assert supervision.due(token_state,budget_facts=facts(2200,220))['suppression_reason']=='automatic_review_budget_exhausted'


def test_invalid_a_verdict_still_charges_every_original_attempt_once_at_publication():
    summary=accepted_search.empty();summary['node_count']=2;summary['last_w']=1
    summary['supervision']=receipt(supervision.empty(POLICY),1)
    request=freeze(summary['supervision'],automatic=True,budget_facts=facts(1100,110))
    delta=accepted_search._Delta(summary,lambda name:None)
    accepted_search._supervision(delta,dict(role='A',step=2,supervision_request=request),
        dict(scientific_result='Existing assessment was inconclusive',next_action='continue_w'),{},None,
        origin={},bindings={},valid=False,policy=dict(supervision_policy=POLICY),
        automatic_review_cost=dict(tokens=800,seconds=20,complete=dict(tokens=True,seconds=True),
            job_ref=JOB_REF,task_ref=JOB_REF,step=2,role='A'),
        job_ref=JOB_REF,budget_facts=facts(1200,120))
    state=delta.summary['supervision']
    assert state['adaptive']['automatic_cost']==dict(tokens=800,seconds=20.)
    assert state['automatic_reviews']==1 and state['last_covered_cutoff'] is None
    status=supervision.due(state,budget_facts=facts(1200,120))
    assert not status['automatic_allowed'] and not status['fresh_input']


@pytest.mark.parametrize('dimension',['tokens','seconds'])
def test_unknown_cost_retains_lower_bound_and_blocks_only_automatic_admission(dimension):
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True,budget_facts=facts(1100,110))
    cost=dict(tokens=300,seconds=30,complete=dict(tokens=True,seconds=True))
    cost['complete'][dimension]=False
    state=charge(state,request,cost=cost)
    status=supervision.due(state,budget_facts=facts(2200,220))
    assert not status['automatic_allowed']
    assert status['suppression_reason']=='automatic_review_cost_unknown_reconcile_original_usage'
    assert state['adaptive']['automatic_cost'][dimension]==cost[dimension]
    assert supervision.worker_review(dict(role='W'),{},summary=state,budget_fraction=.22,
        selected_policy=POLICY,budget_facts=facts(2200,220)) is None


def test_unknown_completion_never_becomes_a_zero_cooldown_baseline():
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True,budget_facts=facts(1100,110))
    state=supervision.cover(state,request,source_step=2,budget_facts=None)
    assert not supervision.due(state,budget_facts=facts(9000,900))['automatic_allowed']


def test_unlimited_original_dimension_is_not_applicable_not_zero_cost():
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True,
        budget_facts=facts(tokens=None,seconds=0,token_limit=None,tokens_complete=False))
    state=charge(state,request,
        cost=dict(tokens=None,seconds=10,complete=dict(tokens=False,seconds=True)),
        budget_facts=facts(tokens=None,seconds=10,token_limit=None,tokens_complete=False))
    current=facts(tokens=None,seconds=200,token_limit=None,tokens_complete=False)
    assert supervision.due(state,budget_facts=current)['automatic_allowed']
    unavailable=facts(tokens=None,seconds=None,token_limit=None,seconds_limit=None,
        tokens_complete=False,seconds_complete=False)
    assert not supervision.due(state,budget_facts=unavailable)['automatic_allowed']


def test_raw_shared_fact_producer_and_all_attempt_time_keep_separate_dimensions():
    produced=budget.supervision_budget_facts(dict(max_tokens=10000,max_seconds=1000),
        dict(observed_tokens=800,accounted_tokens=900,unknown_step_count=1),
        dict(consumed_seconds=200,unmeasured_shutdown_tail=True))
    assert produced==facts(900,200,tokens_complete=False,seconds_complete=False)
    duration=budget._advice_duration(dict(budget_slice=dict(advance_seconds=40,previous_seconds=15)),
        dict(advance_seconds=80))
    assert duration==55  # Prior attempts retained, independently of token fractions.


def test_cost_blocked_live_hint_does_not_publish_direction_pause_or_stop_new_work(tmp_path):
    root,job,intent,limits,host,live=scene(tmp_path,'review',policy=POLICY)
    state=receipt(host['head']['search']['supervision'],1)
    original=facts(1100,1,token_limit=100000,seconds_limit=120)
    request=freeze(state,automatic=True,budget_facts=original)
    state=charge(state,request,
        cost=dict(tokens=25000,seconds=0,complete=dict(tokens=True,seconds=True)),
        budget_facts=facts(26100,2,token_limit=100000,seconds_limit=120))
    host['head']['search']['supervision']=state
    host['research_investment']=live
    host['shared']=dict(host['shared'],observed_tokens=30000,accounted_tokens=30000)
    limits=dict(limits,consumed_seconds=20)
    work,hint=publish(root,job,intent,limits,host)
    assert 'direction_review' not in hint
    assert hint['research_investment']['suppression_reason']=='automatic_review_budget_exhausted'
    review=c.read(budget._review_directory(root,job)/'review.json')
    assert review['reason']!='worker_direction_review_due' and not review['pending']
    assert 'review' not in hint.get('closeout_notices',{})


def test_wrong_actual_a_cost_owner_is_rejected_before_any_charge():
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True)
    before=copy.deepcopy(state)
    with pytest.raises(ValueError,match='exact accepted A job'):
        supervision.charge_automatic(state,request,cost=dict(tokens=100,seconds=10,
            complete=dict(tokens=True,seconds=True),job_ref=JOB_REF,task_ref=dict(path='wrong-original'),step=2,role='A'),
            job=dict(role='A',step=2,supervision_request=request),job_ref=JOB_REF,budget_facts=facts(1200,120))
    assert state==before


def _prepare_publication_instance(tmp_path, **changes):
    from scidiscovery.research_loop import controller
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain original synthetic evidence and costs.')
    root=tmp_path/'instance'
    options=dict(native_backend='protocol',model='explicit-fixture-model',reasoning_effort='high',
        experiment_mode='external',max_tokens=10000,max_seconds=600,max_steps=20,supervision_policy=POLICY)
    options.update(changes)
    controller.prepare(root,objective,public,['fixture-never-launch'],**options)
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','synthetic original publication')
    return root


def _publish_synthetic(root,job,*,tokens,request=None,intent=None,record=None):
    """Only original JSON receipts; no model, executor or subprocess is started."""
    from scidiscovery.research_loop import accepted_budget, execution, native
    step=job['step'];record=execution.record(root,step) if record is None else record
    if intent is None:
        anchor=budget.accept_node(root,job)
        intent=dict(step=step,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
            **(dict(budget_slice=anchor) if anchor is not None else {}))
        accepted_budget.accept_native(root,step,record,intent)
    payload=dict(summary='Synthetic retained contribution',scientific_result='Synthetic evidence; no efficacy claim.',
        limitations=['Fixture only; no scientific process was run.'],implementation_selection=None)
    if job['role']=='W':
        payload.update(stage_intent='decision',mechanism_findings=None,candidate_nominations=None,
            research_memory=dict(question='Does the retained fixture evidence address the question?',candidates=[]))
    else:
        payload.update(next_action='continue_w',route_reason='Retain existing useful research',
            candidate_recommendation=None,selection_explanation=None,search_review=None,progress_assessment=None,
            decision_request=None,portfolio_review=None,supervision_result='continue_existing')
    c.write(record/'native-intent.json',intent)
    c.write(record/'science-output.json',payload)
    meter=dict(input_tokens=tokens,cached_input_tokens=min(2,tokens),output_tokens=0)
    c.write(record/'usage.json',meter)
    outcome=dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json'),metering=dict(coverage='terminal',
            reporting_delay_unmeasured=False,inflight_usage_unknown=False))
    if intent.get('budget_slice') is not None:
        outcome['budget_slice']=dict(budget.node_report(root,job,intent,meter),usage_observed=True)
    c.write(record/'native-outcome.json',outcome)
    return record,intent,meter,outcome


def test_public_prepare_anchor_publisher_cold_reopen_and_fork_preserve_actual_cost(tmp_path):
    from scidiscovery.research_loop import accepted_budget, accepted_prefix, controller, fork_runtime, fork_service
    root=_prepare_publication_instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    _publish_synthetic(root,worker,tokens=1100)
    c.seal_result(root,1)
    produced=controller._supervision_facts(root,c.settings(root))
    assert supervision.due(produced['summary'],budget_facts=produced['budget_facts'])['automatic_allowed']
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    assessor=c.build_job(root,2,'A',[1],supervision_request=request)
    record,intent,_,_= _publish_synthetic(root,assessor,tokens=30)
    assert intent['budget_slice']['limits']==dict(max_observed_tokens=10000,max_seconds=600)
    assert budget.stop_node(root,assessor,intent,budget.snapshot(root),dict(input_tokens=30,output_tokens=0)) is None
    c.seal_result(root,2)
    reopened=accepted_budget.read(root)
    state=reopened['search']['supervision']
    assert state['adaptive']['automatic_cost']['tokens']==30
    assert state['adaptive']['cost_known']==dict(tokens=True,seconds=True)
    assert state['adaptive']['automatic_cost']['seconds']>=0
    assert accepted_budget.read(root)['search']['supervision']==state
    navigation=accepted_prefix.navigation(root,reopened['prefix'])
    live=controller._supervision_facts(root,c.settings(root))
    persisted=navigation['research']['direction_supervision']['adaptive_review']
    assert persisted['suppression_reason']=='cooldown'
    assert supervision.due(live['summary'],budget_facts=live['budget_facts'])['suppression_reason']=='cooldown'
    c.build_job(root,3,'W',[1,2])
    with controller._writer(root):pass
    child=tmp_path/'fork'
    limits=dict(max_tokens=10000,max_seconds=60,max_steps=8)
    preview=fork_service.preview(root,3,child,budget=limits)
    assert preview['status']=='fork_preview_ready',preview
    fork_service.create(root,3,child,budget=limits,expected_preview_id=preview['preview_id'],operation_id='adaptive-cost-input-cut')
    forked=accepted_budget.read(child)
    assert forked['search']['supervision']==state
    assert fork_runtime.build_selected_job(child,3)['history']['prefix']==forked['prefix']


def test_public_build_job_refuses_wrong_v4_budget_source_and_old_policy(tmp_path):
    from scidiscovery.research_loop import controller
    root=_prepare_publication_instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    _publish_synthetic(root,worker,tokens=1100)
    c.seal_result(root,1)
    produced=controller._supervision_facts(root,c.settings(root))
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    for key in ('limits','tokens','seconds','source_w','policy_version','float_policy_version'):
        wrong=copy.deepcopy(request)
        if key=='limits':wrong['budget_scope']['limits']['seconds']=1
        elif key=='tokens':wrong['budget_scope']['tokens']=1
        elif key=='seconds':wrong['budget_scope']['seconds']=1e9
        elif key=='source_w':wrong['source_w']=0
        elif key=='float_policy_version':wrong['policy_version']=4.0
        else:wrong['policy_version']=3
        with pytest.raises(ValueError):c.build_job(root,2,'A',[1],supervision_request=wrong)
    assert not (root/'jobs/002.json').exists()


def test_real_two_attempt_a_keeps_original_token_and_active_time_costs(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget, controller, worker_delivery
    root=_prepare_publication_instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    _publish_synthetic(root,worker,tokens=1100)
    c.seal_result(root,1)
    produced=controller._supervision_facts(root,c.settings(root))
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    assessor=c.build_job(root,2,'A',[1],supervision_request=request)
    first,first_intent,first_meter,first_outcome=_publish_synthetic(root,assessor,tokens=30)
    accepted_budget.accept_terminal(root,2,first,meter=first_meter,unknown=False,metering=first_outcome['metering'])
    # The existing accounting owner verifies attempt zero before retaining its
    # previous_tokens/previous_seconds in the successor's original anchor.
    anchor=budget.accept_continuation(root,assessor,[first])
    assert anchor['previous_tokens']==30
    second=first/'continuations/001'
    intent=dict(step=2,attempt=1,continuation_kind='reentry',task_ref=first_intent['task_ref'],
        root_intent_ref=dict(path=str(first/'native-intent.json')),
        previous={'native-intent.json':dict(path=str(first/'native-intent.json'))},budget_slice=anchor)
    c.write(second/'native-intent.json',intent)
    accepted_budget.accept_native(root,2,second,intent)
    _,_,_,outcome=_publish_synthetic(root,assessor,tokens=40,intent=intent,record=second)
    binding=dict(path=str(second/'native-intent.json'))
    processes={name:dict(task_ref=binding,boot_id=anchor['boot_id'],pid=99999991+number,
        start_ticks=1,pgid=99999991+number,sid=99999991+number,ppid=1)
        for number,name in enumerate(('runner-process.json','native-process.json'))}
    outcome['execution_processes']={name:dict(path=str(second/name)) for name in processes}
    c.write(second/'native-outcome.json',outcome)
    for name,value in processes.items():c.write(second/name,value)
    # No process exists in this fixture: substitute only the stopped-process
    # observation owner. Acceptance, accounting, continuation identity and
    # scientific publication all run through their production implementations.
    monkeypatch.setattr(worker_delivery,'_writers_stopped',lambda *args,**kwargs:processes)
    c.seal_result(root,2)
    state=accepted_budget.read(root)['search']['supervision']
    assert state['adaptive']['automatic_cost']['tokens']==70
    assert state['adaptive']['automatic_cost']['seconds']>=anchor['previous_seconds']
    assert state['adaptive']['cost_known']==dict(tokens=True,seconds=True)
    assert state['automatic_reviews']==1
    assert accepted_budget.read(root)['search']['supervision']==state


@pytest.mark.parametrize('dimension',['max_tokens','max_seconds'])
def test_public_unlimited_original_dimension_requires_existing_finite_node_policy(tmp_path,dimension):
    with pytest.raises(ValueError,match='Unlimited global budgets require explicit finite all-role'):
        _prepare_publication_instance(tmp_path,**{dimension:None})


@pytest.mark.parametrize('dimension',['max_tokens','max_seconds'])
def test_public_unlimited_amendment_cannot_remove_finite_target_requirement(tmp_path,dimension):
    root=_prepare_publication_instance(tmp_path)
    with pytest.raises(ValueError,match='Unlimited global budgets require explicit finite all-role'):
        budget.amend(root,**{dimension:'unlimited'},authorization_note='Synthetic original operator amendment')


@pytest.mark.parametrize('dimension',['max_tokens','max_seconds'])
def test_public_one_unlimited_dimension_with_declared_finite_targets_publishes_a_cost(tmp_path,dimension):
    from scidiscovery.research_loop import accepted_budget, controller
    targets={role:dict(max_observed_tokens=10000,max_seconds=600) for role in 'HWAE'}
    chosen=dict(POLICY,min_budget_fraction=1e-12)
    root=_prepare_publication_instance(tmp_path,**{dimension:None},node_budget_policy=targets,supervision_policy=chosen)
    worker=c.build_job(root,1,'W',[])
    _publish_synthetic(root,worker,tokens=1100)
    c.seal_result(root,1)
    produced=controller._supervision_facts(root,c.settings(root))
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    assessor=c.build_job(root,2,'A',[1],supervision_request=request)
    _,intent,_,_=_publish_synthetic(root,assessor,tokens=30)
    assert intent['budget_slice']['limits']==targets['A']
    c.seal_result(root,2)
    state=accepted_budget.read(root)['search']['supervision']
    assert state['adaptive']['automatic_cost']['tokens']==30
    assert state['adaptive']['cost_known']==dict(tokens=True,seconds=True)
    original_dimension='tokens' if dimension=='max_tokens' else 'seconds'
    assert request['budget_scope']['limits'][original_dimension] is None


def test_v4_toml_export_roundtrip_omits_only_unset_emergency_count_cap(tmp_path):
    import tomllib
    from scidiscovery.research_loop import configuration_form, research_config
    root=_prepare_publication_instance(tmp_path)
    selected=c.settings(root)
    document=research_config.to_document(root,selected)
    assert 'max_automatic_reviews' not in document['policies']['supervision']
    draft=configuration_form.imported(document,base=root)
    exported=configuration_form.export_toml(draft)
    decoded=research_config.from_document(tomllib.loads(exported),base=root)
    assert supervision.policy(decoded['supervision_policy'])==POLICY
    assert selected['supervision_policy']['max_automatic_reviews'] is None
    historical=dict(selected,supervision_policy=supervision.AUTOMATIC_REFLECTION_POLICY)
    assert research_config.to_document(root,historical)['policies']['supervision']['max_automatic_reviews']==2


def test_public_unknown_head_cannot_schedule_a_with_forged_complete_coverage(tmp_path):
    from scidiscovery.research_loop import accepted_budget, controller
    root=_prepare_publication_instance(tmp_path)
    worker=c.build_job(root,1,'W',[])
    record,_,_,outcome=_publish_synthetic(root,worker,tokens=1100)
    outcome['metering']=dict(coverage='unknown',reporting_delay_unmeasured=True,inflight_usage_unknown=True)
    c.write(record/'native-outcome.json',outcome)
    c.seal_result(root,1)
    assert accepted_budget.read(root)['usage']['unknown_count']>0
    produced=controller._supervision_facts(root,c.settings(root))
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    request['budget_scope']['complete']['tokens']=True
    with pytest.raises(ValueError,match='overstated original budget coverage'):
        c.build_job(root,2,'A',[1],supervision_request=request)
    assert not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('dimension',['tokens','seconds'])
def test_actual_admission_coverage_is_required_but_later_unknown_cost_is_retained(dimension):
    state=receipt(supervision.empty(POLICY),1)
    request=freeze(state,automatic=True)
    observed=facts(1200,120)
    observed['complete'][dimension]=False
    with pytest.raises(ValueError,match='overstated original budget coverage'):
        supervision.validate_adaptive_request(state,request,job=dict(step=2),budget_facts=observed,
            source_w=1,admission=True)
    # Uncertainty appearing in this already accepted A is a consumed original,
    # not an admission retry. It remains charged and suppresses the next A.
    cost=dict(tokens=100,seconds=10,complete=dict(tokens=True,seconds=True))
    cost['complete'][dimension]=False
    charged=charge(state,request,cost=cost,budget_facts=observed)
    assert charged['adaptive']['automatic_cost'][dimension]==cost[dimension]
    assert not supervision.due(charged,budget_facts=facts(3300,330))['automatic_allowed']
