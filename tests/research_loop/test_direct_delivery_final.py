"""Sealed ordinary A choices keep their bounded host delivery without a new A."""

from unittest.mock import patch

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, delivery, handoff_state, native
from test_history_handoff import selection_explanation


def legacy_scene(tmp_path):
    from test_closeout_protocol import scene
    original = controller.prepare
    with patch.object(controller, 'prepare', lambda *args, **kwargs:
            original(*args, **kwargs, research_strategy_version=None)):
        return scene(tmp_path, [])


def publish(root, job, *, action='finish', tokens=10, recommendation=None):
    """Seal deterministic native facts, including the original cleanup evidence."""
    step = job['step']
    record = c.host_record(c.workspace(root, step))
    intent = dict(step=step, task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        budget_slice=budget.accept_node(root, job))
    c.write(record/'native-intent.json', intent)
    accepted_budget.accept_native(root, step, record, intent)
    payload = dict(summary='Retained bounded fixture assessment',
        scientific_result='No experiment or model was run; retain the missing evidence.',
        limitations=['Deterministic control fixture; no scientific qualification'],
        implementation_selection=None)
    if job['role'] == 'H':
        payload['search_plan'] = None
    if job['role'] == 'A':
        payload.update(candidate_recommendation=recommendation,
            selection_explanation=selection_explanation() if recommendation is not None else None,
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


@pytest.mark.process_e2e
@pytest.mark.parametrize('boundary', ['tokens', 'wall'])
def test_sealed_overbudget_a_delivers_its_exact_choice_without_final(tmp_path, monkeypatch, boundary):
    root = legacy_scene(tmp_path)
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Original ordinary A transport fixture')
    job = c.build_job(root, 3, 'A', [1, 2])
    record = publish(root, job, action='deliver', tokens=120000, recommendation='run-1')
    if boundary == 'wall':
        stamp = budget.clock_stamp()
        later = dict(stamp, at_unix=stamp['at_unix']+1000, at_boottime=stamp['at_boottime']+1000)
        monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(later))
        assert budget.snapshot(root)['seconds_remaining'] == 0
    originals = {path: path.read_bytes() for path in (
        root/'settings.json', root/'host-started.json', root/'jobs/003.json', record/'native-intent.json',
        record/'native-outcome.json', record/'science-output.json', record/'usage.json', record/'result-identity.json')}
    usage = native.usage(root)
    assert usage['accounted_tokens'] == 120020
    assert usage['accounted_tokens'] > budget.snapshot(root)['max_tokens']
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('The accepted A choice needs no new native call'))
    result = controller.run(root)
    assert result['status'] == 'delivery_confirmed', result
    assert result['last_confirmed']['candidate_id'] == 'run-1'
    assert (root/'submission/model.pkl').read_bytes() == (root/'runs/model.bin').read_bytes()
    state = budget.closeout_state(root)
    assert state['begin']['mode'] == 'model' and state['begin']['authorization_step'] == 3
    assert [row['intent']['action_kind'] for row in state['actions']] == ['submit', 'confirm']
    assert all(row['intent']['decision']['step'] == 3 and row['intent']['binding']['candidate_id'] == 'run-1'
        and 'final_return' not in row['intent'] for row in state['actions'])
    assert len({row['intent']['operation_id'] for row in state['actions']}) == 1
    assert budget.final_a_barrier(root) is None and not (root/'jobs/004.json').exists()
    assert native.usage(root) == usage
    events, _ = budget.audit_events(root)
    assert not any(row['kind'] in {'budget_amend', 'final_a_return'} for row in events)
    assert controller.run(root)['status'] == controller.closeout(root)['status'] == 'delivery_confirmed'
    assert budget.closeout_state(root)['actions'] == state['actions']
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    last = state['actions'][-1]['intent']
    policy = c.settings(root)['delivery_policy']
    for kind in ('submit', 'confirm'):
        with pytest.raises(budget.NodeBudgetUnavailable, match='direct_delivery_already_confirmed'):
            budget.accept_action(root, kind, last['operation_id'], last['decision'], last['binding'], policy[kind+'_seconds'])
    assert budget.closeout_state(root)['actions'] == state['actions']
    events, parent = budget.audit_events(root)
    sequence = events[-1]['seq'] + 1
    forged = dict(last, seq=sequence, parent_ref=parent, action_id=f'action-{sequence:06d}',
        action_kind='confirm', max_seconds=policy['confirm_seconds'],
        advance_seconds=budget.snapshot(root)['advance_seconds'], **budget.clock_stamp())
    c.write(root/'control-ledger'/f'{sequence:06d}.json', forged)
    with pytest.raises(ValueError, match='Direct delivery exceeds its exact bounded operation'):
        budget.audit_events(root)


