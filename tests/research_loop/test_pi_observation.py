"""Native-boundary qualification, not scientific evidence or all-call receipts.

Source nodes do not launch processes. One marked clock node evaluates the
controlled producer in Node vm with mocked SDK/clock/delivery and no Pi. Other
marked nodes use installed Pi against a finite loopback peer and the production
collector in a separate process; pytest never performs native group cleanup
itself. No live provider/model is used.
"""
import copy
import fcntl
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (budget, budget_notice, contracts as c,
    execution, native_usage, pi_namespace, pi_observation as observation, pi_sessions,
    pi_transport, research_config)
from test_pi_backend_v2 import (OPTIONS, PROFILE, STAMP, USAGE, adapter, message,
    options, installed_pi_fixture, configure_installed_pi_fixture, collect_separately)
from test_pi_backend_interfaces import RESUME, retain_installed_repair_diagnostics

PROMPT = 'Exact original request\nUTF-8: 科学\u2028still-one-record'


def append(path, *rows):
    with path.open('ab') as stream:
        for row in rows: stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8'))


def entry(identity, parent, *, kind='message', **fields):
    return dict(type=kind, id=identity, parentId=parent, timestamp=STAMP, **fields)


def bound(tmp_path, *, retained=None):
    backend = adapter()
    home, work = tmp_path / 'home', tmp_path / 'work'
    (work / 'work').mkdir(parents=True)
    backend.bind_attempt(home, work)
    backend.launch(PROFILE, work, None, None, 'Original contract')
    meter = backend.meter(home, work, context_state=retained)
    observer = meter.observation
    observer.configure(0, required=True)
    observer.prompt_hash = hashlib.sha256(PROMPT.encode()).hexdigest()
    meter.metadata_callback = lambda value: None
    header = dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work))
    append(meter.path, header, entry('native-user', None,
        message=dict(role='user', content=[dict(type='text', text=PROMPT)], timestamp=0)))
    meter.poll()
    observer.bind(dict(type=observation.BOUND, nonce=observer.proof['nonce'], path=str(meter.path),
        workspace=str(work), session_id='exact-session'))
    return backend, meter, observer


def event(meter, *, kind='context', leaf='native-user', **changes):
    value = dict(kind=kind, session_id='exact-session', path=str(meter.path), workspace=str(meter.workspace),
        leaf=leaf, turn_id='native-user', prompt_hash=hashlib.sha256(PROMPT.encode()).hexdigest(),
        baseline_leaf=None, baseline_count=0, compaction_id=None, pre_cursor=None,
        assistant_id=None, status=None, context=dict(tokens=85, contextWindow=100), observed_at=10)
    value.update(changes)
    return value


def pending(observer, value, *, sequence=None, started=None):
    original = budget_notice.read_metadata(observer.path)
    original.update(event=value, sequence=observer.sequence + 1 if sequence is None else sequence,
        pending=True, pending_started_at=time.monotonic() if started is None else started)
    budget_notice._publish_metadata(observer.path, original)


