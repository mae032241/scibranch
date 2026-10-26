"""Finite standard-Codex recovery decisions from native transport facts only."""
from __future__ import annotations

import math
import re


DEFAULT_POLICY = dict(enabled=True, max_continues=2, max_retries=3,
    initial_backoff_seconds=30, max_backoff_seconds=300,
    jitter_fraction=0.1, max_wait_seconds=1800)


def policy(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_POLICY):
        raise ValueError('Unsupported automatic recovery policy')
    result = dict(DEFAULT_POLICY, **value)
    if type(result['enabled']) is not bool:
        raise ValueError('Automatic recovery enabled must be boolean')
    for key in ('max_continues', 'max_retries'):
        if type(result[key]) is not int or not 0 <= result[key] <= 100:
            raise ValueError('Automatic recovery attempts must be finite integers from 0 to 100')
    for key in ('initial_backoff_seconds', 'max_backoff_seconds', 'max_wait_seconds', 'jitter_fraction'):
        if type(result[key]) not in (int, float) or not math.isfinite(result[key]) or result[key] < 0:
            raise ValueError('Automatic recovery waits must be finite nonnegative numbers')
    if (result['initial_backoff_seconds'] > result['max_backoff_seconds']
            or result['max_backoff_seconds'] > result['max_wait_seconds']
            or result['jitter_fraction'] > 1):
        raise ValueError('Automatic recovery wait bounds are inconsistent')
    return result


_PERMANENT = {
    'authentication_error', 'unauthorized', 'invalid_api_key', 'authentication_failed',
    'permission_denied', 'permission_error', 'forbidden', 'insufficient_quota',
    'quota_exceeded', 'billing_hard_limit_reached', 'usage_limit_reached',
    'credit_balance_exhausted', 'organization_usage_limit_exceeded', 'usage_limit_exceeded',
    'invalid_request_error', 'bad_request', 'model_not_found', 'unsupported_model',
    'invalid_model', 'invalid_configuration', 'configuration_error',
    'context_length_exceeded', 'unsupported_parameter', 'invalid_parameter',
    'resource_not_found', 'executor_version_incompatible',
}
_TEMPORARY = {
    'rate_limit_exceeded', 'rate_limit_error', 'too_many_requests', 'capacity_exceeded',
    'overloaded_error', 'server_overloaded', 'service_unavailable', 'server_error',
    'server_is_overloaded', 'flex_unavailable', 'internal_error',
    'internal_server_error', 'connection_error', 'connection_failed', 'connection_reset',
    'stream_disconnected', 'stream_error', 'timeout', 'request_timeout',
}
_INCOMPLETE = {'turn_incomplete', 'turn_interrupted', 'response_incomplete'}


def _native_message(layers):
    """Exact exec-JSON translations confirmed by local HTTP conformance.

    Codex exec discards the provider's structured error code for these shapes.
    Match only the complete native failure message, never ordinary model text.
    """
    matched = []
    for layer in layers:
        message = layer.get('message')
        if not isinstance(message, str):continue
        if message == 'Quota exceeded. Check your plan and billing details.':
            matched.append(('insufficient_quota', None, 'codex_exec_quota_v1'))
        elif message == 'exceeded retry limit, last status: 429 Too Many Requests':
            matched.append(('rate_limit_exceeded', 429, 'codex_exec_http_429_v1'))
        elif re.fullmatch(r'unexpected status 503 Service Unavailable: [^\r\n]{1,4096}, url: https?://[^\s]{1,2048}', message):
            matched.append(('service_unavailable', 503, 'codex_exec_http_503_v1'))
    return matched


def classify(event):
    """Never inspect agent messages, tool output, or unqualified error prose.

    The caller retains the complete original JSONL. This projection deliberately
    does not contain native error messages, paths, headers, or credentials.
    """
    if not isinstance(event, dict) or event.get('type') not in {
            'error', 'turn.failed', 'turn.incomplete', 'turn.interrupted'}:
        return None
    layers = [event]
    for key in ('error', 'response'):
        child = event.get(key)
        if isinstance(child, dict):
            layers.append(child)
            if isinstance(child.get('error'), dict):layers.append(child['error'])
    codes = {value.lower() for layer in layers for key in ('code', 'type', 'error_type')
             if isinstance(value := layer.get(key), str)}
    statuses = {value for layer in layers for key in ('status', 'status_code', 'http_status_code')
                if type(value := layer.get(key)) is int}
    messages = _native_message(layers)
    codes.update(code for code, _, _ in messages)
    statuses.update(status for _, status, _ in messages if status is not None)
    # Permanent codes always dominate a generic HTTP 429 or capacity code.
    category = ('permanent' if codes & _PERMANENT or any(code.endswith('spend_limit_exceeded') for code in codes)
                    or statuses & {400, 401, 403, 404, 422}
                else 'temporary' if codes & _TEMPORARY or statuses & {408, 429, 500, 502, 503, 504}
                else 'incomplete' if codes & _INCOMPLETE or event['type'] in {'turn.incomplete', 'turn.interrupted'}
                else 'unknown')
    retry = [value for layer in layers for key in ('retry_after_seconds', 'retry_after')
             if type(value := layer.get(key)) in (int, float) and math.isfinite(value) and value >= 0]
    return dict(category=category, event_type=event['type'],
        codes=sorted(codes - {'error', 'turn.failed', 'turn.incomplete', 'turn.interrupted'}),
        statuses=sorted(statuses), retry_after_seconds=max(retry, default=None),
        message_patterns=sorted({pattern for _, _, pattern in messages}),
        not_accepted=any(layer.get('accepted') is False or layer.get('request_started') is False for layer in layers))


