"""Display-only liveness of a managed external-execution wait.

The owning wait loop records a small heartbeat with the latest state it queried
for each original call. The UI reads that heartbeat plus the local executor's
own process identity and stdout/stderr files. Nothing here queries an adapter,
grants authority, replays a call or enters scientific evidence; a missing or
stale file only degrades what the overview can show.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import time

from . import contracts as c, runtime

WRITE_EVERY_SECONDS = 5.
STALE_AFTER_SECONDS = 30.
LOG_TAIL_BYTES = 8192
LOG_TAIL_LINES = 40
LOG_NAMES = ('stdout.raw', 'stderr.raw')
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_written = {}


def heartbeat_path(root, step):
    return Path(root) / 'host-runtime/execution-wait' / f'{step:03d}.json'


def record_poll(root, step, states):
    """Owner-side, best effort: never let display bookkeeping stop a wait."""
    key = (str(root), step)
    now = time.time()
    last = _written.get(key)
    if last is not None and last[1] == states and now - last[0] < WRITE_EVERY_SECONDS:
        return
    path = heartbeat_path(root, step)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(step=step, pid=os.getpid(), polled_at=now, states=states),
                                        sort_keys=True), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        return
    _written[key] = (now, dict(states))


def _heartbeat(root, step):
    try:
        value = json.loads(heartbeat_path(root, step).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or type(value.get('polled_at')) not in (int, float):
        return None
    states = value.get('states') if isinstance(value.get('states'), dict) else {}
    return dict(polled_at=float(value['polled_at']),
                states={str(k): str(v)[:40] for k, v in states.items()})


def _tail(path):
    try:
        with path.open('rb') as handle:
            size = os.fstat(handle.fileno()).st_size
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            data = handle.read(LOG_TAIL_BYTES)
    except OSError:
        return None
    text = data.decode('utf-8', 'replace')
    if size > LOG_TAIL_BYTES and '\n' in text:
        text = text.split('\n', 1)[1]  # Drop the cut first line.
    lines = []
    for line in text.split('\n'):
        line = _ANSI.sub('', line).rsplit('\r', 1)[-1]  # Keep the final progress-bar frame.
        lines.append(_CONTROL.sub('', line))
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines[-LOG_TAIL_LINES:])


def _call(directory, heartbeat, now, include_logs, clean):
    row = dict(call_id=directory.name, result_ready=(directory / 'execution-observation.json').is_file(),
               finished=(directory / 'completed.json').is_file(), process=None, last_output_age=None,
               queried_state=(heartbeat or {}).get('states', {}).get(directory.name), logs=[])
    try:
        identity = json.loads((directory / 'process.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        identity = None
    if isinstance(identity, dict) and type(identity.get('pid')) is int:
        alive = runtime.process_live(identity, dict(path=str(directory / 'accepted.json')))
        row['process'] = dict(pid=identity['pid'], alive=bool(alive))
    newest = None
    for name in LOG_NAMES:
        path = directory / name
        try:
            info = path.stat()
        except OSError:
            continue
        newest = info.st_mtime if newest is None else max(newest, info.st_mtime)
        if include_logs and info.st_size:
            text = _tail(path)
            if text:
                row['logs'].append(dict(name=name.removesuffix('.raw'), size=info.st_size,
                                        text=clean(text, LOG_TAIL_BYTES) if clean else text))
    if newest is not None:
        row['last_output_age'] = max(0., now - newest)
    return row


def observe(root, record, step, *, include_logs=False, clean=None):
    """Read-only observation for one suspended W record; None when not waiting."""
    path = Path(record) / 'execution-wait.json'
    try:
        value = c.read(path)
        since = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    now = time.time()
    heartbeat = _heartbeat(root, step)
    calls = []
    for item in value.get('calls', []) if isinstance(value, dict) else []:
        try:
            directory = Path(item['accepted']['path']).parent
        except (KeyError, TypeError):
            continue
        calls.append(_call(directory, heartbeat, now, include_logs, clean))
    return dict(waited_seconds=max(0., now - since), since=datetime.fromtimestamp(since).astimezone().isoformat(timespec='seconds'),
                heartbeat_age=None if heartbeat is None else max(0., now - heartbeat['polled_at']),
                stale_after=STALE_AFTER_SECONDS, calls=calls)
