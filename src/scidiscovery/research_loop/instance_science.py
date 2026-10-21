"""Only sealed scientific output and explicitly sealed deliverables, without advancement."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

from scidiscovery import archive_io
from . import budget, contracts as c, execution


class Original:
    """Verify and serve the same open regular file; never reopen a caller path."""
    def __init__(self, path, proof=None):
        self.stream = None
        try:
            if proof is not None and (type(proof.get('size_bytes')) is not int or proof['size_bytes']<0
                    or not isinstance(proof.get('sha256'),str) or len(proof['sha256'])!=64
                    or any(value not in '0123456789abcdef' for value in proof['sha256'])):
                raise ValueError('Scientific original proof is malformed')
            archive_io.check_path(path)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            self.stream = os.fdopen(descriptor, 'rb')
            before = os.fstat(self.stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('Scientific original must be a regular file')
            self.identity = self._identity(before)
            self.size = before.st_size
            if proof is not None and self.size != proof['size_bytes']:
                raise ValueError('Sealed scientific original size changed')
            digest = hashlib.sha256()
            for block in iter(lambda:self.stream.read(1024*1024), b''):
                digest.update(block)
            self.sha256 = digest.hexdigest()
            self.check()
            if proof is not None and self.sha256 != proof['sha256']:
                raise ValueError('Sealed scientific original bytes changed')
            self.stream.seek(0)
        except OSError:
            self.close()
            raise ValueError('Sealed scientific original is missing or inaccessible') from None
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def check(self):
        if self._identity(os.fstat(self.stream.fileno())) != self.identity:
            raise ValueError('Scientific original changed during reading')

    def copy_to(self, output):
        digest = hashlib.sha256()
        for block in iter(lambda:self.stream.read(1024*1024), b''):
            self.check(); digest.update(block); output.write(block)
        self.check()
        if digest.hexdigest() != self.sha256:
            raise ValueError('Scientific original changed during transfer')

    def close(self):
        if self.stream is not None:
            self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _json(path, proof=None):
    with Original(path, proof) as original:
        value = json.load(original.stream)
        original.check()
        return value


def _proof(path, sha256):
    # Size is not separately recorded for these existing node JSON originals.
    return dict(sha256=sha256, size_bytes=c.regular(path).stat().st_size)


def _sealed(root, step):
    root = Path(root).resolve()
    from . import originals
    if type(step) is int and originals.inherited(root, step):
        source = originals.node(root, step)
        c.validate_original(source)
        seal = _json(originals.source_record(source, 'result-identity.json'))
        raw = originals.source_raw(source)
        return root, seal, raw, _proof(raw, seal['raw_sha256'])
    try:
        settings = c.settings(root)
        if type(step) is not int or not 1 <= step <= budget.historical_step_limit(root):
            raise ValueError('Exact sealed scientific node required')
        record = execution.origin(root, step)
        seal = _json(record/'result-identity.json')
        job_path = root/'jobs'/f'{step:03d}.json'
        job = _json(job_path, _proof(job_path, seal['job_sha256']))
        if job['step'] != step or job['role'] not in {'H','W','A','E'}:
            raise ValueError('Sealed scientific node identity changed')
        reference = execution.reference(root, step)
        if seal.get('execution_ref') != reference:
            raise ValueError('Sealed scientific attempt reference changed')
        # The explicit original attempt avoids unrelated continuation/budget validation.
        raw = execution.record(root, step, attempt=reference['attempt'] if reference else 0)/'science-output.json'
        with Original(record/'result.json', _proof(record/'result.json', seal['sha256'])):
            pass
        return root, seal, raw, _proof(raw, seal['raw_sha256'])
    except OSError:
        raise ValueError('Sealed scientific node is unavailable') from None


def science(root, step):
    _, _, raw, proof = _sealed(root, step)
    return Original(raw, proof)


def _label(material, step):
    prefix = f'inputs/parents/{step:03d}/artifacts/'
    logical = material['path']
    if (not isinstance(logical, str) or not logical.startswith(prefix) or '\\' in logical
            or '..' in PurePosixPath(logical).parts or PurePosixPath(logical).as_posix() != logical
            or not logical.removeprefix(prefix)):
        raise ValueError('Scientific deliverable has no exact node-owned name')
    return logical.removeprefix(prefix)


def materials(root, step, *, after=0, limit=20):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Exact scientific deliverable continuation required')
    _, seal, raw, proof = _sealed(root, step)
    with Original(raw, proof):
        pass
    rows = seal['materials']
    page = [dict(index=number+1, name=_label(row,step), size_bytes=row['size_bytes'])
            for number,row in enumerate(rows[after:after+limit], after)]
    last = after+len(page)
    return dict(step=step, items=page, total=len(rows), after=after, next_after=last if last<len(rows) else None,
        scope='Exact sealed scientific deliverables; byte identity is not scientific qualification or execution permission.')


def artifact(root, step, index):
    root, seal, raw, proof = _sealed(root, step)
    with Original(raw, proof):
        pass
    rows = seal['materials']
    if type(index) is not int or not 1 <= index <= len(rows):
        raise ValueError('Exact sealed scientific deliverable index required')
    material = rows[index-1]; name = _label(material,step)
    # The ordinal selects an existing seal member, never a caller-provided path.
    if sum(row['path']==material['path'] for row in rows)!=1:
        raise ValueError('Scientific deliverable name is ambiguous')
    from . import originals
    if originals.inherited(root, step):
        c.material_name(c.workspace(root, step), material)
        return Original(originals.material_path(root, material), material), name
    source = Path(material['source']); work = c.workspace(root,step)
    if not source.is_absolute() or not source.is_relative_to(root) or source.resolve()!=source:
        raise ValueError('Scientific deliverable source escaped its exact output owner')
    if source == work/'artifacts'/name or name.startswith('experiments/') and source == work/name:
        return Original(source,material), name
    # Managed results may have an existing retained original outside artifacts/.
    # Only the sealed output membership authorizes that exact existing object.
    receipt = seal.get('observations')
    if receipt is not None:
        if receipt['path'] != f'host-history/observations/{step:03d}.json':
            raise ValueError('Scientific output receipt escaped its sealed node')
        observation = _json(root/receipt['path'], _proof(root/receipt['path'], receipt['sha256']))
        if observation['binding']['source_sha256'] != seal['raw_sha256']:
            raise ValueError('Scientific output receipt differs from its sealed conclusion')
        for call in observation.get('managed_calls',[]):
            for output in call.get('outputs',[]):
                if (name == f'experiments/{call["call_id"]}/artifacts/{output["name"]}'
                        and call['scope']['step'] == step and call['scope']['job_sha256'] == seal['job_sha256']
                        and source == root/output['path'] and output.get('retention') == 'archived'
                        and all(output[key] == material[key] for key in ('sha256','size_bytes'))
                        and output['snapshot'] == 'host-history/objects/'+material['sha256']):
                    return Original(root/output['snapshot'],material), name
    raise ValueError('Scientific deliverable is outside its exact sealed output owner')
