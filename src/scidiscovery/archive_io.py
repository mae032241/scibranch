"""Shared archive file verification and publication; no service or database imports."""
from __future__ import annotations

import errno
import hashlib
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


def hash_file(path, *, context=None):
    binding = None
    for attempt in range(3):
        if context is not None:
            context.remaining_seconds()
        check_path(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ArchiveError("archive source is not a regular file")
            generation = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if binding is not None and generation[:-1] != binding:
                raise ArchiveError("archive file changed while hashing")
            binding = generation[:-1]
            sha = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    if context is not None:
                        context.remaining_seconds()
                    sha.update(chunk)
            after = os.fstat(fd)
            checked = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            if checked != generation:
                # Linking an immutable original changes ctime. Retry the read,
                # preserving every other generation field and the retry bound.
                if attempt < 2 and stat.S_IMODE(info.st_mode) == 0o400 and checked[:-1] == binding:
                    continue
                raise ArchiveError("archive file changed while hashing")
            return {"size_bytes": info.st_size, "sha256": sha.hexdigest(), "mode": stat.S_IMODE(info.st_mode),
                    "source_stat": [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]}
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


def link_verified(source, destination, entry, *, context=None):
    """Retain one immutable inode without changing its bytes or permissions."""
    source, destination = check_path(source), check_path(destination)
    if entry['mode'] != 0o400:
        raise ArchiveError('Shared originals must have mode 0400')
    original = hash_file(source, context=context)
    if any(original[key] != entry[key] for key in ('sha256', 'size_bytes', 'mode')):
        raise ArchiveError('Shared source differs from its immutable binding')
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
        actual = hash_file(destination, context=context)
        if any(actual[key] != entry[key] for key in ('sha256', 'size_bytes', 'mode')):
            raise ArchiveError('Shared destination differs from its immutable binding')
        if actual['source_stat'][:2] != original['source_stat'][:2]:
            raise ArchiveError('Shared source inode changed during publication')
        fsync_directory(destination.parent)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
            fsync_directory(destination.parent)
        raise


def copy_verified(source, destination, entry, *, context=None):
    """Copy before unlink, including on the same filesystem, to preserve drafts."""
    check_path(source)
    check_path(destination)
    if entry["kind"] == "directory":
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        return
    if destination.exists():
        actual = hash_file(destination, context=context)
        if any(actual[key] != entry[key] for key in ("sha256", "size_bytes", "mode")):
            raise ArchiveError("destination file collision")
        return
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + ".archive-copy")
    parent_mode = stat.S_IMODE(destination.parent.stat().st_mode)
    descriptor = None
    try:
        os.chmod(destination.parent, parent_mode | 0o700)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as output, open(source, "rb") as input_file:
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                if context is not None:
                    context.remaining_seconds()
                output.write(chunk)
            output.flush()
            os.fchmod(descriptor, entry["mode"])
            os.fsync(descriptor)
        actual = hash_file(temporary, context=context)
        if any(actual[key] != entry[key] for key in ("sha256", "size_bytes")):
            raise ArchiveError("copy verification failed")
        # CAS may acquire a new owner while this instance is restoring. Publish
        # without replacement, matching the CAS store's own immutable protocol.
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            actual = hash_file(destination, context=context)
            if any(actual[key] != entry[key] for key in ("sha256", "size_bytes", "mode")):
                raise ArchiveError("destination appeared with different content or permissions")
        fsync_directory(destination.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        finally:
            os.chmod(destination.parent, parent_mode)


def copy_independent_verified(source, destination, entry, *, context=None):
    """Publish one bounded independent inode, retaining exact existing originals."""
    from .research_loop import atomic_files
    source, destination = check_path(source), check_path(destination)
    original = hash_file(source, context=context)
    if original['mode'] != 0o400 or any(original[key] != entry[key] for key in ('sha256', 'size_bytes')):
        raise ArchiveError('Independent copy source differs from its frozen binding')
    try:
        atomic_files.copy_original(source, destination, sha256=entry['sha256'],
            size_bytes=entry['size_bytes'], mode=entry['mode'], context=context)
    except ValueError as error:
        raise ArchiveError('Independent copy identity changed') from error
    except OSError as error:
        if error.errno in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM, errno.EMLINK}:
            raise SharingUnsupported('Independent copy publication requires supported destination hardlinks') from error
        raise
    actual = hash_file(destination, context=context)
    if (any(actual[key] != entry[key] for key in ('sha256', 'size_bytes', 'mode'))
            or actual['source_stat'][:2] == original['source_stat'][:2]):
        # A competing no-replacement destination remains its owner's original.
        # The archive/checkpoint caller owns cleanup only of its exact stage.
        raise ArchiveError('Independent copy destination is not an independent exact original')
