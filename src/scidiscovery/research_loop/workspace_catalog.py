"""Private, explicit UI navigation catalogs; never research authority or discovery."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile


def _directory(root, *, create=False):
    root = Path(root)
    if root.resolve() != root or not root.is_dir():
        raise ValueError('Workspace catalog requires its existing canonical directory')
    directory = root / '.scid-ui'
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    if directory.exists() or directory.is_symlink():
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError('Unsafe workspace catalog directory')
    return directory


def _private(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError('Unsafe workspace catalog file')


def _name(kind):
    if kind not in {'instances', 'workspaces'}:
        raise ValueError('Unknown UI catalog')
    return kind + '.json'


def load(root, kind):
    """Read only this named registry. Bad entries are isolated by the UI owner."""
    path = _directory(root) / _name(kind)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        _private(info)
        value = json.load(source)
    if (not isinstance(value, dict) or set(value) != {'version', kind}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value[kind], list)):
        raise ValueError('Unsupported workspace catalog; no migration')
    return value[kind]


def record(value, kind):
    fields = {'root', 'goal_id'} if kind == 'instances' else {'root'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid UI catalog entry')
    if not isinstance(value['root'], str) or not value['root']:
        raise ValueError('UI catalog requires an explicit canonical root')
    root = Path(value['root'])
    if not root.is_absolute() or str(root) != value['root'] or root.resolve() != root:
        raise ValueError('UI catalog root has changed or is not canonical')
    if kind == 'instances' and (not isinstance(value['goal_id'], str) or not value['goal_id']):
        raise ValueError('UI catalog requires an exact instance identity')
    return root


@contextmanager
def _locked(directory):
    descriptor = os.open(directory / 'catalog.lock',
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
    try:
        _private(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def remember(root, kind, value, *, remove=False):
    """Atomically update one explicit root without replacing another identity."""
    record(value, kind)
    directory = _directory(root, create=True)
    with _locked(directory):
        rows = load(root, kind)
        existing = next((item for item in rows if isinstance(item, dict)
                         and item.get('root') == value['root']), None)
        if existing is not None and existing != value:
            raise ValueError('Registered instance identity changed; refusing to rebind its root')
        rows = [item for item in rows if not (isinstance(item, dict) and item.get('root') == value['root'])]
        if not remove:
            rows.append(value)
        descriptor, temporary = tempfile.mkstemp(prefix='.catalog-', dir=directory)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                json.dump(dict(version=1, **{kind: rows}), target, ensure_ascii=False)
                target.write('\n'); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, directory / _name(kind))
            directory_fd = os.open(directory, os.O_DIRECTORY | os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
