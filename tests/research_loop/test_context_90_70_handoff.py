"""Explicit context policy uses current occupancy, without scanning native logs."""
import copy
import tomllib

import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, budget_notice as notice, checkpoint, configuration_form,
    contracts as c, controller, protocol, research_config,
)


def observation(started, used, *, current=True, status='last_reported', coverage='live'):
    return dict(context_windows=dict(coverage=coverage,windows_started=started,reason=None),
        context=dict(status=status,current_window=current,total_tokens=used,model_context_window=1000,
            observed_at=123.0,ratio=used/1000 if used is not None else None))


@pytest.mark.parametrize('maximum,started,used,reason,near',[
    (1,1,899,None,False),(1,1,900,None,True),(1,1,1000,None,True),
    (1,2,0,None,False),(1,2,700,None,False),(1,2,701,'worker_context_review_due',False),
    (2,1,950,None,True),(2,2,950,None,True),(2,3,700,None,False),
    (2,3,701,'worker_context_review_due',False),
])
def test_exact_first_90_and_post_compaction_strict_70_boundaries(maximum,started,used,reason,near):
    value=observation(started,used)
    result=notice.worker_context_status(maximum,value,version=2)
    assert result['reason']==reason and result['near'] is near
    assert notice.worker_context_reason(maximum,value,version=2)==reason
    assert notice.worker_context_near(maximum,value,version=2) is near


@pytest.mark.parametrize('changed',[
    dict(current_window=False),dict(status='unavailable'),dict(total_tokens=None),
    dict(model_context_window=None),dict(observed_at=None),
])
def test_missing_or_stale_current_occupancy_cannot_infer_70_from_window_count(changed):
    value=observation(2,999);value['context'].update(changed)
    assert notice.worker_context_status(1,value,version=2)==dict(reason=None,near=False,ratio=None)
    assert notice.worker_context_reason(1,dict(context_windows=dict(coverage='unknown',windows_started=4)),version=2) is None


def context_scene(tmp_path, *, maximum=1, version=None, legacy=False, context='same_session'):
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal.md';goal.write_text('Retain scientific work across native context compaction.')
    root=tmp_path/'instance'
    policy=dict(context=context,max_context_windows=maximum)
    if version is not None:policy['context_handoff_version']=version
    launcher=tmp_path/'codex';launcher.write_text('#!/bin/sh\nexit 99\n');launcher.chmod(0o755)
    options=dict(native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False),
        model='fixture',reasoning_effort='high',max_tokens=100_000,max_seconds=1000,max_steps=8,search_policy=None,
        worker_fragment_policy=policy,node_budget_policy={role:dict(max_observed_tokens=10000,max_seconds=600) for role in 'HWAE'},
        resume_policy=dict(max_attempts=1,protocol='codex_exec_resume',usage_scope='turn',output_repair=True,
            output_only=True,native_home=str(tmp_path/'native-home')))
    if legacy:
        baseline=tmp_path/'baseline'
        controller.prepare(baseline,goal,public,[str(launcher)],**options)
        original=copy.deepcopy(c.settings(baseline))
        original['worker_fragment_policy'].pop('context_handoff_version',None)
        original.pop('advisory_scope_version',None)
        options['_existing_settings']=original
        # The existing-settings path is used by source-preserving preparation.
        # No source is declared qualified in this synthetic historical fixture.
        options['_source_facts_factory']=lambda _root,_globals:[]
    controller.prepare(root,goal,public,[str(launcher)],**options)
    job=c.build_job(root,1,'W',[])
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','context fixture')
    anchor=budget.accept_node(root,job);intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor)
    head=accepted_budget.read(root);limits=budget.snapshot(root)
    shared=accepted_budget.usage_from_facts(head,job_rows=accepted_budget.load_usage_rows(head),current_native=None)
    facts=dict(settings=c.settings(root),head=head,shared=shared,stamp=budget.clock_stamp(),task=intent['task_ref'],
        scope=protocol.stage_scope(root,job),final_return=None,closeout_preparation_seconds=None,
        native_attempt=dict(number=0,execution_key=intent['task_ref']))
    return root,job,intent,limits,facts


@pytest.mark.parametrize('started,used,reason,progress',[
    (1,900,None,True),(2,700,None,False),(2,701,'worker_context_review_due',True),
])
def test_real_hint_and_review_publish_same_context_fact(tmp_path,started,used,reason,progress):
    root,job,intent,limits,facts=context_scene(tmp_path)
    value=observation(started,used)
    budget.write_node_hint(root,job,intent,limits,dict(input_tokens=2,cached_input_tokens=1,output_tokens=1),
        facts=facts,observed=True,context_observation=value)
    review=c.read(budget._review_directory(root,job)/'review.json')
    hint=c.read(c.workspace(root,1)/'work/budget-status.json')
    assert review['reason']==reason and review['pending'] is (reason is not None)
    assert ('progress' in hint.get('closeout_notices',{})) is progress
    if started==1:
        assert hint['target_state']=='near'
        assert hint['closeout_notices']['progress']['text']==notice.WORKER_CONTEXT_90_TEXT
    assert hint['worker_context_windows']['context_handoff_version']==2
    assert hint['worker_context_windows']['complete_windows']==1
    # Context deferral cannot hide the existing shared-reserve admission gate.
    exhausted=dict(limits,seconds_remaining=0)
    budget.publish_review(root,job,intent,exhausted,dict(input_tokens=2,output_tokens=1),
        facts=facts,observed=True,context_observation=observation(2,200))
    assert c.read(budget._review_directory(root,job)/'review.json')['reason']=='reserve_reached'


