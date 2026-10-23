"""Explicit, quiescent instance retirement with exact-operation cleanup retries."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

from scidiscovery import archive_io as files
from . import contracts as c, instance_archive as archive, instance_owner as owner, protocol


class DeleteError(files.ArchiveError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise DeleteError('delete_path_unsafe', 'Delete target must be an original directory')
    return dict(dev=info.st_dev, ino=info.st_ino)


def _same(path, identity):
    files.check_path(path)
    if _identity(path) != identity:
        raise DeleteError('delete_identity_changed', 'Delete target directory identity changed')


@contextmanager
def _lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        owner._same_gate_path(path, fd, directory=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeleteError('delete_busy', 'Another delete operation owns this target or operation ID') from error
        yield
        owner._same_gate_path(path, fd, directory=False)
    finally:
        os.close(fd)


def _write_record(path, record):
    files.durable_json(path, record)


def _mount_points():
    # Same-device bind mounts are not detected by st_dev or Path.is_mount().
    def decode(value):
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
    return [Path(decode(line.split()[4]))
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def _quiescence_namespace(parts):
    # Archive admission enumerates these exact owners. It must not follow their
    # symlinks before the generic unlink-only cleanup can handle other entries.
    if parts[0] in {'jobs', 'native-evidence', 'execution-authority', 'host-delivery', 'control-ledger'}:
        return True
    if parts[0] == 'host-runtime':
        return len(parts) == 1 or parts[1] == 'experiment-control'
    if parts[0] == 'project':
        return (len(parts) == 1 or parts[1] == 'nodes' and
                (len(parts) <= 3 or parts[3] == 'work' and
                 (len(parts) == 4 or parts[4] == 'lineage')))
    return False


def _check_mounts(root):
    if any(path == root or path.is_relative_to(root) for path in _mount_points()):
        raise DeleteError('delete_mount_unsafe', 'Delete target contains a mounted tree')


def _preflight_tree(root, identity, *, context=None):
    if context is not None:context.remaining_seconds()
    _same(root, identity)
    _check_mounts(root)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise DeleteError('delete_path_unsafe', 'Symlink-safe directory removal is unavailable')
    def failed(error):
        raise error
    for current, directories, names in os.walk(root, followlinks=False, onerror=failed):
        if context is not None:context.remaining_seconds()
        current = Path(current)
        originals = {}
        for name in directories + names:
            if context is not None:context.remaining_seconds()
            path = current / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if _quiescence_namespace(path.relative_to(root).parts):
                    raise DeleteError('delete_path_unsafe', 'A quiescence owner namespace contains a symbolic link')
                continue  # Delete the entry only; its target is never inspected.
            if info.st_dev != identity['dev']:
                raise DeleteError('delete_mount_unsafe', 'Delete target crosses a filesystem boundary')
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise DeleteError('delete_path_unsafe', 'Delete target contains a special file')
            originals[name] = info.st_mode
        # Identity damage must not turn an independent instance into payload.
        # Fork originals retain these settings in separate hash-versioned trees,
        # without an adjacent settings pair or local global-material directory.
        settings = {'settings.json', 'settings-identity.json'} & originals.keys()
        project = False
        if current != root and settings and stat.S_ISDIR(originals.get('project', 0)):
            try:
                project = stat.S_ISDIR((current / 'project/global').lstat().st_mode)
            except FileNotFoundError:
                pass
        if current != root and (
                {owner.BINDING, owner.RETIREMENT} & originals.keys()
                or len(settings) == 2
                or project):
            raise DeleteError('delete_nested_instance', 'Delete target contains an independent nested instance')
    _same(root, identity)


def _publish_retirement(root, tombstone):
    archive._publish(root, tombstone)  # Atomic no-replacement rename and parent fsync.


def _remove_tree(root, identity, *, context=None):
    # Directory inodes cannot be hard-linked by supported instance storage.
    # Open without following links, and never chmod a shared regular inode.
    if not shutil.rmtree.avoids_symlink_attacks:
        raise DeleteError('delete_path_unsafe', 'Symlink-safe directory removal is unavailable')
    _same(root, identity)
    def failed(error):
        raise error
    for current, _, _ in os.walk(root, followlinks=False, onerror=failed):
        if context is not None:context.remaining_seconds()
        path = Path(current)
        expected = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            actual = os.fstat(fd)
            if ((actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
                    or actual.st_dev != identity['dev']):
                raise DeleteError('delete_identity_changed', 'Delete directory changed during cleanup')
            os.fchmod(fd, stat.S_IMODE(actual.st_mode) | 0o700)
        finally:
            os.close(fd)
    _same(root, identity)
    if context is None:
        shutil.rmtree(root)
    else:
        # shutil's safe fd traversal has no per-entry deadline callback. Keep
        # the same no-follow/inode fences while checking this finite operation
        # before every unlink, open and rmdir; default instance deletion above
        # retains its original traversal.
        def remove(descriptor):
            for name in os.listdir(descriptor):
                context.remaining_seconds()
                expected = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(expected.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=descriptor)
                    try:
                        actual = os.fstat(child)
                        if ((actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
                                or actual.st_dev != identity['dev']):
                            raise DeleteError('delete_identity_changed', 'Delete directory changed during cleanup')
                        remove(child)
                    finally:
                        os.close(child)
                    context.remaining_seconds()
                    current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if (current.st_dev,current.st_ino) != (expected.st_dev,expected.st_ino):
                        raise DeleteError('delete_identity_changed', 'Delete directory changed before unlink')
                    os.rmdir(name, dir_fd=descriptor)
                else:
                    context.remaining_seconds()
                    os.unlink(name, dir_fd=descriptor)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            actual = os.fstat(descriptor)
            if dict(dev=actual.st_dev, ino=actual.st_ino) != identity:
                raise DeleteError('delete_identity_changed', 'Delete target directory identity changed')
            remove(descriptor)
        finally:
            os.close(descriptor)
        context.remaining_seconds()
        _same(root, identity)
        os.rmdir(root)
    files.fsync_directory(root.parent)


def _verify_instance(root, request):
    settings = c.settings(root)
    if (settings.get('goal_id') != request['instance_id']
            or c.digest(root / 'settings.json') != request['settings_sha256']):
        raise DeleteError('delete_identity_mismatch', 'Explicit instance identity differs from the frozen original')
    if type(settings.get('handoff_protocol')) is not int or settings['handoff_protocol'] != protocol.CURRENT:
        raise protocol.UnsupportedProtocol('Instance deletion')


def _anchor_state(binding, anchor, record):
    if binding is None:
        return
    if anchor.get('generation') != binding['generation'] or anchor.get('root_identity') != record['root_identity']:
        raise DeleteError('delete_owner_conflict', 'Delete owner generation or original directory differs')
    if anchor.get('state') == 'active':
        if record['phase'] != 'prepared':
            raise DeleteError('delete_owner_conflict', 'Retired instance has no matching retired owner')
    elif (anchor.get('state') not in {'delete_pending', 'deleted'}
            or anchor.get('delete_operation') != record['request']):
        raise DeleteError('delete_owner_conflict', 'Owner is frozen, superseded, or belongs to another delete')


def _retire_anchor(directory, anchor, record, state):
    if directory is not None:
        files.durable_json(directory / 'owner.json', dict(anchor, state=state,
            delete_operation=record['request']))


def _receipt(path, record):
    return dict(status='instance_deleted', operation_id=record['request']['operation_id'],
        instance_id=record['request']['instance_id'], settings_sha256=record['request']['settings_sha256'],
        original_root=record['request']['root'], receipt=str(path))


def _validate_record(record, request, parent_identity, tombstone):
    if (not isinstance(record, dict) or record.get('version') != 1
            or set(record) != {'version', 'request', 'parent_identity', 'root_identity',
                               'tombstone', 'owner_binding', 'phase'}
            or record.get('request') != request or record.get('parent_identity') != parent_identity
            or record.get('tombstone') != str(tombstone)
            or record.get('phase') not in {'prepared', 'retired', 'deleted'}
            or not isinstance(record.get('root_identity'), dict)
            or set(record['root_identity']) != {'dev', 'ino'}
            or any(type(value) is not int or value < 0 for value in record['root_identity'].values())):
        raise DeleteError('delete_operation_conflict', 'Operation ID is bound to another request or storage identity')
    binding = record['owner_binding']
    if binding is not None and (not isinstance(binding, dict)
            or set(binding) != {'version', 'instance_id', 'settings_sha256', 'root', 'host',
                                'anchor', 'anchor_id', 'generation'}
            or any(binding.get(key) != request[key] for key in ('instance_id', 'settings_sha256', 'root'))
            or binding.get('host') != owner._host()
            or type(binding.get('generation')) is not int or binding['generation'] <= 0):
        raise DeleteError('delete_owner_conflict', 'Delete receipt lost its exact external owner binding')


def _perform(root, path, record):
    request, identity = record['request'], record['root_identity']
    tombstone, binding = Path(record['tombstone']), record['owner_binding']
    phase = record['phase']
    if phase == 'deleted':
        return _receipt(path, record)
    # A prepared record does not establish that unlinking ever began.
    retired = tombstone.exists() or tombstone.is_symlink()
    if not retired and phase == 'prepared':
        if not root.exists() and not root.is_symlink():
            raise DeleteError('delete_state_unknown', 'Prepared delete lost both its original root and tombstone')
        _same(root, identity)
    elif retired:
        _same(tombstone, identity)
        _check_mounts(tombstone)
    elif phase != 'retired':
        raise DeleteError('delete_state_unknown', 'Delete has no proven retired original')
    with owner.disposal(root, tombstone, identity, binding=binding, retired=retired or phase == 'retired') as (token, directory, anchor):
        _anchor_state(binding, anchor, record)
        if retired and phase == 'prepared':
            if archive._read(tombstone / owner.RETIREMENT) != request:
                raise DeleteError('delete_state_unknown', 'Prepared tombstone has no exact retirement marker')
            if binding is not None and anchor.get('state') == 'active':
                raise DeleteError('delete_owner_conflict', 'Prepared tombstone has no retired external owner')
        if not retired and phase == 'prepared':
            _preflight_tree(root, identity)
            _verify_instance(root, request)
            marker = root / owner.RETIREMENT
            if marker.exists() or marker.is_symlink():
                if archive._read(marker) != request:
                    raise DeleteError('delete_operation_conflict', 'Instance already belongs to another delete operation')
            with ExitStack() as locks:
                archive._owner_locks(locks, root, disposing=True)
                archive._quiescent(root)
                _preflight_tree(root, identity)
                _verify_instance(root, request)
                _write_record(path, record)
                files.durable_json(marker, request)
                _retire_anchor(directory, anchor, record, 'delete_pending')
                token.check()
                try:
                    _publish_retirement(root, tombstone)
                finally:
                    # Rename may have committed even when its fsync/response failed.
                    if tombstone.exists() or tombstone.is_symlink():
                        token.retire()
                record = dict(record, phase='retired')
                _write_record(path, record)
            retired = True
        elif retired and phase == 'prepared':
            # Rename succeeded but its receipt update did not return.
            record = dict(record, phase='retired')
            _write_record(path, record)
        if retired:
            # Repeat mount checks on every pending cleanup, including retries.
            _preflight_tree(tombstone, identity)
            try:
                _remove_tree(tombstone, identity)
            finally:
                if not tombstone.exists() and not tombstone.is_symlink():
                    token.finish()
        token.finish()
        _retire_anchor(directory, anchor, record, 'deleted')
        record = dict(record, phase='deleted')
        _write_record(path, record)
        return _receipt(path, record)


def delete_instance(root, *, instance_id, settings_sha256, operation_id):
    """Permanently delete only the exact named quiescent instance or its tombstone."""
    if (not isinstance(instance_id, str) or not instance_id
            or not isinstance(settings_sha256, str) or not re.fullmatch('[0-9a-f]{64}', settings_sha256)):
        raise DeleteError('delete_identity_required', 'Exact instance ID and frozen settings SHA256 are required')
    try:
        files.safe_id(operation_id)
    except files.ArchiveError as error:
        raise DeleteError('delete_operation_required', 'One bounded original operation ID is required') from error
    root = Path(root).expanduser().absolute()
    parent = files.check_path(root.parent)
    if parent.resolve() != parent or not parent.is_dir() or root == parent:
        raise DeleteError('delete_path_unsafe', 'Explicit canonical root parent is required')
    if any(protected == root or protected.is_relative_to(root)
           for protected in (Path.cwd(), owner._gate_directory())):
        raise DeleteError('delete_path_unsafe', 'Delete root may not contain working directory or owner gates')
    parent_identity = _identity(parent)
    directory = parent / '.scid-delete-operations'
    if root == directory or root.name.startswith('.scid-delete-'):
        raise DeleteError('delete_path_unsafe', 'Delete root may not name deletion control storage')
    files.check_path(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    files.fsync_directory(parent)
    owner._private_stat(directory.lstat(), directory=True)
    key = hashlib.sha256(operation_id.encode()).hexdigest()
    path = directory / (key + '.json')
    tombstone = parent / ('.scid-delete-' + key[:24])
    target_key = hashlib.sha256(os.fsencode(root.name)).hexdigest()
    request = dict(root=str(root), instance_id=instance_id, settings_sha256=settings_sha256,
                   operation_id=operation_id)
    with _lock(directory / (key + '.lock')), _lock(directory / ('target-' + target_key + '.lock')):
        _same(parent, parent_identity)
        if path.exists() or path.is_symlink():
            record = archive._read(path)
            _validate_record(record, request, parent_identity, tombstone)
        else:
            files.check_path(root)
            if root.resolve() != root:
                raise DeleteError('delete_path_unsafe', 'Delete root must be the exact canonical directory')
            identity = _identity(root)
            if tombstone.exists() or tombstone.is_symlink():
                raise DeleteError('delete_operation_conflict', 'Reserved delete tombstone already exists')
            _check_mounts(root)
            _verify_instance(root, request)
            binding = owner._binding(root)
            if binding is not None:
                anchor = Path(binding['anchor'])
                if anchor == root or anchor.is_relative_to(root) or root.is_relative_to(anchor):
                    raise DeleteError('delete_path_unsafe', 'Delete root must remain separate from its external owner anchor')
            record = dict(version=1, request=request, parent_identity=parent_identity,
                root_identity=identity, tombstone=str(tombstone), owner_binding=binding, phase='prepared')
        result = _perform(root, path, record)
        _same(parent, parent_identity)
        return result
