"""Read saved instance documents for the UI without requalifying their contents.

These reads are presentation only. Execution and scientific qualification keep
using their existing readers; viewing a saved record does not grant authority.
"""
import json
import os
from pathlib import Path, PurePosixPath
import shutil


class Original:
    """Stream the selected saved file without hashing or identity checks."""

    def __init__(self, path):
        self.stream = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            self.stream = os.fdopen(descriptor, 'rb')
            self.size = self.stream.seek(0, os.SEEK_END)
            self.stream.seek(0)
        except OSError:
            self.close()
            raise ValueError('Saved scientific original is missing or inaccessible') from None

    def check(self):
        """Compatibility with the download handler; display does not reverify."""

    def copy_to(self, output):
        shutil.copyfileobj(self.stream, output, length=1024 * 1024)

    def close(self):
        if self.stream is not None:
            self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _root(root):
    if root is None or not str(root):
        raise ValueError('An explicit instance root is required')
    return Path(os.path.realpath(root))


def _path(root, relative):
    """Restrict a selected saved location; this does not inspect its contents."""
    if not isinstance(relative, str):
        raise ValueError('Saved document escaped its instance location')
    logical = PurePosixPath(relative)
    if (logical.is_absolute() or '\\' in relative
            or '..' in logical.parts or logical.as_posix() != relative):
        raise ValueError('Saved document escaped its instance location')
    path = root / relative
    if Path(os.path.realpath(path)) != path:
        raise ValueError('Saved document escaped its instance location')
    return path


def _json(path):
    try:
        with path.open(encoding='utf-8') as source:
            value = json.load(source)
    except OSError:
        raise ValueError('Saved instance document is missing or inaccessible') from None
    if not isinstance(value, dict):
        raise ValueError('Saved instance document is not an object')
    return value


def settings(root):
    root = _root(root)
    return _json(_path(root, 'settings.json'))


def _inherited(root, step):
    from . import fork_runtime, originals
    if fork_runtime.is_fork(root) and not (Path(root) / 'jobs' / f'{step:03d}.json').exists():
        return originals.node(root, step)
    return None


def _saved(root, step):
    root = _root(root)
    if type(step) is not int or step < 1:
        raise ValueError('Exact saved scientific node required')
    source = _inherited(root, step)
    if source is not None:
        from . import originals
        seal = _json(originals.source_record(source, 'result-identity.json'))
        return root, source, seal, originals.source_raw(source)
    record = f'native-evidence/{step:03d}'
    seal = _json(_path(root, record + '/result-identity.json'))
    reference = seal.get('execution_ref')
    attempt = reference.get('attempt', 0) if isinstance(reference, dict) else 0
    if type(attempt) is not int or attempt < 0:
        raise ValueError('Saved scientific attempt is malformed')
    raw_record = record + f'/continuations/{attempt:03d}' if attempt else record
    raw = _path(root, raw_record + '/science-output.json')
    return root, record, seal, raw


def node(root, step):
    from .contracts import read_scientific_output
    root, record, seal, raw = _saved(root, step)
    try:
        payload = read_scientific_output(raw)
    except OSError:
        raise ValueError('Saved instance document is missing or inaccessible') from None
    if not isinstance(record, str):
        from . import originals
        return dict(job=originals.source_job(record), payload=payload, seal=seal,
                    result=_json(originals.source_record(record, 'result.json')))
    return dict(job=_json(_path(root, f'jobs/{step:03d}.json')),
                payload=payload, seal=seal,
                result=_json(_path(root, record + '/result.json')))


def observation(root, step, node=None):
    root = _root(root)
    if type(step) is not int or step < 1:
        raise ValueError('Exact saved scientific node required')
    seal = node['seal'] if node is not None else _saved(root, step)[2]
    receipt = seal.get('observations')
    if not receipt:
        return None
    relative = f'host-history/observations/{step:03d}.json'
    if receipt.get('path',) != relative:
        raise ValueError('Saved observation escaped its node')
    source = _inherited(root, step)
    if source is not None:
        from . import originals
        return _json(originals.resolve_ref(source, receipt))
    return _json(_path(root, relative))


