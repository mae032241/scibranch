"""Read saved instance documents for the UI without requalifying their contents.

These reads are presentation only. Execution and scientific qualification keep
using their existing readers; viewing a saved record does not grant authority.
"""
import json
import os
import re
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


def _navigation_file(root,job):
    """Derive one accepted publisher address from the frozen branch identity."""
    from . import accepted_prefix, originals
    if job.get('research_strategy_version')!=3:return None
    prefix=(job.get('history') or {}).get('prefix')
    if not isinstance(prefix,dict) or prefix.get('version')!=1:return None
    try:
        namespace=accepted_prefix._view_key(prefix)
        key=originals.identity_key(prefix['branch'])
        return _path(root,f'host-history/accepted-public/{namespace}/nodes/{key}/{job["step"]:03d}/index.json')
    except (ValueError,KeyError,TypeError):return None


def file_identity(path):
    """Metadata including absence, acquired by the UI record owner."""
    try:info=path.stat()
    except FileNotFoundError:return (str(path),None)
    return (str(path),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _node(root, step):
    from .contracts import read_scientific_output
    root, record, seal, raw = _saved(root, step)
    try:
        payload = read_scientific_output(raw)
    except OSError:
        raise ValueError('Saved instance document is missing or inaccessible') from None
    if not isinstance(record, str):
        from . import originals,candidate_refs
        raw_job=originals.source_job(record)
        # This acquisition owner resolves input identities once. Both typed
        # projections receive it explicitly and cannot lazily reopen sources.
        mapping=candidate_refs.source_input_mapping(record,raw_job)
        prefix=(raw_job.get('history') or {}).get('prefix')
        if isinstance(prefix,dict):
            from . import accepted_prefix
            choices=[]
            for authored in (payload,):
                choices.append(authored.get('candidate_recommendation'))
                nominated=authored.get('candidate_nominations') or {}
                if isinstance(nominated,dict):
                    for kind in ('representatives','extras'):
                        rows=nominated.get(kind)
                        if isinstance(rows,list):choices.extend(row.get('candidate') for row in rows if isinstance(row,dict))
            # Normalized output can retain a raw ID while the authored slot is
            # a public alias. Each exact reference is acquired once at this cut.
            saved_result=_json(originals.source_record(record,'result.json'))
            choices.append(saved_result.get('candidate_recommendation'))
            for choice in dict.fromkeys(value for value in choices if isinstance(value,str)):
                if choice in mapping:continue
                try:
                    selected=choice
                    if re.fullmatch(r'[HWAE]\d{3,}-I\d{3,}',selected):
                        selected=accepted_prefix.candidate_for_implementation(Path(record.original_root),prefix,selected)
                        if selected is None:continue
                    fact=accepted_prefix.candidate(Path(record.original_root),prefix,selected)
                    if fact.get('origin_identity') is not None:
                        mapping[choice]=candidate_refs.origin_candidate(fact['origin_identity'],fact['observation']['id'])
                except (ValueError,FileNotFoundError):pass
        else:saved_result=_json(originals.source_record(record,'result.json'))
        job=candidate_refs.source_job_view(record,raw_job,input_mapping=mapping)
        result=saved_result
        navigation=_navigation_file(Path(record.original_root),job)
        origin=record.origin_identity
        observed=None
        if seal.get('observations'):
            receipt=seal['observations']
            if receipt.get('path')!=f'host-history/observations/{step:03d}.json':
                raise ValueError('Saved observation escaped its node')
            raw_observation=_json(originals.resolve_ref(record,receipt))
            observed=candidate_refs.source_observation(record,raw_job,raw_observation,input_mapping=mapping)
            mapping=dict(mapping,**{row['id']:candidate_refs.origin_candidate(origin,row['id'])
                for row in raw_observation.get('candidates',[])})
        unresolved=False
        for authored in (payload,result):
            choice=authored.get('candidate_recommendation')
            if (isinstance(choice,str) and choice not in mapping
                    and not candidate_refs._MACHINE_CANDIDATE.fullmatch(choice)
                    and not candidate_refs._ORIGIN_CANDIDATE.fullmatch(choice)):
                mapping[choice]=None;unresolved=True
        payload=candidate_refs.public_science_fields(payload,mapping)
        result=candidate_refs.public_science_fields(result,mapping)
    else:
        job=_json(_path(root,f'jobs/{step:03d}.json'))
        result=_json(_path(root,record+'/result.json'))
        navigation=_navigation_file(root,job)
        origin=((job.get('history') or {}).get('prefix') or {}).get('branch')
    research=None;navigation_fingerprint=None
    if navigation is not None:
        try:before=file_identity(navigation)
        except OSError:before=None
        try:
            value=_json(navigation)
            if value.get('step')==step and value.get('role')==job['role'] and isinstance(value.get('research'),dict):
                research=value['research']
        except (OSError,ValueError):pass
        try:after=file_identity(navigation)
        except OSError:after=None
        if before is not None and before==after:navigation_fingerprint=before
    value=dict(job=job,payload=payload,seal=seal,result=result,research=research,origin_identity=origin,
        navigation_file=navigation,navigation_fingerprint=navigation_fingerprint)
    if not isinstance(record,str):
        value['_observation']=observed
        value['candidate_reference_unresolved']=unresolved
    return value


def node(root,step):
    return _node(root,step)


def observation(root, step, node=None):
    root = _root(root)
    if type(step) is not int or step < 1:
        raise ValueError('Exact saved scientific node required')
    if node is None:node=_node(root,step)
    if '_observation' in node:return node['_observation']
    seal = node['seal'] if node is not None else _saved(root, step)[2]
    receipt = seal.get('observations')
    if not receipt:
        return None
    relative = f'host-history/observations/{step:03d}.json'
    if receipt.get('path',) != relative:
        raise ValueError('Saved observation escaped its node')
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
    # The saved observation binds a managed member to its exact call output.
    # Current retained outputs stay inside that node's original call directory.
    saved_observation = observation(root, step, node=dict(seal=seal))
    for call in (saved_observation or {}).get('managed_calls', []):
        for output in call.get('outputs', []):
            if (name == f'experiments/{call["call_id"]}/artifacts/{output["name"]}'
                    and call['scope']['step'] == step and output.get('retention') == 'archived'
                    and output.get('path',) == relative):
                call_id = call['call_id']
                if (isinstance(call_id, str) and call_id not in {'', '.', '..'}
                        and PurePosixPath(call_id).name == call_id and '\\' not in call_id
                        and isinstance(output['name'], str)
                        and source == work / 'work/retained-experiments' / call_id / 'outputs' / output['name']
                        and output.get('snapshot') == relative):
                    return Original(_path(root, relative)), name
                return Original(_object(root, output['snapshot'])), name
    raise ValueError('Saved deliverable escaped its output owner')
