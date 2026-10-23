"""Bounded reconnectable public progress, shared by CLI and the loopback UI.

Only typed backend messages and tool names/status are projected from the private
native transport. Cursors contain offsets and opaque file identity, never text.
A reader failure cannot change execution, receipts, originals or usage coverage.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import stat

from . import backends, contracts, execution, native_usage, public_output, runtime

MAX_BYTES = 262144


def _encode(value):
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def _decode(cursor, binding):
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
        if (set(value) != {'v', 'binding', 'stream', 'offset', 'skip'}
                or value['v'] != 2 or value['binding'] != binding
                or not isinstance(value['stream'], list) or len(value['stream']) != 2
                or any(type(item) is not int or item < 0 for item in value['stream'])
                or type(value['offset']) is not int or value['offset'] < 0
                or type(value['skip']) is not bool):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise ValueError('Invalid progress cursor for this exact node and attempt') from error


def _bytes(stream, offset, size):
    stream.seek(offset)
    return stream.read(size)


def read(root, step, *, attempt=0, cursor=None, limit_bytes=65536, tail=False):
    """Read at most limit_bytes transport bytes, with bounded identity probes.

    Partial lines remain at their last complete boundary. An over-limit line is
    skipped incrementally and reported explicitly; no arbitrary transport bytes
    appear in the response or cursor. Replacement/truncation resets require the
    caller to explicitly start again without a cursor.
    """
    if (type(step) is not int or step <= 0 or type(attempt) is not int or attempt < 0
            or type(limit_bytes) is not int or not 1024 <= limit_bytes <= MAX_BYTES):
        raise ValueError('Progress requires an exact positive node, nonnegative attempt and 1024–262144 byte page')
    root = Path(root).expanduser().resolve()
    settings = contracts.settings(root)
    job_path = contracts.regular(root / 'jobs' / f'{step:03d}.json', limit=MAX_BYTES)
    job_bytes = job_path.read_bytes()
    job = json.loads(job_bytes)
    if job.get('step') != step:
        raise ValueError('Exact progress node identity changed')
    binding = [settings['goal_id'], step, attempt]
    previous = _decode(cursor, binding) if cursor is not None else None
    record = execution.origin(root, step)
    if attempt:
        record = record / 'continuations' / f'{attempt:03d}'
    result = dict(status='progress_read', step=step, attempt=attempt, events=[], cursor=cursor,
                  scope='public_draft_log', sealed_conclusion=dict(step=step, available=(execution.origin(root, step) / 'result-identity.json').exists(), qualification='not_asserted'), bytes_read=0)
    result['role'] = job['role']
    result['context'] = dict(status='unavailable')
    try:
        result['state'] = 'unstarted'
        intent_path = record / 'native-intent.json'
        if intent_path.exists():
            intent_bytes = contracts.regular(intent_path, limit=MAX_BYTES).read_bytes()
            intent = json.loads(intent_bytes)
            result['state'] = 'intent_present'
            runner_path = record / 'runner-process.json'
            if runner_path.exists():
                runner = contracts.read(contracts.regular(runner_path, limit=4096))
                runtime_binding = contracts.record_ref(intent_path if attempt else job_path)
                result['state'] = 'running' if runtime.process_matches(runner, runtime_binding) else 'unknown'
            if type(intent.get('prepared_at')) in (int, float):
                result['prepared_at'] = intent['prepared_at']
        meter_path = record / 'usage.json'
        observation_path = record / 'usage-observation.json'
        result['usage'] = dict(coverage='unknown')
        result['usage']['measurement_status'] = native_usage.measurement_status(None)
        if meter_path.exists():
            meter = contracts.read(contracts.regular(meter_path, limit=4096))
            safe = public_output.event(dict(type='usage', usage=meter), lambda value: value)
            if safe:
                result['usage'].update(safe['usage'], coverage='observed')
        if (record / 'native-outcome.json').exists():
            result['state'] = 'native_outcome_present'
        if result['sealed_conclusion']['available']:
            result['state'] = 'seal_present_unverified'
        if observation_path.exists():
            observation = contracts.read(contracts.regular(observation_path, limit=MAX_BYTES))
            if observation.get('coverage') in {'live', 'terminal', 'unknown'}:
                result['usage']['coverage'] = observation['coverage']
            result['usage']['measurement_status'] = native_usage.measurement_status(observation)
            if observation.get('coverage') in {'live', 'terminal'}:
                result['context'] = native_usage.context(observation.get('context'))
    except (OSError, ValueError, TypeError, KeyError):
        result['observation_state'] = 'unavailable'
        result['context'] = dict(status='unavailable')
    result.setdefault('usage', dict(coverage='unknown'))
    result['usage'].update(native_usage.breakdown(result['usage']))
    path = record / 'native.jsonl'
    try:
        # The record namespace is fixed, and the final file cannot be a symlink.
        if record.resolve() != record:
            raise ValueError('Progress record namespace changed')
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Progress transport is not a regular file')
            identity = [info.st_dev, info.st_ino]
            offset = previous['offset'] if previous else max(0, info.st_size - limit_bytes) if tail else 0
            tail_omitted = bool(tail and previous is None and offset)
            tail_skipping = tail_omitted and _bytes(stream, offset - 1, 1) != b'\n'
            if tail_omitted:
                # A bounded tail starts inside a line; discard that first line
                # even when its bytes happen to resemble a valid transport event.
                result['tail_omitted'] = True
            if previous:
                if identity != previous['stream']:
                    return dict(result, stream_state='replaced', restart_required=True)
                if info.st_size < offset:
                    return dict(result, stream_state='truncated', restart_required=True)
            data = _bytes(stream, offset, limit_bytes)
            result['bytes_read'] = len(data)
            skipping = previous['skip'] if previous else tail_skipping
            lines = data.split(b'\n')
            consumed = sum(len(line) + 1 for line in lines[:-1])
            complete = lines[:-1]
            if skipping and complete:
                complete = complete[1:]
                skipping = False
            omitted = 0
            if not complete and not consumed and len(data) == limit_bytes:
                # An unbounded native line must not make the reader retain an
                # unbounded raw tail or repeatedly read all prior chunks.
                consumed = len(data)
                omitted = int(not skipping)
                skipping = True
            backend = backends.selected(settings)
            clean = public_output.cleaner(root, settings)
            malformed = 0
            for line in complete:
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    malformed += 1
                    continue
                if not isinstance(value, dict):
                    malformed += 1
                    continue
                try:
                    projected_events = backend.public_events(value)
                except (TypeError, ValueError, KeyError):
                    malformed += 1
                    continue
                for projected in projected_events:
                    safe = public_output.event(projected, clean)
                    if safe is not None:
                        result['events'].append(safe)
            offset += consumed
            current = dict(v=2, binding=binding, stream=identity, offset=offset, skip=skipping)
            # Detect changes during this single bounded read before returning it.
            after = os.fstat(stream.fileno())
            if after.st_size < info.st_size:
                return dict(result, events=[], stream_state='truncated', restart_required=True)
            return dict(result, cursor=_encode(current), stream_state='partial_line' if data[consumed:] or skipping else 'ready',
                        has_more=after.st_size > offset + len(data[consumed:]),
                        malformed_lines=malformed, omitted_oversize_lines=omitted)
    except FileNotFoundError:
        return dict(result, stream_state='missing', restart_required=previous is not None)
    except (OSError, ValueError):
        return dict(result, status='progress_unavailable', stream_state='unavailable', restart_required=False)


def tail(root, step, *, attempt=0, limit_bytes=65536):
    """Overview-only cold tail; full progress remains forward-paginated."""
    return read(root, step, attempt=attempt, limit_bytes=limit_bytes, tail=True)
