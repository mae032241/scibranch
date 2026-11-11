"""Display-only liveness of a managed external-execution wait.

The owning wait loop records a small heartbeat with the latest state it queried
for each original call. The UI reads that heartbeat plus the local executor's
own process identity and stdout/stderr files. Nothing here queries an adapter,
grants authority, replays a call or enters scientific evidence; a missing or
stale file only degrades what the overview can show.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from . import contracts as c, runtime

WRITE_EVERY_SECONDS = 5.
STALE_AFTER_SECONDS = 30.
LOG_TAIL_BYTES = 8192
LOG_TAIL_LINES = 40
LOG_NAMES = ('stdout.raw', 'stderr.raw')
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_LOCK_ROW = re.compile(
    r'\d+:\s+(?P<blocked>->\s+)?(?P<kind>FLOCK|POSIX|OFDLCK|LEASE)\s+[A-Z]+\s+'
    r'(?:READ|WRITE)\s+-?\d+\s+(?P<major>[0-9a-fA-F]+):(?P<minor>[0-9a-fA-F]+):'
    r'(?P<inode>\d+)\s+\d+\s+(?:\d+|EOF)')
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
        owner = runtime.process_identity(os.getpid(), dict(step=step))
        temporary.write_text(json.dumps(dict(step=step, pid=os.getpid(), owner=owner, polled_at=now, states=states),
                                        sort_keys=True), encoding='utf-8')
        temporary.replace(path)
    except (OSError, ValueError, IndexError):
        return
    _written[key] = (now, dict(states))


def _heartbeat(root, step):
    try:
        value = json.loads(heartbeat_path(root, step).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get('step') != step
            or type(value.get('polled_at')) not in (int, float)
            or not math.isfinite(value['polled_at']) or value['polled_at'] > time.time()):
        return None
    states = value.get('states') if isinstance(value.get('states'), dict) else {}
    return dict(polled_at=float(value['polled_at']), owner=value.get('owner'),
                states={str(k): str(v)[:40] for k, v in states.items()})


def process_observation(identity, binding):
    """Optional local identity probe; unavailable PID namespaces stay unknown."""
    keys = ('pid', 'start_ticks', 'pgid', 'sid')
    if (not isinstance(identity, dict) or identity.get('task_ref') != binding
            or not isinstance(identity.get('boot_id'), str) or not identity['boot_id']
            or any(type(identity.get(key)) is not int or identity[key] <= 0 for key in keys)):
        return None
    try:
        current = runtime.process_identity(identity['pid'], binding)
    except (OSError, ValueError, IndexError, KeyError, TypeError):
        return None
    if (not isinstance(current, dict) or not isinstance(current.get('state'), str)
            or not current['state'] or any(key not in current for key in (*keys, 'boot_id'))):
        return None
    return (current['state'] not in {'Z', 'X', 'x'}
            and all(current[key] == identity[key] for key in (*keys, 'boot_id')))


def _observed_flock(info):
    """Passive Linux snapshot, limited to the proc mount's PID namespace."""
    locked = False
    for line in Path('/proc/locks').read_text(encoding='ascii').splitlines():
        row = _LOCK_ROW.fullmatch(line.strip())
        if row is None:
            return None
        if (row['kind'] == 'FLOCK' and row['blocked'] is None
                and (int(row['major'], 16), int(row['minor'], 16), int(row['inode']))
                == (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)):
            locked = True
    return locked


def controller_observation(root, operations, live=None):
    """Display-only local lock/owner snapshots; never acquire a writer lock."""
    locked = None
    descriptor = None
    path = Path(root) / 'host-run.lock'
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
            locked = _observed_flock(before)
            after = path.stat(follow_symlinks=False)
            if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
                    or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
                locked = None
    except (OSError, UnicodeError, ValueError):
        locked = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    owners = []
    for operation in operations:
        if operation.get('kind') == 'controller' and operation.get('status') in {'accepted', 'running'}:
            alive = process_observation(operation.get('process'), operation.get('operation_id'))
            if alive is True:
                owners.append(operation['operation_id'])
    heartbeat_alive = (live or {}).get('owner_alive')
    # A live exact owner with no visible lock can reflect a different proc
    # namespace or an observation race; it does not establish stopped monitoring.
    monitoring = (False if locked is False and not owners and heartbeat_alive is not True else
                  True if locked is True and (owners or heartbeat_alive is True)
                  else None)
    return dict(monitoring=monitoring, host_locked=locked, exact_owner=bool(owners))


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


def _call(directory, flags, heartbeat, now, include_logs, clean):
    row = dict(call_id=directory.name, result_ready=flags['result_ready'],
               finished=flags['finished'], process=None, last_output_age=None,
               queried_state=(heartbeat or {}).get('states', {}).get(directory.name), logs=[])
    try:
        identity = json.loads((directory / 'process.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        identity = None
    if isinstance(identity, dict) and type(identity.get('pid')) is int:
        alive = process_observation(identity, dict(path=str(directory / 'accepted.json')))
        row['process'] = dict(pid=identity['pid'], alive=alive)
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


def read_wait(record):
    """Acquire a suspended wait once, keeping original paths out of public data."""
    path = Path(record) / 'execution-wait.json'
    try:
        value = c.read(path)
        since = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    calls = []
    local_calls = []
    for item in value.get('calls', []) if isinstance(value, dict) else []:
        try:
            directory = Path(item['accepted']['path']).parent
        except (KeyError, TypeError):
            continue
        result_ready = (directory / 'execution-observation.json').is_file()
        local_calls.append(dict(directory=directory, result_ready=result_ready,
                               finished=(directory / 'completed.json').is_file()))
        calls.append(dict(call_id=directory.name, external_run_id=str(item.get('external_run_id') or '')[:120],
            submitted_state=str(item.get('observed_state') or '')[:40],
            result_ready=result_ready))
    wait = dict(calls=calls, since=datetime.fromtimestamp(since, timezone.utc).isoformat(timespec='seconds'),
                readable=True, all_ready=bool(calls) and all(row['result_ready'] for row in calls))
    return wait, dict(since=since, local_calls=local_calls)


def observe(root, record, step, *, include_logs=False, clean=None):
    """Standalone read-only producer for one suspended wait."""
    acquired = read_wait(record)
    if acquired is None:
        return None
    _, source = acquired
    return observe_acquired(root, step, source, include_logs=include_logs, clean=clean)


def observe_acquired(root, step, source, *, include_logs=False, clean=None):
    """Produce local observations from explicit, already acquired wait facts."""
    since = source['since']
    now = time.time()
    heartbeat = _heartbeat(root, step)
    calls = []
    for item in source['local_calls']:
        calls.append(_call(item['directory'], item, heartbeat, now, include_logs, clean))
    return dict(waited_seconds=max(0., now - since), since=datetime.fromtimestamp(since).astimezone().isoformat(timespec='seconds'),
                heartbeat_age=None if heartbeat is None else max(0., now - heartbeat['polled_at']),
                owner_alive=None if heartbeat is None else process_observation(heartbeat.get('owner'), dict(step=step)),
                stale_after=STALE_AFTER_SECONDS, calls=calls)
