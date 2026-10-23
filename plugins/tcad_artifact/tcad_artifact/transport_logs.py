"""Complete durable diagnostics with opaque per-publication locators."""
import os
from pathlib import Path
import uuid


def preserve_log(root: Path, stream: str, raw: bytes) -> Path:
    if stream not in {'stdout','stderr','ssh-stdout','ssh-stderr'}:
        raise ValueError('Exact transport stream required')
    directory=root/'logs'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if root.is_symlink() or directory.is_symlink():
        raise ValueError('Transport log directory must not be a symlink')
    path=directory/(uuid.uuid4().hex+'.'+stream+'.log')
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as target:
        target.write(raw);target.flush();os.fsync(target.fileno())
    return path
