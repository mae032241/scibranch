"""An amended step ceiling meters later native jobs without covering new unknowns."""
import json

import pytest

from scidiscovery.research_loop import budget,contracts as c,controller,delivery,execution,native,profiles
from test_history_handoff import finish
from test_delivery import descriptor,delivery_policy


@pytest.mark.parametrize('coverage',['terminal','unknown'])
def test_amended_step_six_counts_usage_and_keeps_its_unknown_distinct(tmp_path,coverage):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md'
    objective.write_text('Retain prior usage and account for a bounded review.')
    root=tmp_path/'instance'
    harness=public/'harness';harness.mkdir()
    for name in ('exp_cli.py','runner.py','train.csv','val.csv','meta.json'):
        (harness/name).write_text('Frozen fixture input; never executed.\n')
    controller.prepare(root,objective,public,['fixture-never-launch'],max_tokens=2000,max_seconds=200,max_steps=5,
        model='fixture',reasoning_effort='high',node_budget_mode='soft',
        node_budget_policy={r:dict(max_observed_tokens=1000,max_seconds=50) for r in 'HWAE'},
        delivery_policy=delivery_policy(descriptor(root,public)))
    finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1]);original=execution.origin(root,2)
    old_meter=dict(input_tokens=100,cached_input_tokens=50,output_tokens=20)
    old_observation=dict(coverage='unknown',reason='terminal_usage_unreported',thread_id='old-two',tokens=old_meter)
    c.write(original/'native-intent.json',dict(step=2,task_ref=c.record_ref(root/'jobs/002.json')))
    c.write(original/'usage.json',old_meter);c.write(original/'usage-observation.json',old_observation)
    c.write(original/'native-outcome.json',dict(returncode=-15,stop_reason='token_limit',
        cleanup='observed_group_cleaned',thread_id='old-two',metering=old_observation))
    (original/'native.jsonl').write_text('{"type":"thread.started"}\n')
    session=root/'native-homes/002/sessions/old-two.jsonl';session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta',payload=dict(id='old-two',cwd=str(c.workspace(root,2)))))+'\n')
    old_files={p:p.read_bytes() for p in original.iterdir() if p.is_file()}
    settings=(root/'settings.json').read_bytes()
    budget.amend(root,max_steps=24,authorization_note='Finite additional review steps.',authorization_statement='Finish retained work.')
    # This meter-only review uses the sealed H prefix; the unsealed W stays out
    # of scientific inputs while its historical consumption remains in accounting.
    sources=delivery.freeze_sources(root,[dict(step=1,seal_ref=c.record_ref(execution.origin(root,1)/'result-identity.json'))])
    budget.begin_closeout(root,1,6,'fixture_bounded_review',delivery_sources=sources)
    job=c.build_job(root,6,'A',[1],purpose='closeout',source_cutoff=1,delivery_sources=sources)
    record=execution.origin(root,6)
    c.write(record/'native-intent.json',dict(step=6,task_ref=c.record_ref(root/'jobs/006.json'),
        budget_slice=budget.accept_node(root,job),**profiles.binding(c.settings(root),job)))
    meter=dict(input_tokens=11,cached_input_tokens=4,output_tokens=3)
    observation=dict(coverage=coverage,reason='terminal_usage_unreported' if coverage=='unknown' else None,tokens=meter)
    c.write(record/'usage.json',meter);c.write(record/'usage-observation.json',observation)
    c.write(record/'native-outcome.json',dict(returncode=0 if coverage=='terminal' else -15,
        stop_reason=None if coverage=='terminal' else 'token_limit',metering=observation))
    result=native.usage(root)
    assert c.settings(root)['max_steps']==5 and budget.effective_max_steps(root)==24
    assert result['input_tokens']==118 and result['output_tokens']==26
    assert result['observed_tokens']==144 and result['accounted_tokens']==144
    assert result['reserved_unknown_steps']==[] and result['reserved_tokens']==0
    assert result['unknown_steps']==([2] if coverage=='terminal' else [2,6])
    assert result['blocking_unknown_steps']==[]
    assert (root/'settings.json').read_bytes()==settings
    assert all(p.read_bytes()==data for p,data in old_files.items())
