"""Bounded token metadata observation for one exact native home/thread.

Session text is never exposed to a role. Cumulative counters cover reported model
usage only; a running request and platform reporting delay remain unmeasured.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import stat
import time

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens')
READ_BYTES = 1024 * 1024
LINE_BYTES = 65536
DISCOVERY_ENTRIES = 256
DISCOVERY_DIRECTORIES = 4096
MAX_SAFE_INTEGER = 2 ** 53 - 1

# The owner publishes only these compact transport facts. Consumers never
# recover a diagnosis from private stderr, session text or exception messages.
TRANSPORT_PHASES = frozenset({'launch_or_declaration', 'initialization_submit',
    'event_projection', 'initialization_response', 'thread_response', 'turn_response',
    'rpc_response', 'turn_completion', 'turn_start_notice', 'assistant_or_compaction',
    'context_report', 'native_notification', 'event_stream', 'metadata_delivery', 'native_execution', 'native_cleanup'})
TRANSPORT_CODES = frozenset({'terminal_status_unavailable', 'final_unavailable',
    'assistant_text_unavailable', 'turn_identity_changed', 'thread_identity_changed',
    'launch_binding_mismatch', 'rpc_response_unavailable', 'metadata_scalar_invalid',
    'native_io_error', 'native_projection_invalid', 'native_terminal_unreported',
    'native_metadata_unavailable', 'native_metadata_unsupported', 'native_execution_stopped'})


def transport_diagnostic(value):
    """Pure, fail-closed projection of an owner's small typed diagnosis."""
    if (not isinstance(value, dict)
            or any(not isinstance(value.get(key),str) for key in ('phase','code','original_call_state'))
            or value.get('phase') not in TRANSPORT_PHASES
            or value.get('code') not in TRANSPORT_CODES
            or value.get('original_call_state') not in {'not_submitted', 'unknown', 'terminal'}
            or any(type(value.get(key)) is not bool for key in ('thread_bound', 'turn_bound'))):
        return None
    result = {key: value[key] for key in ('phase', 'code', 'original_call_state', 'thread_bound', 'turn_bound')}
    result['next_action'] = {'not_submitted': 'recheck_before_same_task_restart',
        'unknown': 'reconcile_original_without_replay', 'terminal': 'inspect_terminal_original'}[result['original_call_state']]
    return result


def actionable_stop(outcome):
    """Public facts only; consumed budgets and originals remain on the outcome."""
    selected = transport_diagnostic(outcome.get('technical_diagnostic')) if isinstance(outcome, dict) else None
    if selected is None:
        return None
    # This does not set terminal_observed: a real failed native terminal is
    # terminal for reconciliation but cannot verify scientific completion.
    return dict(selected, category='technical', budgets_preserved=True,
        original_records=['native-intent.json', 'native-outcome.json'])


class MetadataUnavailable(ValueError):
    """The declared native launcher cannot provide required compact facts."""


def stream_binding(path):
    path = Path(path)
    info = path.stat()
    if path.resolve() != path or not stat.S_ISREG(info.st_mode):
        raise ValueError('Native original is not an ordinary file')
    return dict(path=str(path), identity=[info.st_dev, info.st_ino],
        size_bytes=info.st_size, mtime_ns=info.st_mtime_ns)


def validate_session_binding(value, thread, workspace, home):
    """Check the retained native hook identity without reading transcript bytes."""
    if (not isinstance(value, dict) or value.get('source') != 'native_metadata_hooks'
            or value.get('thread_id') != thread or value.get('workspace') != str(workspace)
            or value.get('home') != str(home) or not isinstance(value.get('path'), str)
            or not isinstance(value.get('identity'), list) or len(value['identity']) != 2
            or any(type(item) is not int for item in value['identity'])
            or type(value.get('offset')) is not int or value['offset'] < 0):
        raise MetadataUnavailable('worker_resume_metadata_unavailable')
    path = Path(value['path'])
    if (Path(home).resolve() != Path(home) or path.resolve() != path
            or not path.is_relative_to(Path(home) / 'sessions')):
        raise ValueError('Native session original path changed')
    current = stream_binding(path)
    if current['identity'] != value['identity'] or current['size_bytes'] < value['offset']:
        raise ValueError('Native session original replaced or truncated')
    return value