def test_native_user_scope_estimate_and_initial_window_not_message_or_rpc_counts(tmp_path):
    backend, meter, observer = bound(tmp_path)
    assert meter.snapshot()['context']['status'] == 'unavailable'
    observer.accept(event(meter))
    view = meter.snapshot()
    assert view['turn_id'] == 'native-user' and view['context_windows']['windows_started'] == 1
    assert view['context']['total_tokens'] == 85 and view['context']['current_window'] is True
    assert view['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    assert view['has_observation'] is False  # Unknown billing is not zero usage.
    assert view['calls'] == dict(completed=0, failed=0, cancelled=0)
    observer.accept(event(meter))
    assert meter.snapshot()['context_windows']['windows_started'] == 1
    backend.event(dict(type='message_start', message=dict(role='assistant')))
    assert meter.snapshot()['context'] == dict(status='unavailable')
    observer.accept(event(meter, context=dict(tokens=None, contextWindow=100)))
    assert meter.snapshot()['context'] == dict(status='unavailable')


def test_controlled_observer_stamp_is_epoch_anchored_monotonic_capture_time(tmp_path):
    source = observation.extension_source(tmp_path, {})
    assert 'import { performance } from "node:perf_hooks";' in source
    assert 'const observedAt = () => (performance.timeOrigin + performance.now()) / 1000;' in source
    assert 'context: ctx.getContextUsage() ?? null, observed_at: observedAt(), ...extra' in source
    assert 'observed_at: Date.now()' not in source
    assert 'Math.max(' not in source  # No clamp disguises stale/invalid input.


@pytest.mark.parametrize('stamp', [-1, 9, True, None, '10', float('nan'), float('inf'),
    -float('inf'), native_usage.MAX_SAFE_INTEGER + 1])
def test_strict_consumer_rejects_stale_or_invalid_stamp_without_mutation(tmp_path, stamp):
    _, meter, observer = bound(tmp_path)
    observer.accept(event(meter))
    before = observer.snapshot()
    with pytest.raises(ValueError, match='^pi_metadata_source_or_scope_invalid$'):
        observer.accept(event(meter, observed_at=stamp))
    assert observer.last_observed_at == 10 and observer.snapshot() == before
    assert observer.windows_started == 1 and not observer.responses


# Evaluate the complete generated producer, not a Python reimplementation of its
# clock. Only built-in Node vm/crypto/fs are used; SDK and helper calls are mocks.
CLOCK_HARNESS = r'''
const vm = require("node:vm"), fs = require("node:fs");
const { createHash } = require("node:crypto");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
let source = input.source;
for (const line of [
  'import { execFileSync } from "node:child_process";\n',
  'import { createHash } from "node:crypto";\n',
  'import { performance } from "node:perf_hooks";\n'
]) source = source.replace(line, "");
source = source.replace("export default function (pi)", "function install(pi)");
function run(body) {
  const handlers = new Map(), events = [], entries = [];
  let leaf = null, wall = 1800000001000, elapsed = 1000, dateReads = 0;
  const pi = { registerCommand() {}, on: (name, fn) => handlers.set(name, fn) };
  const ctx = {
    cwd: input.workspace,
    sessionManager: {
      getSessionId: () => "exact-session", getSessionFile: () => input.path,
      getLeafId: () => leaf, getEntries: () => entries, getBranch: () => entries
    },
    getContextUsage: () => ({ tokens: 85, contextWindow: 100 }),
    abort: () => { throw new Error("unexpected abort"); }
  };
  const sandbox = {
    pi, createHash,
    performance: { timeOrigin: 1800000000000, now: () => elapsed },
    Date: { now: () => { dateReads++; return wall; } },
    execFileSync: (_python, args, options) => {
      if (args.includes("--delivery-path")) {
        if (args[args.indexOf("--delivery-path") + 1] !== input.metadata ||
            args[args.indexOf("--nonce") + 1] !== input.nonce) throw new Error("binding changed");
        events.push(JSON.parse(options.input));
      }
      return "{}";  // No advice and no real helper/process invocation.
    }
  };
  vm.runInNewContext(body + "\ninstall(pi);", sandbox, { timeout: 1000 });
  handlers.get("before_agent_start")({}, ctx);
  entries.push({ id: "native-user", type: "message", message: { role: "user", content: input.prompt } });
  leaf = "native-user";
  function step(kind, value) {
    handlers.get(kind)(value, ctx);
    wall -= 250; elapsed += 250;  // Adjustable wall clock reverses; monotonic clock advances.
  }
  step("context", {});
  entries.push({ id: "native-assistant", type: "message", message: { role: "assistant" } });
  leaf = "native-assistant";
  step("tool_result", { content: [{ type: "text", text: "Original tool result" }] });
  step("turn_end", { messageEntryId: "native-assistant", outcome: "completed" });
  step("agent_settled", {});
  return { events, dateReads };
}
const actual = run(source);
// Counterfactual old producer under the SAME deterministic clock schedule.
const wall = run(source.replace("observed_at: observedAt()", "observed_at: Date.now() / 1000"));
process.stdout.write(JSON.stringify({ actual, wall }));
'''


@pytest.mark.process_e2e
def test_controlled_producer_wall_clock_rollback_delivers_ordered_stamps_and_strict_stale_negative(tmp_path):
    _, meter, observer = bound(tmp_path)
    source = observation.extension_source(meter.workspace, observer.proof)
    result = subprocess.run(['node', '--input-type=commonjs', '-e', CLOCK_HARNESS],
        input=json.dumps(dict(source=source, path=str(meter.path), workspace=str(meter.workspace),
            prompt=PROMPT, metadata=str(observer.path), nonce=observer.proof['nonce'])),
        text=True, capture_output=True, timeout=5, check=True)
    samples = json.loads(result.stdout)
    actual, wall = samples['actual'], samples['wall']
    assert actual['dateReads'] == 0 and wall['dateReads'] == 4
    assert [row['observed_at'] for row in actual['events']] == [
        1800000001, 1800000001.25, 1800000001.5, 1800000001.75]
    assert [row['observed_at'] for row in wall['events']] == [
        1800000001, 1800000000.75, 1800000000.5, 1800000000.25]
    assert [row['kind'] for row in actual['events']] == ['context', 'tool_result', 'turn_end', 'terminal']
    append(meter.path, entry('native-assistant', 'native-user', message=message()))
    meter.poll()
    published = []
    def callback(value):
        current = budget_notice.read_metadata(observer.path)
        assert current['pending'] is True and current['acknowledged'] == len(published)
        assert value['context']['observed_at'] == current['event']['observed_at']
        published.append(value)
    meter.metadata_callback = callback
    for sequence, value in enumerate(actual['events'], 1):
        assert set(value) == set(event(meter))  # No new permission/correlation field.
        assert value['context'] == dict(tokens=85, contextWindow=100)  # SDK-owned amount.
        pending(observer, value); observer.poll()
        current = budget_notice.read_metadata(observer.path)
        assert not observer.fault and current['event'] == value
        assert current['sequence'] == current['acknowledged'] == observer.acknowledged == sequence
        assert not current['pending'] and len(published) == sequence
    assert observer.terminal_seen and observer.windows_started == 1
    assert observer.responses == {'native-assistant': 'completed'}
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    observer.poll()
    assert len(published) == 4  # No replay/rebill after the final ACK.

    _, rollback_meter, rollback = bound(tmp_path / 'wall-clock-counterfactual')
    rollback.accept(event(rollback_meter, observed_at=wall['events'][0]['observed_at']))
    with pytest.raises(ValueError, match='^pi_metadata_source_or_scope_invalid$'):
        rollback.accept(event(rollback_meter, kind='tool_result', observed_at=wall['events'][1]['observed_at']))
    assert rollback.last_observed_at == 1800000001 and rollback.windows_started == 1


@pytest.mark.parametrize('damage', ['thread', 'path', 'work', 'user', 'hash', 'baseline', 'count', 'leaf', 'kind'])
def test_original_scope_native_id_prompt_hash_and_baseline_spoofs_fail_sticky(tmp_path, damage):
    _, meter, observer = bound(tmp_path)
    value = event(meter)
    key, replacement = dict(thread=('session_id', 'other-session'), path=('path', '/other/session'),
        work=('workspace', '/other/work'), user=('turn_id', 'rpc-1'), hash=('prompt_hash', '0' * 64),
        baseline=('baseline_leaf', 'rpc-1'), count=('baseline_count', 1),
        leaf=('leaf', 'tool-call-id'), kind=('kind', 'message_end'))[damage]
    value[key] = replacement
    pending(observer, value)
    observer.poll()
    assert observer.fault and budget_notice.read_metadata(observer.path)['acknowledged'] == 0
    observer.poll()
    assert observer.fault and observer.windows_started == 0


@pytest.mark.parametrize('damage', ['source', 'nonce', 'attempt', 'sequence', 'path', 'session_id'])
def test_newer_stamp_cannot_authorize_stale_source_or_replayed_watermark(tmp_path, damage):
    _, meter, observer = bound(tmp_path)
    pending(observer, event(meter, observed_at=1800000001)); observer.poll()
    append(meter.path, entry('native-assistant', 'native-user', message=message()))
    meter.poll()
    pending(observer, event(meter, kind='tool_result', leaf='native-assistant', observed_at=1800000001.25))
    value = budget_notice.read_metadata(observer.path)
    if damage in {'path', 'session_id'}: value['event'][damage] = '/other/session' if damage == 'path' else 'other-session'
    else: value[damage] = dict(source='other-source', nonce='0' * 64, attempt=1, sequence=1)[damage]
    budget_notice._publish_metadata(observer.path, value)
    published = []
    meter.metadata_callback = published.append
    observer.poll()
    assert observer.fault == 'native_metadata_unavailable' and published == []
    assert observer.last_observed_at == 1800000001
    assert observer.sequence == observer.acknowledged == budget_notice.read_metadata(observer.path)['acknowledged'] == 1
    assert observer.windows_started == 1 and meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    observer.poll()
    assert published == [] and budget_notice.read_metadata(observer.path)['acknowledged'] == 1


def test_compaction_append_delta_dedup_identical_summaries_null_and_numeric_projection(tmp_path):
    _, meter, observer = bound(tmp_path)
    observer.accept(event(meter))
    append(meter.path, entry('assistant-one', 'native-user', message=message()),
        entry('compact-one', 'assistant-one', kind='compaction', summary='IDENTICAL',
            firstKeptEntryId='native-user', tokensBefore=85, usage=USAGE))
    meter.poll()
    observer.accept(event(meter, kind='compact_start', leaf='assistant-one', context=None))
    observer.accept(event(meter, kind='compact', leaf='compact-one', context=None,
        compaction_id='compact-one', pre_cursor='assistant-one'))
    assert observer.windows_started == 2 and observer.current == dict(status='unavailable')
    with pytest.raises(ValueError, match='compaction_delta'):
        observer.accept(event(meter, kind='compact', leaf='compact-one', context=None,
            compaction_id='compact-one', pre_cursor='assistant-one'))
    append(meter.path, entry('assistant-two', 'compact-one', message=message(stamp=2)),
        entry('compact-two', 'assistant-two', kind='compaction', summary='IDENTICAL',
            firstKeptEntryId='native-user', tokensBefore=85, usage=USAGE))
    meter.poll()
    observer.accept(event(meter, kind='compact_start', leaf='assistant-two', context=None))
    observer.accept(event(meter, kind='compact', leaf='compact-two', context=None,
        compaction_id='compact-two', pre_cursor='assistant-two'))
    assert observer.windows_started == 3
    projected = native_usage.context_windows(observer.snapshot()['context_windows'])
    assert projected == dict(coverage='live', windows_started=3, reason=None)
    assert not {'cursor', 'thread_id', 'leaf', 'path', 'nonce'} & projected.keys()
    assert observer.snapshot()['context'] == dict(status='unavailable')


def test_compaction_cursor_allows_native_auxiliary_append_but_requires_new_unique_id(tmp_path):
    _, meter, observer = bound(tmp_path)
    observer.accept(event(meter))
    observer.accept(event(meter, kind='compact_start', context=None))
    append(meter.path,
        dict(entry('native-warm', 'native-user', kind='usage', usage=USAGE), kind='cache_warm'),
        entry('native-compact', 'native-warm', kind='compaction', summary='same',
            firstKeptEntryId='native-user', tokensBefore=85, usage=USAGE))
    meter.poll()
    observer.accept(event(meter, kind='compact', leaf='native-compact', context=None,
        compaction_id='native-compact', pre_cursor='native-user'))
    assert observer.windows_started == 2
    assert meter.snapshot()['tokens']['input_tokens'] == 2 * pi_sessions.usage_tokens(USAGE)['input_tokens']


def test_response_receipts_are_lower_bound_and_never_exhaustive_calls_or_billing(tmp_path):
    backend, meter, observer = bound(tmp_path)
    observer.accept(event(meter))
    append(meter.path, entry('native-assistant', 'native-user', message=message()))
    meter.poll()
    value = event(meter, kind='turn_end', leaf='native-assistant', assistant_id='native-assistant', status='completed')
    observer.accept(value)
    assert observer.snapshot()['calls']['completed'] == 1
    assert backend.observation_capability('native_calls') == dict(status='unsupported', reason=observation.CALL_LIMITATION)
    assert 'lower_bound' in observer.snapshot()['native_calls_scope']
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    with pytest.raises(ValueError, match='response_receipt'): observer.accept(value)
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)


def test_required_callback_publication_is_synchronous_and_precedes_ack(tmp_path):
    _, meter, observer = bound(tmp_path)
    published = []
    def callback(value):
        current = budget_notice.read_metadata(observer.path)
        assert current['pending'] is True and current['acknowledged'] == 0
        assert value['context_windows']['coverage'] == 'live'
        assert value['context_windows']['windows_started'] == 1
        published.append(value)
    meter.metadata_callback = callback
    pending(observer, event(meter))
    observer.poll()
    current = budget_notice.read_metadata(observer.path)
    assert len(published) == 1 and current['acknowledged'] == 1 and current['pending'] is False
    observer.poll()
    assert len(published) == 1  # Polling an acknowledgement is not another acceptance.