def science(root, step):
    return Original(_saved(root, step)[3])


def _label(material, step):
    prefix = f'inputs/parents/{step:03d}/artifacts/'
    logical = material.get('path',)
    if (not isinstance(logical, str) or not logical.startswith(prefix) or '\\' in logical
            or '..' in PurePosixPath(logical).parts or PurePosixPath(logical).as_posix() != logical
            or not logical.removeprefix(prefix)):
        raise ValueError('Saved deliverable has no exact node-owned name')
    return logical.removeprefix(prefix)


def materials(root, step, *, after=0, limit=20, directory=None):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Exact scientific deliverable continuation required')
    if directory is not None:
        from .instance_science import _directory, _directory_items
        directory = _directory(directory)
    rows = _saved(root, step)[2]['materials']
    if directory is None:
        page = [dict(index=number + 1, name=_label(row, step), size_bytes=row['size_bytes'])
                for number, row in enumerate(rows[after:after + limit], after)]
        total = len(rows)
    else:
        entries = _directory_items(rows, step, directory)
        page = entries[after:after + limit]
        total = len(entries)
    last = after + len(page)
    result = dict(step=step, items=page, total=total, after=after,
                next_after=last if last < total else None,
                scope='Saved scientific deliverables; viewing does not grant scientific qualification or execution permission.')
    if directory is not None:
        result.update(directory=directory, total_files=len(rows))
    return result


def _object(root, relative):
    """A retained object's name is a saved archive address, not a new digest."""
    if not isinstance(relative, str):
        raise ValueError('Saved deliverable escaped its output owner')
    path = PurePosixPath(relative)
    if (path.is_absolute() or '..' in path.parts or path.as_posix()!=relative
            or not ((path.parent==PurePosixPath('host-history/objects') and path.name)
                or (path.parts[:2]==('project','nodes') and 'artifacts' in path.parts)
                or (path.parts[:2]==('host-runtime','experiment-control') and 'outputs' in path.parts))):
        raise ValueError('Saved deliverable escaped its output owner')
    return _path(root, relative)


def artifact(root, step, index):
    root, _, seal, _ = _saved(root, step)
    rows = seal['materials']
    if type(index) is not int or not 1 <= index <= len(rows):
        raise ValueError('Exact saved scientific deliverable index required')
    material = rows[index - 1]
    name = _label(material, step)
    if sum(row['path'] == material['path'] for row in rows) != 1:
        raise ValueError('Saved scientific deliverable name is ambiguous')
    inherited = _inherited(root, step)
    if inherited is not None:
        from . import originals
        source = Path(material['source'])
        original_root = Path(inherited.original_root)
        if not source.is_absolute() or not source.is_relative_to(original_root):
            raise ValueError('Saved deliverable escaped its original output owner')
        reference = dict(material, path=source.relative_to(original_root).as_posix())
        return Original(originals.resolve_ref(inherited, reference)), name
    source = Path(material['source'])
    if (not source.is_absolute() or not source.is_relative_to(root)
            or '..' in source.parts or '\\' in str(source)):
        raise ValueError('Saved deliverable escaped its output owner')
    relative = source.relative_to(root).as_posix()
    work = root / 'project/nodes' / f'{step:03d}'
    if source == work / 'artifacts' / name or name.startswith('experiments/') and source == work / name:
        return Original(_path(root, relative)), name
    if source.parent == root / 'host-history/objects':
        return Original(_object(root, relative)), name
    # Older saved members retain the former managed-output source location.
    # Its saved observation maps that exact member to an existing archive object.
    saved_observation = observation(root, step, node=dict(seal=seal))
    for call in (saved_observation or {}).get('managed_calls', []):
        for output in call.get('outputs', []):
            if (name == f'experiments/{call["call_id"]}/artifacts/{output["name"]}'
                    and call['scope']['step'] == step and output.get('retention') == 'archived'
                    and output.get('path',) == relative):
                return Original(_object(root, output['snapshot'])), name
    raise ValueError('Saved deliverable escaped its output owner')
