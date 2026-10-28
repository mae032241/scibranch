"""Token-only captured rollout fixtures and bounded append-only observations."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop.native_usage import SessionUsage, READ_BYTES, LINE_BYTES, terminal_attempt_tokens

THREAD = '01a13eb3-81b8-74e2-97f7-84e8f170ab5d'
FIXTURES = Path(__file__).parent / 'fixtures/native_usage'


def session(tmp_path, lines=b''):
    home, work = tmp_path / 'home', tmp_path / 'work'
    path = home / 'sessions' / f'rollout-{THREAD}.jsonl'
    path.parent.mkdir(parents=True)
    header = json.dumps(dict(type='session_meta', payload=dict(id=THREAD, cwd=str(work)))).encode() + b'\n'
    path.write_bytes(header + lines)
    return home, work, path


def counter(input_tokens=100, cached_input_tokens=80, output_tokens=5):
    return json.dumps(dict(type='event_msg', payload=dict(type='token_count', info=dict(
        total_token_usage=dict(input_tokens=input_tokens, cached_input_tokens=cached_input_tokens,
                               output_tokens=output_tokens))))).encode() + b'\n'


@pytest.mark.parametrize('step,expected', [('002', (869474, 798976, 14761)), ('004', (130660, 84480, 2921))])
def test_captured_counters_are_cumulative_and_never_readded(tmp_path, step, expected):
    data = (FIXTURES / f'{step}-token-count.jsonl').read_bytes()
    home, work, path = session(tmp_path)
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    previous = 0
    for line in data.splitlines(keepends=True):
        with path.open('ab') as stream:
            stream.write(line)
        observation = reader.poll()
        assert observation['coverage'] == 'live'
        assert observation['tokens']['input_tokens'] >= previous
        previous = observation['tokens']['input_tokens']
    assert tuple(observation['tokens'].values()) == expected
    assert reader.poll() == observation
    assert observation['individual_request_usage'] == 'unavailable'


def test_resume_subtracts_prelaunch_baseline_and_fresh_context_counts_independently(tmp_path):
    home, work, path = session(tmp_path, counter())
    reader = SessionUsage(home, work, session=dict(path=str(path), thread_id=THREAD))
    assert not reader.poll()['has_observation']
    with path.open('ab') as stream:
        stream.write(counter(130, 100, 8))
    assert reader.poll()['tokens'] == dict(input_tokens=30, cached_input_tokens=20, output_tokens=3)
    fresh = SessionUsage(home, work)
    fresh.bind_thread(THREAD)
    assert fresh.poll()['tokens']['input_tokens'] == 130


def test_resume_terminal_session_counter_uses_exact_append_only_delta(tmp_path):
    baseline = dict(input_tokens=2017365, cached_input_tokens=1926656, output_tokens=22940)
    terminal = dict(input_tokens=2385841, cached_input_tokens=2180096, output_tokens=24982)
    delta = dict(input_tokens=368476, cached_input_tokens=253440, output_tokens=2042)
    home, work, path = session(tmp_path, counter(**baseline))
    reader = SessionUsage(home, work, session=dict(path=str(path), thread_id=THREAD))
    with path.open('ab') as stream:
        stream.write(counter(**terminal))
    observation = reader.poll()
    assert terminal_attempt_tokens(terminal, observation) == delta
    assert terminal_attempt_tokens(delta, observation) == delta  # turn-local versions
    assert terminal == dict(input_tokens=2385841, cached_input_tokens=2180096, output_tokens=24982)


@pytest.mark.parametrize('mutation', ['unknown', 'missing_baseline', 'no_observation', 'different_counter', 'first_turn'])
def test_terminal_baseline_is_not_subtracted_without_exact_session_proof(mutation):
    baseline = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    live = dict(input_tokens=30, cached_input_tokens=20, output_tokens=3)
    terminal = {key: baseline[key] + live[key] for key in baseline}
    observation = dict(coverage='live', has_observation=True, baseline_tokens=baseline, tokens=live)
    if mutation == 'unknown':observation['coverage'] = 'unknown'
    if mutation == 'missing_baseline':observation['baseline_tokens'] = None
    if mutation == 'no_observation':observation['has_observation'] = False
    if mutation == 'different_counter':terminal['output_tokens'] += 1
    if mutation == 'first_turn':observation['baseline_tokens'] = dict.fromkeys(baseline, 0)
    assert terminal_attempt_tokens(terminal, observation) == terminal


def test_missing_partial_regressed_and_replaced_logs_keep_unknown_and_high_water(tmp_path):
    home, work, path = session(tmp_path)
    reader = SessionUsage(home, work)
    assert reader.poll()['coverage'] == 'unknown'
    reader.bind_thread(THREAD)
    with path.open('ab') as stream:
        stream.write(counter()[:-1])
    assert reader.poll()['reason'] == 'session_partial_line'
    with path.open('ab') as stream:
        stream.write(b'\n')
    assert reader.poll()['tokens']['input_tokens'] == 100
    with path.open('ab') as stream:
        stream.write(counter(99, 80, 5))
    observation = reader.poll()
    assert observation['reason'] == 'token_counter_regressed'
    assert observation['tokens']['input_tokens'] == 100
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0] + counter())
    other = SessionUsage(home, work)
    other.bind_thread(THREAD)
    other.poll()
    path.unlink()
    path.write_bytes(b'{}\n')
    assert other.poll()['reason'] == 'session_log_replaced_or_truncated'


def test_reader_is_bounded_and_skips_oversized_conversation_lines(tmp_path):
    home, work, path = session(tmp_path, b'x' * (READ_BYTES * 2) + b'\n' + counter())
    with path.open('rb') as stream:
        header_bytes = len(stream.readline(LINE_BYTES + 1))
    assert header_bytes <= LINE_BYTES
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    reader.poll()
    # Discovery validates a separately bounded header before polling its body.
    # The actual workspace path can make that header longer than 200 bytes.
    assert reader.offset - header_bytes == READ_BYTES
    assert len(reader.pending) <= LINE_BYTES
    assert reader.poll()['coverage'] == 'unknown'
    assert reader.poll()['tokens']['input_tokens'] == 100


def test_exact_home_thread_workspace_and_resume_baseline_are_required(tmp_path):
    home, work, path = session(tmp_path, counter())
    reader = SessionUsage(home, tmp_path / 'wrong-work')
    reader.bind_thread(THREAD)
    assert reader.poll()['coverage'] == 'unknown'
    other = SessionUsage(home, work)
    other.bind_thread('different-thread')
    assert other.poll()['coverage'] == 'unknown'
    other.bind_thread(THREAD)
    assert other.poll()['reason'] == 'native_thread_identity_changed'
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
    resumed = SessionUsage(home, work, session=dict(path=str(path), thread_id=THREAD))
    assert resumed.poll()['reason'] == 'resume_usage_baseline_unavailable'


def test_invalid_counter_and_missing_log_preserve_known_partial_sum(tmp_path):
    home, work, path = session(tmp_path, counter())
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    reader.poll()
    path.unlink()
    missing = reader.poll()
    assert missing['coverage'] == 'unknown' and missing['tokens']['input_tokens'] == 100
    home, work, path = session(tmp_path / 'invalid', counter() + counter(cached_input_tokens=101))
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    assert reader.poll()['reason'] == 'invalid_token_counter'
    assert reader.snapshot()['tokens']['input_tokens'] == 100


def test_resume_missing_baseline_remains_unknown_for_live_but_allows_terminal_fallback(tmp_path):
    home, work, path = session(tmp_path)
    reader = SessionUsage(home, work, session=dict(thread_id=THREAD, path=str(path)))
    with path.open('ab') as stream:
        stream.write(counter())
    observation = reader.poll()
    assert observation['coverage'] == 'unknown'
    assert observation['baseline_tokens'] is None
    assert not observation['has_observation']
    assert not reader.fault  # Independent turn.completed still permits the existing terminal fallback.


@pytest.mark.parametrize('kind', ['files', 'directories'])
def test_discovery_limits_all_entries_not_only_session_matches(tmp_path, monkeypatch, kind):
    from scidiscovery.research_loop import native_usage
    home, work, path = session(tmp_path)
    path.unlink()
    for index in range(8):
        entry = path.parent / f'unrelated-{index}'
        entry.mkdir() if kind == 'directories' else entry.touch()
    # A small budget exercises the boundary without making a large test tree.
    monkeypatch.setattr(native_usage, 'DISCOVERY_ENTRIES', 4)
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    observation = reader.poll()
    assert observation['coverage'] == 'unknown'
    assert observation['reason'] == 'session_discovery_capacity'
    assert observation['tokens']['input_tokens'] == 0
    assert not observation['has_observation']


def test_discovery_handles_nested_native_date_directories_and_ignores_symlinks(tmp_path):
    home, work, path = session(tmp_path, counter())
    nested = path.parent / '2026/10/15'
    nested.mkdir(parents=True)
    path.rename(nested / path.name)
    (path.parent / 'loop').symlink_to(path.parent, target_is_directory=True)
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    assert reader.poll()['tokens']['input_tokens'] == 100


def context_counter(input_tokens=100, used=60, window=100, **extra):
    value = json.loads(counter(input_tokens=input_tokens))
    value['timestamp'] = '2026-10-21T00:00:00Z'
    value['payload']['info'].update(last_token_usage=dict(total_tokens=used), model_context_window=window, **extra)
    return json.dumps(value).encode() + b'\n'


def test_context_is_last_reported_not_cumulative_and_compaction_can_decrease(tmp_path):
    home, work, path = session(tmp_path, context_counter(private_text='hidden-conversation'))
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    first = reader.poll()
    assert first['context']['ratio'] == .6
    assert first['context']['total_tokens'] == 60
    assert first['context']['reported_at'] == 1792540800
    assert 'hidden-conversation' not in json.dumps(first)
    assert reader.poll() == first  # No new event means no invented freshness.
    with path.open('ab') as stream:
        stream.write(context_counter(input_tokens=200, used=20))
    compacted = reader.poll()
    assert compacted['tokens']['input_tokens'] == 200
    assert compacted['context']['ratio'] == .2
    assert compacted['coverage'] == 'live'
    path.unlink()
    assert reader.poll()['context'] == dict(status='unavailable')


@pytest.mark.parametrize('used,window', [(True, 100), (10, 0), (-1, 100), (2 ** 53, 100), (10, 'private-window'), (None, 100)])
def test_malformed_optional_context_does_not_corrupt_billing(tmp_path, used, window):
    home, work, _ = session(tmp_path, context_counter(used=used, window=window))
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    observation = reader.poll()
    assert observation['tokens'] == dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    assert observation['coverage'] == 'live'
    assert observation['context'] == dict(status='unavailable')


def test_resume_context_never_inherits_parent_baseline_or_subtracts_window_usage(tmp_path):
    home, work, path = session(tmp_path, context_counter())
    reader = SessionUsage(home, work, session=dict(path=str(path), thread_id=THREAD))
    assert reader.poll()['context'] == dict(status='unavailable')
    with path.open('ab') as stream:
        stream.write(context_counter(input_tokens=150, used=30))
    observation = reader.poll()
    assert observation['tokens']['input_tokens'] == 50
    assert observation['context']['total_tokens'] == 30
    assert observation['context']['ratio'] == .3
    with path.open('ab') as stream:
        stream.write(counter(input_tokens=200))
    assert reader.poll()['context'] == dict(status='unavailable')


def test_discovery_continues_bounded_sweep_and_never_scans_after_binding(tmp_path, monkeypatch):
    from scidiscovery.research_loop import native_usage
    home, work, path = session(tmp_path, counter())
    for index in range(20):
        (path.parent / f'unrelated-{index}').touch()
    monkeypatch.setattr(native_usage, 'DISCOVERY_ENTRIES', 4)
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    assert reader.poll()['reason'] == 'session_discovery_capacity'
    for _ in range(8):
        observation = reader.poll()
        if observation['coverage'] == 'live':
            break
    assert observation['tokens']['input_tokens'] == 100
    assert reader.path == path
    monkeypatch.setattr(native_usage.os, 'scandir', lambda *args: pytest.fail('Bound session must not rediscover'))
    assert reader.poll()['tokens']['input_tokens'] == 100


def test_discovery_rejects_duplicate_threads_across_batches_and_retries_absent_sweeps(tmp_path, monkeypatch):
    from scidiscovery.research_loop import native_usage
    home, work, path = session(tmp_path, counter())
    original = path.read_bytes()
    path.unlink()
    monkeypatch.setattr(native_usage, 'DISCOVERY_ENTRIES', 1)
    reader = SessionUsage(home, work)
    reader.bind_thread(THREAD)
    assert reader.poll()['reason'] == 'session_unavailable'
    path.write_bytes(original)
    nested = path.parent / 'other'
    nested.mkdir()
    (nested / path.name).write_bytes(original)
    for _ in range(12):
        observation = reader.poll()
        if observation['reason'] == 'session_ambiguous':
            break
    assert observation['reason'] == 'session_ambiguous'
    assert reader.path is None and not observation['has_observation']
    assert reader.discovery_entries is None