def test_v4_reflection_hint_publication_precedes_pi_metadata_ack(tmp_path):
    from scidiscovery.research_loop import supervision
    from test_worker_first_investment import scene
    # This publication-order fixture opts into operational reflection without
    # fabricating a newly accepted question or original experiment receipt.
    policy = dict(supervision.DEFAULT_WORKER_FIRST_REFLECTION_POLICY, require_fresh_input=False)
    assert policy['version'] == 4
    root, job, intent, limits, facts, live = scene(tmp_path, 'review',
        policy=policy)
    facts['shared'] = dict(facts['shared'], observed_tokens=30_000,
        accounted_tokens=30_000, unknown_steps=[])
    work = c.workspace(root, 1)
    backend = adapter(); home = root/'native-homes/001'
    backend.bind_attempt(home, work); backend.launch(PROFILE, work, None, None, 'Original scope')
    meter = backend.meter(home, work); observer = meter.observation
    observer.configure(0, required=True)
    observer.prompt_hash = hashlib.sha256(PROMPT.encode()).hexdigest()
    append(meter.path, dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work)),
        entry('native-user', None, message=dict(role='user', content=PROMPT, timestamp=0)))
    meter.poll()
    observer.bind(dict(type=observation.BOUND, nonce=observer.proof['nonce'], path=str(meter.path),
        workspace=str(work), session_id='exact-session'))
    facts['research_investment'] = live
    published = []
    def callback(value):
        before = budget_notice.read_metadata(observer.path)
        assert before['pending'] is True and before['acknowledged'] == 0
        budget.write_node_hint(root, job, intent, limits, value['tokens'], facts=facts,
            observed=value['has_observation'], context_observation=value)
        hint = c.read(work/'work/budget-status.json')
        assert hint['research_investment']['coverage'] == 'live'
        assert hint['research_investment']['total_calls'] == 4
        assert hint['direction_review']['review_kind'] == 'ordinary_reflection'
        assert hint['direction_review']['reasons'] == ['operational_no_new_experiment']
        output = budget_notice.hook_output(work, 0)
        assert output['hookSpecificOutput']['additionalContext'] == budget_notice.WORKER_REFLECTION_TEXT
        assert len(output['hookSpecificOutput']['additionalContext'].encode()) <= 200
        assert budget_notice.read_metadata(observer.path) == before
        published.append(output)
    backend.bind_metadata_callback(meter, callback)
    pending(observer, event(meter)); observer.poll()
    assert len(published) == 1 and not observer.fault
    delivered = budget_notice.read_metadata(observer.path)
    assert delivered['acknowledged'] == 1 and delivered['pending'] is False
    observer.poll()
    assert len(published) == 1 and budget_notice.hook_output(work, 0) is None
    assert facts['head']['search']['supervision']['investment']['total_calls'] == 0
    assert live['total_calls'] == 4  # Advice never manufactures scientific rounds or grants.


@pytest.mark.parametrize('arrival', ['before-poll', 'after-consume'])
def test_valid_turn_end_delivery_across_session_metadata_race(tmp_path, monkeypatch, arrival):
    _, meter, observer = bound(tmp_path)
    real_read = budget_notice.read_metadata

    class DeliveryPublished(Exception):
        pass

    def publish(value):
        before = real_read(observer.path)
        assert before['pending'] is False and before['fault'] is None
        assert before['sequence'] == before['acknowledged']
        reads = []
        def producer_read(path):
            current = real_read(path)
            reads.append(current)
            if len(reads) == 2:
                # Stop only the public producer's acknowledgement wait, after
                # its real locked publication. No thread, sleep or forged state.
                assert current['pending'] is True
                assert current['sequence'] == before['sequence'] + 1
                assert current['event'] == value
                raise DeliveryPublished
            return current
        with monkeypatch.context() as producer:
            producer.setattr(budget_notice, 'read_metadata', producer_read)
            with pytest.raises(DeliveryPublished):
                budget_notice.deliver_metadata(observer.path, observer.proof['nonce'], value)
        assert len(reads) == 2

    publish(event(meter))
    observer.poll()
    initial = real_read(observer.path)
    assert initial['acknowledged'] == 1 and initial['pending'] is False
    assert not observer.fault
    baseline = meter.snapshot()['tokens']
    assert baseline == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)

    assistant = entry('native-assistant', 'native-user', message=message())
    delivery = event(meter, kind='turn_end', leaf='native-assistant',
        assistant_id='native-assistant', status='completed')
    arrivals = []
    def arrive():
        assert not arrivals
        append(meter.path, assistant)
        publish(delivery)
        arrivals.append(delivery)

    if arrival == 'before-poll': arrive()
    original_size = meter.path.stat().st_size
    consumed = []
    real_open = meter._open
    @contextmanager
    def tracked_original():
        with real_open() as stream:
            yield stream
        # SessionUsage.poll has finished its fstat-bounded consume and closed
        # the real original before entering Observation.poll's metadata read.
        consumed.append(meter.offset)
    monkeypatch.setattr(meter, '_open', tracked_original)

    def observation_read(path):
        if arrival == 'after-consume' and not arrivals:
            assert path == observer.path
            assert consumed == [original_size] and meter.offset == original_size
            assert 'native-assistant' not in meter.seen
            arrive()
        return real_read(path)
    monkeypatch.setattr(budget_notice, 'read_metadata', observation_read)

    published = []
    expected = pi_sessions.usage_tokens(USAGE)
    def callback(value):
        current = real_read(observer.path)
        assert current['pending'] is True and current['acknowledged'] == 1
        assert current['event'] == delivery
        assert value['calls']['completed'] == 1
        assert value['context_windows']['coverage'] == 'live'
        assert value['tokens'] == expected
        published.append(value)
    meter.metadata_callback = callback
    result = meter.poll()
    assert arrivals == [delivery]
    assert not meter.fault and not observer.fault
    current = real_read(observer.path)
    assert current['fault'] is None and current['pending'] is False
    assert current['sequence'] == current['acknowledged'] == 2
    assert len(published) == 1 and observer.acknowledged == 2
    assert result['tokens'] == meter.known == expected
    assert all(result['tokens'][key] >= baseline[key] for key in baseline)
    assert meter.poll()['tokens'] == expected  # Neither receipt nor re-poll rebills.
    assert len(published) == 1 and not observer.fault and not meter.fault


@pytest.mark.parametrize('forged', ['leaf', 'assistant_id'])
def test_pending_forged_id_faults_after_refresh_of_growing_unrelated_original(tmp_path, monkeypatch, forged):
    _, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    real_read = budget_notice.read_metadata
    delivery = event(meter, kind='turn_end', leaf='unrelated-assistant',
        assistant_id='unrelated-assistant', status='completed')
    delivery[forged] = 'forged-native-id'
    pending(observer, delivery)
    arrivals = []
    def observation_read(path):
        if not arrivals:
            append(meter.path, entry('unrelated-assistant', 'native-user', message=message()))
            arrivals.append(meter.path.stat().st_size)
        return real_read(path)
    monkeypatch.setattr(budget_notice, 'read_metadata', observation_read)
    published = []
    meter.metadata_callback = published.append
    meter.poll()
    assert arrivals == [meter.offset] and 'unrelated-assistant' in meter.seen
    assert 'forged-native-id' not in meter.seen and not meter.fault
    assert observer.fault and published == []
    current = real_read(observer.path)
    assert current['pending'] is True and current['acknowledged'] == observer.acknowledged == 1
    assert current['fault'] == observer.fault
    fault = observer.fault
    append(meter.path, entry('later-unrelated', 'unrelated-assistant', kind='label', label='growing'))
    meter.poll()
    assert observer.fault == fault and published == []
    assert real_read(observer.path)['acknowledged'] == 1
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)


