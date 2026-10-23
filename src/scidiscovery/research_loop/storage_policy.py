"""Opt-in bounds on managed scanning and retention, not a filesystem quota."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import os
from pathlib import Path
import stat
import time


DIMENSIONS = ('max_scan_entries', 'max_files', 'max_file_bytes',
              'max_total_bytes', 'max_instance_bytes')
_writers = ContextVar('managed_storage_writers', default=frozenset())
_policies = ContextVar('managed_storage_policies', default={})


class StorageQuotaExceeded(ValueError):
    pass


def configuration(value=None):
    value = {} if value is None else value
    if (not isinstance(value, dict) or set(value) - {'version', 'enabled', *DIMENSIONS}
            or type(value.get('version', 1)) is not int or value.get('version', 1) != 1
            or type(value.get('enabled', False)) is not bool):
        raise ValueError('Storage policy requires version 1 and a boolean enabled field')
    result = dict(version=1, enabled=value.get('enabled', False))
    for name in DIMENSIONS:
        limit = value.get(name)
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError('Storage limits must be positive integers or null')
        result[name] = limit
    if result['enabled'] and all(result[name] is None for name in DIMENSIONS):
        raise ValueError('Enabled storage policy requires an explicit positive limit')
    return result


class Deadline:
    """Finite IO window; an upstream original deadline is checked as well."""
    def __init__(self, seconds, upstream=None):
        if type(seconds) not in (int, float) or not 0 < seconds < float('inf'):
            raise ValueError('Finite storage operation deadline required')
        self.end = time.monotonic() + seconds
        self.upstream = upstream

    def remaining_seconds(self):
        remaining = self.end - time.monotonic()
        if self.upstream is not None:
            remaining = min(remaining, self.upstream.remaining_seconds())
        if remaining <= 0:
            raise TimeoutError('Managed storage operation deadline reached')
        return remaining


def check(context):
    if context is not None:
        context.remaining_seconds()


class Inventory:
    def __init__(self, policy=None, *, context=None):
        self.policy = configuration(policy)
        self.context = context
        self.entries = self.files = self.total = 0

    def limit(self, name, amount):
        check(self.context)
        limit = self.policy[name]
        if self.policy['enabled'] and limit is not None and amount > limit:
            raise StorageQuotaExceeded('Managed storage limit exceeded: ' + name)

    def entry(self):
        self.entries += 1
        self.limit('max_scan_entries', self.entries)

    def file(self, size):
        if type(size) is not int or size<0:
            raise ValueError('Managed file size must be a nonnegative integer')
        self.files += 1
        self.total += size
        self.limit('max_files', self.files)
        self.limit('max_file_bytes', size)
        self.limit('max_total_bytes', self.total)


def for_root(root):
    from . import contracts as c
    if str(root) in _policies.get():
        return _policies.get()[str(root)]
    if not (Path(root) / 'settings.json').exists():
        return configuration()
    return configuration(c.settings(Path(root)).get('storage_policy'))


def for_path(root, path):
    if root is None:
        return configuration()
    if str(root) in _policies.get():
        return for_root(root)
    parts = Path(path).relative_to(root).parts
    step = (parts[2] if len(parts) > 2 and parts[:2] == ('project', 'nodes') else
            parts[1] if len(parts) > 1 and parts[0] == 'native-evidence' else '')
    if step.isdecimal():
        from . import configuration_updates
        return configuration(configuration_updates.for_new_job(root, int(step)).get('storage_policy'))
    return for_root(root)


@contextmanager
def bind(root, policy):
    """An original call keeps its frozen limits across later config revisions."""
    if root is None:
        yield
        return
    token = _policies.set({**_policies.get(), str(root): configuration(policy)})
    try:
        yield
    finally:
        _policies.reset(token)


def usage(root, *, context=None):
    """Conservative file-size accounting: aliases count once, reflinks twice.

    Symlinks are neither followed nor interpreted as managed original bytes.
    Partial regular files are included. This is not extent/block accounting.
    """
    seen = set()
    total = 0
    pending = [Path(root)]
    while pending:
        check(context)
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            if directory == Path(root):
                raise
            continue
        with entries:
            for entry in entries:
                check(context)
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    key = (info.st_dev, info.st_ino)
                    if key not in seen:
                        seen.add(key)
                        total += info.st_size
    return total


@contextmanager
def writer_lock(root, *, context=None):
    """Serialize content publication even when quota accounting is disabled."""
    root = Path(root)
    context = context or Deadline(65)
    key = (os.getpid(), str(root))
    if key in _writers.get():
        check(context)
        yield
        return
    lock = root / 'host-runtime/storage.lock'
    if root.resolve()!=root or any(part.is_symlink() for part in (lock,*lock.parents)):
        raise ValueError('Managed storage lock requires its exact instance namespace')
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    token = None
    try:
        while True:
            check(context)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.01)
        token = _writers.set(_writers.get() | {key})
        yield
    finally:
        if token is not None:
            _writers.reset(token)
        os.close(fd)


@contextmanager
def mutation(root, growth, *, policy=None, context=None, target=None):
    """Reserve surviving partial bytes under the common storage writer lock."""
    if type(growth) is not int or growth<0:
        raise ValueError('Managed storage growth must be a nonnegative integer')
    if root is None:
        yield
        return
    root = Path(root)
    policy = for_root(root) if policy is None else configuration(policy)
    if not policy['enabled'] or policy['max_instance_bytes'] is None:
        yield
        return
    context = context or Deadline(65)
    with writer_lock(root, context=context):
        required=0 if target is not None and Path(target).exists() else growth
        if usage(root,context=context)+required>policy['max_instance_bytes']:
            raise StorageQuotaExceeded('Managed storage limit exceeded: max_instance_bytes')
        yield
