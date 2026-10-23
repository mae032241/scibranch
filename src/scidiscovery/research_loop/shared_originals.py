"""Reuse the instance's existing content archive; preserve logical identities.

Mutable sources freeze once; all managed read-only views hold strict hardlinks.
Scientific identity remains with each original call and path.
"""
import errno
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import re
import stat

from . import atomic_files, storage_policy as storage

_SHARING_UNSUPPORTED = {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL,
                        errno.ENOSYS, errno.EPERM, errno.EMLINK}
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


def _identity(sha256, size_bytes):
    if not isinstance(sha256, str) or re.fullmatch('[0-9a-f]{64}', sha256) is None:
        raise ValueError('Exact content identity required')
    if type(size_bytes) is not int or size_bytes < 0:
        raise ValueError('Managed file size must be a nonnegative integer')


def _frozen(path, sha256, size_bytes, context):
    generation = _verify(path, sha256, size_bytes, context)
    if stat.S_IMODE(generation[2]) != 0o400:
        raise ValueError('Only a frozen host original may share its inode')
    return generation


def _hardlink(source, target):
    from ..archive_io import SharingUnsupported
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError as error:
        if error.errno in _SHARING_UNSUPPORTED:
            raise SharingUnsupported('Managed originals require same-filesystem hardlink support') from error
        raise


def retain(root, source, *, sha256, size_bytes, source_kind, context=None, policy=None):
    root, source = _path(root), _path(source)
    _identity(sha256, size_bytes)
    if source_kind not in {'mutable', 'managed_frozen'}:
        raise ValueError('Explicit mutable or managed_frozen source kind required')
    if source_kind == 'managed_frozen' and not source.is_relative_to(root):
        raise ValueError('Managed frozen source escaped its explicit instance root')
    context = context or storage.Deadline(65)
    target = _path(root / 'host-history/objects' / sha256)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits, context=context).file(size_bytes)
    with storage.writer_lock(root, context=context):
        before = (_frozen if source_kind == 'managed_frozen' else _verify)(source, sha256, size_bytes, context)
        if target.exists():
            _frozen(target, sha256, size_bytes, context)
        elif source_kind == 'managed_frozen':
            link(source, target, sha256=sha256, size_bytes=size_bytes, root=root, context=context, policy=limits)
        else:
            with storage.mutation(root,size_bytes,policy=limits,context=context,target=target):
                atomic_files.copy_original(source, target, sha256=sha256, size_bytes=size_bytes, context=context)
                if _generation(source) != before:
                    # The published independent bytes remain a valid CAS object,
                    # but this changing source cannot establish an exact binding.
                    raise ValueError('Shared source changed during first freeze')
                _frozen(target,sha256,size_bytes,context)
    return target


def publish_bytes(root, payload, *, context=None, policy=None):
    """Publish new bytes in CAS first, so repeated projections never copy again."""
    root = _path(root)
    if type(payload) is not bytes:
        raise ValueError('Managed publication requires bytes')
    context = context or storage.Deadline(65)
    sha256 = hashlib.sha256(payload).hexdigest()
    target = _path(root / 'host-history/objects' / sha256)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits,context=context).file(len(payload))
    with storage.writer_lock(root,context=context):
        if target.exists():
            _frozen(target,sha256,len(payload),context)
        else:
            with storage.mutation(root,len(payload),policy=limits,context=context,target=target):
                storage.check(context)
                atomic_files.publish_bytes(target,payload)
                _frozen(target,sha256,len(payload),context)
    return target


def link(original, destination, *, sha256, size_bytes, root, context=None, policy=None):
    root, original, destination = _path(root), _path(original), _path(destination)
    _identity(sha256, size_bytes)
    if not original.is_relative_to(root) or not destination.is_relative_to(root):
        raise ValueError('Managed alias escaped its explicit instance root')
    context = context or storage.Deadline(65)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits,context=context).file(size_bytes)
    with storage.writer_lock(root,context=context), storage.mutation(root,0,policy=limits,context=context):
        before = _frozen(original,sha256,size_bytes,context)
        def existing():
            generation = _frozen(destination,sha256,size_bytes,context)
            if generation[:2] != before[:2]:
                raise ValueError('Managed alias collision: expected the exact shared inode')
        if destination.exists():
            existing()
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        storage.check(context)
        created = False
        try:
            try:
                _hardlink(original,destination)
                created = True
            except FileExistsError:
                existing()
            if created:
                after = _generation(destination)
                if after[:-1]!=before[:-1] or _generation(original)[:-1]!=after[:-1]:
                    raise ValueError('Shared source changed during alias publication')
                _remember(after,sha256)
            atomic_files._sync_directory(destination.parent)
            final = _frozen(original,sha256,size_bytes,context)
            if final[:-1] != before[:-1] or _generation(destination)[:-1] != final[:-1]:
                raise ValueError('Shared source changed during alias publication')
            _remember(final,sha256)
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
                atomic_files._sync_directory(destination.parent)
            raise
