"""Current consumers reuse produced policy and receipts, retaining raw evidence."""
import copy
import json
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    backends, budget, configuration_updates, continuation, contracts as c,
    native_recovery, native_usage, search,
)
from test_automatic_recovery import format_error
from test_final_a_completion_contract import accept, prepare


def forbid_control_loaders(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('A strict consumer reopened produced control facts')

    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(c, 'settings', unexpected)
    monkeypatch.setattr(c, 'native_outcome', unexpected)
    monkeypatch.setattr(configuration_updates, 'for_step', unexpected)
    monkeypatch.setattr(budget, '_events', unexpected)


def search_state():
    return dict(mechanisms={}, authorized=set(), repairs={7: 1}, focus=None,
        order=dict(remaining=[], seen_w=set(), allocation=None, discriminating_work=[]))


def test_interpret_repair_uses_selected_policy_through_real_consumers(tmp_path, monkeypatch):
    state = search_state()
    job = dict(role='H', step=1)
    payload = dict(next_action='experiment', route_reason='Check the retained failure',
        search_plan=dict(commitments=[dict(label='Mechanism', minimal_test='Bounded repair')],
            focus=dict(index=1, action='repair')))
    # The exact historical source is a separate input owner; exercise the
    # interpretation and repair-limit consumers with its already known choice.
    monkeypatch.setattr(search, '_repair_choice',
        lambda *args, **kwargs: dict(status='available', root=7, source={}))
    forbid_control_loaders(monkeypatch)

    limited = search._interpret_from_facts(tmp_path, job, payload, state=state,
        policy=dict(search_policy=dict(max_requested_repairs=1)), selected_inputs=[])
    allowed = search._interpret_from_facts(tmp_path, job, payload, state=state,
        policy=dict(search_policy=dict(max_requested_repairs=2)), selected_inputs=[])

    assert limited['diagnostics'] == [dict(code='search_repair_limit', subject='h001.m1')]
    assert allowed['diagnostics'] == []
    assert allowed['focus'] == dict(mechanism_ref='h001.m1', action='repair')
    assert state == search_state()


@pytest.mark.parametrize('purpose', [None, 'closeout'])
def test_interpret_a_uses_policy_in_real_assessment_context(tmp_path, monkeypatch, purpose):
    state = search_state()
    job = dict(role='A', step=3, purpose=purpose, direct_refs=[], history=dict(nodes=[]))
    forbid_control_loaders(monkeypatch)

    result = search._interpret_from_facts(tmp_path, job, dict(next_action='finish'),
        policy=dict(search_policy={}), state=state, selected_inputs=[])

    assert result['diagnostics'] == []
    assert result['focus'] is None
    assert state == search_state()


@pytest.mark.parametrize('missing', ['policy', 'state'])
def test_interpret_does_not_load_missing_owner_facts(tmp_path, monkeypatch, missing):
    facts = dict(policy=dict(search_policy={}), state=search_state(), selected_inputs=[])
    facts[missing] = None
    forbid_control_loaders(monkeypatch)
    with pytest.raises(ValueError, match='produced policy and state'):
        search._interpret_from_facts(tmp_path, dict(role='A'), {}, **facts)


@pytest.mark.parametrize('job', [dict(role='H', purpose='formal_review'),
    dict(role='H', engineering_repair=True), dict(role='W', purpose='closeout')])
def test_public_interpret_keeps_no_work_early_returns(tmp_path, monkeypatch, job):
    forbid_control_loaders(monkeypatch)
    assert search.interpret(tmp_path, job, {}, state={}) == dict(
        state={}, focus=None, allocation=None, diagnostics=[])


def missing_meter_receipt(record, *, source='automatic'):
    event = (format_error() if source == 'format' else
        dict(type='turn.failed', error=dict(code=(
            'insufficient_quota' if source == 'permanent' else 'rate_limit_exceeded'))))
    raw = (json.dumps(event) + '\n').encode()
    (record / 'native.jsonl').write_bytes(raw)
    limits = dict(max_observed_tokens=100, max_seconds=10)
    task_ref = dict(path='jobs/001.json')
    intent = dict(task_ref=task_ref, budget_slice=dict(role='W', limits=limits))
    original = dict(returncode=7, stop_reason='native_failure_event', cleanup='observed_group_cleaned',
        terminal_observed=False, native_activity_observed=False, thread_id='original',
        native_failure=native_recovery.classify(event), native_format_rejected=source == 'format',
        native_stream=native_usage.stream_binding(record / 'native.jsonl'),
        usage=dict(unknown_steps=[1]), budget_slice=dict(role='W', limits=limits,
            usage_observed=False, dispatches=1, input_tokens=0, cached_input_tokens=0,
            output_tokens=0, observed_tokens=0, advance_seconds=3.))
    return (record, intent, original, None, False), limits, task_ref, raw


@pytest.mark.parametrize('source', ['automatic', 'format', 'permanent'])
def test_recovery_consumes_writer_facts_and_preserves_missing_meter(tmp_path, monkeypatch, source):
    receipt, limits, task_ref, raw = missing_meter_receipt(tmp_path, source=source)
    retained = copy.deepcopy(receipt[1:])
    forbid_control_loaders(monkeypatch)

    result = budget._attempt_consumption_from_facts(tmp_path, dict(role='W', step=1), [receipt],
        policy=dict(native_launcher_argv=['codex']), limits=limits, task_ref=task_ref,
        automatic_recovery=dict(format_fallback=True) if source == 'format' else {})

    assert result == (0, 3.)
    assert receipt[1:] == retained
    assert (tmp_path / 'native.jsonl').read_bytes() == raw
    assert not (tmp_path / 'usage.json').exists()


@pytest.mark.parametrize('source,change,message', [
    ('automatic', 'stream', 'Original native stream changed'),
    ('format', 'binding', 'native_compact_control_facts_unavailable'),
    ('automatic', 'control', 'Native writer control facts are unavailable'),
])
def test_recovery_rejects_changed_or_absent_writer_facts(
        tmp_path, monkeypatch, source, change, message):
    receipt, _, _, _ = missing_meter_receipt(tmp_path, source=source)
    _, intent, outcome, _, _ = receipt
    if change == 'stream':
        (tmp_path / 'native.jsonl').write_text('Changed retained original.\n')
    elif change == 'binding':
        outcome.pop('native_stream')
    else:
        outcome['terminal_observed'] = None
    forbid_control_loaders(monkeypatch)
    consume = (continuation._format_source_from_facts if source == 'format'
        else continuation._automatic_source_from_facts)
    with pytest.raises(ValueError, match=message):
        consume(tmp_path, policy=dict(native_launcher_argv=['codex']), intent=intent, outcome=outcome)


def test_format_recovery_requires_its_produced_rejection(tmp_path, monkeypatch):
    receipt, _, _, _ = missing_meter_receipt(tmp_path, source='format')
    _, intent, outcome, _, _ = receipt
    outcome['native_format_rejected'] = False
    forbid_control_loaders(monkeypatch)
    assert continuation._format_source_from_facts(tmp_path,
        policy=dict(native_launcher_argv=['codex']), intent=intent, outcome=outcome) is None


def test_public_recovery_sources_keep_unsupported_backend_early_returns(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('An unsupported source loaded a receipt')

    monkeypatch.setattr(configuration_updates, 'for_step', lambda *args: {})
    monkeypatch.setattr(backends, 'selected',
        lambda *args: SimpleNamespace(recovery_capability=lambda name: dict(status='unsupported')))
    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(c, 'native_outcome', unexpected)
    assert continuation.format_source(tmp_path, 1, tmp_path) is None
    with pytest.raises(continuation.ResumeUnavailable, match='unsupported_backend'):
        continuation.automatic_source(tmp_path, 1, tmp_path)


def test_public_format_source_rejects_outcome_before_loading_intent(tmp_path, monkeypatch):
    outcome = tmp_path / 'native-outcome.json'
    outcome.write_text('{}')
    reads = []

    def read(path):
        assert path == outcome
        reads.append(path)
        return dict(terminal_observed=True, returncode=0)

    monkeypatch.setattr(configuration_updates, 'for_step',
        lambda *args: dict(native_launcher_argv=['codex']))
    monkeypatch.setattr(c, 'read', read)
    assert continuation.format_source(tmp_path, 1, tmp_path) is None
    assert reads == [outcome]


def test_final_consumers_use_selected_policy_and_same_accepted_head(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    job, intent = accept(root)
    policy = c.settings(root, job)
    events, _ = budget._events(root)
    forbid_control_loaders(monkeypatch)

    report = budget._validate_final_report_inputs_from_facts(root, job, policy=policy, events=events)
    accepted = budget._final_a_return_from_facts(root, job, intent, policy=policy, events=events)
    assert report == job['final_report']
    assert accepted['anchor']['task_ref'] == intent['task_ref']
    assert accepted['step'] == job['step']

    reentry = copy.deepcopy(intent)
    reentry.update(attempt=1, continuation_kind='reentry')
    reentry['budget_slice'].update(previous_tokens=4, previous_seconds=2.,
        advance_seconds=3., boot_id='reattached-original')
    assert budget._final_a_return_from_facts(root, job, reentry,
        policy=policy, events=events) == accepted

    for change in ('task', 'missing_marker', 'attempt', 'repair', 'format'):
        changed = copy.deepcopy(intent)
        if change == 'task':
            changed['task_ref'] = dict(path='jobs/replacement.json')
        elif change == 'missing_marker':
            changed['budget_slice'].pop('final_return')
        elif change == 'attempt':
            changed['attempt'] = 0
        elif change == 'repair':
            changed.update(attempt=1, continuation_kind='repair', output_repair={})
        else:
            changed.update(attempt=0, continuation_kind='automatic_recovery',
                automatic_recovery=dict(format_fallback=True))
        selected_policy = dict(policy, resume_policy=None) if change == 'repair' else policy
        with pytest.raises(ValueError, match='Final A'):
            budget._final_a_return_from_facts(root, job, changed, policy=selected_policy, events=events)


def test_public_final_report_without_inputs_does_not_load_policy(tmp_path, monkeypatch):
    forbid_control_loaders(monkeypatch)
    assert budget.validate_final_report_inputs(tmp_path, {}) is None