def combine(failures):
    """A generic wrapper cannot erase a structured underlying failure."""
    if not failures:return None
    rank = dict(permanent=4, unknown=0, temporary=2, incomplete=1)
    selected = max(failures, key=lambda item:rank[item['category']])
    retry = [item['retry_after_seconds'] for item in failures if item.get('retry_after_seconds') is not None]
    return dict(selected, retry_after_seconds=max(retry, default=None),
                not_accepted=all(item.get('not_accepted') is True for item in failures))


def counters(intents):
    result = dict(continues=0, retries=0, wait_seconds=0.)
    for intent in intents:
        saved = intent.get('automatic_recovery')
        if saved is None:continue
        if not isinstance(saved, dict) or saved.get('action') not in {'continue', 'retry'}:
            raise ValueError('Automatic recovery attempt changed')
        result['continues' if saved['action'] == 'continue' else 'retries'] += 1
        wait = saved.get('wait_seconds', 0)
        if type(wait) not in (int, float) or not math.isfinite(wait) or wait < 0:
            raise ValueError('Automatic recovery wait accounting changed')
        result['wait_seconds'] += wait
    return result


def recoverable_source(outcome):
    return (isinstance(outcome, dict) and outcome.get('terminal_observed') is False
        and (outcome.get('native_failure') or {}).get('category') in {'temporary','incomplete'}
        and outcome.get('stop_reason') in {None,'native_failure_event'}
        and outcome.get('cleanup') == 'observed_group_cleaned')


def work_decision(limits, used, *, session_available):
    """A verified delivery failure spends the same original continue budget."""
    result = dict(action='stop', reason='automatic_recovery_continue_limit', counters=dict(used))
    if not limits['enabled']:return dict(result, action='none', reason='automatic_recovery_disabled')
    if used['continues'] >= limits['max_continues']:return result
    if not session_available:return dict(result, reason='automatic_recovery_session_unavailable')
    return dict(result, action='continue', reason='worker_delivery_incomplete', startup_retry=False,
        delay_seconds=0., retry_after_seconds=None)


def decision(outcome, limits, used, *, session_available, activity_observed=False, jitter=0.):
    """Choose one action after lifecycle, exact-process and managed-call gates."""
    result = dict(action='stop', reason='automatic_recovery_unknown_failure', counters=dict(used))
    if not limits['enabled']:return dict(result, action='none', reason='automatic_recovery_disabled')
    failure = outcome.get('native_failure')
    if outcome.get('terminal_observed') is True:
        return dict(result, action='none', reason='automatic_recovery_completed_turn')
    if not isinstance(failure, dict):return result
    category = failure.get('category')
    if category not in {'temporary', 'incomplete'}:
        return dict(result, reason='automatic_recovery_' + ('permanent_failure' if category == 'permanent' else 'unknown_failure'))
    action = 'retry' if category == 'temporary' else 'continue'
    key = 'retries' if action == 'retry' else 'continues'
    cap = limits['max_retries' if action == 'retry' else 'max_continues']
    if used[key] >= cap:return dict(result, reason='automatic_recovery_' + action + '_limit')
    startup = not session_available
    if startup and (action != 'retry' or failure.get('not_accepted') is not True or activity_observed
                    or outcome.get('thread_id') is not None):
        return dict(result, reason='automatic_recovery_session_unavailable')
    delay = 0.
    if action == 'retry':
        delay = min(limits['max_backoff_seconds'], limits['initial_backoff_seconds'] * 2 ** used['retries'])
        delay = min(limits['max_backoff_seconds'], max(0., delay * (1 + limits['jitter_fraction'] * max(-1., min(1., jitter)))))
        delay = max(delay, failure.get('retry_after_seconds') or 0.)
        if delay > limits['max_backoff_seconds']:
            return dict(result, reason='automatic_recovery_retry_after_limit')
    if used['wait_seconds'] + delay > limits['max_wait_seconds']:
        return dict(result, reason='automatic_recovery_wait_limit')
    return dict(result, action=action, reason='native_' + category, delay_seconds=delay,
                startup_retry=startup, retry_after_seconds=failure.get('retry_after_seconds'))
