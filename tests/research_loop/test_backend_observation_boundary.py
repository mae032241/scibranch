"""Native control consumes Backend observations without inspecting its meter."""
import copy

import pytest

from scidiscovery.research_loop import backends, contracts as c, native, native_recovery, shared_input_views
from scidiscovery.research_loop.native_usage import MetadataUnavailable
from test_agent_backends import intent, prepare


@pytest.mark.parametrize('turn_id', ['contract-turn', None])
def test_metadata_stop_preserves_original_outcome_with_an_opaque_meter(tmp_path, monkeypatch, turn_id):
    popen_calls = []

    def forbidden_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        pytest.fail('Metadata preflight must stop before native submission')

    monkeypatch.setattr(native.subprocess, 'Popen', forbidden_popen)
    root = prepare(tmp_path, node_budget_policy={
        role: dict(max_observed_tokens=100, max_seconds=10) for role in 'HWAE'})
    record = intent(root)
    original_intent = (record / 'native-intent.json').read_bytes()

    # These mechanical view seams are unrelated to observation ownership and
    # must not require a host mount capability for this pre-submission test.
    monkeypatch.setattr(shared_input_views, 'load_source_facts', lambda *a, **k: {})
    monkeypatch.setattr(shared_input_views, 'selected_code_from_facts', lambda *a, **k: ())
    monkeypatch.setattr(shared_input_views, 'materialize_selected_code', lambda *a, **k: ())
    monkeypatch.setattr(shared_input_views, 'load_wrap_facts', lambda *a, **k: {})
    monkeypatch.setattr(shared_input_views, 'wrap_from_facts', lambda facts, args: args)

    # No .turn, .thread, .snapshot or other collector layout is available to
    # control. Only the existing Backend methods expose this invocation's facts.
    opaque_meter = object()
    observation = dict(source='contract_observation', coverage='unknown',
        reason='token_counter_unreported', has_observation=False, baseline_tokens=None,
        tokens=dict(input_tokens=0, cached_input_tokens=0, output_tokens=0),
        context=dict(status='unavailable'),
        context_windows=dict(coverage='unknown', windows_started=0,
            reason='context_capability_unavailable'))
    if turn_id is not None:
        observation['turn_id'] = turn_id
    metadata_error = MetadataUnavailable('Required metadata capability unavailable before submission')

    def snapshot(self, meter):
        assert meter is opaque_meter
        return copy.deepcopy(observation)

    def configure(self, meter, work, attempt, thread, channel, *, required):
        assert meter is opaque_meter
        raise metadata_error

    monkeypatch.setattr(backends.Protocol, 'meter', lambda *a, **k: opaque_meter)
    monkeypatch.setattr(backends.Protocol, 'snapshot_observation', snapshot)
    monkeypatch.setattr(backends.Protocol, 'configure_observation', configure)

    with pytest.raises(MetadataUnavailable) as raised:
        native.run_native(root, 1)

    assert raised.value is metadata_error
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_backend'] == 'protocol'
    assert outcome['native_started'] is False and outcome['returncode'] is None
    assert outcome['stop_reason'] == 'native_metadata_unsupported'
    assert outcome['terminal_observed'] is False
    assert outcome['metering'] == observation
    assert outcome['technical_diagnostic'] == dict(phase='metadata_delivery',
        code='native_metadata_unsupported', original_call_state='not_submitted',
        thread_bound=False, turn_bound=turn_id is not None,
        next_action='recheck_before_same_task_restart')
    assert outcome['budget_slice']['advance_seconds'] >= 0
    assert outcome['budget_slice']['observed_tokens'] == 0
    assert (record / 'native-intent.json').read_bytes() == original_intent
    assert not (record / 'native-process.json').exists()
    assert not (record / 'continuations').exists()
    assert popen_calls == []


def test_missing_turn_does_not_authorize_replay_of_an_unknown_original_call():
    outcome = dict(native_started=True, returncode=7, terminal_observed=False,
        native_activity_observed=False, thread_id=None,
        stop_reason='native_metadata_unavailable', cleanup='observed_group_cleaned',
        metering=dict(coverage='unknown'),
        native_failure=dict(category='temporary', retry_after_seconds=None, not_accepted=True),
        technical_diagnostic=dict(phase='metadata_delivery', code='native_metadata_unavailable',
            original_call_state='unknown', thread_bound=False, turn_bound=False))
    used = dict(continues=1, retries=1, wait_seconds=30.)
    original_used = dict(used)

    decision = native_recovery.decision(outcome, native_recovery.policy({}), used,
        session_available=False, activity_observed=False)

    assert decision['action'] == 'stop'
    assert decision['reason'] == 'automatic_recovery_original_call_unknown'
    assert decision['counters'] == original_used
    assert used == original_used
    assert not native_recovery.never_started(outcome)
    assert not native_recovery.recoverable_source(outcome)
