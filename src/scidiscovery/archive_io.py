"""Shared archive file verification and publication; no service or database imports."""
from __future__ import annotations

import errno
import json
import os
import re
import stat
from pathlib import Path


class ArchiveError(RuntimeError):
    pass


class SharingUnsupported(ArchiveError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value) or value in {".", ".."}:
        raise ArchiveError("invalid storage identity")
    return value


def check_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ArchiveError("symbolic links are unsupported in managed archive paths")
    return path


def stat_file(path, *, context=None):
    """Read file metadata without reading or fingerprinting its contents."""
    if context is not None:
        context.remaining_seconds()
    path = check_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ArchiveError("archive source is not a regular file")
        return dict(size_bytes=info.st_size, mode=stat.S_IMODE(info.st_mode),
            source_stat=[info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns])
    finally:
        os.close(fd)

def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_json(path, value):
    check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def link_original(source, destination, entry, *, context=None):
    """Publish the exact immutable inode, rejecting every foreign collision."""
    source, destination = check_path(source), check_path(destination)
    original = stat_file(source, context=context)
    if original['mode'] != 0o400 or entry['mode'] != 0o400 or original['size_bytes'] != entry['size_bytes']:
        raise ArchiveError('Shared original is not a frozen exact source')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    created = False
    try:
        try:
            os.link(source, destination, follow_symlinks=False)
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            if error.errno in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM, errno.EMLINK}:
                raise SharingUnsupported('Managed originals require supported same-filesystem hardlinks') from error
            raise
        actual = stat_file(destination, context=context)
        if (actual['source_stat'][:2] != original['source_stat'][:2]
                or actual['size_bytes'] != original['size_bytes'] or actual['mode'] != 0o400):
            raise ArchiveError('Shared destination is not its exact source inode')
        after = stat_file(source, context=context)
        if any(after[k] != original[k] for k in ('size_bytes', 'mode')) or after['source_stat'][:3] != original['source_stat'][:3]:
            raise ArchiveError('Shared original changed during publication')
        fsync_directory(destination.parent)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
            fsync_directory(destination.parent)
        raise


def copy_original(source, destination, entry, *, context=None):
    """Copy one exact source once; a same-size destination is never a receipt."""
    from .research_loop import atomic_files
    source, destination = check_path(source), check_path(destination)
    if entry['kind'] == 'directory':
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        return
    original = stat_file(source, context=context)
    if original['size_bytes'] != entry['size_bytes']:
        raise ArchiveError('Copy source size changed')
    try:
        atomic_files.copy_original(source, destination, size_bytes=entry['size_bytes'],
            mode=entry['mode'], context=context)
    except ValueError as error:
        raise ArchiveError('Copy source changed or destination collides') from error
    actual = stat_file(destination, context=context)
    if (actual['source_stat'][:2] == original['source_stat'][:2]
            or actual['size_bytes'] != entry['size_bytes'] or actual['mode'] != entry['mode']):
        raise ArchiveError('Copy destination is not an independent original')


def copy_independent(source, destination, entry, *, context=None):
    if stat_file(source, context=context)['mode'] != 0o400:
        raise ArchiveError('Independent archive copy source must be frozen')
    copy_original(source, destination, dict(entry, kind='file'), context=context)