@pytest.mark.parametrize('version',[1,2])
def test_explicit_export_form_roundtrip_preserves_context_version(tmp_path,version):
    root,job,_,_,_=context_scene(tmp_path,version=version)
    selected=c.settings(root)
    assert selected['worker_fragment_policy']['context_handoff_version']==version
    document=research_config.to_document(root,selected)
    assert document['policies']['worker_context_handoff_version']==version
    encoded=configuration_form.export_toml(configuration_form.imported(document,base=root))
    options=research_config.from_document(tomllib.loads(encoded),base=root)
    assert options['worker_fragment_policy']==selected['worker_fragment_policy']
    restored=research_config.validate_update(root,document)
    assert restored['worker_fragment_policy']==selected['worker_fragment_policy']


def test_legacy_absence_retains_count_rule_and_exports_explicit_one(tmp_path):
    root,_,_,_,_=context_scene(tmp_path)
    selected=c.settings(root);legacy=copy.deepcopy(selected)
    legacy['worker_fragment_policy'].pop('context_handoff_version')
    assert notice.worker_context_reason(1,observation(2,100))=='worker_context_review_due'
    assert notice.worker_context_near(1,observation(1,850))
    assert notice.worker_context_reason(1,observation(2,100),version=1)=='worker_context_review_due'
    document=research_config.to_document(root,legacy)
    assert document['policies']['worker_context_handoff_version']==1
    assert research_config.from_document(document,base=root)['worker_fragment_policy']['context_handoff_version']==1
    assert checkpoint.policy(legacy['worker_fragment_policy'])==legacy['worker_fragment_policy']


def test_real_legacy_context_fork_preserves_exact_absent_private_version(tmp_path):
    from scidiscovery.research_loop import fork_runtime, fork_service
    from test_worker_e_consultation import publish
    root,job,_,_,_=context_scene(tmp_path,legacy=True)
    assert 'context_handoff_version' not in c.settings(root)['worker_fragment_policy']
    publish(root,job,stage_intent='handoff')
    c.build_job(root,2,'W',[1])
    child=tmp_path/'child';limits=dict(max_tokens=100_000,max_seconds=120,max_steps=8)
    preview=fork_service.preview(root,2,child,budget=limits)
    assert preview['status']=='fork_preview_ready',preview
    result=fork_service.create(root,2,child,budget=limits,
        expected_preview_id=preview['preview_id'],operation_id='legacy-context-exact-freeze')
    assert result['status']=='fork_created',result
    frozen=fork_runtime.build_selected_job(child,2)
    assert 'context_handoff_version' not in frozen['worker_fragment_policy']
    assert frozen['worker_fragment_policy']==job['worker_fragment_policy']


@pytest.mark.parametrize('used,allowed',[(700,True),(701,False)])
def test_actual_fragment_guard_uses_current_70_instead_of_started_count(tmp_path,monkeypatch,used,allowed):
    from unittest.mock import patch
    from scidiscovery.research_loop import continuation, execution, profiles, runtime
    from test_adaptive_a_budget import _publish_synthetic
    root,job,_,_,_=context_scene(tmp_path,context='new_context')
    record=c.host_record(c.workspace(root,1));output=record/'science-output.json';write=c.write
    def scientific(path,value):
        if path==output:value=dict(value,stage_intent='continue')
        return write(path,value)
    anchor=budget.accept_node(root,job)
    intent=dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor,
        **profiles.binding(c.settings(root,job),job))
    c.write(record/'native-intent.json',intent);accepted_budget.accept_native(root,1,record,intent)
    with patch.object(c,'write',scientific):
        _,intent,_,outcome=_publish_synthetic(root,job,tokens=30,intent=intent,record=record)
    from test_direct_public_collect import stopped_attempt
    _,processes=stopped_attempt(root,job,record,monkeypatch)
    # Continuation additionally requires this boot and a parent receipt; keep
    # the stopped-process producer's exact task identities and bridge outcome.
    for name,value in processes.items():
        value.update(boot_id=intent['budget_slice']['boot_id'],ppid=1)
        c.write(record/name,value)
    c.write(record/'bridge-outcome.json',dict(process=processes['bridge-process.json'],cleanup='observed_group_cleaned'))
    outcome=c.read(record/'native-outcome.json')
    outcome.update(terminal_observed=True,native_backend='codex',
        metering=dict(outcome['metering'],**observation(2,used)))
    c.write(record/'native-outcome.json',outcome)
    c.write(record/'usage-observation.json',outcome['metering'])
    inventory=c.accept_current_inventory(root,1)
    launched=[]
    monkeypatch.setattr(runtime,'start',lambda *args,**kwargs:launched.append((args,kwargs)))
    if not allowed:
        # Simulate a stale low-occupancy live projection: the original terminal
        # context guard must still refuse, without a provider/model invocation.
        monkeypatch.setattr(continuation,'fragment_review',lambda *args,**kwargs:
            dict(requested=False,enforced=True,reason=None))
        with pytest.raises(continuation.ResumeUnavailable) as refused:
            continuation.fragment_start(root,1,inventory=inventory)
        assert refused.value.review_reason=='worker_context_review_due'
        assert not launched and len(execution.records(root,1))==1
    else:
        accepted=continuation.fragment_start(root,1,inventory=inventory)
        assert accepted['attempt']==1 and accepted['continuation_kind']=='fragment'
        assert accepted['budget_slice']['previous_tokens']==30
        assert len(launched)==1 and len(execution.records(root,1))==2
