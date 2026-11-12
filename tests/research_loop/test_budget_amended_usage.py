"""An amended step ceiling meters later native jobs without covering new unknowns."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget,budget,contracts as c,controller,execution,native,profiles
from test_history_handoff import finish


@pytest.mark.parametrize('coverage',['terminal','unknown'])
def test_amended_step_six_counts_usage_and_keeps_its_unknown_distinct(tmp_path,coverage):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Retain prior usage and account for a bounded review.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture-never-launch'],max_tokens=2000,max_seconds=200,max_steps=5,
        model='fixture',reasoning_effort='high',node_budget_mode='soft',
        search_policy=None,supervision_policy=None,
        node_budget_policy={r:dict(max_observed_tokens=1000,max_seconds=50) for r in 'HWAE'})
    finish(root,1,'W',[])
    old_job=c.build_job(root,2,'W',[1]);original=execution.origin(root,2)
    old_meter=dict(input_tokens=100,cached_input_tokens=50,output_tokens=20)
    old_observation=dict(coverage='unknown',source='turn.completed',reason='terminal_usage_unreported',
        thread_id='old-two',tokens=old_meter)
    old_intent=dict(step=2,task_ref=c.record_ref(root/'jobs/002.json'),
        budget_slice=budget.accept_node(root,old_job),**profiles.binding(c.settings(root),old_job))
    accepted_budget.accept_native(root,2,original,old_intent)
    c.write(original/'usage.json',old_meter);c.write(original/'usage-observation.json',old_observation)
    # Synthetic completed science, not a model run or a successful execution
    # claim. Completion and unknown billing are independent; the reported meter
    # remains a lower bound. Use the real seal owner to close this logical job.
    c.write(original/'science-output.json',dict(summary='Retained bounded observation',
        scientific_result='The fixture has no scientific qualification or runnable candidate.',
        limitations=['Synthetic no-model evidence; terminal billing remains unknown.'],
        stage_intent='decision',implementation_selection=None,mechanism_findings=None))
    c.write(original/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        cleanup='observed_group_cleaned',thread_id='old-two',metering=old_observation,
        usage=native.usage(root),raw_ref=c.file_ref(original/'science-output.json')))
    (original/'native.jsonl').write_text('{"type":"thread.started"}\n{"type":"turn.completed"}\n')
    session=root/'native-homes/002/sessions/old-two.jsonl';session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta',payload=dict(id='old-two',cwd=str(c.workspace(root,2)))))+'\n')
    accepted_budget.accept_terminal(root,2,original,meter=old_meter,unknown=True,metering=old_observation)
    old_files={original/name:(original/name).read_bytes() for name in (
        'native-intent.json','usage.json','usage-observation.json','native-outcome.json','native.jsonl','science-output.json')}
    old_files[session]=session.read_bytes()
    before=native.usage(root)
    assert before['accounted_tokens']==130 and before['unknown_steps']==[2]
    terminal=accepted_budget.read(root)
    assert terminal.current_job['status']=='terminal' and terminal['current_native'] is None
    old_attempt=accepted_budget.native_attempt(root,2,original,head=terminal)
    assert old_attempt['status']=='closed' and old_attempt['unknown'] is True
    c.seal_result(root,2)
    sealed=accepted_budget.read(root)
    assert sealed.current_job['step']==2 and sealed.current_job['status']=='sealed'
    assert accepted_budget.native_attempt(root,2,original,head=sealed)==old_attempt
    assert native.usage(root)==before
    with pytest.raises(ValueError,match='closed or superseded native original cannot be launched again'):
        accepted_budget.accept_native(root,2,original,old_intent)
    assert native.usage(root)==before
    settings=(root/'settings.json').read_bytes()
    budget.amend(root,max_steps=24,authorization_note='Finite additional review steps.',authorization_statement='Finish retained work.')
    assert native.usage(root)==before  # A step grant neither refunds usage nor covers old unknowns.
    # The later ordinary A reads the exact sealed W original. Metering this
    # bounded advice requires no delivery adapter or scientific qualification.
    job=c.build_job(root,6,'A',[1])
    record=execution.origin(root,6)
    accepted_budget.accept_native(root,6,record,dict(step=6,task_ref=c.record_ref(root/'jobs/006.json'),
        budget_slice=budget.accept_node(root,job),**profiles.binding(c.settings(root),job)))
    meter=dict(input_tokens=11,cached_input_tokens=4,output_tokens=3)
    observation=dict(coverage=coverage,reason='terminal_usage_unreported' if coverage=='unknown' else None,tokens=meter)
    c.write(record/'usage.json',meter);c.write(record/'usage-observation.json',observation)
    c.write(record/'native-outcome.json',dict(returncode=0 if coverage=='terminal' else -15,
        stop_reason=None if coverage=='terminal' else 'token_limit',metering=observation))
    accepted_budget.accept_terminal(root,6,record,meter=meter,unknown=coverage=='unknown',metering=observation)
    result=native.usage(root)
    # A repeated read cannot re-add either terminal meter or erase old gaps.
    assert native.usage(root)==result
    head=accepted_budget.read(root)
    assert head['dispatch_count']==3 and head['usage']['unknown_count']==(1 if coverage=='terminal' else 2)
    assert accepted_budget.native_attempt(root,2,original,head=head)==old_attempt
    assert accepted_budget.native_attempt(root,2,original,head=head)['unknown'] is True
    assert accepted_budget.native_attempt(root,6,record,head=head)['unknown'] is (coverage=='unknown')
    assert c.settings(root)['max_steps']==5 and budget.effective_max_steps(root)==24
    assert result['input_tokens']==118 and result['output_tokens']==26
    assert result['observed_tokens']==144 and result['accounted_tokens']==144
    assert result['reserved_unknown_steps']==[] and result['reserved_tokens']==0
    assert result['unknown_steps']==([2] if coverage=='terminal' else [2,6])
    assert result['blocking_unknown_steps']==[]
    assert (root/'settings.json').read_bytes()==settings
    assert all(p.read_bytes()==data for p,data in old_files.items())
