"""Ordinary A delivery budget refusal enters the existing single FINAL phase."""

from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, native


def publish(root, job, *, action='finish', tokens=10):
    """Seal deterministic native facts, including the original cleanup evidence."""
    step = job['step']
    record = c.host_record(c.workspace(root, step))
    intent = dict(step=step, task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        budget_slice=budget.accept_node(root, job))
    c.write(record/'native-intent.json', intent)
    payload = dict(summary='Retained bounded fixture assessment',
        scientific_result='No experiment or model was run; retain the missing evidence.',
        limitations=['Deterministic control fixture; no scientific qualification'],
        implementation_selection=None)
    if job['role'] == 'H':
        payload['search_plan'] = None
    if job['role'] == 'A':
        payload.update(candidate_recommendation=None, selection_explanation=None,
            search_review=None, progress_assessment=None, decision_request=None,
            portfolio_review=None)
    payload.update(next_action=action, route_reason='Retain the original bounded conclusion')
    meter = dict(input_tokens=tokens-3, cached_input_tokens=2, output_tokens=3)
    observation = dict(coverage='terminal', source='turn.completed', tokens=meter)
    c.write(record/'science-output.json', payload)
    c.write(record/'usage.json', meter)
    c.write(record/'usage-observation.json', observation)
    outcome = dict(returncode=0, stop_reason=None, native_started=False, cleanup='observed_group_cleaned',
        metering=observation, usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json'))
    accepted = budget.final_a_return(root, job, intent)
    if accepted is not None:
        outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
    c.write(record/'native-outcome.json', outcome)
    c.seal_result(root, step)
    return record


def direct_scene(tmp_path, monkeypatch, *, legacy=False):
    public = tmp_path/'public'
    public.mkdir()
    objective = public/'objective.md'
    objective.write_text('Retain the exact bounded conclusion and missing evidence.')
    adapter = tmp_path/'adapter.py'
    adapter.write_text('# This isolated control fixture never invokes an adapter.\n')
    root = tmp_path/'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=1000, max_seconds=200, max_steps=8,
        model='fixture-no-model', reasoning_effort='high', search_policy=None,
        node_budget_mode='soft',
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=10) for role in 'HWAE'},
        delivery_policy=dict(closeout_a=dict(max_observed_tokens=100, max_seconds=10),
            inflight_tokens=0, cleanup_seconds=0, submit_seconds=1, confirm_seconds=1,
            adapter=dict(adapter=str(adapter), launcher_argv=[str(Path(sys.executable).resolve())],
                config={}, target={}, inputs=[], source_roots=[str(tmp_path)],
                readonly_inputs_version=1, capabilities=[])))
    if legacy:
        settings = c.settings(root)
        settings.pop('final_a_completion_policy')
        c.write(root/'settings.json', settings)
        c.write(root/'settings-identity.json', dict(path='settings.json'))
    c.write(root/'host-started.json', budget.clock_stamp())
    publish(root, c.build_job(root, 1, 'H', []), action='analyze')
    ordinary = c.build_job(root, 2, 'A', [1])
    record = publish(root, ordinary, action='deliver', tokens=1200)
    begin = budget.begin_closeout(root, 2, 3, 'ordinary_a_delivery', authorization_step=2)
    binding = dict(operation_id='fixture-original-operation', decision=dict(step=2))
    monkeypatch.setattr(delivery, 'prepare_binding', lambda *args: binding)
    monkeypatch.setattr(delivery, 'binding', lambda *args: binding)
    return root, begin, record, binding