@pytest.mark.process_e2e
@pytest.mark.parametrize('recommendation', [None, 'run-1'])
def test_expired_sealed_a_can_finish_with_gaps_without_delivery_or_final(tmp_path, monkeypatch, recommendation):
    root = legacy_scene(tmp_path)
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Original ordinary A finish fixture')
    record = publish(root, c.build_job(root, 3, 'A', [1, 2]),
        action='finish', recommendation=recommendation)
    stamp = budget.clock_stamp()
    later = dict(stamp, at_unix=stamp['at_unix']+1000, at_boottime=stamp['at_boottime']+1000)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(later))
    assert budget.snapshot(root)['seconds_remaining'] == 0
    originals = {path: path.read_bytes() for path in (
        root/'settings.json', root/'jobs/003.json', record/'native-intent.json',
        record/'science-output.json', record/'native-outcome.json', record/'result-identity.json')}
    usage = native.usage(root)
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('The accepted A finish needs no new native call'))
    monkeypatch.setattr(delivery, 'invoke', lambda *args, **kwargs: pytest.fail('Finish does not authorize delivery'))
    for _ in range(2):
        result = controller.run(root)
        assert result['status'] == 'finish' and result['last_step'] == 3
        assert budget.closeout_state(root)['begin'] is None and not budget.closeout_state(root)['actions']
        assert budget.final_a_barrier(root) is None and not (root/'jobs/004.json').exists()
        assert not (root/'submission/model.pkl').exists() and native.usage(root) == usage
        assert all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.process_e2e
def test_unknown_overbudget_a_delivery_reconciles_the_same_original(tmp_path, monkeypatch):
    root = legacy_scene(tmp_path)
    c.write(root/'host-started.json', budget.clock_stamp())
    record = publish(root, c.build_job(root, 3, 'A', [1, 2]),
        action='deliver', tokens=120000, recommendation='run-1')
    original = (record/'result-identity.json').read_bytes()
    usage = native.usage(root)
    calls = []
    invoke = delivery.invoke

    def uncertain(root, intent, remaining, **kwargs):
        calls.append((intent['action_kind'], intent['operation_id']))
        result = invoke(root, intent, remaining, **kwargs)
        if intent['action_kind'] == 'submit':
            assert result['status'] == 'confirmed'
            return delivery._result(intent['binding'], 'unknown', 'Fixture lost the original submission receipt')
        return result

    monkeypatch.setattr(delivery, 'invoke', uncertain)
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('Reconciliation cannot start another author'))
    assert controller.run(root)['status'] == 'delivery_unknown'
    submit = budget.closeout_state(root)['actions'][0]
    assert submit['outcome']['result']['status'] == 'unknown'
    intent = submit['intent']
    changed = dict(intent['binding'], candidate_id='run-2')
    seconds = c.settings(root)['delivery_policy']['confirm_seconds']
    with pytest.raises(ValueError, match='Direct delivery exceeds its exact bounded operation'):
        budget.accept_action(root, 'confirm', intent['operation_id'], intent['decision'], changed, seconds)
    with pytest.raises(ValueError, match='Direct delivery exceeds its exact bounded operation'):
        budget.accept_action(root, 'confirm', intent['operation_id'], intent['decision'], intent['binding'], seconds+1)
    assert budget.closeout_state(root)['actions'] == [submit]
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert calls == [('submit', submit['intent']['operation_id']), ('confirm', submit['intent']['operation_id'])]
    actions = budget.closeout_state(root)['actions']
    assert actions[0] == submit
    assert actions[1]['intent']['binding'] == submit['intent']['binding']
    assert controller.run(root)['status'] == 'delivery_confirmed' and len(calls) == 2
    assert native.usage(root) == usage and (record/'result-identity.json').read_bytes() == original
    assert budget.final_a_barrier(root) is None and not (root/'jobs/004.json').exists()


