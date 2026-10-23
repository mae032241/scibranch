"""Reuse the instance's existing content archive; preserve logical identities.

Host originals may share inodes. Every Worker-writable view gets an independent
inode, using a reflink where supported and a bounded copy otherwise.
"""
import errno
import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import re
import stat
import struct

from . import atomic_files, storage_policy as storage

FICLONERANGE = 0x4020940D
_COPY_FALLBACK = {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL,
                  errno.ENOSYS, errno.EPERM}
_proofs = ContextVar('shared_original_proofs', default=None)


@contextmanager
def verified():
    """Only one synchronous operation may reuse verified inode generations."""
    if _proofs.get() is not None:
        yield
        return
    token=_proofs.set({})
    try:
        yield
    finally:
        _proofs.reset(token)


def _generation(path):
    info=path.stat()
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _remember(generation, sha256):
    cache=_proofs.get()
    if cache is not None:
        if len(cache)>=4096:cache.clear()
        cache[generation]=sha256


def _path(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve()!=path or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Managed original requires an exact path without links')
    return path


def _verify(path, sha256, size_bytes, context):
    from . import contracts as c
    for attempt in range(3):
        storage.check(context)
        before=_generation(path)
        if not stat.S_ISREG(before[2]) or before[3] != size_bytes:
            raise ValueError('Shared original differs from its exact binding')
        cache=_proofs.get()
        actual=cache.get(before) if cache is not None else None
        if actual is None:actual=c.digest(path) if context is None else _digest(path,context)
        after=_generation(path)
        if after!=before:
            if attempt<2 and after[:-1]==before[:-1] and stat.S_IMODE(before[2])==0o400:continue
            raise ValueError('Shared original changed during verification')
        if actual!=sha256:raise ValueError('Shared original differs from its exact binding')
        _remember(after,sha256)
        return after


def _digest(path, context):
    from . import experiments as e
    return e.proof(path, context=context)['sha256']


def retain(root, source, *, sha256, size_bytes, context=None, policy=None):
    root, source = _path(root), _path(source)
    context = context or storage.Deadline(65)
    if not isinstance(sha256, str) or re.fullmatch('[0-9a-f]{64}', sha256) is None:
        raise ValueError('Exact content identity required')
    target = _path(root / 'host-history/objects' / sha256)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits, context=context).file(size_bytes)
    with storage.mutation(root,size_bytes,policy=limits,context=context,target=target):
        if target.exists():
            _verify(target, sha256, size_bytes, context)
        else:
            atomic_files.copy_original(source, target, sha256=sha256, size_bytes=size_bytes, context=context)
            _remember(_generation(target),sha256)
    return target


def link(original, destination, *, sha256, size_bytes, context=None, root=None, policy=None):
    original, destination = _path(original), _path(destination)
    if stat.S_IMODE(original.stat().st_mode) != 0o400:
        raise ValueError('Only a frozen host original may share its inode')
    before=_verify(original, sha256, size_bytes, context)
    if destination.exists():
        _verify(destination, sha256, size_bytes, context)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    storage.check(context)
    created=False
    try:
        os.link(original, destination, follow_symlinks=False)
        created=True
    except FileExistsError:
        _verify(destination, sha256, size_bytes, context)
    except OSError as error:
        if error.errno not in _COPY_FALLBACK | {errno.EMLINK}:
            raise
        isolated_copy(original, destination, sha256=sha256, size_bytes=size_bytes, context=context,root=root,policy=policy)
    if created:
        after=_generation(destination)
        if after[:-1]!=before[:-1] or _generation(original)[:-1]!=after[:-1]:
            destination.unlink()
            atomic_files._sync_directory(destination.parent)
            raise ValueError('Shared source changed during alias publication')
        _remember(after,sha256)
    atomic_files._sync_directory(destination.parent)


def isolated_copy(source, destination, *, sha256, size_bytes=None, context=None, policy=None, root=None):
    source, destination = _path(source), _path(destination)
    if root is not None and not destination.is_relative_to(_path(root)):
        raise ValueError('Managed materialization escaped its explicit instance root')
    size_bytes = source.stat().st_size if size_bytes is None else size_bytes
    limits = storage.for_path(root, destination) if policy is None else storage.configuration(policy)
    context = context or storage.Deadline(65)
    storage.Inventory(limits, context=context).file(size_bytes)
    with storage.mutation(root,size_bytes,policy=limits,context=context,target=destination):
        if destination.exists():
            _verify(destination, sha256, size_bytes, context)
            return
        fallback = False
        with atomic_files._temporary(destination) as (temporary, output):
            with atomic_files._regular_stream(source) as original:
                if os.fstat(original.fileno()).st_size != size_bytes:
                    raise ValueError('Isolated source changed its declared size')
                storage.check(context)
                try:
                    if size_bytes == 0:
                        fallback = True
                    else:
                        # A whole-file clone could exceed the reservation if a
                        # writable source grows between fstat and ioctl.
                        fcntl.ioctl(output.fileno(), FICLONERANGE,
                            struct.pack('qQQQ',original.fileno(),0,size_bytes,0))
                except OSError as error:
                    if error.errno not in _COPY_FALLBACK:
                        raise
                    fallback = True
            if not fallback:
                atomic_files._finish(output, 0o400)
                _verify(temporary, sha256, size_bytes, context)
                storage.check(context)
                try:
                    os.link(temporary, destination, follow_symlinks=False)
                except FileExistsError:
                    _verify(destination, sha256, size_bytes, context)
                atomic_files._sync_directory(destination.parent)
        if fallback:
            atomic_files.copy_original(source, destination, sha256=sha256,
                size_bytes=size_bytes, context=context)
