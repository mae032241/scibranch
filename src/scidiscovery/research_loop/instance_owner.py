"""Explicit same-host cold-transfer owner; no scheduling or budget authority."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from scidiscovery import archive_io as files

BINDING = 'instance-owner.json'
_held = ContextVar('instance_owner_held', default={})
_gates = ContextVar('instance_owner_gates', default={})


def _gate_directory():
    # Deliberately independent of TMPDIR and other caller-selected environments.
    return Path('/tmp') / f'scidiscovery-owner-gates-{os.geteuid()}'


def _gate_root(root):
    root = files.check_path(Path(root).absolute())
    if root.resolve() != root or not root.is_dir():
        raise files.ArchiveError('Host owner gate requires the exact canonical instance directory')
    return root, _identity(root)


def _same_gate_root(root, identity):
    if _gate_root(root)[1] != identity:
        raise files.ArchiveError('Host owner gate lost its original instance directory')


def _private_stat(info, *, directory):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    mode = 0o700 if directory else 0o600
    if (not kind(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != mode or (not directory and info.st_nlink != 1)):
        raise files.ArchiveError('Unsafe host owner gate directory or lock')


def _same_gate_path(path, descriptor, *, directory):
    actual = path.lstat()
    opened = os.fstat(descriptor)
    _private_stat(actual, directory=directory)
    _private_stat(opened, directory=directory)
    if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
        raise files.ArchiveError('Host owner gate path changed while held')


@contextmanager
def _host_gate(root, *, exclusive, create=True, cold=False, allow_missing=False):
    root = files.check_path(Path(root).absolute())
    # Cold restore may own an absent destination using its existing anchor.
    # Every lock here and at that anchor is nonblocking: no waiting lock cycle.
    if allow_missing and not root.exists():
        if root.resolve() != root:
            raise files.ArchiveError('Cold owner root must remain canonical')
        yield None
        return
    root, identity = _gate_root(root)
    previous = _gates.get()
    held = previous.get(str(root))
    if held is not None:
        _same_gate_root(root, held['identity'])
        if exclusive or (held['exclusive'] and not held['cold']):
            raise files.ArchiveError('Source read guard cannot be entered as an effect lease')
        yield held
        _same_gate_root(root, held['identity'])
        return
    directory = files.check_path(_gate_directory())
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    elif not directory.exists():
        yield None
        _same_gate_root(root, identity)
        return
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    fd = None
    try:
        _same_gate_path(directory, directory_fd, directory=True)
        key = hashlib.sha256(files.canonical(dict(root=str(root), **identity))).hexdigest() + '.lock'
        try:
            fd = os.open(key, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                | (os.O_CREAT if create else 0), 0o600, dir_fd=directory_fd)
        except FileNotFoundError:
            if create:
                raise
            yield None
            _same_gate_root(root, identity)
            return
        _same_gate_path(directory / key, fd, directory=False)
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise files.ArchiveError('An instance effect is still active or source read guard is active') from error
        _same_gate_path(directory, directory_fd, directory=True)
        _same_gate_path(directory / key, fd, directory=False)
        _same_gate_root(root, identity)
        value = dict(fd=fd, identity=identity, exclusive=exclusive, cold=cold)
        token = _gates.set({**previous, str(root): value})
        try:
            yield value
        finally:
            _gates.reset(token)
            _same_gate_root(root, identity)
            _same_gate_path(directory, directory_fd, directory=True)
            _same_gate_path(directory / key, fd, directory=False)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory_fd)


@contextmanager
def source_lease(root, *, create=True):
    """Host-private EX fork guard; create=False probes without creating files.

    This is same-user cooperation, not authorization or a sandbox. Gate files
    are never removed or rotated: external deletion (including /tmp cleanup)
    while any process lives invalidates the coordination guarantee. Existing
    local locks/quiescence remain necessary for pre-upgrade running processes.
    """
    with _host_gate(root, exclusive=True, create=create) as value:
        yield value


def _read(path):
    files.check_path(path)
    with Path(path).open('rb') as stream:
        return json.load(stream)


def _host():
    return hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()


def _identity(root):
    info = Path(root).stat()
    return dict(dev=info.st_dev, ino=info.st_ino)


def _binding(root):
    root = files.check_path(Path(root).absolute())
    path = root / BINDING
    identity = root / 'settings-identity.json'
    marker = _read(identity).get('cold_owner') if identity.exists() else None
    if not path.exists() and not path.is_symlink():
        if marker is not None:
            raise files.ArchiveError('Enrolled owner binding is missing; explicit enrollment repair required')
        return None
    value = _read(path)
    if marker is not None and marker != dict(anchor=value['anchor'],anchor_id=value['anchor_id']):
        raise files.ArchiveError('Enrolled owner marker differs from its exact binding')
    if (value.get('version') != 1 or value.get('root') != str(root)
            or root.resolve() != root or value.get('host') != _host()
            or type(value.get('generation')) is not int or value['generation'] <= 0):
        raise files.ArchiveError('Enrolled owner is unavailable on this host or original root')
    from . import contracts as c
    settings = c.settings(root)
    if (value.get('instance_id') != settings['goal_id']
            or value.get('settings_sha256') != c.digest(root / 'settings.json')):
        raise files.ArchiveError('Enrolled owner lost its exact instance')
    return value


def _anchor(binding):
    directory = files.check_path(Path(binding['anchor']))
    if not directory.is_absolute() or not directory.is_dir():
        raise files.ArchiveError('Original external owner anchor is unavailable')
    value = _read(directory / 'owner.json')
    if any(value.get(key) != binding.get(key) for key in
            ('version', 'instance_id', 'settings_sha256', 'root', 'host', 'anchor_id')):
        raise files.ArchiveError('External owner anchor differs from its binding')
    return directory, value


def _active(root, binding):
    _, value = _anchor(binding)
    if (value.get('state') != 'active' or value.get('generation') != binding['generation']
            or value.get('root_identity') != _identity(root)):
        raise files.ArchiveError('Original owner generation is frozen or superseded')
    return value


def require_owner(root):
    """A check only; effect owners also hold lease for their full lifetime."""
    root = Path(root).absolute()
    binding = _binding(root)
    if binding is None:
        return None
    held = _held.get().get(str(root))
    if held is not None and held['exclusive']:
        return binding  # Only the explicit cold-transfer owner has this context.
    return _active(root, binding)


@contextmanager
def lease(root):
    root = Path(root).absolute()
    with _host_gate(root, exclusive=False):
        with _owner_lease(root) as value:
            yield value


@contextmanager
def _owner_lease(root):
    binding = _binding(root)
    previous = _held.get()
    if str(root) in previous:
        require_owner(root)
        yield previous[str(root)]
        return
    if binding is None:
        yield None
        return
    directory, _ = _anchor(binding)
    fd = os.open(directory / 'writer.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise files.ArchiveError('Cold owner transfer is in progress') from error
        _active(root, binding)
        token = _held.set({**previous, str(root): dict(exclusive=False, binding=binding)})
        try:
            yield binding
        finally:
            _held.reset(token)
    finally:
        os.close(fd)


def effect(function):
    @wraps(function)
    def guarded(root, *args, **kwargs):
        with lease(root):
            return function(root, *args, **kwargs)
    return guarded


def configuration_root(config):
    """Only the original host task namespace, never a guessed ancestor."""
    from . import contracts as c, experiment_client
    root = experiment_client.root_for_configuration(config, allow_standalone=True)
    if root is None:
        return None
    scope = config['scope']
    job_path = root / 'jobs' / f'{scope["step"]:03d}.json'
    job = c.read(c.regular(job_path))
    if (scope.get('step') != job['step'] or scope.get('role') != job['role']
            or scope.get('job_sha256') != c.digest(job_path)):
        raise files.ArchiveError('Managed execution owner lost its original job')
    return root


@contextmanager
def lease_for_configuration(config):
    root = configuration_root(config)
    if root is None:
        yield None
    else:
        with lease(root) as value:
            yield value


def enroll(root, anchor_directory):
    """Called only while archive holds all pre-enrollment local owner locks."""
    root = files.check_path(Path(root).absolute())
    parent = files.check_path(Path(anchor_directory).absolute())
    if (root.resolve() != root or parent.resolve() != parent or not parent.is_dir()
            or parent == root or parent.is_relative_to(root) or root.is_relative_to(parent)):
        raise files.ArchiveError('Explicit external anchor directory must be separate')
    existing = _binding(root)
    if existing is not None:
        require_owner(root)
        if Path(existing['anchor']).parent != parent:
            raise files.ArchiveError('Enrolled owner already belongs to another explicit anchor directory')
        return existing
    from . import contracts as c
    settings = c.settings(root)
    key = hashlib.sha256((settings['goal_id'] + c.digest(root / 'settings.json')).encode()).hexdigest()
    directory = parent / key
    value = dict(version=1, instance_id=settings['goal_id'], settings_sha256=c.digest(root / 'settings.json'),
        root=str(root), host=_host(), anchor=str(directory), anchor_id=uuid.uuid4().hex, generation=1)
    if directory.exists():
        original = _read(directory / 'owner.json')
        if (any(original.get(k) != value[k] for k in ('version','instance_id','settings_sha256','root','host','anchor'))
                or original.get('state') != 'active' or original.get('generation') != 1
                or original.get('root_identity') != _identity(root)):
            raise files.ArchiveError('Existing enrollment anchor cannot be adopted or replaced')
        value = {key:original[key] for key in value}
    else:
        directory.mkdir(mode=0o700)
        fd = os.open(directory / 'writer.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        files.durable_json(directory / 'owner.json', dict(value, state='active', root_identity=_identity(root)))
    identity = _read(root / 'settings-identity.json')
    files.durable_json(root / 'settings-identity.json',dict(identity,cold_owner=dict(anchor=str(directory),anchor_id=value['anchor_id'])))
    files.durable_json(root / BINDING, value)
    return value


def repair_enrollment(root, anchor_directory):
    """Only explicit enrollment retries may complete marker-before-binding crash."""
    root = files.check_path(Path(root).absolute())
    if (root / BINDING).exists() or (root / BINDING).is_symlink():
        return
    marker = _read(root / 'settings-identity.json').get('cold_owner')
    if marker is None:
        return
    directory = files.check_path(Path(marker['anchor']))
    if directory.parent != Path(anchor_directory).absolute():
        raise files.ArchiveError('Enrollment retry must use the original external anchor')
    original = _read(directory / 'owner.json')
    binding = {key:original[key] for key in ('version','instance_id','settings_sha256','root','host','anchor','anchor_id','generation')}
    from . import contracts as c
    if (marker != dict(anchor=binding['anchor'],anchor_id=binding['anchor_id'])
            or binding['host'] != _host() or binding['root'] != str(root)
            or binding['instance_id'] != c.settings(root)['goal_id']
            or binding['settings_sha256'] != c.digest(root / 'settings.json')):
        raise files.ArchiveError('Enrollment retry lost its original host and instance')
    with exclusive(root,binding=binding) as (_, current, _):
        if current != original or current['state'] != 'active' or current['root_identity'] != _identity(root):
            raise files.ArchiveError('Enrollment retry cannot recreate a frozen or superseded binding')
        files.durable_json(root / BINDING,binding)


@contextmanager
def exclusive(root, *, binding=None):
    root = Path(root).absolute()
    binding = _binding(root) if binding is None else binding
    if binding is None or str(root) in _held.get():
        raise files.ArchiveError('Explicit enrolled cold owner required, outside an effect lease')
    with _host_gate(root, exclusive=True, cold=True, allow_missing=True):
        with _exclusive_owner(root, binding) as value:
            yield value


@contextmanager
def _exclusive_owner(root, binding):
    directory, _ = _anchor(binding)
    fd = os.open(directory / 'writer.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise files.ArchiveError('An enrolled effect is still active') from error
        token = _held.set({**_held.get(), str(root): dict(exclusive=True, binding=binding)})
        try:
            yield directory, _read(directory / 'owner.json'), binding
        finally:
            _held.reset(token)
    finally:
        os.close(fd)


def read_transition(root, checkpoint_ref, transfer_ref):
    from . import qualification as q
    checkpoint, transfer = q._read(root, checkpoint_ref), q._read(root, transfer_ref)
    if (checkpoint.get('version') != 1 or checkpoint.get('kind') != 'cold_checkpoint'
            or transfer.get('version') != 1 or transfer.get('kind') != 'owner_restore'
            or transfer.get('checkpoint_ref') != checkpoint_ref
            or any(transfer.get(key) != checkpoint.get(key) for key in
                ('transaction', 'instance_id', 'original_root', 'anchor_id', 'host'))
            or transfer.get('from_generation') != checkpoint['source_generation']
            or transfer.get('to_generation') != checkpoint['source_generation'] + 1
            or checkpoint['original_root'] != str(Path(root).absolute())):
        raise files.ArchiveError('Cold restore immutable identities differ')
    return dict(checkpoint=checkpoint, transfer=transfer)


def verify_transition(root, checkpoint_ref, transfer_ref):
    held = _held.get().get(str(Path(root).absolute()))
    if held is None or not held['exclusive']:
        raise files.ArchiveError('Publishing a cold transition requires its exclusive restore owner')
    value = read_transition(root, checkpoint_ref, transfer_ref)
    checkpoint, transfer = value['checkpoint'], value['transfer']
    binding = _binding(root)
    if binding is None:
        raise files.ArchiveError('Restored owner binding is unavailable')
    _, anchor = _anchor(binding)
    if (anchor.get('state') not in {'restore_pending', 'active'}
            or anchor.get('generation') != transfer['to_generation']
            or anchor.get('transaction') != checkpoint['transaction']
            or anchor.get('checkpoint_sha256') != checkpoint_ref['sha256']
            or anchor.get('root_identity') != _identity(root)
            or transfer.get('root_identity') != _identity(root)):
        raise files.ArchiveError('Cold restore has no current exclusive owner transition')
    return value
