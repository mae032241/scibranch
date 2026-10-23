"""Small atomic publication helpers; temporary bytes never name an original.

The hard link publishes an independent temporary inode, never a writable source.
Callers retain ownership of path, identity and operation-context policy.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile


def _check(context):
    if context is not None:
        context.remaining_seconds()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _temporary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.scid-', suffix='.partial', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            yield temporary, output
    finally:
        temporary.unlink(missing_ok=True)


def _finish(output, mode):
    output.flush()
    os.fchmod(output.fileno(), mode)
    os.fsync(output.fileno())


@contextmanager
def _regular_stream(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Atomic original must be a regular file')
        stream = os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


def publish_bytes(path, payload, *, mode=0o400, existing_ok=False):
    """Publish fully durable bytes without replacing an existing original."""
    path = Path(path)
    with _temporary(path) as (temporary, output):
        output.write(payload)
        _finish(output, mode)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if not existing_ok:
                raise
            with _regular_stream(path) as original:
                if original.read(len(payload) + 1) != payload:
                    raise ValueError('Atomic original bytes changed')
        temporary.unlink()
        _sync_directory(path.parent)


def replace_bytes(path, payload, *, mode=0o600):
    """Replace a mutable state slot with one complete durable generation."""
    path = Path(path)
    with _temporary(path) as (temporary, output):
        output.write(payload)
        _finish(output, mode)
        os.replace(temporary, path)
        _sync_directory(path.parent)


def _generation(info):
    # Hardlinks legitimately change ctime; byte-generation checks do not use it.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def copy_original(source, path, *, size_bytes, mode=0o400, context=None):
    """Freeze bounded bytes to a fresh inode, with no content fingerprint.

    An existing destination is never adopted merely because its size matches.
    The caller owns logical provenance and reuse of an already published slot.
    """
    source, path = Path(source), Path(path)
    if type(size_bytes) is not int or size_bytes < 0:
        raise ValueError('Atomic original size must be a nonnegative integer')
    _check(context)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    with _temporary(path) as (temporary, output), _regular_stream(source) as original:
        before = _generation(os.fstat(original.fileno()))
        if before[3] != size_bytes or _generation(source.lstat()) != before:
            raise ValueError('Atomic original changed before retention')
        size = 0
        while True:
            _check(context)
            block = original.read(min(1024 * 1024, size_bytes - size + 1))
            if not block:
                break
            size += len(block)
            if size > size_bytes:
                raise ValueError('Atomic original exceeds its declared size')
            output.write(block)
        if (size != size_bytes or _generation(os.fstat(original.fileno())) != before
                or _generation(source.lstat()) != before):
            raise ValueError('Atomic original changed during retention')
        _finish(output, mode)
        _check(context)
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        _sync_directory(path.parent)


def publish_stream(path, blocks, *, size_bytes=None, context=None, inventory=None):
    """Publish one bounded generated stream without a measurement/hash pass."""
    path = Path(path)
    if size_bytes is not None and (type(size_bytes) is not int or size_bytes < 0):
        raise ValueError('Atomic stream size must be a nonnegative integer')
    size = 0
    _check(context)
    with _temporary(path) as (temporary, output):
        for block in blocks:
            _check(context)
            if type(block) is not bytes:
                raise ValueError('Atomic stream requires bytes blocks')
            size += len(block)
            if size_bytes is not None and size > size_bytes:
                raise ValueError('Atomic stream exceeds its declared size')
            if inventory is not None:
                inventory.limit('max_file_bytes', size)
                inventory.limit('max_total_bytes', inventory.total + size)
            output.write(block)
        if size_bytes is not None and size != size_bytes:
            raise ValueError('Atomic stream size changed during publication')
        if inventory is not None:
            inventory.file(size)
        _finish(output, 0o400)
        _check(context)
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        _sync_directory(path.parent)
        _check(context)
    return size