def test_pending_delivery_defers_bounded_original_backlog_without_double_billing(tmp_path, monkeypatch):
    _, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    monkeypatch.setattr(pi_sessions, 'READ_BYTES', 96)
    append(meter.path,
        entry('large-label', 'native-user', kind='label', label='x' * 1200),
        entry('native-assistant', 'large-label', message=message()))
    # The live mirror and original overlap: refresh must retain category maxima.
    meter.events.add(USAGE, identity=pi_sessions.message_identity(message()))
    delivery = event(meter, kind='turn_end', leaf='native-assistant',
        assistant_id='native-assistant', status='completed')
    pending(observer, delivery)
    real_read = budget_notice.read_metadata
    published = []
    def callback(value):
        current = real_read(observer.path)
        assert current['pending'] is True and current['acknowledged'] == 1
        assert value['tokens'] == pi_sessions.usage_tokens(USAGE)
        published.append(value)
    meter.metadata_callback = callback
    end = meter.path.stat().st_size
    steps = (end - meter.offset + 95) // 96
    assert steps > 2
    for step in range(steps):
        before = meter.offset
        observer.poll()
        assert 0 < meter.offset - before <= 96
        assert not meter.fault and not observer.fault
        if step < steps - 1:
            current = real_read(observer.path)
            assert meter.reason == 'session_read_backlog' and observer.pending
            assert current['pending'] is True and current['acknowledged'] == 1
            assert observer.sequence == observer.acknowledged == 1 and published == []
    assert meter.offset == end and len(published) == 1
    assert observer.sequence == observer.acknowledged == 2 and not observer.pending
    assert real_read(observer.path)['acknowledged'] == 2
    assert meter.poll()['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert len(published) == 1


@pytest.mark.parametrize('arrival', ['during-terminal-poll', 'after-terminal-proof'])
def test_terminal_delivery_refresh_keeps_post_exit_digest_proof(tmp_path, monkeypatch, arrival):
    backend, meter, observer = bound(tmp_path)
    append(meter.path, entry('assistant-one', 'native-user', message=message()))
    meter.poll()
    pending(observer, event(meter, leaf='assistant-one')); observer.poll()
    real_read = budget_notice.read_metadata
    arrivals = []
    delivery = event(meter, kind='terminal', leaf='assistant-two')
    # Publish through the original reader to avoid re-entering this boundary.
    def publish_delivery():
        current = real_read(observer.path)
        current.update(event=delivery, sequence=2, pending=True, pending_started_at=time.monotonic())
        budget_notice._publish_metadata(observer.path, current)
    def observation_read(path):
        if not arrivals and (arrival == 'during-terminal-poll' or meter.finished):
            if meter.finished: assert meter.verified_offset == meter.offset
            append(meter.path, entry('assistant-two', 'assistant-one', message=message(stamp=2)))
            publish_delivery()
            arrivals.append(delivery)
        return real_read(path)
    monkeypatch.setattr(budget_notice, 'read_metadata', observation_read)
    published = []
    def callback(value):
        current = real_read(observer.path)
        assert current['pending'] is True and current['acknowledged'] == 1
        assert value['tokens'] == {key: 2 * tokens for key, tokens in pi_sessions.usage_tokens(USAGE).items()}
        if meter.finished:
            assert meter.verified_offset == meter.offset and value['session_binding'] is not None
        published.append(value)
    meter.metadata_callback = callback
    result = backend.finish_observation(meter, required=True)
    assert arrivals == [delivery] and len(published) == 1
    assert not meter.fault and not observer.fault and meter.finished
    assert observer.terminal_seen and observer.acknowledged == 2
    assert result['coverage'] == 'terminal' and meter.verified_offset == meter.offset
    binding = meter.session_binding()
    assert binding == result['session_binding']
    assert binding['size_bytes'] == meter.path.stat().st_size
    assert binding['sha256'] == hashlib.sha256(meter.path.read_bytes()).hexdigest()


def test_pending_post_exit_refresh_rehash_rejects_early_prefix_rewrite(tmp_path, monkeypatch):
    backend, meter, observer = bound(tmp_path)
    append(meter.path, entry('assistant-one', 'native-user', message=message()),
        entry('large-label', 'assistant-one', kind='label', label='x' * (2 * pi_sessions.ANCHOR_BYTES)))
    meter.poll()
    pending(observer, event(meter, leaf='large-label')); observer.poll()
    real_read = budget_notice.read_metadata
    arrivals = []
    def observation_read(path):
        if meter.finished and not arrivals:
            assert meter.verified_offset == meter.offset
            data = meter.path.read_bytes()
            assert data.count(b'Exact original request') == 1
            meter.path.write_bytes(data.replace(b'Exact original request', b'Other original request'))
            append(meter.path, entry('assistant-two', 'large-label', message=message(stamp=2)))
            current = real_read(path)
            current.update(event=event(meter, kind='terminal', leaf='assistant-two'),
                sequence=2, pending=True, pending_started_at=time.monotonic())
            budget_notice._publish_metadata(path, current)
            arrivals.append(True)
        return real_read(path)
    monkeypatch.setattr(budget_notice, 'read_metadata', observation_read)
    published = []
    meter.metadata_callback = published.append
    result = backend.finish_observation(meter, required=True)
    assert arrivals == [True] and published == []
    assert meter.fault == 'session_terminal_original_changed_or_unverified' and observer.fault
    assert meter.verified_offset is None and meter.session_binding() is None
    assert result['coverage'] == 'unknown' and result['session_binding'] is None
    assert result['tokens'] == {key: 2 * value for key, value in pi_sessions.usage_tokens(USAGE).items()}
    assert real_read(observer.path)['acknowledged'] == observer.acknowledged == 1


@pytest.mark.parametrize('fault', ['callback', 'missing-callback', 'gap', 'timeout', 'invalid-clock', 'regressed-ack', 'wrong-nonce'])
def test_pending_watermark_callback_and_timeout_faults_are_sticky_keep_debit(tmp_path, fault):
    backend, meter, observer = bound(tmp_path)
    append(meter.path, entry('assistant', 'native-user', message=message()))
    meter.poll()
    if fault == 'callback':
        def fail(_): raise RuntimeError('private details must never become evidence')
        meter.metadata_callback = fail
    elif fault == 'missing-callback': meter.metadata_callback = None
    pending(observer, event(meter), sequence=2 if fault == 'gap' else 1,
        started=0 if fault == 'invalid-clock' else time.monotonic() - 6 if fault == 'timeout' else None)
    if fault in {'regressed-ack', 'wrong-nonce'}:
        value = budget_notice.read_metadata(observer.path)
        value['acknowledged' if fault == 'regressed-ack' else 'nonce'] = -1 if fault == 'regressed-ack' else 'f' * 64
        budget_notice._publish_metadata(observer.path, value)
    observer.poll()
    reason = observer.fault
    assert reason and budget_notice.read_metadata(observer.path)['acknowledged'] != 1
    assert native_usage.metadata_stop(meter.snapshot(), required=True) == 'native_metadata_unavailable'
    backend.finish_observation(meter, required=True)
    assert observer.fault == reason and meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert 'private details' not in json.dumps(meter.snapshot())


def test_private_clock_reversal_diagnostic_preserves_scope_checks_ack_and_bill_floor(tmp_path):
    _, meter, observer = bound(tmp_path)
    # Synthetic exact quarter-second reversal; not a claim about retained host
    # clock/NTP history. All source/identity/receipt checks are otherwise valid.
    pending(observer, event(meter, observed_at=1800000000)); observer.poll()
    append(meter.path, entry('native-assistant', 'native-user', message=message()))
    meter.poll()
    pending(observer, event(meter, kind='tool_result', leaf='native-assistant', observed_at=1800000001))
    observer.poll()
    assert observer.sequence == observer.acknowledged == 2
    published = []
    meter.metadata_callback = published.append
    pending(observer, event(meter, kind='turn_end', leaf='native-assistant',
        assistant_id='native-assistant', status='completed', observed_at=1800000000.75))
    observer.poll()
    current = budget_notice.read_metadata(observer.path)
    diagnostic_path = observer.path.with_suffix('.diagnostic.json')
    saved = diagnostic_path.read_bytes()
    diagnostic = json.loads(saved)
    assert diagnostic['phase'] == 'metadata_validation'
    assert diagnostic['code'] == 'pi_metadata_source_or_scope_invalid'
    assert diagnostic['last_observed_at'] == observer.last_observed_at == 1800000001
    assert diagnostic['event_observed_at'] == 1800000000.75
    assert diagnostic['last_observed_at'] - diagnostic['event_observed_at'] == .25
    assert diagnostic['scope_checks'] == dict(bound=True, terminal_not_seen=True,
        field_count=15, session=True, path=True, workspace=True, leaf_id=True,
        leaf_seen=True, baseline_count=True, baseline_seen=True, observed_order=False)
    assert current['pending'] is True and current['sequence'] == 3
    assert current['acknowledged'] == observer.acknowledged == observer.sequence == diagnostic['acknowledged'] == 2
    assert observer.fault == current['fault'] == 'native_metadata_unavailable'
    assert published == [] and not observer.responses and not observer.terminal_seen
    assert observer.windows_started == 1 and not meter.fault
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert meter.snapshot()['context'] == dict(status='unavailable')
    assert not {'last_observed_at', 'event_observed_at', 'scope_checks'} & meter.snapshot()['metadata_delivery'].keys()
    assert not {'event', 'nonce', 'path', 'workspace', 'session_id', 'turn_id', 'leaf'} & diagnostic.keys()
    observer.poll()
    assert diagnostic_path.read_bytes() == saved and published == []
    assert budget_notice.read_metadata(observer.path)['acknowledged'] == 2


@pytest.mark.parametrize('stamp', [None, True, 'PRIVATE invalid clock /auth/token', {'raw': 'PRIVATE invalid clock /auth/token'}])
def test_private_clock_diagnostic_never_copies_nonnumeric_stamp(tmp_path, stamp):
    _, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    pending(observer, event(meter, observed_at=stamp)); observer.poll()
    diagnostic = json.loads(observer.path.with_suffix('.diagnostic.json').read_bytes())
    assert diagnostic['last_observed_at'] == 10
    assert 'event_observed_at' not in diagnostic
    assert diagnostic['scope_checks']['observed_order'] is False
    assert 'PRIVATE' not in json.dumps(diagnostic) + json.dumps(meter.snapshot())
    assert observer.acknowledged == 1 and observer.fault == 'native_metadata_unavailable'


@pytest.mark.parametrize('phase,code', [
    ('metadata_capture', 'pi_metadata_capture_failed'),
    ('original_capture', 'pi_metadata_original_capture_failed'),
    ('metadata_validation', 'pi_metadata_source_or_scope_invalid'),
    ('callback', 'pi_metadata_callback_failed'),
    ('ack', 'pi_metadata_ack_failed')])
def test_private_delivery_fault_boundary_keeps_ack_and_bill_floor_without_exception_text(
        tmp_path, monkeypatch, phase, code):
    backend, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    assert observer.acknowledged == 1
    append(meter.path, entry('native-assistant', 'native-user', message=message()))
    meter.poll()
    delivery = event(meter, kind='turn_end', leaf='native-assistant',
        assistant_id='native-assistant', status='completed')
    if phase == 'metadata_validation': delivery['leaf'] = 'forged-id'
    pending(observer, delivery)
    real_read, real_update = budget_notice.read_metadata, budget_notice.update_delivery
    published = []
    meter.metadata_callback = published.append
    secret = 'PRIVATE raw exception /auth/config/token must not be retained'
    def capture_failure(*args, **kwargs): raise OSError(secret)
    def callback_failure(_): raise RuntimeError(secret)
    def ack_failure(*args, **kwargs):
        if kwargs.get('fault') is None: raise OSError(secret)
        return real_update(*args, **kwargs)
    if phase == 'metadata_capture': monkeypatch.setattr(budget_notice, 'read_metadata', capture_failure)
    elif phase == 'original_capture': monkeypatch.setattr(meter, 'refresh_pending_original', capture_failure)
    elif phase == 'callback': meter.metadata_callback = callback_failure
    elif phase == 'ack': monkeypatch.setattr(budget_notice, 'update_delivery', ack_failure)
    observer.poll()
    current = real_read(observer.path)
    diagnostic = json.loads(observer.path.with_suffix('.diagnostic.json').read_bytes())
    assert diagnostic['phase'] == phase and diagnostic['code'] == code
    if phase == 'metadata_validation':
        assert diagnostic['last_observed_at'] == diagnostic['event_observed_at'] == 10
        assert diagnostic['scope_checks'] == dict(bound=True, terminal_not_seen=True,
            field_count=15, session=True, path=True, workspace=True, leaf_id=True,
            leaf_seen=False, baseline_count=True, baseline_seen=True, observed_order=True)
    else:
        assert not {'last_observed_at', 'event_observed_at', 'scope_checks'} & diagnostic.keys()
    assert diagnostic['acknowledged'] == observer.acknowledged == current['acknowledged'] == 1
    assert diagnostic['original_offset'] == meter.offset
    assert diagnostic['poll_elapsed_seconds'] >= diagnostic['phase_elapsed_seconds'] >= 0
    assert (diagnostic['pending_elapsed_seconds'] is None) == (phase == 'metadata_capture')
    assert current['pending'] is True and observer.fault == 'native_metadata_unavailable'
    assert meter.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert native_usage.metadata_stop(meter.snapshot(), required=True) == 'native_metadata_unavailable'
    assert len(published) == (1 if phase == 'ack' else 0)
    public = json.dumps(backend.public_events(dict(type='extension_error', error=secret)))
    assert public == '[]'
    assert secret not in json.dumps(diagnostic) + json.dumps(meter.snapshot())
    assert 'phase' not in meter.snapshot()['metadata_delivery']  # Private, not evidence/advice.
    saved = observer.path.with_suffix('.diagnostic.json').read_bytes()
    observer.poll()
    assert observer.path.with_suffix('.diagnostic.json').read_bytes() == saved
    assert real_read(observer.path)['acknowledged'] == 1  # No retry or recovery.


@pytest.mark.parametrize('binding', ['exact', 'missing', 'wrong-path', 'bad-nonce', 'duplicate'])
def test_retention_selects_only_exact_private_metadata_files_from_bounded_stream(tmp_path, binding):
    root = tmp_path/'instance'
    record = execution.origin(root, 1); record.mkdir(parents=True)
    sessions = root/'native-homes/001/sessions'; sessions.mkdir(parents=True)
    nonce = 'a' * 64
    path = sessions/('.pi-observation-' + nonce + '.json')
    private = dict(version=1, pending=True, acknowledged=1, sequence=2,
        event=dict(kind='turn_end', assistant_id='original-id'))
    path.write_text(json.dumps(private))
    diagnostic = dict(version=1, phase='metadata_validation', code='pi_metadata_response_receipt_invalid')
    path.with_suffix('.diagnostic.json').write_text(json.dumps(diagnostic))
    (sessions/'.pi-observation-unselected.json').write_text('UNSELECTED PRIVATE ORIGINAL')
    frame = dict(type=observation.BOUND, nonce=nonce, path=str(sessions/'pi-session.jsonl'))
    if binding == 'wrong-path': frame['path'] = '/unselected/session.jsonl'
    if binding == 'bad-nonce': frame['nonce'] = '../auth'
    frames = [] if binding == 'missing' else [frame, frame] if binding == 'duplicate' else [frame]
    append(record/'native.jsonl', *frames)
    destination = retain_installed_repair_diagnostics(root, tmp_path/'logs', None)
    retained = destination/'attempt-000/metadata-delivery.json'
    detail = destination/'attempt-000/metadata-delivery-diagnostic.json'
    manifest = json.loads((destination/'manifest.json').read_bytes())
    rows = {row['diagnostic']: row for row in manifest['records']}
    if binding == 'exact':
        assert retained.read_bytes() == path.read_bytes()
        assert json.loads(detail.read_bytes()) == diagnostic
        assert rows['attempt-000/metadata-delivery.json']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        path.unlink(); path.with_suffix('.diagnostic.json').unlink()
        retain_installed_repair_diagnostics(root, tmp_path/'logs', None)
        assert not retained.exists() and not detail.exists()  # No stale originals.
    else:
        assert not retained.exists() and not detail.exists()
        assert rows['attempt-000/metadata-delivery.json']['status'] == 'binding_unavailable'
    assert rows['attempt-001/metadata-delivery.json']['status'] == 'binding_unavailable'
    assert all('unselected' not in row['source'] for row in manifest['records'])


@pytest.mark.parametrize('mode', ['missing', 'pending', 'late'])
def test_terminal_source_specific_completion_rejects_missing_pending_late(tmp_path, mode):
    backend, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    if mode == 'pending': pending(observer, event(meter, kind='terminal'))
    elif mode == 'late':
        pending(observer, event(meter, kind='terminal')); observer.poll()
        assert observer.terminal_seen and not observer.fault
        pending(observer, event(meter))
    # Missing terminal cannot be satisfied by response/RPC/tool counts.
    if mode == 'pending':
        value = budget_notice.read_metadata(observer.path)
        value['pending_started_at'] = time.monotonic() - 6
        budget_notice._publish_metadata(observer.path, value)
    backend.finish_observation(meter, required=True)
    assert observer.fault and native_usage.metadata_stop(meter.snapshot(), required=True, terminal=True)


def test_terminal_metadata_does_not_require_false_full_native_call_capability(tmp_path):
    backend, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    append(meter.path, entry('assistant', 'native-user', message=message()))
    meter.poll()
    pending(observer, event(meter, kind='terminal', leaf='assistant')); observer.poll()
    result = backend.finish_observation(meter, required=True)
    assert not observer.fault and result['metadata_delivery']['acknowledged'] == 2
    assert native_usage.metadata_stop(result, required=True, terminal=True) is None
    assert result['coverage'] == 'terminal' and result['calls']['completed'] == 0
    assert result['tokens'] == pi_sessions.usage_tokens(USAGE)


def test_compact_delivery_helper_releases_lock_while_waiting_for_parent(tmp_path):
    _, meter, observer = bound(tmp_path)
    errors = []
    def deliver():
        try: budget_notice.deliver_metadata(observer.path, observer.proof['nonce'], event(meter), timeout=1)
        except Exception as error: errors.append(type(error).__name__)
    thread = threading.Thread(target=deliver)
    thread.start()
    try:
        deadline = time.monotonic() + 1
        while not budget_notice.read_metadata(observer.path)['pending'] and time.monotonic() < deadline:
            time.sleep(.001)
        observer.poll()  # Would deadlock if the helper retained its lock.
        thread.join(timeout=2)
        assert not thread.is_alive() and errors == [] and observer.acknowledged == 1
    finally:
        thread.join(timeout=2)


def test_resume_and_new_context_retain_consumed_windows_without_rebilling(tmp_path):
    backend, meter, observer = bound(tmp_path)
    pending(observer, event(meter, observed_at=1800000010)); observer.poll()
    append(meter.path, entry('assistant', 'native-user', message=message()))
    meter.poll()
    pending(observer, event(meter, kind='terminal', leaf='assistant', observed_at=1800000010)); observer.poll()
    prior = backend.finish_observation(meter, required=True)
    session = meter.session_binding()
    retained = dict(prior['context_windows'], windows_started=3)
    resumed = adapter()
    resumed.validate_resume_session(session, meter.workspace, meter.home)
    resumed.bind_attempt(meter.home, meter.workspace, session)
    resumed.launch(PROFILE, meter.workspace, None, None, 'Original scope')
    next_meter = resumed.meter(meter.home, meter.workspace, session, context_state=retained)
    next_observer = next_meter.observation
    # Clock order is attempt-local, not reset scientific spending or a new
    # native author/session. Even a lower new process epoch anchor is allowed.
    assert next_meter.thread == meter.thread and next_observer.last_observed_at == 0
    assert observer.last_observed_at == 1800000010
    next_observer.bound = True
    next_observer.prompt_hash = hashlib.sha256(PROMPT.encode()).hexdigest()
    append(next_meter.path, entry('repair-user', 'assistant', message=dict(role='user', content=PROMPT, timestamp=2)))
    next_meter.poll()
    next_observer.available = True
    next_observer.accept(event(next_meter, leaf='repair-user', turn_id='repair-user', baseline_leaf='assistant', baseline_count=2))
    assert next_observer.windows_started == 3
    assert next_observer.last_observed_at == 10
    assert next_observer.snapshot()['context_windows']['cursor']['source'] == observation.SOURCE
    assert next_meter.snapshot()['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    _, fresh, fresh_observer = bound(tmp_path / 'new-context', retained=retained)
    fresh_observer.accept(event(fresh))
    assert fresh_observer.windows_started == 4


@pytest.mark.parametrize('damage', ['missing', 'path', 'source', 'scope', 'duplicate', 'nonce'])
def test_controlled_resource_source_and_start_nonce_before_one_prompt(tmp_path, damage):
    backend, meter, observer = bound(tmp_path)
    proof = dict(observer.proof, required=True)
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', str(meter.path), PROMPT, observation=proof)
    binding.session_id = 'exact-session'
    startup = dict(type=observation.START, nonce=proof['nonce'], path=str(meter.path),
        workspace=str(meter.workspace), session_id='exact-session')
    if damage == 'nonce':
        startup['nonce'] = '0' * 64
        with pytest.raises(ValueError): binding.event(startup)
        assert not binding.prompt_sent
        return
    binding.event(startup)
    inventory = dict(commands=[observation.command_proof(proof['extension'])])
    row = inventory['commands'][0]
    if damage == 'missing': inventory['commands'] = []
    elif damage == 'path': row['sourceInfo']['path'] = '/arbitrary/worker-extension.ts'
    elif damage == 'source': row['sourceInfo']['source'] = 'local'
    elif damage == 'scope': row['sourceInfo']['scope'] = 'project'
    elif damage == 'duplicate': inventory['commands'].append(copy.deepcopy(row))
    command = binding.command('get_commands')
    with pytest.raises(ValueError): binding.response(dict(type='response', id=command['id'],
        command='get_commands', success=True, data=inventory))
    assert not binding.prompt_sent and 'prompt' not in binding.pending.values()


def test_extension_is_readonly_and_advice_preserves_results_without_native_calls(tmp_path):
    backend, meter, observer = bound(tmp_path)
    backend.stage_notice(meter.workspace)
    source_path = Path(observer.proof['extension'])
    source = source_path.read_text()
    assert source_path.parent == meter.home / 'sessions'
    assert not source_path.stat().st_mode & 0o222
    helper = meter.workspace / 'work' / budget_notice.HELPER
    assert not helper.stat().st_mode & 0o222
    assert not (meter.workspace / 'work' / budget_notice.EMPTY_CONFIG).exists()
    for forbidden in ('pi.registerTool(', 'pi.registerProvider(', 'pi.sendMessage(', 'pi.sendUserMessage(', 'ctx.compact(', 'continue: true'):
        assert forbidden not in source
    assert 'event.parentToolCallId !== undefined' in source
    assert 'structuredContent: event.structuredContent' in source
    assert 'Buffer.byteLength(output.additionalContext, "utf8") > 200' in source
    assert 'compactionEntry' in source and '.slice(compactCount)' in source
    assert observer.proof['nonce'] not in observation.DESCRIPTION
    assert backend.public_events(dict(type=observation.BOUND, **observer.proof)) == []


def test_private_observer_namespace_exact_ro_exception_and_local_alias_refusal(tmp_path):
    backend, meter, observer = bound(tmp_path)
    backend.stage_notice(meter.workspace)
    source = Path(observer.proof['extension'])
    facts = dict(root=tmp_path, work=meter.workspace, sessions=meter.home / 'sessions',
        auth=tmp_path / 'agent', runtime=(), observer_sources=(source,))
    argv = pi_namespace.arguments_from_facts(facts)
    writable = [argv[i + 1] for i, arg in enumerate(argv) if arg == '--bind']
    assert writable == [str(meter.home / 'sessions'), str(tmp_path / 'agent')]
    assert argv[-3:] == ['--ro-bind', str(source), str(source)]
    os.link(source, meter.workspace / 'work' / 'private-source-alias')
    from scidiscovery.research_loop.experiments import UnsupportedExecution
    with pytest.raises(UnsupportedExecution, match='private observer source'):
        pi_namespace._load_execution_mounts(tmp_path, {}, meter.workspace,
            context=None, source_facts=None, selected_code=None, mounts=[], code_root=None,
            cwd=meter.workspace, metadata_root=None, output=None, output_mounts=[],
            value=dict(private_observer_source=str(source)))


def test_startup_uses_supported_private_rpc_ui_frame_not_guarded_raw_stdout(tmp_path):
    _, meter, observer = bound(tmp_path)
    frame = dict(type=observation.START, nonce=observer.proof['nonce'], path=str(meter.path),
        workspace=str(meter.workspace), session_id='exact-session')
    wire = dict(type='extension_ui_request', id='native-ui-id', method='notify',
        notifyType='info', message=json.dumps(frame))
    assert pi_transport.observation_start_frame(wire) == frame
    assert adapter().public_events(wire) == []
    with pytest.raises(ValueError): pi_transport.observation_start_frame(dict(wire, unexpected=True))
    assert pi_transport.observation_start_frame(dict(wire, method='setStatus')) is None
    source = observation.extension_source(meter.workspace, observer.proof)
    assert 'ctx.ui.notify(JSON.stringify' in source and 'process.stdout.write' not in source


def test_qualified_internal_inventory_start_allows_exactly_one_prompt(tmp_path):
    _, meter, observer = bound(tmp_path)
    proof = dict(observer.proof, required=True)
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', str(meter.path), PROMPT, observation=proof)
    binding.session_id = 'exact-session'
    binding.event(dict(type=observation.START, nonce=proof['nonce'], path=str(meter.path),
        workspace=str(meter.workspace), session_id='exact-session'))
    command = binding.command('get_commands')
    prompt = binding.response(dict(type='response', id=command['id'], command='get_commands', success=True,
        data=dict(commands=[observation.command_proof(proof['extension'])])))
    assert prompt['type'] == 'prompt' and prompt['message'] == PROMPT
    assert binding.observer_bound and list(binding.pending.values()) == ['prompt']
    with pytest.raises(ValueError): binding.event(dict(type=observation.START, nonce=proof['nonce'],
        path=str(meter.path), workspace=str(meter.workspace), session_id='exact-session'))
    assert list(binding.pending.values()) == ['prompt']


@contextmanager
def native_peer(final, *, compact=False, on_first_request=None):
    """At most three main responses + four real summary POSTs, loopback only."""
    requests, errors = [], []
    stop = threading.Event()
    main_count = summary_count = 0
    turn_summary = 'IDENTICAL NATIVE SUMMARY'
    # compact() composes split-turn checkpoints itself. A history-summary
    # response must reproduce that composition, not just the prefix response.
    history_summary = ('No prior history.\n\n---\n\n'
        '**Turn Context (split turn):**\n\n' + turn_summary)
    # The first split keeps the read tool outside its summarized history, so
    # its model checkpoint names that read. On the next compaction the SDK
    # itself appends the same file-operation trailer. Both persisted summaries
    # are therefore genuinely identical without fabricating session entries.
    turn_summary += '\n\n<read-files>\nscience-task.json\n</read-files>'
    class Handler(BaseHTTPRequestHandler):
        def setup(self): self.request.settimeout(2); super().setup()
        def log_message(self, *args): pass
        def do_POST(self):
            nonlocal main_count, summary_count
            try:
                size = int(self.headers.get('Content-Length', '0'))
                assert 0 < size <= 256 * 1024 and self.path == '/v1/chat/completions'
                body = json.loads(self.rfile.read(size)); requests.append(body)
                assert len(requests) <= (7 if compact else 2)
                assert body['model'] == 'exact-model'
                assert self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential'
                main = bool(body.get('tools'))
                if main:
                    main_count += 1
                    assert main_count <= (3 if compact else 2)
                    if main_count == 1 and on_first_request is not None: on_first_request()
                if main and main_count == 1:
                    delta = dict(role='assistant', tool_calls=[dict(index=0, id='native-read', type='function',
                        function=dict(name='read', arguments=json.dumps(dict(path='science-task.json', limit=1))))])
                    finish, used = 'tool_calls', 900
                else:
                    if main:
                        text = '{broken' if compact and main_count == 2 else final
                    else:
                        summary_count += 1
                        assert compact and summary_count <= 4
                        users = [m['content'] for m in body['messages'] if m.get('role') == 'user']
                        assert len(users) == 1
                        prompt = users[0]
                        if isinstance(prompt, list):
                            assert all(block.get('type') == 'text' for block in prompt)
                            prompt = ''.join(block['text'] for block in prompt)
                        # Match the native generateTurnPrefixSummary() and
                        # generateSummaryWithUsage() prompt forms, not request
                        # order: split summaries may also request history.
                        if prompt.startswith('# Conversation\n'):
                            assert ('\n\n# Instructions\nThe messages above are earlier context '
                                'from an ongoing conversation.') in prompt
                            text = turn_summary
                        else:
                            assert prompt.startswith('<conversation>\n')
                            assert '\n</conversation>\n\n' in prompt
                            assert ('The messages above are NEW conversation messages to incorporate '
                                'into the existing summary provided in <previous-summary> tags.' in prompt
                                or 'The messages above are a conversation to summarize. Create a '
                                'structured context checkpoint summary' in prompt)
                            text = history_summary
                    delta = dict(role='assistant', content=text)
                    finish, used = 'stop', 900 if compact and main and main_count == 2 else 20
                chunks = [dict(choices=[dict(index=0, delta=delta, finish_reason=None)]),
                    dict(choices=[dict(index=0, delta={}, finish_reason=finish)]),
                    dict(choices=[], usage=dict(prompt_tokens=used, completion_tokens=5, total_tokens=used + 5))]
                wire = b''.join(b'data: ' + json.dumps(dict(id='fixture-' + str(len(requests)),
                    object='chat.completion.chunk', created=1, model='exact-model', **chunk)).encode() + b'\n\n'
                    for chunk in chunks) + b'data: [DONE]\n\n'
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(wire))); self.send_header('Connection', 'close'); self.end_headers()
                self.wfile.write(wire); self.wfile.flush(); self.close_connection = True
            except Exception as error:
                errors.append(type(error).__name__); self.close_connection = True
    server = HTTPServer(('127.0.0.1', 0), Handler); server.timeout = .05
    def serve():
        while not stop.is_set(): server.handle_request()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    try: yield 'http://127.0.0.1:' + str(server.server_port) + '/v1', requests, errors
    finally:
        stop.set(); thread.join(timeout=3); server.server_close()
        assert not thread.is_alive()


