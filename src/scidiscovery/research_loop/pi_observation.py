"""Qualified Pi observations; billing remains owned by pi_sessions.SessionUsage.

The controlled extension only observes native persisted boundaries and patches an
existing top-level tool result with the common bounded advice. No tool, provider,
model request, prompt, continuation, ledger or scientific policy is introduced.
Private source proofs/cursors never belong in the public numeric context view.
observed_at is observer capture time in epoch seconds, wall-anchored once by
Node's performance.timeOrigin and advanced by its monotonic performance.now().
Ordering is per observer attempt, not a provider-request timestamp or a budget
clock. SDK context amounts, spent windows/tokens and admission clocks retain
their existing owners across attempts, even when the session author is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from . import budget_notice
from .native_usage import MAX_SAFE_INTEGER, context, context_windows

EXTENSION = '.scid-pi-observation.ts'
COMMAND = 'scid-internal-observation-v1'
DESCRIPTION = 'Framework native observation adapter v1 (no action)'
SOURCE = 'pi_persisted_boundaries_v1'
START = 'pi_observer_start'
BOUND = 'pi_observer_bound'
CALL_LIMITATION = 'pi_sdk_auxiliary_retry_and_cache_warming_calls_not_exhaustively_receipted'
# Private diagnostics only. Never serialize arbitrary exception text/arguments.
_BOUNDARY_FAULTS = dict(metadata_capture='pi_metadata_capture_failed',
    watermark_validation='pi_metadata_watermark_validation_failed',
    original_capture='pi_metadata_original_capture_failed',
    metadata_validation='pi_metadata_validation_failed',
    callback='pi_metadata_callback_failed', ack='pi_metadata_ack_failed')
_VALIDATION_FAULTS = frozenset({
    'pi_metadata_watermark_invalid', 'pi_metadata_unaccepted_watermark',
    'pi_metadata_clock_invalid', 'pi_metadata_watermark_gap',
    'pi_metadata_original_invalid', 'pi_metadata_source_or_scope_invalid',
    'pi_metadata_baseline_invalid', 'pi_metadata_baseline_changed',
    'pi_metadata_boundary_invalid', 'pi_metadata_native_user_invalid',
    'pi_metadata_native_user_missing', 'pi_metadata_compaction_pending',
    'pi_metadata_compaction_delta_invalid', 'pi_metadata_compaction_boundary_invalid',
    'pi_metadata_response_receipt_invalid', 'pi_metadata_response_boundary_invalid',
    'pi_metadata_callback_missing'})


def capability(fact):
    supported = fact in {'tokens', 'terminal', 'context', 'compactions'}
    return dict(status='supported' if supported else 'unsupported',
        reason=None if supported else CALL_LIMITATION if fact == 'native_calls' else 'pi_observation_unsupported')


def command_proof(extension):
    return dict(name=COMMAND, description=DESCRIPTION, source='extension',
        sourceInfo=dict(path=str(extension), source='cli', scope='temporary', origin='top-level'))


def validate_start(value, proof, *, session=None):
    if (not isinstance(value, dict) or set(value) != {'type', 'nonce', 'path', 'workspace', 'session_id'}
            or value.get('type') != START or value.get('nonce') != proof['nonce']
            or value.get('path') != proof['path'] or value.get('workspace') != proof['workspace']
            or not isinstance(value.get('session_id'), str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,255}', value['session_id']) is None
            or session is not None and value['session_id'] != session):
        raise ValueError('pi_observer_start_invalid')
    return dict(value, type=BOUND)


def _id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value) is not None


class Observation:
    """Pure original-fact acceptance plus an explicit compact storage owner.

    Original entries are passed by the existing session reader. This consumer
    never reopens them, estimates billing, or debits another usage mirror.
    """
    def __init__(self, meter, proof, *, retained=None):
        self.meter, self.proof = meter, dict(proof)
        self.path = Path(proof['metadata'])
        self.available = self.required = self.bound = self.pending = self.terminal_seen = False
        self.attempt = None
        self.prompt_hash = None
        self.acknowledged = self.sequence = 0
        self.fault = None
        self.turn = self.leaf = None
        self.current = dict(status='unavailable')
        self.entries = {}
        self.compactions = set()
        self.compact_pending = False
        self.compact_cursor = None
        self.compact_index = 0
        self.responses = {}
        self.windows = context_windows(retained) if retained is not None else dict(
            coverage='awaiting', windows_started=0, reason='context_awaiting_session')
        self.windows_started = self.windows['windows_started']
        self.window_fault = self.windows.get('reason') if self.windows['coverage'] == 'unknown' else None
        cursor = retained.get('cursor') if isinstance(retained, dict) else None
        if meter.resumed and (not isinstance(cursor, dict) or cursor.get('thread_id') != meter.thread
                or cursor.get('source') != SOURCE or cursor.get('leaf') not in meter.seen):
            self.window_fault = self.window_fault or 'context_cursor_changed'
        self.initial_counted = meter.resumed
        self.baseline_leaf = None
        self.last_observed_at = 0

    def configure(self, attempt, *, required):
        self.attempt, self.required, self.available = attempt, required, True
        budget_notice.start_delivery(self.path, source=SOURCE, nonce=self.proof['nonce'],
            cwd=self.meter.workspace, attempt=attempt)

    def bind(self, value):
        expected = validate_start(dict(value, type=START), self.proof, session=self.meter.thread)
        if self.bound or expected != value: raise ValueError('pi_observer_binding_changed')
        self.bound = True

    def entry(self, value):
        """Keep only compact private identities from the original append delta."""
        row = dict(type=value['type'], parent=value['parentId'], index=len(self.meter.seen))
        if value['type'] == 'message':
            message = value.get('message') or {}
            row['role'] = message.get('role')
            if row['role'] == 'assistant':
                row['status'] = ('cancelled' if message.get('stopReason') == 'aborted' else
                    'failed' if message.get('stopReason') in {'error', 'failed'} else 'completed')
                row['profile'] = dict(model=str(message.get('provider')) + '/' + str(message.get('model')),
                    reasoning_effort=message.get('thinkingLevel', (self.meter.profile or {}).get('reasoning_effort')),
                    service_tier=None)
                content = message.get('content')
                if message.get('stopReason') == 'stop' and isinstance(content, list):
                    text = ''.join(b['text'] for b in content
                        if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str))
                    raw = text.encode('utf-8')
                    row['final'] = dict(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
            if row['role'] == 'user':
                content = message.get('content')
                text = content if isinstance(content, str) else ''.join(
                    b['text'] for b in content if isinstance(b, dict) and b.get('type') == 'text') if isinstance(content, list) else None
                row['prompt_hash'] = hashlib.sha256(text.encode('utf-8')).hexdigest() if text is not None else None
        self.entries[value['id']] = row

    def invalidate(self):
        self.current = dict(status='unavailable')

    def fail(self, reason):
        self.fault = self.fault or reason
        self.window_fault = self.window_fault or 'context_observation_unavailable'
        self.invalidate()

    def _ancestor(self, child, ancestor):
        for _ in range(len(self.entries) + 1):
            if child == ancestor: return True
            if child not in self.entries: return False
            child = self.entries[child]['parent']
        return False

    def accept(self, event):
        """Pure acceptance from source binding and already acquired originals."""
        meter = self.meter
        fields = {'kind', 'session_id', 'path', 'workspace', 'leaf', 'turn_id', 'prompt_hash',
            'baseline_leaf', 'baseline_count', 'compaction_id', 'pre_cursor', 'assistant_id',
            'status', 'context', 'observed_at'}
        if (not self.bound or self.terminal_seen or not isinstance(event, dict) or set(event) != fields
                or event['session_id'] != meter.thread or event['path'] != str(meter.path)
                or event['workspace'] != str(meter.workspace) or not _id(event['leaf'])
                or event['leaf'] not in meter.seen
                or type(event['baseline_count']) is not int or not 0 <= event['baseline_count'] <= 65536
                or event['baseline_leaf'] is not None and event['baseline_leaf'] not in meter.seen
                or type(event['observed_at']) not in (int, float)
                or not self.last_observed_at <= event['observed_at'] <= MAX_SAFE_INTEGER):
            raise ValueError('pi_metadata_source_or_scope_invalid')
        baseline = event['baseline_leaf']
        baseline_index = ((self.entries.get(baseline) or {}).get('index')
            if baseline != meter.original_leaf else meter.original_count)
        if baseline is None: baseline_index = 0
        if baseline_index != event['baseline_count']:
            raise ValueError('pi_metadata_baseline_invalid')
        if self.turn is not None and baseline != self.baseline_leaf:
            raise ValueError('pi_metadata_baseline_changed')
        kind = event['kind']
        if kind not in {'context', 'tool_result', 'turn_end', 'compact_start', 'compact', 'compact_failed', 'terminal'}:
            raise ValueError('pi_metadata_boundary_invalid')
        turn = event['turn_id']
        if turn is not None:
            row = self.entries.get(turn) or {}
            if (not _id(turn) or row.get('role') != 'user' or row.get('prompt_hash') != self.prompt_hash
                    or event['prompt_hash'] != self.prompt_hash or self.prompt_hash is None
                    or self.turn not in (None, turn) or not self._ancestor(turn, event['baseline_leaf'])
                    or not self._ancestor(event['leaf'], turn)):
                # Compaction retains original ancestry even if it drops that user
                # from future model context. Never correlate by text/turnIndex.
                raise ValueError('pi_metadata_native_user_invalid')
            if self.turn is None:
                self.turn, self.baseline_leaf = turn, event['baseline_leaf']
                if not self.initial_counted:
                    self.windows_started += 1
                    self.initial_counted = True
        elif self.turn is not None or kind not in {'compact_start', 'compact', 'compact_failed'}:
            raise ValueError('pi_metadata_native_user_missing')
        if kind == 'compact_start':
            if self.compact_pending: raise ValueError('pi_metadata_compaction_pending')
            self.compact_pending = True
            self.compact_cursor = event['leaf']
            self.compact_index = (self.entries.get(event['leaf']) or {}).get('index', meter.original_count)
        if kind == 'compact':
            identity, cursor = event['compaction_id'], event['pre_cursor']
            row = self.entries.get(identity) or {}
            delta = [key for key, item in self.entries.items() if item['type'] == 'compaction'
                and item['index'] > self.compact_index and self._ancestor(event['leaf'], key)]
            if (not self.compact_pending or not _id(identity) or identity in self.compactions
                    or row.get('type') != 'compaction' or cursor != self.compact_cursor
                    or delta != [identity] or not self._ancestor(row.get('parent'), cursor)):
                raise ValueError('pi_metadata_compaction_delta_invalid')
            self.compactions.add(identity)
            self.compact_pending = False
            self.windows_started += 1
        elif event['compaction_id'] is not None:
            raise ValueError('pi_metadata_compaction_boundary_invalid')
        if kind == 'compact_failed': self.compact_pending = False
        if kind == 'terminal' and self.compact_pending:
            raise ValueError('pi_metadata_compaction_pending')
        if kind == 'turn_end':
            identity, status = event['assistant_id'], event['status']
            row = self.entries.get(identity) or {}
            if (not _id(identity) or row.get('role') != 'assistant' or identity in self.responses
                    or status not in {'completed', 'failed', 'cancelled'} or status != row.get('status')
                    or not self._ancestor(event['leaf'], identity)):
                raise ValueError('pi_metadata_response_receipt_invalid')
            self.responses[identity] = status
        elif event['assistant_id'] is not None or event['status'] is not None:
            raise ValueError('pi_metadata_response_boundary_invalid')
        self.leaf = event['leaf']
        self.last_observed_at = event['observed_at']
        self.invalidate()
        # The SDK owns estimate/null semantics; no cumulative-token division.
        sample = event['context']
        if kind not in {'compact_start', 'compact', 'compact_failed'} and self.turn is not None and isinstance(sample, dict):
            safe = context(dict(status='last_reported', total_tokens=sample.get('tokens'),
                model_context_window=sample.get('contextWindow'), observed_at=event['observed_at'], current_window=True))
            self.current = safe
        if kind == 'terminal': self.terminal_seen = True

    def _diagnose(self, phase, error, poll_started, phase_started, pending_started, failed_at, event=None):
        """Best-effort PRIVATE fault boundary, not metering or acceptance input."""
        code = error.args[0] if type(error) is ValueError and len(error.args) == 1 else None
        if not isinstance(code, str) or code not in _VALIDATION_FAULTS:
            code = _BOUNDARY_FAULTS[phase]
        value = dict(version=1, phase=phase, code=code,
            poll_elapsed_seconds=max(0, failed_at - poll_started),
            phase_elapsed_seconds=max(0, failed_at - phase_started),
            pending_elapsed_seconds=(max(0, failed_at - pending_started)
                if pending_started is not None else None),
            sequence=self.sequence, acknowledged=self.acknowledged,
            original_offset=self.meter.offset, original_faulted=bool(self.meter.fault),
            original_backlog=self.meter.reason == 'session_read_backlog')
        if phase == 'metadata_validation' and isinstance(event, dict):
            observed = event.get('observed_at')
            count = event.get('baseline_count')
            # Numeric clock facts only; never copy raw event/source identifiers.
            value['last_observed_at'] = self.last_observed_at
            if type(observed) is int or type(observed) is float and math.isfinite(observed):
                value['event_observed_at'] = observed
            value['scope_checks'] = dict(bound=bool(self.bound), terminal_not_seen=not self.terminal_seen,
                field_count=len(event), session=event.get('session_id') == self.meter.thread,
                path=event.get('path') == str(self.meter.path), workspace=event.get('workspace') == str(self.meter.workspace),
                leaf_id=_id(event.get('leaf')), leaf_seen=type(event.get('leaf')) is str and event['leaf'] in self.meter.seen,
                baseline_count=type(count) is int and 0 <= count <= 65536,
                baseline_seen=event.get('baseline_leaf') is None or type(event.get('baseline_leaf')) is str and event['baseline_leaf'] in self.meter.seen,
                observed_order=type(observed) in (int, float) and self.last_observed_at <= observed <= MAX_SAFE_INTEGER)
        try:
            budget_notice._publish_metadata(self.path.with_suffix('.diagnostic.json'), value)
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics cannot acknowledge, recover or replace the sticky fault.

    def poll(self):
        if not self.available or self.fault: return
        poll_started = phase_started = time.monotonic()
        phase, pending_started = 'metadata_capture', None
        try:
            value = budget_notice.read_metadata(self.path)
            phase, phase_started = 'watermark_validation', time.monotonic()
            if (value.get('source') != SOURCE or value.get('nonce') != self.proof['nonce']
                    or value.get('attempt') != self.attempt or value.get('cwd') != str(self.meter.workspace)
                    or type(value.get('sequence')) is not int or value['sequence'] < self.sequence
                    or type(value.get('acknowledged')) is not int or value['acknowledged'] != self.acknowledged
                    or type(value.get('pending')) is not bool):
                raise ValueError('pi_metadata_watermark_invalid')
            if value.get('fault') is not None:
                self.fail('native_metadata_delivery_failed'); return
            self.pending = value['pending']
            if not self.pending:
                if value['sequence'] != self.sequence: raise ValueError('pi_metadata_unaccepted_watermark')
                return
            started = value.get('pending_started_at')
            if type(started) not in (int, float) or not 0 < started <= time.monotonic():
                raise ValueError('pi_metadata_clock_invalid')
            pending_started = started
            if time.monotonic() - started >= 5:
                self.fail('native_metadata_delivery_timeout')
                budget_notice.update_delivery(self.path, self.proof['nonce'], value['sequence'], fault=self.fault)
                return
            if not self.bound: return  # Header-first bridge binding has not drained yet.
            if value['sequence'] != self.sequence + 1: raise ValueError('pi_metadata_watermark_gap')
            # The native append/publication may follow the meter's earlier
            # fstat-bounded read. Its sole original/billing owner refreshes AFTER
            # this pending delivery is acquired; pure acceptance never reads IO.
            phase, phase_started = 'original_capture', time.monotonic()
            self.meter.refresh_pending_original()
            if self.meter.fault: raise ValueError('pi_metadata_original_invalid')
            if time.monotonic() - started >= 5:
                self.fail('native_metadata_delivery_timeout')
                budget_notice.update_delivery(self.path, self.proof['nonce'], value['sequence'], fault=self.fault)
                return
            if self.meter.reason == 'session_read_backlog': return
            # Caught up: absent/forged native identities must fail, not wait for
            # unrelated growth to manufacture a future receipt.
            phase, phase_started = 'metadata_validation', time.monotonic()
            self.accept(value.get('event'))
            self.sequence = value['sequence']
            # Publish accepted windows/usage to the existing host review/admission
            # writer BEFORE the native helper can return to the next tool call.
            self.pending = False
            phase, phase_started = 'callback', time.monotonic()
            callback = getattr(self.meter, 'metadata_callback', None)
            if callback is None and self.required: raise ValueError('pi_metadata_callback_missing')
            if callback is not None: callback(self.meter.snapshot())
            phase, phase_started = 'ack', time.monotonic()
            budget_notice.update_delivery(self.path, self.proof['nonce'], self.sequence)
            self.acknowledged = self.sequence
        except Exception as error:
            failed_at = time.monotonic()
            self.fail('native_metadata_unavailable')
            try:
                budget_notice.update_delivery(self.path, self.proof['nonce'], value['sequence'], fault=self.fault)
            except (OSError, ValueError, TypeError, KeyError, UnboundLocalError): pass
            self._diagnose(phase, error, poll_started, phase_started, pending_started, failed_at,
                value.get('event') if phase == 'metadata_validation' else None)

    def finish(self):
        self.poll()
        if self.available and (not self.bound or not self.terminal_seen or self.pending
                or not self.acknowledged or self.meter.fault):
            self.fail('native_metadata_delivery_incomplete')

    def controlled_facts(self):
        """Compact private receipt from accepted originals, never a session read.

        Actor completeness is not a census of provider requests. Retain native
        user/branch/response identities without copying text or changing meters.
        """
        if (not self.available or not self.bound or not self.terminal_seen or self.pending
                or self.fault or self.compact_pending or self.turn is None
                or not self.sequence or self.sequence != self.acknowledged):
            raise ValueError('pi_authorship_delivery_incomplete')
        user = self.entries.get(self.turn) or {}
        users = [key for key, row in self.entries.items() if row.get('role') == 'user']
        assistants = {key: row for key, row in self.entries.items() if row.get('role') == 'assistant'}
        if (users != [self.turn] or user.get('prompt_hash') != self.prompt_hash
                or not self._ancestor(self.leaf, self.turn) or not assistants
                or set(assistants) != set(self.responses)
                or any(row.get('profile') != self.meter.profile
                    or row.get('status') != self.responses[key]
                    or not self._ancestor(key, self.turn) or not self._ancestor(self.leaf, key)
                    for key, row in assistants.items())
                or any(row['type'] in {'branch_summary', 'custom', 'custom_message', 'context_edit'}
                    or row.get('role') in {'custom', 'bashExecution', 'branchSummary', 'compactionSummary'}
                    for row in self.entries.values())):
            raise ValueError('pi_authorship_persisted_branch_incomplete')
        responses = [dict(id=key, status=row['status'], profile=row['profile'], final=row.get('final'))
            for key, row in assistants.items()]
        # The final persisted response, not pass text or agent_end, binds output.
        if responses[-1]['status'] != 'completed' or responses[-1]['final'] is None:
            raise ValueError('pi_authorship_final_response_missing')
        return dict(user=dict(id=self.turn, prompt_sha256=self.prompt_hash), leaf=self.leaf,
            baseline_leaf=self.baseline_leaf, baseline_count=(self.entries.get(self.baseline_leaf) or {}).get(
                'index', self.meter.original_count if self.baseline_leaf == self.meter.original_leaf else 0),
            baseline_offset=self.meter.baseline_offset, entry_count=len(self.meter.seen),
            branch_sha256=hashlib.sha256(json.dumps(self.entries, sort_keys=True,
                separators=(',', ':')).encode('utf-8')).hexdigest(), responses=responses,
            delivery=dict(source=SOURCE, nonce=self.proof['nonce'], attempt=self.attempt,
                sequence=self.sequence, acknowledged=self.acknowledged, pending=self.pending,
                fault=self.fault, terminal=self.terminal_seen))

    def snapshot(self):
        live = (self.available and self.bound and self.turn is not None and not self.pending
            and not self.fault and not self.window_fault and not self.meter.fault)
        windows = dict(coverage='live' if live else 'unknown' if self.fault or self.window_fault else 'awaiting',
            windows_started=self.windows_started, reason=self.window_fault or (
                None if live else 'context_awaiting_session'))
        if self.bound:
            windows['cursor'] = dict(thread_id=self.meter.thread, leaf=self.leaf, source=SOURCE)
        return dict(context=dict(self.current) if live else dict(status='unavailable'),
            turn_id=self.turn, calls={status: sum(v == status for v in self.responses.values())
                for status in ('completed', 'failed', 'cancelled')},
            native_calls_scope='persisted_assistant_response_receipts_lower_bound_not_all_native_calls',
            context_precision='native_sdk_estimate', context_windows=windows,
            metadata_delivery=dict(acknowledged=self.acknowledged, pending=self.pending, fault=self.fault))


def extension_source(work, proof):
    """Fixed observational code; nonce is private framing, never description/context."""
    binding = dict(proof, work=str(work), python=sys.executable,
        helper=str(Path(work) / 'work' / budget_notice.HELPER), command=COMMAND, description=DESCRIPTION)
    return '''// Controlled readonly observer. No tools, providers, prompts or continuations.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
const binding = ''' + json.dumps(binding, ensure_ascii=True, separators=(',', ':')) + ''';
export default function (pi) {
  let baselineCount = 0, baselineLeaf = null, user = null, promptHash = null;
  let compactCount = null, compactCursor = null;
  let unavailable = false;
  const on = (name, handler) => pi.on(name, (event, ctx) => {
    if (unavailable) return;
    try { return handler(event, ctx); }
    catch {
      unavailable = true;
      // Required context/identity admission must finish before native work can
      // cross another boundary. Optional observation failure only loses facts;
      // throwing would become extension_error and erase a valid native final.
      if (binding.required) {
        ctx.abort();
        throw new Error("Native metadata delivery failed");
      }
    }
  });
  // Private observer capture time: epoch seconds with process-monotonic order,
  // not adjustable Date.now(), a provider timestamp, or an execution grant.
  // Each new attempt has its own observer baseline; spent budgets do not reset.
  const observedAt = () => (performance.timeOrigin + performance.now()) / 1000;
  const hash = text => createHash("sha256").update(text, "utf8").digest("hex");
  const invoke = (args, input) => execFileSync(binding.python,
    [binding.helper, "--work", binding.work, "--attempt", "0", ...args],
    { input, encoding: "utf8", timeout: 6500, maxBuffer: 4096,
      stdio: [input === undefined ? "ignore" : "pipe", "pipe", "ignore"] });
  const send = (kind, ctx, extra = {}) => {
    const sm = ctx.sessionManager;
    const value = { kind, session_id: sm.getSessionId(), path: sm.getSessionFile(),
      workspace: ctx.cwd, leaf: sm.getLeafId(), turn_id: user, prompt_hash: promptHash,
      baseline_leaf: baselineLeaf, baseline_count: baselineCount,
      compaction_id: null, pre_cursor: null, assistant_id: null, status: null,
      context: ctx.getContextUsage() ?? null, observed_at: observedAt(), ...extra };
    invoke(["--delivery-path", binding.metadata, "--nonce", binding.nonce], JSON.stringify(value));
  };
  pi.registerCommand(binding.command, { description: binding.description, handler: async () => {} });
  on("session_start", (_event, ctx) => {
    const sm = ctx.sessionManager;
    // Use the supported RPC UI frame; Pi reserves/guards raw stdout.
    ctx.ui.notify(JSON.stringify({ type: "pi_observer_start", nonce: binding.nonce,
      path: sm.getSessionFile(), workspace: ctx.cwd, session_id: sm.getSessionId() }), "info");
  });
  on("before_agent_start", (_event, ctx) => {
    if (user !== null) return;
    const entries = ctx.sessionManager.getEntries();
    baselineCount = entries.length; baselineLeaf = ctx.sessionManager.getLeafId();
  });
  on("context", (_event, ctx) => {
    if (user === null) {
      const sm = ctx.sessionManager;
      const entries = sm.getEntries().slice(baselineCount);
      const users = entries.filter(e => e.type === "message" && e.message.role === "user");
      if (users.length !== 1 || !sm.getBranch().some(e => e.id === users[0].id))
        throw new Error("Native user boundary unavailable");
      const entry = users[0], content = entry.message.content;
      const text = typeof content === "string" ? content : content.filter(b => b.type === "text").map(b => b.text).join("");
      user = entry.id; promptHash = hash(text);
    }
    send("context", ctx);
  });
  on("turn_end", (event, ctx) => send("turn_end", ctx, {
    assistant_id: event.messageEntryId,
    status: event.outcome === "aborted" ? "cancelled" : event.outcome === "error" ? "failed" : "completed" }));
  on("session_before_compact", (_event, ctx) => {
    const entries = ctx.sessionManager.getEntries();
    compactCount = entries.length; compactCursor = entries.at(-1)?.id ?? null;
    send("compact_start", ctx, { context: null });
  });
  on("session_compact", (_event, ctx) => {
    // SDK event.compactionEntry can be OLD when two summaries are identical.
    // Only a unique new append on the active branch establishes this boundary.
    if (compactCount === null) throw new Error("Compaction cursor missing");
    const sm = ctx.sessionManager, branch = new Set(sm.getBranch().map(e => e.id));
    const delta = sm.getEntries().slice(compactCount).filter(e => e.type === "compaction" && branch.has(e.id));
    // Auxiliary usage may append during summarization; the cursor must remain
    // on this active branch, not necessarily the compaction's direct parent.
    if (delta.length !== 1 || (compactCursor !== null && !branch.has(compactCursor)))
      throw new Error("Compaction append delta unavailable");
    send("compact", ctx, { compaction_id: delta[0].id, pre_cursor: compactCursor, context: null });
    compactCount = null;
  });
  on("session_compact_failed", (_event, ctx) => {
    send("compact_failed", ctx, { context: null }); compactCount = null;
  });
  on("agent_settled", (_event, ctx) => send("terminal", ctx));
  on("tool_result", (event, ctx) => {
    if (event.parentToolCallId !== undefined || !Array.isArray(event.content)) return;
    send("tool_result", ctx);
    try {
      const value = JSON.parse(invoke([])), output = value.hookSpecificOutput;
      if (Object.keys(value).length !== 1 || !output || Object.keys(output).length !== 2 ||
          output.hookEventName !== "PostToolUse" || typeof output.additionalContext !== "string" ||
          Buffer.byteLength(output.additionalContext, "utf8") > 200) return;
      return { content: [...event.content, { type: "text", text: output.additionalContext }],
        structuredContent: event.structuredContent };
    } catch { /* Advice/readonly-marker failure never re-runs or fails work. */ }
  });
}
'''


def stage(work, proof):
    helper = budget_notice.stage_helper(work, empty_config=False)
    # Private native source is outside the scientific workspace and hidden by
    # Local's existing session-root exclusion. Only this exact file is RO-bound
    # back over the existing native session write exception.
    destination = Path(proof['extension'])
    temporary = destination.with_name(destination.name + '-' + os.urandom(8).hex())
    try:
        data = extension_source(work, proof).encode('utf-8')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass
    return helper