def metadata_stop(observation, *, required, terminal=False):
    """Missing native delivery is an operational stop, never a new W cadence."""
    if not required:
        return None
    delivery = observation.get('metadata_delivery') or {}
    if delivery.get('fault') or terminal and (not delivery.get('acknowledged') or delivery.get('pending')):
        return 'native_metadata_unavailable'
    return None


def context_windows(value):
    """Public numeric projection; private rollout cursors never leave the host."""
    if (not isinstance(value, dict) or value.get('coverage') not in {'live', 'unknown', 'awaiting'}
            or type(value.get('windows_started')) is not int
            or not 0 <= value['windows_started'] <= MAX_SAFE_INTEGER):
        return dict(coverage='unknown', windows_started=0, reason='context_observation_unavailable')
    result = {key: value[key] for key in ('coverage', 'windows_started')}
    allowed = {'context_observation_unavailable', 'context_history_unavailable', 'context_event_oversized',
        'context_event_invalid', 'context_cursor_changed', 'context_read_backlog', 'context_partial_line',
        'context_session_unavailable', 'context_awaiting_session', 'context_identity_unavailable',
        'context_compaction_unverified', 'context_compaction_identity_unavailable', 'context_compaction_capacity',
        'context_compaction_pending', 'context_capability_unavailable'}
    result['reason'] = value.get('reason') if value.get('reason') in allowed else None
    peak = value.get('peak_ratio')
    if type(peak) in (int, float) and 0 <= peak <= MAX_SAFE_INTEGER:
        result['peak_ratio'] = peak
    return result


def tokens(value):
    if (not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k] < 0 for k in FIELDS)
            or value['cached_input_tokens'] > value['input_tokens']):
        return None
    return {key: value[key] for key in FIELDS}


def terminal_attempt_tokens(terminal, observation):
    """Normalize only a terminal proven equal to the exact session counter.

    A turn meter is normally invocation-local. On some resumed Codex versions
    it is session-cumulative: the independently bound append-only counter must
    prove that interpretation, field for field, before subtracting anything.
    """
    baseline = tokens(observation.get('baseline_tokens'))
    live = tokens(observation.get('tokens'))
    if (tokens(terminal) is not None and baseline is not None and live is not None
            and observation.get('coverage') == 'live' and observation.get('has_observation') is True
            and any(baseline.values())
            and all(terminal[key] == baseline[key] + live[key] for key in FIELDS)):
        return live
    return terminal


def context(value):
    """Allowlist optional numeric telemetry, independently of billable counters."""
    if not isinstance(value, dict) or value.get('status') != 'last_reported':
        return dict(status='unavailable')
    used, window = value.get('total_tokens'), value.get('model_context_window')
    if (type(used) is not int or not 0 <= used <= MAX_SAFE_INTEGER
            or type(window) is not int or not 0 < window <= MAX_SAFE_INTEGER):
        return dict(status='unavailable')
    result = dict(status='last_reported', total_tokens=used, model_context_window=window, ratio=used / window)
    for key in ('reported_at', 'observed_at'):
        stamp = value.get(key)
        if type(stamp) in (int, float) and 0 <= stamp <= MAX_SAFE_INTEGER:
            result[key] = stamp
    for key in ('thread_id', 'turn_id'):
        identity = value.get(key)
        if isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', identity):
            result[key] = identity
    if value.get('current_window') is True:
        result['current_window'] = True
    return result