@pytest.mark.process_e2e
@pytest.mark.parametrize('compact', [False, True], ids=['qualified-tool-two-post-notice', 'real-repeated-summary-compact-repair-retains-windows'])
def test_installed_pi_accepted_w_metadata_notice_and_native_compaction(tmp_path, monkeypatch, record_property, execution_registry, compact):
    diagnostic_log_dir = os.environ.get('SCID_TEST_LOG_DIR')
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    payload = dict(summary='Mechanical observation fixture', scientific_result='No scientific qualification.',
        limitations=['Loopback transport only.'], implementation_selection=None, stage_intent='review',
        consultation_question='Does the retained native observation leave any unsupported mechanical claim?',
        mechanism_findings=[dict(mechanism='Fixture', basis='observed', finding='Mechanical native boundary.')])
    final = ' \n' + json.dumps(payload, ensure_ascii=False) + '\n\t'
    with native_peer(final, compact=compact) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        if compact:
            config = c.read(agent / 'settings.json')
            config['compaction'] = dict(enabled=True, reserveTokens=130272, keepRecentTokens=0)
            c.write(agent / 'settings.json', config)
        value = options(tmp_path, launcher=[launcher], max_tokens=10000, max_seconds=25, max_steps=3,
            experiment_mode='local', native_options=dict(OPTIONS, workspace='workspace-write'),
            resume_policy=dict(RESUME), worker_fragment_policy=dict(context='same_session', max_context_windows=1),
            node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=25) for role in 'HWAE'})
        value['public_root'].mkdir(); value['objective'].write_text('Finite mechanical native observation check.')
        research_config.prepare(**value)
        root = value['root']; c.build_job(root, 1, 'W', [])
        c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'pi_observation_fixture')
        result = None
        collector_stderr = None
        try:
            result = collect_separately(root, timeout=45)
            collector_stderr = result.stderr
        except subprocess.TimeoutExpired as error:
            collector_stderr = error.stderr
            raise
        finally:
            if diagnostic_log_dir and (compact or result is None or result.returncode != 0):
                diagnostics = retain_installed_repair_diagnostics(root,
                    str(Path(diagnostic_log_dir) / ('compact-repair' if compact else 'metadata-notice')),
                    collector_stderr)
                assert diagnostics is not None and (diagnostics / 'manifest.json').is_file()
                record_property('pi_observation_diagnostics', str(diagnostics))
        first = execution.origin(root, 1)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert errors == []
        main_requests = [body for body in requests if body.get('tools')]
        assert len(main_requests) == (3 if compact else 2)
        if not compact: assert len(requests) == 2
        # Advice reaches the real naturally required second POST, not a prompt
        # injection or forced continuation. Original result text is retained.
        tool_messages = [m for m in main_requests[1]['messages'] if m.get('role') == 'tool']
        assert len(tool_messages) == 1 and 'Token budget risk.' in tool_messages[0]['content']
        for body in requests:
            serialized = json.dumps(body, ensure_ascii=False)
            assert '.pi-observation-' not in serialized and 'pi_observer_bound' not in serialized
        records = execution.records(root, 1)
        assert len(records) == (2 if compact else 1)
        outcomes = [c.read(record / 'native-outcome.json') for record in records]
        for outcome in outcomes:
            assert outcome['cleanup'] == 'observed_group_cleaned' and outcome['terminal_observed']
            assert outcome['metering']['metadata_delivery']['acknowledged'] > 0
            assert not outcome['metering']['metadata_delivery']['fault']
            assert outcome['metering']['coverage'] == 'terminal'
            assert outcome['metering']['observation_capabilities']['native_calls']['status'] == 'unsupported'
        assert (records[-1] / 'science-output.json').read_bytes() == final.encode('utf-8')
        if compact:
            assert (first / 'science-output.json').read_bytes() == b'{broken'
            session_path = Path(outcomes[-1]['native_identity']['session']['path'])
            rows = [json.loads(row) for row in session_path.read_bytes().split(b'\n') if row]
            compactions = [row for row in rows if row.get('type') == 'compaction']
            assert len(compactions) == 2 and len({row['id'] for row in compactions}) == 2
            assert compactions[0]['summary'] == compactions[1]['summary']
            assert outcomes[0]['metering']['context_windows']['windows_started'] == 3
            assert outcomes[1]['metering']['context_windows']['windows_started'] == 3
            assert outcomes[0]['metering']['context'] == dict(status='unavailable')
        else:
            assert outcomes[0]['metering']['context_windows']['windows_started'] == 1
            assert outcomes[0]['metering']['context']['current_window'] is True
        assert c.read(records[-1] / 'usage.json')['input_tokens'] >= 20
        assert c.read(root / 'settings.json')['experiment_mode'] == 'local'  # Explicit fixture declaration.
        assert adapter().default_experiment_mode() == 'local'
        assert RESUME['protocol'] == 'pi_rpc_v1'  # Explicit one-repair fixture; defaults tested separately.