def test_budget_refusal_upgrades_direct_delivery_to_one_final(tmp_path, monkeypatch):
    root, begin, record, binding = direct_scene(tmp_path, monkeypatch)
    assert begin['mode'] == 'model' and begin['authorization_step'] == 2
    assert budget.final_a_barrier(root) is None
    assert native.usage(root)['accounted_tokens'] == 1210
    assert budget.snapshot(root)['max_tokens'] == 1000
    assert not (root/'jobs/003.json').exists()
    originals = {path: path.read_bytes() for path in (
        root/'settings.json', root/'host-started.json', root/'jobs/002.json', record/'native-intent.json',
        record/'native-outcome.json', record/'usage.json', record/'result-identity.json',
        root/'control-ledger'/f'{begin["seq"]:06d}.json')}
    with pytest.raises(budget.NodeBudgetUnavailable, match='closeout_budget_unavailable'):
        budget.accept_action(root, 'submit', binding['operation_id'], binding['decision'], binding, 1)
    assert budget.closeout_state(root)['actions'] == []
    executed = []

    def complete_final(selected, step):
        job = c.read(selected/'jobs'/f'{step:03d}.json')
        assert job['role'] == 'A' and job['final_only']
        executed.append(step)
        publish(selected, job)

    def forbidden(*args, **kwargs):
        pytest.fail('The refused original submit cannot invoke an adapter')

    monkeypatch.setattr(native, 'execute_native', complete_final)
    monkeypatch.setattr(delivery, 'invoke', forbidden)
    result = controller.closeout(root)
    assert result['status'] == 'final_a_completed', result
    final = budget.closeout_state(root)['begin']
    final_step = 3
    assert final['mode'] == 'final' and final['source_cutoff'] == begin['source_cutoff']
    assert final['first_step'] == final_step
    assert final['stop_reason'] == 'original_delivery_budget_unavailable'
    assert executed == [final_step]
    assert budget.final_a_barrier(root)['step'] == final_step
    assert budget.closeout_state(root)['actions'] == []
    assert not (root/'jobs'/f'{final_step+1:03d}.json').exists()
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert budget.snapshot(root)['max_tokens'] == 1000
    assert native.usage(root)['accounted_tokens'] == 1220
    events, _ = budget._events(root)
    phases = [row for row in events if row['kind'] == 'closeout_begin']
    claims = [row for row in events if row['kind'] == 'final_a_return']
    assert [row['mode'] for row in phases] == ['model', 'final']
    assert len(claims) == 1
    assert not any(row['kind'] == 'budget_amend' for row in events)
    assert controller.closeout(root)['status'] == 'final_a_completed'
    repeated, _ = budget._events(root)
    assert [row for row in repeated if row['kind'] == 'closeout_begin'] == phases
    assert [row for row in repeated if row['kind'] == 'final_a_return'] == claims
    assert executed == [final_step]
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_direct_delivery_without_final_policy_keeps_budget_unavailable(tmp_path, monkeypatch):
    root, begin, _, _ = direct_scene(tmp_path, monkeypatch, legacy=True)

    def forbidden(*args, **kwargs):
        pytest.fail('Historical settings grant no FINAL author')

    monkeypatch.setattr(native, 'execute_native', forbidden)
    result = controller.closeout(root)
    assert result['status'] == 'closeout_budget_unavailable'
    assert budget.closeout_state(root)['begin'] == begin
    assert budget.final_a_barrier(root) is None
    assert not (root/'jobs/003.json').exists()
    assert budget.closeout_state(root)['actions'] == []


@pytest.mark.parametrize('outcome', [
    dict(status='delivery_confirmed'),
    dict(status='delivery_reselect', reason='candidate_rejected'),
    dict(status='delivery_unknown', reason='Original operation remains unverified'),
    dict(status='closeout_budget_unavailable'),
    dict(status='closeout_unresolved', reason='candidate_originals_unavailable'),
])
def test_other_direct_delivery_outcomes_do_not_authorize_final(tmp_path, monkeypatch, outcome):
    begin = dict(source_cutoff=10, first_step=11, authorization_step=10)
    job = dict(step=10, role='A')
    result = dict(next_action='deliver')
    monkeypatch.setattr(c, 'read', lambda path: job)
    monkeypatch.setattr(c, 'workspace', lambda root, step: tmp_path)
    monkeypatch.setattr(c, 'validate', lambda work: result)
    monkeypatch.setattr(delivery, 'prepare_binding', lambda *args: None)
    monkeypatch.setattr(controller, '_run_delivery_actions', lambda *args: outcome)
    monkeypatch.setattr(controller, '_closeout_result',
        lambda root, status, **fields: dict(status=status, **fields))

    def forbidden(*args, **kwargs):
        pytest.fail('This direct delivery outcome cannot authorize another scientific task')

    monkeypatch.setattr(controller, '_start_budget_closeout', forbidden)
    actual = controller._run_direct_delivery(tmp_path, begin)
    if outcome['status'] == 'delivery_reselect':
        assert actual == dict(status='delivery_rejected', reason='candidate_rejected', step=10)
    else:
        assert actual is outcome


@pytest.mark.parametrize('error,status', [
    (delivery.PreparationUnavailable('qualification_missing'), 'closeout_preparation_unavailable'),
    (delivery.DeliveryUnresolved('candidate_originals_unavailable'), 'closeout_unresolved'),
])
def test_direct_delivery_preparation_failure_does_not_authorize_final(tmp_path, monkeypatch, error, status):
    begin = dict(source_cutoff=10, first_step=11, authorization_step=10)
    monkeypatch.setattr(c, 'read', lambda path: dict(step=10, role='A'))
    monkeypatch.setattr(c, 'workspace', lambda root, step: tmp_path)
    monkeypatch.setattr(c, 'validate', lambda work: dict(next_action='deliver'))
    monkeypatch.setattr(controller, '_closeout_result',
        lambda root, status, **fields: dict(status=status, **fields))

    def unavailable(*args):
        raise error

    def forbidden(*args, **kwargs):
        pytest.fail('Preparation failure cannot authorize another scientific task')

    monkeypatch.setattr(delivery, 'prepare_binding', unavailable)
    monkeypatch.setattr(controller, '_start_budget_closeout', forbidden)
    monkeypatch.setattr(controller, '_run_delivery_actions', forbidden)
    assert controller._run_direct_delivery(tmp_path, begin) == dict(
        status=status, reason=error.code, step=10)
