"""Complete local log projections without retaining a whole log in memory."""
import os
from pathlib import Path

from . import atomic_files, storage_policy as storage

BLOCK_BYTES = 64 * 1024


def encode_replacements(replacements):
    """Keep the historical stable string-length order before filesystem encoding."""
    return tuple((os.fsencode(source), os.fsencode(destination)) for source, destination
                 in sorted(replacements.items(), key=lambda row: len(row[0]), reverse=True))


class _Replacement:
    def __init__(self, source, destination, block_size, context):
        if not source:
            raise ValueError('Scientific log replacement requires a nonempty source')
        self.source, self.destination = source, destination
        self.block_size, self.context = block_size, context
        self.carry = b''

    def _pieces(self, value):
        for offset in range(0, len(value), self.block_size):
            storage.check(self.context)
            yield value[offset:offset + self.block_size]

    def feed(self, block):
        value = self.carry + block
        offset = 0
        while True:
            storage.check(self.context)
            match = value.find(self.source, offset)
            if match < 0:
                break
            yield from self._pieces(value[offset:match])
            yield from self._pieces(self.destination)
            offset = match + len(self.source)
        end = max(offset, len(value) - len(self.source) + 1)
        yield from self._pieces(value[offset:end])
        self.carry = value[end:]

    def finish(self):
        yield from self._pieces(self.carry)
        self.carry = b''


def replace_blocks(blocks, replacements, *, context, block_size=BLOCK_BYTES):
    """Apply ordered bytes.replace stages, including cross-block cascades.

    The explicit stack avoids a Python call frame for each snapshot path.
    Each stage buffers only one block and its possible incomplete needle.
    """
    if type(block_size) is not int or block_size <= 0:
        raise ValueError('Positive scientific log block size required')
    stages = [_Replacement(source, destination, block_size, context)
              for source, destination in replacements]

    def drain(index, pieces):
        pending = [(index, iter(pieces))]
        while pending:
            storage.check(context)
            current, iterator = pending[-1]
            try:
                block = next(iterator)
            except StopIteration:
                pending.pop()
                continue
            if len(block) > block_size:
                raise ValueError('Scientific log input block exceeds its bound')
            if current == len(stages):
                yield block
            else:
                pending.append((current + 1, iter(stages[current].feed(block))))

    yield from drain(0, blocks)
    for index, stage in enumerate(stages):
        yield from drain(index + 1, stage.finish())


def _generation(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _raw_blocks(path, size_bytes, context, block_size):
    from .experiments import IntegrityError
    path = Path(path)
    size = 0
    storage.check(context)
    with atomic_files._regular_stream(path) as stream:
        before = _generation(os.fstat(stream.fileno()))
        if before[3] != size_bytes or _generation(path.lstat()) != before:
            raise IntegrityError('Scientific log original differs from its exact binding')
        while True:
            storage.check(context)
            block = stream.read(min(block_size, size_bytes - size + 1))
            if not block:
                break
            size += len(block)
            if size > size_bytes:
                raise IntegrityError('Scientific log original grew during projection')
            yield block
        storage.check(context)
        if size != size_bytes:
            raise IntegrityError('Scientific log original differs from its exact binding')
        if (_generation(os.fstat(stream.fileno())) != before
                or _generation(path.lstat()) != before):
            raise IntegrityError('Scientific log original changed during projection')


def project(path, *, size_bytes, replacements, context, block_size=BLOCK_BYTES):
    """A fresh iteration verifies the exact raw original before completing EOF."""
    return replace_blocks(_raw_blocks(path, size_bytes, context, block_size),
                          replacements, context=context, block_size=block_size)
