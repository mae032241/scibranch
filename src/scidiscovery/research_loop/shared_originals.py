"""Publish immutable original slots; logical provenance belongs to each caller."""
import errno
import os
from pathlib import Path
import stat
import uuid

from . import atomic_files, storage_policy as storage

_SHARING_UNSUPPORTED = {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL,
                        errno.ENOSYS, errno.EPERM, errno.EMLINK}


def _generation(path):
    return atomic_files._generation(path.lstat())


def _path(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve()!=path or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Managed original requires an exact path without links')
    return path


def _file(path, size_bytes, *, frozen=False):
    if type(size_bytes) is not int or size_bytes < 0:
        raise ValueError('Managed file size must be a nonnegative integer')
    value = _generation(path)
    if not stat.S_ISREG(value[2]) or value[3] != size_bytes:
        raise ValueError('Managed original differs from its declared file')
    if frozen and stat.S_IMODE(value[2]) != 0o400:
        raise ValueError('Only a frozen host original may share its inode')
    return value


def _hardlink(source, target):
    from ..archive_io import SharingUnsupported
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError as error:
        if error.errno in _SHARING_UNSUPPORTED:
            raise SharingUnsupported('Managed originals require same-filesystem hardlink support') from error
        raise


def retain(root, source, *, size_bytes, source_kind, context=None, policy=None):
    root, source = _path(root), _path(source)
    if source_kind not in {'mutable', 'managed_frozen'}:
        raise ValueError('Explicit mutable or managed_frozen source kind required')
    context = context or storage.Deadline(65)
    storage.check(context)
    if source_kind == 'managed_frozen':
        if not source.is_relative_to(root):
            raise ValueError('Managed frozen source escaped its instance root')
        _file(source, size_bytes, frozen=True)
        return source
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits, context=context).file(size_bytes)
    target = _path(root / 'host-history/objects' / uuid.uuid4().hex)
    with storage.writer_lock(root, context=context):
        _file(source, size_bytes)
        with storage.mutation(root,size_bytes,policy=limits,context=context,target=target):
            atomic_files.copy_original(source,target,size_bytes=size_bytes,context=context)
    return target


def publish_bytes(root, payload, *, context=None, policy=None):
    root = _path(root)
    if type(payload) is not bytes:
        raise ValueError('Managed publication requires bytes')
    context = context or storage.Deadline(65)
    target = _path(root / 'host-history/objects' / uuid.uuid4().hex)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits,context=context).file(len(payload))
    with storage.writer_lock(root,context=context):
        with storage.mutation(root,len(payload),policy=limits,context=context,target=target):
            storage.check(context)
            atomic_files.publish_bytes(target,payload)
    return target


def publish_stream(root, blocks_factory, *, size_bytes=None, context=None, policy=None):
    root = _path(root)
    context = context or storage.Deadline(65)
    target = _path(root / 'host-history/objects' / uuid.uuid4().hex)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    inventory = storage.Inventory(limits,context=context)
    inventory.limit('max_files',1)
    # Only an explicitly enabled instance byte quota needs advance measurement.
    # Default publication transforms and writes the stream in one pass.
    if size_bytes is None and limits['enabled'] and limits['max_instance_bytes'] is not None:
        size_bytes=0
        for block in blocks_factory():
            storage.check(context)
            if type(block) is not bytes:raise ValueError('Atomic stream requires bytes blocks')
            size_bytes+=len(block)
            inventory.limit('max_file_bytes',size_bytes)
            inventory.limit('max_total_bytes',size_bytes)
    with storage.writer_lock(root,context=context):
        with storage.mutation(root,size_bytes or 0,policy=limits,context=context,target=target):
            atomic_files.publish_stream(target,blocks_factory(),size_bytes=size_bytes,
                context=context,inventory=inventory)
    return target


def link(original, destination, *, size_bytes, root, context=None, policy=None):
    root, original, destination = _path(root), _path(original), _path(destination)
    if not original.is_relative_to(root) or not destination.is_relative_to(root):
        raise ValueError('Managed alias escaped its explicit instance root')
    context = context or storage.Deadline(65)
    limits = storage.for_root(root) if policy is None else storage.configuration(policy)
    storage.Inventory(limits,context=context).file(size_bytes)
    with storage.writer_lock(root,context=context):
        before = _file(original,size_bytes,frozen=True)
        def existing():
            if _file(destination,size_bytes,frozen=True)[:2] != before[:2]:
                raise ValueError('Managed alias collision: expected the exact shared inode')
        if destination.exists() or destination.is_symlink():
            existing()
            return
        destination.parent.mkdir(parents=True,exist_ok=True)
        storage.check(context)
        created = False
        try:
            try:
                _hardlink(original,destination)
                created = True
            except FileExistsError:
                existing()
            if _generation(original)!=before or _generation(destination)!=before:
                raise ValueError('Shared source changed during alias publication')
            atomic_files._sync_directory(destination.parent)
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
                atomic_files._sync_directory(destination.parent)
            raise
