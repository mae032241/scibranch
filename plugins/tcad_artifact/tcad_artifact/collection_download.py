"""Extract a bounded result archive against the exact frozen file manifest."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tarfile
import tempfile
import time

HEADER_LIMIT = 1024 * 1024


def unpack_results(archive_path, items, records, *, context, on_complete):
    # Never extractall: only predetermined local paths may be written, and only
    # after the corresponding regular member has matched its frozen name and size.
    with tarfile.open(archive_path, mode="r|") as archive:
        manifest = archive.next()
        if (manifest is None or manifest.name != "manifest.json" or not manifest.isreg()
                or manifest.size > HEADER_LIMIT):
            raise RuntimeError("result archive manifest is missing or invalid")
        with archive.extractfile(manifest) as source:
            if json.load(source) != records:
                raise RuntimeError("result archive manifest differs from request")
        for index, (descriptor, path) in enumerate(items):
            started = time.monotonic()
            def check():
                if context:
                    context.remaining_seconds()
                    if time.monotonic() - started >= context.file_timeout_seconds:
                        error = TimeoutError("result extraction exceeded per-file budget")
                        error.timeout_kind = "file_total"
                        raise error
            check()
            member = archive.next()
            if (member is None or member.name != "files/%d" % index or not member.isreg()
                    or member.size != descriptor.size_bytes):
                raise RuntimeError("result archive member differs from manifest")
            fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".download", dir=path.parent)
            temporary = Path(temporary)
            try:
                size = 0
                with os.fdopen(fd, "wb") as target, archive.extractfile(member) as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        check()
                        target.write(block)
                        size += len(block)
                    if size != descriptor.size_bytes:
                        raise RuntimeError("downloaded result differs from remote descriptor")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, path)
                on_complete(descriptor, path)
            finally:
                temporary.unlink(missing_ok=True)
        if archive.next() is not None:
            raise RuntimeError("result archive contains undeclared members")