FAULT_WRAPPER = r'''#!/usr/bin/env python3
import fcntl,json,os,pathlib,subprocess,sys,threading,time
launcher=__LAUNCHER__
fault=__FAULT__
args=sys.argv[1:]
path=pathlib.Path(args[args.index('--session')+1])
child=subprocess.Popen([launcher,*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
metadata=None
changed=False
stop=threading.Event()
def replace(value):
 temporary=metadata.with_name(metadata.name+'.fixture-tmp')
 temporary.write_text(json.dumps(value,separators=(',',':')))
 temporary.replace(metadata)
def forward():
 try:
  for line in sys.stdin.buffer:
   child.stdin.write(line);child.stdin.flush()
 finally:
  child.stdin.close()
def corrupt():
 global changed
 while not stop.wait(.001):
  if metadata is None or changed:continue
  try:value=json.loads(metadata.read_bytes())
  except (OSError,ValueError):continue
  event=value.get('event') or {}
  kind=event.get('kind')
  if not value.get('pending'):continue
  if fault!='timeout' or kind!='turn_end':continue
  # The native pending clock timeout itself is covered without races in the
  # source contract. This installed case injects its sticky source fault.
  with metadata.with_suffix('.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   value=json.loads(metadata.read_bytes())
   value['fault']='native_metadata_delivery_timeout'
   replace(value);changed=True
threading.Thread(target=forward,daemon=True).start()
watcher=threading.Thread(target=corrupt,daemon=True);watcher.start()
try:
 for line in child.stdout:
  value=json.loads(line)
  if value.get('type')=='extension_ui_request' and value.get('method')=='notify':
   try:frame=json.loads(value.get('message',''))
   except ValueError:frame={}
   if frame.get('type')=='pi_observer_start':
    metadata=path.parent/('.pi-observation-'+frame['nonce']+'.json')
    if fault=='nonce-spoof':
     frame['nonce']='0'*64;value['message']=json.dumps(frame,separators=(',',':'))
  if fault=='source-spoof' and value.get('command')=='get_commands' and value.get('success'):
   for command in value['data']['commands']:
    if command.get('source')=='extension':command['sourceInfo']['path']='/unselected/worker-extension.ts'
  if fault=='timeout' and value.get('type')=='message_end' and value.get('message',{}).get('role')=='assistant':
   with metadata.with_suffix('.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    current=json.loads(metadata.read_bytes());current['fault']='native_metadata_delivery_timeout'
    replace(current);changed=True
  if fault=='metadata-loss' and value.get('type')=='agent_settled':
   metadata.unlink()
  sys.stdout.buffer.write((json.dumps(value,ensure_ascii=False)+'\n').encode());sys.stdout.buffer.flush()
 child.wait(timeout=5)
 if fault=='late' and metadata is not None:
  value=json.loads(metadata.read_bytes())
  assert value['event']['kind']=='terminal' and value['pending'] is False
  value.update(sequence=value['sequence']+1,pending=True,pending_started_at=time.monotonic())
  value['event']['kind']='context'
  replace(value)
finally:
 stop.set();watcher.join(timeout=1);child.stdout.close()
 if child.poll() is None:child.terminate();child.wait(timeout=5)
sys.exit(child.returncode)
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('fault', ['source-spoof', 'nonce-spoof', 'timeout', 'metadata-loss', 'late'])
def test_installed_pi_metadata_fault_no_replay_preserves_native_original_and_bill_floor(
        tmp_path, monkeypatch, record_property, execution_registry, fault):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    wrapper = tmp_path / 'pi-metadata-fault'
    wrapper.write_text(FAULT_WRAPPER.replace('__LAUNCHER__', repr(launcher)).replace('__FAULT__', repr(fault)))
    wrapper.chmod(0o700)
    final = ' \n' + json.dumps(dict(summary='UTF-8 科学原件', scientific_result='Mechanical fixture only.',
        limitations=['Not scientific evidence.'], implementation_selection=None, stage_intent='review',
        consultation_question='Which limitation remains after this exact metadata transport failure?',
        mechanism_findings=[dict(mechanism='Fixture', basis='observed', finding='Bounded transport.')]),
        ensure_ascii=False) + '\n\t'
    with native_peer(final) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        value = options(tmp_path, launcher=[str(wrapper)], max_tokens=10000, max_seconds=25, max_steps=3,
            experiment_mode='local', native_options=dict(OPTIONS, workspace='workspace-write'),
            resume_policy=dict(RESUME), worker_fragment_policy=dict(context='same_session', max_context_windows=1),
            node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=25) for role in 'HWAE'})
        value['public_root'].mkdir(); value['objective'].write_text('Finite native metadata fault check.')
        research_config.prepare(**value)
        root = value['root']; c.build_job(root, 1, 'W', [])
        original_job = (root / 'jobs/001.json').read_bytes()
        c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'pi_metadata_fault_fixture')
        result = collect_separately(root, timeout=45)
        assert result.returncode != 0
        record = execution.origin(root, 1); outcome = c.read(record / 'native-outcome.json')
        assert outcome['cleanup'] == 'observed_group_cleaned'
        assert not (record / 'result-identity.json').exists() and not (record / 'output-repair.json').exists()
        assert len(execution.records(root, 1)) == 1 and (root / 'jobs/001.json').read_bytes() == original_job
        assert errors == []
        if fault in {'source-spoof', 'nonce-spoof'}:
            assert requests == [] and outcome['terminal_observed'] is False
        else:
            assert 1 <= len(requests) <= 2
            metering = outcome['metering']
            assert metering['metadata_delivery']['fault']
            assert metering['tokens']['input_tokens'] >= 900
            assert metering['tokens']['output_tokens'] >= 5
            session = root / 'native-homes/001/sessions/pi-session.jsonl'
            rows = [json.loads(row) for row in session.read_bytes().split(b'\n') if row]
            assert sum(row.get('type') == 'message' and row['message'].get('role') == 'user' for row in rows) == 1
            if fault in {'metadata-loss', 'late'}:
                assert len(requests) == 2
                assert any(row.get('type') == 'message' and row['message'].get('role') == 'assistant'
                    and ''.join(block.get('text', '') for block in row['message']['content']) == final for row in rows)


@pytest.mark.process_e2e
def test_installed_pi_real_delivery_timeout_waits_without_lock_then_aborts_no_next_post(
        tmp_path, monkeypatch, record_property, execution_registry):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    root = tmp_path / 'instance'
    release = threading.Event()
    holders = []
    def hold_review():
        # First native context was already acknowledged before this POST. Hold
        # the EXISTING host review writer at the next persisted tool boundary,
        # not the metadata lock: the native helper must really expire at 5s.
        path = root / 'host-runtime/experiment-control/001/accept.lock'
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        def unlock():
            try: release.wait(6)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)
        thread = threading.Thread(target=unlock, daemon=True); holders.append(thread); thread.start()
    with native_peer('{not-reached}', on_first_request=hold_review) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        value = options(tmp_path, launcher=[launcher], max_tokens=10000, max_seconds=25, max_steps=3,
            experiment_mode='local', native_options=dict(OPTIONS, workspace='workspace-write'),
            resume_policy=dict(RESUME), worker_fragment_policy=dict(context='same_session', max_context_windows=1),
            node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=25) for role in 'HWAE'})
        value['public_root'].mkdir(); value['objective'].write_text('Finite real metadata timeout fixture.')
        research_config.prepare(**value); c.build_job(root, 1, 'W', [])
        c.write(root / 'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'pi_real_timeout_fixture')
        try: result = collect_separately(root, timeout=45)
        finally:
            release.set()
            for thread in holders:
                thread.join(timeout=2); assert not thread.is_alive()
        assert result.returncode != 0 and len(requests) == 1 and errors == []
        record = execution.origin(root, 1); outcome = c.read(record / 'native-outcome.json')
        assert outcome['cleanup'] == 'observed_group_cleaned'
        assert outcome['metering']['metadata_delivery']['fault']
        assert outcome['metering']['tokens']['input_tokens'] >= 900
        rows = [json.loads(row) for row in (record / 'native.jsonl').read_bytes().split(b'\n') if row]
        binding = next(row for row in rows if row.get('type') == observation.BOUND)
        metadata = Path(binding['path']).parent / ('.pi-observation-' + binding['nonce'] + '.json')
        assert budget_notice.read_metadata(metadata)['fault'] == 'native_metadata_delivery_timeout'
        assert not (record / 'result-identity.json').exists() and len(execution.records(root, 1)) == 1
