"""FINAL lifecycle through deterministic native/adapter transport; no real model.

The shared fixture launches local Python and adapter CLI processes, so this
module conservatively groups its related boundary assertions as process_e2e.
"""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, native, runtime
from test_history_handoff import finish
import test_soft_external_closeout as external_fixture

pytestmark = pytest.mark.process_e2e


def external_scene(tmp_path, monkeypatch, **options):
    return external_fixture.external_scene(tmp_path, monkeypatch, **options)


def report_instance(tmp_path, monkeypatch, **options):
    from scidiscovery.research_loop import backends
    from test_final_a_completion import instance
    from test_final_a_completion_contract import complete
    root = instance(tmp_path, **options)
    def return_report(actual_root, step, attempt=0):
        assert actual_root == root and attempt == 0
        job = c.read(root/'jobs'/f'{step:03d}.json')
        record = execution.origin(root, step)
        _, outcome = complete(root, job, c.read(record/'native-intent.json'))
        backend = backends.selected(c.settings(root, job))
        outcome['native_backend'] = backend.name
        outcome['metering']['source'] = backend.terminal_source
        c.write(record/'usage-observation.json', outcome['metering'])
        c.write(record/'native-outcome.json', outcome)
    monkeypatch.setattr(runtime, 'start', return_report)
    return root


def final_job(root):
    state = budget.closeout_state(root)
    assert state['begin']['mode'] == 'final'
    event = budget.final_a_barrier(root)
    assert event['basis'] == 'final_state'
    job = c.read(root/'jobs'/f'{event["step"]:03d}.json')
    assert job['role'] == 'A' and job['final_only']
    assert not (root/'jobs'/f'{event["step"]+1:03d}.json').exists()
    return job, state


def cleaned_unknown_author(root, *, stop_reason='token_limit', cleanup='observed_group_cleaned'):
    """Only the interrupted author's billing is unknown; W originals stay sealed."""
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Fixture original author started')
    c.build_job(root, 3, 'A', [1, 2])
    record = execution.origin(root, 3)
    intent = dict(step=3, task_ref=c.record_ref(root/'jobs/003.json'))
    c.write(record/'native-intent.json', intent)
    accepted_budget.accept_native(root, 3, record, intent)
    meter = dict(input_tokens=100500, cached_input_tokens=100000, output_tokens=5)
    observation = dict(coverage='unknown', reason='terminal_usage_unreported', tokens=meter)
    c.write(record/'usage.json', meter)
    c.write(record/'usage-observation.json', observation)
    c.write(record/'native-outcome.json', dict(returncode=-15, stop_reason=stop_reason,
        cleanup=cleanup, metering=observation, usage=native.usage(root)))
    if cleanup == 'observed_group_cleaned' and stop_reason == 'token_limit':
        accepted_budget.accept_terminal(root, 3, record, meter=meter,
            unknown=True, metering=observation)
    return record


def test_unknown_billing_does_not_clear_unresolved_native_execution(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    cleaned_unknown_author(root, stop_reason='cleanup_unknown', cleanup='cleanup_unknown')
    assert budget.execution_blocked(root)
    result = controller._start_budget_closeout(root, 2, 3, 'A', 'token_limit')
    assert result['status'] == 'exported_unsubmitted'
    assert result['reason'] == 'original_execution_unresolved'
    assert budget.final_a_barrier(root) is None
    assert budget.closeout_state(root)['actions'] == []
    assert native.usage(root)['unknown_steps'] == [3] and native.usage(root)['blocking_unknown_steps'] == []


@pytest.mark.parametrize('shared_ceiling', [10, 5])
def test_final_does_not_require_optional_node_budget_policy(tmp_path, monkeypatch, shared_ceiling):
    prepare = controller.prepare
    def without_node_policy(*args, **kwargs):
        kwargs['node_budget_policy'] = None
        kwargs['resume_policy'] = None
        return prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', without_node_policy)
    root = report_instance(tmp_path, monkeypatch, max_tokens=shared_ceiling, max_steps=6, mode='soft', route='analyze')
    assert c.settings(root).get('node_budget_policy') is None
    assert budget.snapshot(root)['max_tokens'] - native.usage(root)['observed_tokens'] <= 0
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    job, _ = final_job(root)
    assert budget.node_limits(root, job) == dict(max_observed_tokens=shared_ceiling, max_seconds=90)
    assert result['submission_result']['status'] == 'unsubmitted'


def test_final_without_accepted_worker_still_runs_one_a_with_explicit_unsubmitted_result(tmp_path, monkeypatch):
    root = report_instance(tmp_path, monkeypatch, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert result['reason'] == 'scientific_delivery_declined'
    assert result['submission_result']['status'] == 'unsubmitted'
    assert budget.closeout_state(root)['begin']['mode'] == 'final'
    job, state = final_job(root)
    assert job['step'] == 2 and state['actions'] == []


def test_default_optional_node_policy_uses_original_global_a_call_safety(tmp_path, monkeypatch):
    prepare = controller.prepare
    def default_prepare(*args, **kwargs):
        kwargs['node_budget_policy'] = None
        kwargs['resume_policy'] = None
        return prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', default_prepare)
    root = report_instance(tmp_path, monkeypatch, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    assert controller.run(root)['status'] == 'final_a_completed'
    job, _ = final_job(root)
    assert budget.node_limits(root, job) == dict(max_observed_tokens=5, max_seconds=90)
    assert c.read(execution.record(root, job['step'])/'usage.json')['input_tokens'] == 1200


def test_consultation_after_resume_keeps_exact_requester_history_and_resume_binding(tmp_path):
    from test_worker_led_strategy import instance
    root = instance(tmp_path)
    requester,_,_ = finish(root,1,'W',[],stage_intent='review',
        files={'model.txt':'exact retained requester; no scientific qualification'})
    route = controller._route(root,1,'W',c.validate(c.workspace(root,1)),requester,None,None)
    assert route.evidence_return == dict(role='W',step=1)
    refs = [dict(step=1,seal_ref=c.record_ref(execution.record(root,1)/'result-identity.json'))]
    cursor = dict(role='A',parents=list(route.parents),instruction=route.instruction,evidence_return=route.evidence_return,
        correction_request=None,author_correction=None,source_refs=refs)
    begin = budget.begin_closeout(root,1,2,'known shared admission boundary',final_only=True,resume_cursor=cursor)
    budget.amend(root,max_tokens=200000,authorization_note='Explicit increase restores the pending exact consultation route.')
    assert controller._resume_final(root,begin)
    resumed = budget.normal_resume(root)
    assert resumed['first_step'] == 3
    job = c.build_job(root,3,'A',list(route.parents),instruction=route.instruction,evidence_return=route.evidence_return)
    assert job['purpose'] == 'worker_consultation'
    assert job['evidence_return'] == dict(role='W', step=1)
    assert [row['path'] for row in job['direct_refs']] == ['inputs/parents/001/conclusion.json']
    assert budget.scientific_steps(root,3) == [1]
    c._validate_inputs(root,c.workspace(root,3),job)
    original = (root/'jobs/003.json').read_bytes()
    changed = dict(job,normal_resume={**resumed,'path':'host-recovery/unapproved.json'})
    with pytest.raises(ValueError,match='Scientific inputs differ from their accepted canonical job'):
        c._validate_inputs(root,c.workspace(root,3),changed)
    assert (root/'jobs/003.json').read_bytes() == original
    assert not (execution.origin(root,3)/'native-intent.json').exists()