class BasicObservation:
    """Provider-neutral optional facts bound to the current native invocation.

    Adapters supply native reported values, never estimated capacity or a ratio
    of cumulative billing counters. Missing optional telemetry stays unknown.
    """

    def _basic_init(self):
        self.turn = None
        self.current_context = dict(status='unavailable')
        self.context_blocked=False
        self.context_min_observed_at=None
        self.calls=dict(completed=0,failed=0,cancelled=0)
        self.call_receipts={}
        self.capabilities={fact:dict(status='unsupported',reason='native_observation_unqualified')
            for fact in ('tokens','context','compactions','native_calls','terminal')}

    def bind_turn(self, turn):
        if (not isinstance(turn, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', turn)
                or self.turn is not None and self.turn != turn):
            self.fault = 'native_turn_identity_changed'
            self.invalidate_context()
            return
        self.turn = turn

    def invalidate_context(self,*,freshness_boundary=False):
        self.current_context = dict(status='unavailable')
        if freshness_boundary:self.context_min_observed_at=time.time()

    def observe_context(self, value):
        # Optional bad/mismatched context cannot overwrite billable usage or
        # establish an execution failure. It only removes the optional sample.
        selected = context(value)
        minimum=self.context_min_observed_at
        stamp=selected.get('observed_at')
        if (selected.get('status') != 'last_reported' or self.fault or self.context_blocked
                or minimum is not None and (type(stamp) not in (int,float) or stamp<=minimum)
                or selected.get('thread_id') != self.thread
                or self.turn is None or selected.get('turn_id') != self.turn
                or selected.get('current_window') is not True):
            self.invalidate_context()
            return
        self.current_context = selected
        self.context_min_observed_at=None

    def observe_call(self,value):
        if not isinstance(value,dict): return
        call=value.get('call_id');status=value.get('status')
        if (not isinstance(call,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',call)
                or status not in self.calls): return
        previous=self.call_receipts.get(call)
        if previous is not None and previous!=status:
            self.capabilities['native_calls']=dict(status='unsupported',reason='native_call_receipt_changed')
            return
        if previous is None:
            # Keep only bounded receipts in the invocation metadata collector.
            # Reaching capacity removes this optional capability; billing
            # counters continue through their independent cumulative producer.
            if len(self.call_receipts)>=4096:
                self.capabilities['native_calls']=dict(status='unsupported',reason='native_call_receipt_capacity')
                return
            self.call_receipts[call]=status
            self.calls[status]+=1

    def basic_snapshot(self):
        return dict(context=dict(status='unavailable') if self.fault or self.context_blocked else context(self.current_context), turn_id=self.turn,
            calls=dict(self.calls),
            observation_capabilities={key:dict(value) for key,value in self.capabilities.items()})


def breakdown(value):
    """Cached input is a subset of input, never another budget debit."""
    meter = tokens(value)
    if meter is None:
        return {}
    return dict(noncached_input_tokens=meter['input_tokens'] - meter['cached_input_tokens'],
                cache_hit_ratio=meter['cached_input_tokens'] / meter['input_tokens'] if meter['input_tokens'] else None)


def measurement_status(observation):
    reason = observation.get('reason') if isinstance(observation, dict) else None
    if reason == 'session_discovery_capacity':
        return '正在定位会话计量'
    if reason in {'session_unavailable', 'token_counter_unreported'}:
        return '等待会话用量上报'
    if not isinstance(observation, dict) or observation.get('coverage') not in {'live', 'terminal'}:
        return '观测不可用或滞后，已知用量为下界'
    return '最近已上报用量；运行中请求与平台上报延迟未计入'


class SessionUsage(BasicObservation):
    """Observe the native hooks' current compact facts, never rollout bodies.

    The snapshot is invocation-local. Resuming a thread carries consumed windows,
    but starts a new request-usage sum; no historical counter subtraction occurs.
    """

    def __init__(self, home, workspace, *, session=None, context_state=None):
        self._basic_init()
        self.home, self.workspace = Path(home), Path(workspace)
        self.thread = session.get('thread_id') if session else None
        self.path = Path(session['path']) if session and session.get('path') else None
        self.identity = tuple(session['identity']) if session and session.get('identity') else None
        self.session = session
        self.fault = None
        self.reason = 'token_counter_unreported'
        self.known = dict.fromkeys(FIELDS, 0)
        self.high = None
        cursor = context_state.get('cursor') if isinstance(context_state, dict) else None
        self.baseline = (tokens(cursor.get('terminal_tokens')) if session and isinstance(cursor, dict)
            and cursor.get('thread_id') == self.thread else None)
        self.baseline = self.baseline or dict.fromkeys(FIELDS, 0)
        self.terminal_tokens = None
        self.resumed = session is not None
        retained = context_windows(context_state) if context_state is not None else None
        self.windows_started = retained['windows_started'] if retained else 0
        self.window_fault = (retained.get('reason') or 'context_history_unavailable'
            if retained and retained['coverage'] == 'unknown' else None)
        if self.resumed and retained is None:
            self.window_fault = 'context_history_unavailable'
        if self.resumed and (not isinstance(cursor, dict) or cursor.get('thread_id') != self.thread):
            self.window_fault = self.window_fault or 'context_cursor_changed'
        self.window_bound = False
        self.window_counted = False
        self.compactions = 0
        self.calls = dict(completed=0, failed=0, cancelled=0)
        self.reported_calls = 0
        self.metadata_path = None
        self.attempt = None
        self.available = False
        self.offset = session.get('offset', 0) if session else 0
        self.binding_verified = False
        self.pending = False
        self.acknowledged = 0
        self.delivery_fault = None

    def configure(self, path, attempt, *, available):
        self.metadata_path, self.attempt = Path(path), attempt
        self.available = available
        self.context_blocked=not available or bool(self.fault or self.delivery_fault)
        if self.context_blocked:self.invalidate_context()
        if not available:
            self.window_fault = self.window_fault or 'context_observation_unavailable'

    def bind_thread(self, thread):
        if not isinstance(thread, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', thread):
            self.fault = 'native_thread_identity_invalid'
        elif self.thread is not None and self.thread != thread:
            self.fault = 'native_thread_identity_changed'
        else:
            self.thread = thread
            if not self.window_counted:
                if not self.resumed: self.windows_started += 1
                self.window_counted = True

    def _session_binding(self, value):
        path = value.get('transcript_path')
        if path is None:
            return
        path = Path(path)
        if (not path.is_absolute() or path.resolve() != path or self.home.resolve() != self.home
                or not path.is_relative_to(self.home / 'sessions')):
            raise ValueError('Native session path changed')
        info = path.stat()
        identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Native session is not an ordinary file')
        if self.path is not None and (self.path != path or self.identity != identity or info.st_size < self.offset):
            raise ValueError('Native session original changed')
        self.path, self.identity, self.offset = path, identity, info.st_size

    def poll(self):
        if self.fault or not self.available or self.metadata_path is None:
            self.context_blocked=True;self.invalidate_context()
            return self.snapshot()
        try:
            from . import budget_notice
            value = budget_notice.read_metadata(self.metadata_path)
            if value.get('attempt') != self.attempt or value.get('cwd') != str(self.workspace):
                raise ValueError('Native metadata attempt changed')
            thread = value.get('session_id')
            if thread is not None and (not isinstance(thread, str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', thread)
                    or self.thread is not None and thread != self.thread):
                self.bind_thread(thread)
                raise ValueError('Native metadata session identity changed')
            acknowledged = value.get('acknowledged')
            if (type(acknowledged) is not int or acknowledged < self.acknowledged
                    or type(value.get('pending')) is not bool):
                raise ValueError('Native metadata delivery acknowledgement changed')
            if acknowledged and thread is None:
                raise ValueError('Native metadata session identity is unavailable')
            self.pending, self.acknowledged = value['pending'], acknowledged
            if self.pending:
                if not self.context_blocked:self.context_min_observed_at=time.time()
                self.context_blocked=True;self.invalidate_context()
                started = value.get('pending_started_at')
                if type(started) not in (int, float) or not 0 < started <= time.monotonic():
                    raise ValueError('Native metadata delivery clock changed')
                if time.monotonic() - started >= 5:
                    self.delivery_fault = 'native_metadata_delivery_timeout'
                    self.window_fault = self.window_fault or 'context_observation_unavailable'
            if thread is not None:
                self.bind_thread(thread)
                if self.fault:
                    raise ValueError('Native metadata session identity changed')
                self._session_binding(value)
                if acknowledged and value.get('fault') is None and not self.fault:
                    self.window_bound = True
                if acknowledged and value.get('fault') is None and self.path is not None and not self.fault:
                    self.binding_verified = True
            count = value.get('compactions')
            calls = value.get('calls')
            if (type(count) is not int or count < self.compactions
                    or not isinstance(calls, dict) or set(calls) != set(self.calls)
                    or any(type(calls[k]) is not int or calls[k] < self.calls[k] for k in calls)):
                raise ValueError('Native metadata counter regressed')
            if count != self.compactions:
                self.invalidate_context(freshness_boundary=True)
            self.windows_started += count - self.compactions
            self.compactions, self.calls = count, dict(calls)
            meter = tokens(value.get('tokens'))
            if meter is None or any(meter[k] < self.known[k] for k in FIELDS):
                raise ValueError('Native request usage changed')
            self.known = meter
            reported = value.get('reported_calls')
            if (type(reported) is not int or not self.reported_calls <= reported <= sum(calls.values())
                    or type(value.get('usage_unknown')) is not bool):
                raise ValueError('Native request usage is unavailable')
            self.reported_calls = reported
            if reported:
                self.high = meter
            if value.get('fault') is not None:
                self.context_blocked=True;self.invalidate_context()
                self.binding_verified = False
                self.delivery_fault = 'native_metadata_delivery_failed'
                self.window_fault = self.window_fault or 'context_event_invalid'
                self.reason = 'native_metadata_invalid'
            elif value.get('usage_unknown') is True:
                self.reason = 'token_counter_unreported'
            elif reported:
                self.reason = None
            self.context_blocked=bool(self.pending or self.delivery_fault or self.fault)
        except (OSError, ValueError, TypeError, AttributeError):
            self.context_blocked=True;self.invalidate_context()
            self.binding_verified = False
            self.delivery_fault = 'native_metadata_unavailable'
            self.window_fault = self.window_fault or 'context_identity_unavailable'
            self.reason = 'native_metadata_unavailable'
        return self.snapshot()

    def finish_context(self):
        if (not self.available or not self.window_bound or not any(self.calls.values())
                or self.pending or self.delivery_fault):
            self.window_fault = self.window_fault or 'context_observation_unavailable'
            self.delivery_fault = self.delivery_fault or 'native_metadata_delivery_incomplete'
            self.context_blocked=True;self.invalidate_context()
        return self.snapshot()

    def snapshot(self):
        windows = dict(coverage='unknown' if self.window_fault or self.fault else
            'live' if self.window_bound and self.available and not self.pending else 'awaiting',
            windows_started=self.windows_started, reason=self.window_fault or
            ('context_cursor_changed' if self.fault else None if self.window_bound and self.available else 'context_awaiting_session'))
        if self.window_bound:
            windows['cursor'] = dict(thread_id=self.thread, compactions=self.compactions)
            if self.terminal_tokens is not None:
                windows['cursor']['terminal_tokens'] = dict(self.terminal_tokens)
        binding = None
        if self.binding_verified and self.path is not None and self.identity is not None:
            binding = dict(thread_id=self.thread, home=str(self.home), workspace=str(self.workspace),
                path=str(self.path), identity=list(self.identity), offset=self.offset,
                source='native_metadata_hooks')
        return dict(source='native_post_model_request', thread_id=self.thread,
            coverage='unknown' if self.fault or self.reason else 'live', reason=self.fault or self.reason,
            tokens=dict(self.known), baseline_tokens=dict(self.baseline) if self.resumed else None,
            has_observation=self.high is not None, **self.basic_snapshot(), context_windows=windows,
            session_binding=binding,
            metadata_delivery=dict(acknowledged=self.acknowledged, pending=self.pending,
                fault=self.delivery_fault),
            unreported_usage='Running requests and reporting delay are unmeasured.',
            individual_request_usage='native_post_model_request')
