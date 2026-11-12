"""Budget stops retain prepared originals and the single final response."""
import json
import time

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, role_share
from test_final_a_completion import external_scene, instance
from test_history_handoff import finish


def expire_original_clock(root):
    started = root/'host-started.json'
    if not started.exists():
        c.write(started, budget.clock_stamp())
    original = started.read_bytes()
    budget.transition(root, 'advance', 'begin original active interval')
    remaining = budget.snapshot(root)['seconds_remaining']
    assert 0 <= remaining <= 2
    time.sleep(remaining+.02)
    budget.transition(root, 'advance', 'known original active time consumed')
    assert budget.snapshot(root)['seconds_remaining'] == 0
    assert started.read_bytes() == original


@pytest.mark.process_e2e
def test_adapter_off_final_survives_exhausted_shared_wall_and_steps(tmp_path):
    root = instance(tmp_path, max_steps=1, max_seconds=2)
    expire_original_clock(root)
    originals = {path: path.read_bytes() for path in (root/'settings.json', root/'host-started.json',
        execution.record(root, 1)/'usage.json', execution.record(root, 1)/'result-identity.json')}
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['step'] == 2
    assert budget.effective_max_steps(root) == 1
    assert budget.snapshot(root)['consumed_seconds'] > budget.snapshot(root)['max_seconds']
    assert native.usage(root)['observed_tokens'] == 280944
    hint = c.read(c.workspace(root, 2)/'work/budget-status.json')
    assert hint['current_work_active_seconds_available'] > 0
    assert budget.closeout_state(root)['actions'] == []
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert controller.run(root)['status'] == 'final_a_completed'
    assert (c.workspace(root, 2)/'work/launches').read_text() == 'A\n'
    assert not (root/'jobs/003.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('boundary', ['tokens', 'wall'])
def test_prepared_ordinary_a_is_retained_before_an_explicit_final_assignment(tmp_path, boundary):
    root = instance(tmp_path, max_tokens=5 if boundary == 'tokens' else 1_000_000, max_steps=6,
        max_seconds=2 if boundary == 'wall' else 90)
    job = c.build_job(root, 2, 'A', [1], instruction='Exact scientific question')
    job_path = root/'jobs/002.json';original = job_path.read_bytes()
    assert job.get('purpose') is None and not (execution.record(root, 2)/'native-intent.json').exists()
    if boundary == 'wall':
        expire_original_clock(root)
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    marker = budget.final_a_barrier(root)
    assert marker['basis'] == 'final_state' and marker['step'] == 3
    assert job_path.read_bytes() == original
    assert not (execution.record(root, 2)/'native-intent.json').exists()
    assert budget.closeout_state(root)['actions'] == []
    assert controller.run(root)['status'] == 'final_a_completed'
    assert job_path.read_bytes() == original and not (root/'jobs/004.json').exists()


def test_role_share_stop_preserves_worker_consultation_and_originals(tmp_path, monkeypatch):
    original_prepare = controller.prepare
    def prepare(*args, **kwargs):
        kwargs['role_budget_policy'] = dict(version=1, min_w_share=.99, max_thinking_share=.00001,
            thinking_node_target_tokens=100000)
        return original_prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', prepare)
    root = instance(tmp_path, max_steps=6, route='experiment')
    job, work, _ = finish(root, 2, 'W', [1], stage_intent='review')
    result = c.validate(work)
    usage = native.usage(root)
    assert budget.snapshot(root)['max_tokens']-usage['observed_tokens'] > 900000
    assert role_share.usage(root, 2)['thinking'] == 10
    assert role_share.reason(root, 2, job, result, 'A',
        evidence_return=dict(role='W', step=2)) == 'role_share_thinking_reached'
    paths = [root/'settings.json']
    for step in (1, 2):
        record = execution.record(root, step)
        paths.extend((root/f'jobs/{step:03d}.json', record/'native-intent.json', record/'science-output.json',
            record/'usage.json', record/'result-identity.json'))
    originals = {path: path.read_bytes() for path in paths}
    for _ in range(2):
        result = controller.run(root)
        assert result['status'] == 'role_admission_refused', json.dumps(result, indent=2)
        assert result['reason'] == 'role_share_thinking_reached' and result['next_role'] == 'A'
        assert budget.final_a_barrier(root) is None
        assert budget.closeout_state(root)['begin'] is None
        assert budget.closeout_state(root)['actions'] == []
        assert native.usage(root) == usage
        assert all(path.read_bytes() == raw for path, raw in originals.items())
        assert not (root/'jobs/003.json').exists()
        assert not (root/'native-evidence/003/native-intent.json').exists()


@pytest.mark.process_e2e
def test_postpreflight_budget_race_preserves_unused_w_and_returns_final_report(tmp_path, monkeypatch):
    root = instance(tmp_path, max_steps=6, route='experiment')
    original_build = c.build_job
    original_limit = controller._launch_limit
    unused = []
    def build(*args, **kwargs):
        job = original_build(*args, **kwargs)
        if job['role'] == 'W':
            unused.append((root/'jobs/002.json').read_bytes())
        return job
    def raced_limit(root, settings):
        return 'wall_limit' if unused else original_limit(root, settings)
    monkeypatch.setattr(c, 'build_job', build)
    monkeypatch.setattr(controller, '_launch_limit', raced_limit)
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['step'] == 3
    assert (root/'jobs/002.json').read_bytes() == unused[0]
    assert not (execution.record(root, 2)/'native-intent.json').exists()
    report = c.read(root/'jobs/003.json')['final_report']
    assert report['source_cutoff'] == 1 and report['stopped_original']['records'] == {}
    assert [ref['path'] for ref in c.read(root/'jobs/003.json')['direct_refs']] == ['inputs/parents/001/conclusion.json']
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_a_return_consumed'):
        native.execute_native(root, 2)
    assert not (execution.record(root, 2)/'native-intent.json').exists()


@pytest.mark.process_e2e
def test_confirm_budget_denial_after_submit_preserves_operation_until_reentry(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    accept = budget.accept_action
    def deny_confirmation(root, kind, *args, **kwargs):
        if kind == 'confirm':
            raise budget.NodeBudgetUnavailable('closeout_budget_unavailable')
        return accept(root, kind, *args, **kwargs)
    monkeypatch.setattr(budget, 'accept_action', deny_confirmation)
    for _ in range(2):
        result = controller.closeout(root)
        assert result['status'] == 'delivery_unknown'
        assert result.get('reason') != 'final_author_required'
        assert budget.final_a_barrier(root)['step'] == 3
        assert not (root/'jobs/004.json').exists()
        actions = budget.closeout_state(root)['actions']
        assert [row['intent']['action_kind'] for row in actions] == ['submit']
        assert actions[0]['outcome']['result']['status'] == 'confirmed'
    original = actions[0]
    record = execution.record(root, 3)
    originals = {path: path.read_bytes() for path in (record/'native-intent.json',
        record/'native-outcome.json', record/'result-identity.json')}
    monkeypatch.setattr(budget, 'accept_action', accept)
    assert controller.closeout(root)['status'] == 'delivery_confirmed'
    actions = budget.closeout_state(root)['actions']
    assert actions[0] == original
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    assert all(row['intent']['operation_id'] == original['intent']['operation_id'] for row in actions)
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not (root/'jobs/004.json').exists()