@pytest.mark.process_e2e
def test_unknown_direct_confirmation_exhausts_the_original_host_allowance(tmp_path, monkeypatch):
    from test_closeout_protocol import scene
    root = legacy_scene(tmp_path)
    c.write(root/'host-started.json', budget.clock_stamp())
    record = publish(root, c.build_job(root, 3, 'A', [1, 2]),
        action='deliver', tokens=120000, recommendation='run-1')
    original = (record/'result-identity.json').read_bytes()
    usage = native.usage(root)
    calls = []
    invoke = delivery.invoke

    def lost_receipt(root, intent, remaining, **kwargs):
        calls.append((intent['action_kind'], intent['operation_id']))
        result = invoke(root, intent, remaining, **kwargs)
        assert result['status'] == 'confirmed'
        return delivery._result(intent['binding'], 'unknown', 'Fixture lost this original operation receipt')

    monkeypatch.setattr(delivery, 'invoke', lost_receipt)
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('Unknown delivery cannot grant another author'))
    assert controller.run(root)['status'] == 'delivery_unknown'
    assert controller.run(root)['status'] == 'delivery_unknown'
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    operation = actions[0]['intent']['operation_id']
    assert calls == [('submit', operation), ('confirm', operation)]
    assert all(row['outcome']['result']['status'] == 'unknown' for row in actions)
    for command in (controller.run, controller.closeout, controller.run):
        result = command(root)
        assert result['status'] == 'delivery_unknown'
        assert 'allowance exhausted' in result['reason']
        assert budget.closeout_state(root)['actions'] == actions and len(calls) == 2
    assert native.usage(root) == usage and (record/'result-identity.json').read_bytes() == original
    assert budget.final_a_barrier(root) is None and not (root/'jobs/004.json').exists()
    last = actions[-1]['intent']
    with pytest.raises(budget.NodeBudgetUnavailable, match='direct_delivery_reserve_exhausted'):
        budget.accept_action(root, 'confirm', operation, last['decision'], last['binding'], last['max_seconds'])

    # A forged third protected intent must also fail the independent control replay.
    events, parent = budget.audit_events(root)
    sequence = events[-1]['seq'] + 1
    forged = dict(last, seq=sequence, parent_ref=parent, action_id=f'action-{sequence:06d}',
        advance_seconds=budget.snapshot(root)['advance_seconds'], **budget.clock_stamp())
    c.write(root/'control-ledger'/f'{sequence:06d}.json', forged)
    with pytest.raises(ValueError, match='Direct delivery exceeds its exact bounded operation'):
        budget.audit_events(root)


@pytest.mark.process_e2e
def test_overbudget_a_without_exact_choice_never_invents_delivery_or_final(tmp_path, monkeypatch):
    from test_closeout_protocol import scene
    root = legacy_scene(tmp_path)
    c.write(root/'host-started.json', budget.clock_stamp())
    record = publish(root, c.build_job(root, 3, 'A', [1, 2]), action='deliver', tokens=120000)
    original = (record/'science-output.json').read_bytes()
    monkeypatch.setattr(native, 'execute_native', lambda *args: pytest.fail('Missing choice cannot grant another author'))
    monkeypatch.setattr(delivery, 'invoke', lambda *args, **kwargs: pytest.fail('Missing choice cannot invoke an adapter'))
    for _ in range(2):
        assert controller.run(root)['status'] == 'closeout_unresolved'
        assert not budget.closeout_state(root)['actions'] and budget.final_a_barrier(root) is None
        assert not (root/'jobs/004.json').exists() and not (root/'submission/model.pkl').exists()
        assert (record/'science-output.json').read_bytes() == original


@pytest.mark.parametrize('action', ['finish', 'wait', 'continue_w'])
def test_direct_delivery_requires_the_accepted_terminal_delivery_choice(tmp_path, monkeypatch, action):
    begin = dict(source_cutoff=10, first_step=11, authorization_step=10)
    monkeypatch.setattr(c, 'read_job', lambda path: dict(step=10, role='A'))
    monkeypatch.setattr(handoff_state, 'read_sealed', lambda root, step: dict(next_action=action))
    monkeypatch.setattr(delivery, 'prepare_binding', lambda *args: pytest.fail('No delivery was authorized'))
    monkeypatch.setattr(controller, '_start_budget_closeout', lambda *args: pytest.fail('No new author was authorized'))
    with pytest.raises(ValueError, match='Original direct delivery authorization changed'):
        controller._run_direct_delivery(tmp_path, begin)
