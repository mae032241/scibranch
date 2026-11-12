"""Compact native hook facts; behavior originals are opaque and never observed."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import backends, budget_notice
from scidiscovery.research_loop.native_usage import SessionUsage, terminal_attempt_tokens, validate_session_binding

THREAD = '01a13eb3-81b8-74e2-97f7-84e8f170ab5d'


def metadata(tmp_path, *, attempt=0, session=None, context_state=None):
    home, work = tmp_path / 'home', tmp_path / 'work'
    (work / 'work').mkdir(parents=True, exist_ok=True)
    path = home / 'sessions' / 'opaque.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_bytes(b'opaque native original\n')
    receipt = budget_notice.start_metadata(work, attempt, session=THREAD if session else None)
    reader = SessionUsage(home, work, session=session, context_state=context_state)
    reader.configure(receipt, attempt, available=True)
    reader.bind_thread(THREAD)
    return reader, home, work, path, receipt


def report(work, path, *, attempt=0, kind='PostModelRequest', **extra):
    event = dict(session_id=THREAD, cwd=str(work), transcript_path=str(path), hook_event_name=kind)
    event.update(dict(trigger='auto') if kind == 'PostCompact' else dict(status='completed',
        token_usage=dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)))
    event.update(extra)
    budget_notice.record_metadata(work, attempt, event)


def test_hook_usage_is_invocation_local_and_repeated_snapshots_are_not_readded(tmp_path):
    reader, _, work, path, _ = metadata(tmp_path)
    report(work, path)
    first = reader.poll()
    assert first['tokens'] == dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    assert first['coverage'] == 'live' and first['has_observation']
    assert first['context_windows']['windows_started'] == 1
    assert reader.poll() == first
    report(work, path, token_usage=dict(input_tokens=30, cached_input_tokens=10, output_tokens=2))
    assert reader.poll()['tokens'] == dict(input_tokens=130, cached_input_tokens=90, output_tokens=7)


def test_huge_behavior_originals_are_never_opened_or_parsed(tmp_path, monkeypatch):
    reader, _, work, path, _ = metadata(tmp_path)
    # Includes escaped fake metadata, nested arrays and an unfinished native line.
    body = dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='CommandExecution',
        aggregated_output='"type":"ContextCompaction"\\n' * 50000, arguments=[['synthetic']])))
    path.write_bytes(json.dumps(body).encode())
    report(work, path)
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('Observer must not open native originals'))
    value = reader.poll()
    assert value['coverage'] == value['context_windows']['coverage'] == 'live'
    assert value['context_windows']['windows_started'] == 1
    assert value['tokens']['input_tokens'] == 100


def test_completed_compactions_count_once_per_delivery_across_poll_and_resume(tmp_path):
    reader, home, work, path, _ = metadata(tmp_path)
    report(work, path)
    report(work, path, kind='PostCompact')
    prior = reader.poll()
    assert prior['context_windows']['windows_started'] == 2
    assert reader.poll() == prior
    session = prior['session_binding']
    resumed, _, _, _, _ = metadata(tmp_path, attempt=1, session=session, context_state=prior['context_windows'])
    report(work, path, attempt=1, token_usage=dict(input_tokens=20, cached_input_tokens=3, output_tokens=2))
    assert resumed.poll()['tokens']['input_tokens'] == 20
    assert resumed.poll()['context_windows']['windows_started'] == 2
    report(work, path, attempt=1, kind='PostCompact', trigger='manual')
    assert resumed.poll()['context_windows']['windows_started'] == 3
    assert resumed.poll()['context_windows']['coverage'] == 'live'
    assert resumed.home == home


def test_new_native_context_retains_consumption_and_adds_one_initial_window(tmp_path):
    reader, _, work, path, _ = metadata(tmp_path)
    report(work, path, kind='PostCompact')
    prior = reader.poll()['context_windows']
    fresh, _, new_work, new_path, _ = metadata(tmp_path / 'fresh', context_state=prior)
    report(new_work, new_path)
    assert fresh.poll()['context_windows']['windows_started'] == 3


@pytest.mark.parametrize('field,value', [('session_id', 'other'), ('cwd', '/other'),
    ('trigger', 'not-completed')])
def test_malformed_relevant_hook_metadata_fails_closed(tmp_path, field, value):
    reader, _, work, path, _ = metadata(tmp_path)
    report(work, path, kind='PostCompact', **{field:value})
    observed = reader.poll()
    assert observed['context_windows']['coverage'] == 'unknown'
    assert observed['context_windows']['windows_started'] == 1
    assert observed['session_binding'] is None


def test_missing_token_dimension_remains_unknown_without_cached_input_guess(tmp_path):
    reader, _, work, path, _ = metadata(tmp_path)
    report(work, path, token_usage=dict(input_tokens=10, output_tokens=2))
    observed = reader.poll()
    assert observed['coverage'] == 'unknown' and not observed['has_observation']
    assert observed['context_windows']['coverage'] == 'live'
    assert observed['calls'] == dict(completed=1, failed=0, cancelled=0)
    assert observed['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)


def test_compact_receipt_regression_and_attempt_reuse_are_rejected(tmp_path):
    reader, _, work, path, receipt = metadata(tmp_path)
    report(work, path, kind='PostCompact')
    assert reader.poll()['context_windows']['windows_started'] == 2
    with pytest.raises(ValueError, match='already exists'): budget_notice.start_metadata(work, 0)
    value = budget_notice.read_metadata(receipt); value['compactions'] = 0
    budget_notice._publish_metadata(receipt, value)
    assert reader.poll()['context_windows']['coverage'] == 'unknown'
    assert reader.snapshot()['context_windows']['windows_started'] == 2


def test_exact_session_inode_workspace_and_append_only_size_remain_bound(tmp_path):
    reader, home, work, path, _ = metadata(tmp_path)
    report(work, path)
    binding = reader.poll()['session_binding']
    assert validate_session_binding(binding, THREAD, work, home) == binding
    with pytest.raises(ValueError): validate_session_binding(binding, THREAD, tmp_path / 'other', home)
    path.write_bytes(b'')
    with pytest.raises(ValueError, match='truncated'): validate_session_binding(binding, THREAD, work, home)
    replacement = path.with_name('replacement'); replacement.write_bytes(b'opaque native original\n')
    path.unlink(); replacement.rename(path)
    assert reader.poll()['context_windows']['coverage'] == 'unknown'


def test_missing_compact_producer_is_explicit_unknown_not_a_fabricated_window(tmp_path):
    reader = SessionUsage(tmp_path / 'home', tmp_path / 'work')
    reader.bind_thread(THREAD)
    reader.configure(tmp_path / 'missing', 0, available=False)
    assert reader.finish_context()['context_windows']['coverage'] == 'unknown'
    assert reader.snapshot()['context_windows']['reason'] == 'context_observation_unavailable'


def test_legacy_unknown_context_is_not_refunded_by_a_new_receipt(tmp_path):
    prior = dict(coverage='unknown', windows_started=2, reason='context_history_unavailable')
    reader, _, work, path, _ = metadata(tmp_path, context_state=prior)
    report(work, path)
    value = reader.poll()['context_windows']
    assert value['windows_started'] == 3 and value['coverage'] == 'unknown'


def test_terminal_meter_is_not_subtracted_for_invocation_local_hook_usage():
    terminal = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    observed = dict(coverage='live', has_observation=True, baseline_tokens=dict.fromkeys(terminal, 0), tokens=terminal)
    assert terminal_attempt_tokens(terminal, observed) == terminal


def test_resumed_terminal_subtraction_requires_exact_independent_hook_sum(tmp_path):
    reader, _, work, path, _ = metadata(tmp_path)
    report(work, path)
    prior = reader.poll()
    baseline = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    prior['context_windows']['cursor']['terminal_tokens'] = baseline
    resumed, _, _, _, _ = metadata(tmp_path, attempt=1, session=prior['session_binding'],
        context_state=prior['context_windows'])
    report(work, path, attempt=1, token_usage=dict(input_tokens=20, cached_input_tokens=3, output_tokens=2))
    observation = resumed.poll()
    terminal = dict(input_tokens=120, cached_input_tokens=83, output_tokens=7)
    assert terminal_attempt_tokens(terminal, observation) == dict(input_tokens=20, cached_input_tokens=3, output_tokens=2)
    assert terminal_attempt_tokens(dict(terminal, output_tokens=8), observation)['input_tokens'] == 120


@pytest.mark.parametrize('body_first', [False, True])
def test_stdout_selector_skips_command_bodies_and_retains_compact_controls(tmp_path, body_first, monkeypatch):
    command = dict(id='synthetic', type='command_execution',
        aggregated_output='\\"type\\":\\"turn.completed\\"' * 45000, nested=[['synthetic']])
    if body_first: command = dict(aggregated_output=command['aggregated_output'], **{k:v for k,v in command.items() if k!='aggregated_output'})
    line = json.dumps(dict(type='item.completed', item=command)).encode() + b'\n'
    control = json.dumps(dict(type='turn.completed', usage=dict(input_tokens=10, output_tokens=2))).encode() + b'\n'
    decode = backends.json.loads
    def compact_only(data, *args, **kwargs):
        assert len(data) <= backends.CodexEvents.VIEW_BYTES
        assert b'aggregated_output' not in data
        return decode(data, *args, **kwargs)
    monkeypatch.setattr(backends.json, 'loads', compact_only)
    selector = backends.CodexEvents(); events = []
    combined = line + control
    for start in range(0, len(combined), 4096):
        events.extend(selector.feed(combined[start:start+4096]))
        assert len(selector.pending) <= selector.VIEW_BYTES
    assert events == [dict(type='item.completed'), decode(control)]


def test_stdout_selector_retains_public_assistant_text_and_final_control_without_newline():
    selector = backends.CodexEvents()
    message = dict(type='item.completed', item=dict(id='item_1', type='agent_message', text='Synthetic model progress'))
    terminal = dict(type='turn.completed', usage=dict(input_tokens=12, output_tokens=2))
    data = json.dumps(message).encode() + b'\n' + json.dumps(terminal).encode()
    events = []
    for start in range(0, len(data), 7): events.extend(selector.feed(data[start:start+7]))
    events.extend(selector.feed(b'', final=True))
    assert events == [message, terminal]


def test_qualified_inventory_without_callbacks_stops_once_at_terminal(tmp_path):
    from scidiscovery.research_loop.native_usage import metadata_stop
    reader, _, _, _, _ = metadata(tmp_path)
    initial = reader.poll()
    assert initial['context_windows']['coverage'] == 'awaiting'
    assert initial['metadata_delivery']['acknowledged'] == 0
    assert metadata_stop(initial, required=True) is None
    terminal = reader.finish_context()
    assert metadata_stop(terminal, required=True, terminal=True) == 'native_metadata_unavailable'
    assert terminal['context_windows']['coverage'] == 'unknown'
    assert reader.finish_context() == terminal


@pytest.mark.process_e2e
def test_failed_actual_hook_invocation_overrides_prior_live_receipt(tmp_path):
    import subprocess
    import sys
    from scidiscovery.research_loop.native_usage import metadata_stop
    reader, _, work, path, _ = metadata(tmp_path)
    report(work, path)
    assert reader.poll()['context_windows']['coverage'] == 'live'
    before = reader.snapshot()['tokens']
    failed = subprocess.run([sys.executable, budget_notice.__file__, '--work', str(work), '--attempt', '0', '--metadata'],
        input=b'{invalid', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2)
    assert failed.returncode != 0
    failed_observation = reader.poll()
    assert failed_observation['context_windows']['coverage'] == 'unknown'
    assert failed_observation['tokens'] == before
    assert failed_observation['metadata_delivery']['fault'] == 'native_metadata_delivery_failed'
    assert metadata_stop(failed_observation, required=True) == 'native_metadata_unavailable'


@pytest.mark.process_e2e
def test_timed_out_actual_hook_invocation_cannot_reuse_old_live_coverage(tmp_path,monkeypatch):
    import subprocess
    import sys
    import time
    from scidiscovery.research_loop import native_usage
    reader, _, work, path, receipt = metadata(tmp_path)
    report(work, path)
    assert reader.poll()['context_windows']['coverage'] == 'live'
    process = subprocess.Popen([sys.executable, budget_notice.__file__, '--work', str(work), '--attempt', '0', '--metadata'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        until = time.monotonic() + 2
        while not budget_notice.read_metadata(receipt)['pending']:
            assert process.poll() is None and time.monotonic() < until
            time.sleep(.01)
        pending = reader.poll()
        assert pending['context_windows']['coverage'] == 'awaiting'
        process.kill()  # The native hook timeout terminates this same unfinished callback.
        process.wait(timeout=2)
        started = budget_notice.read_metadata(receipt)['pending_started_at']
        monkeypatch.setattr(native_usage.time, 'monotonic', lambda: started + 6)
        timed_out = reader.poll()
        assert timed_out['context_windows']['coverage'] == 'unknown'
        assert timed_out['metadata_delivery']['fault'] == 'native_metadata_delivery_timeout'
        assert native_usage.metadata_stop(timed_out, required=True) == 'native_metadata_unavailable'
        assert timed_out['context_windows']['windows_started'] == 1
        assert timed_out['tokens']['input_tokens'] == 100
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=2)
        process.stdin.close(); process.stdout.close(); process.stderr.close()


def test_changed_session_metadata_preserves_known_identity_and_all_counter_lower_bounds(tmp_path):
    reader, _, work, path, receipt = metadata(tmp_path)
    report(work, path)
    report(work, path, kind='PostCompact')
    known = reader.poll()
    identity, offset = reader.identity, reader.offset
    replacement = budget_notice.read_metadata(receipt)
    replacement.update(session_id='other', compactions=10, acknowledged=replacement['acknowledged'] + 1,
        tokens=dict(input_tokens=900, cached_input_tokens=800, output_tokens=100), reported_calls=9,
        calls=dict(completed=9, failed=0, cancelled=0))
    budget_notice._publish_metadata(receipt, replacement)
    rejected = reader.poll()
    assert rejected['thread_id'] == THREAD
    assert rejected['context_windows']['coverage'] == 'unknown'
    assert rejected['context_windows']['windows_started'] == known['context_windows']['windows_started'] == 2
    assert rejected['tokens'] == known['tokens']
    assert rejected['calls'] == known['calls']
    assert rejected['metadata_delivery']['acknowledged'] == known['metadata_delivery']['acknowledged']
    assert (reader.identity, reader.offset) == (identity, offset)
    assert rejected['session_binding'] is None
