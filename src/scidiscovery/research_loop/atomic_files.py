"""Small atomic publication helpers; temporary bytes never name an original.

The hard link publishes an independent temporary inode, never a writable source.
Callers retain ownership of path, identity and operation-context policy.
"""
from contextlib import contextmanager
import hashlib
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


def _verify_copy(path, sha256, size_bytes, context):
    digest, size = hashlib.sha256(), 0
    _check(context)
    with _regular_stream(path) as original:
        limit = os.fstat(original.fileno()).st_size if size_bytes is None else size_bytes
        if os.fstat(original.fileno()).st_size != limit:
            raise ValueError('Atomic original size changed')
        while True:
            _check(context)
            block = original.read(min(1024 * 1024, limit - size + 1))
            if not block:
                break
            size += len(block)
            if size > limit:
                raise ValueError('Atomic original exceeds its declared identity')
            digest.update(block)
    _check(context)
    if (digest.hexdigest(), size) != (sha256, limit):
        raise ValueError('Atomic original identity changed')


def copy_original(source, path, *, sha256, size_bytes=None, mode=0o400, context=None):
    """Copy at most the declared size, verify identity, then publish exclusively.

    When the caller only declares SHA, the source's initial size bounds this copy.
    Existing originals are verified and retained; they are never repaired in place.
    """
    source, path = Path(source), Path(path)
    if size_bytes is not None and (type(size_bytes) is not int or size_bytes < 0):
        raise ValueError('Atomic original size must be a nonnegative integer')
    _check(context)
    if path.exists() or path.is_symlink():
        _verify_copy(path, sha256, size_bytes, context)
        return
    with _temporary(path) as (temporary, output), _regular_stream(source) as original:
        limit = os.fstat(original.fileno()).st_size if size_bytes is None else size_bytes
        if os.fstat(original.fileno()).st_size != limit:
            raise ValueError('Atomic original size changed before retention')
        digest, size = hashlib.sha256(), 0
        while True:
            _check(context)
            # One extra byte detects a growing source without writing past its bound.
            block = original.read(min(1024 * 1024, limit - size + 1))
            if not block:
                break
            _check(context)
            size += len(block)
            if size > limit:
                raise ValueError('Atomic original exceeds its declared identity')
            digest.update(block)
            output.write(block)
        if (digest.hexdigest(), size) != (sha256, limit):
            raise ValueError('Atomic original identity changed during retention')
        _finish(output, mode)
        _check(context)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            _verify_copy(path, sha256, limit, context)
        temporary.unlink()
        _sync_directory(path.parent)
