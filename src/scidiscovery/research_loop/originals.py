"""Exact local and inherited originals; storage location is not authorship.

This module only reads retained bytes. It grants no qualification, authority,
budget, recovery, or native-session continuation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from scidiscovery import archive_io as files

FORK_DIRECTORY = 'host-runtime/fork'
MANIFEST = FORK_DIRECTORY + '/manifest.json'


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
            or str(PurePosixPath(value)) != value or value == '.'):
        raise ValueError('Exact canonical relative original path required')
    return value


def identity_key(identity):
    if (not isinstance(identity, dict) or set(identity) != {'goal_id', 'settings_sha256'}
            or not isinstance(identity['goal_id'], str) or not identity['goal_id']
            or not re.fullmatch('[0-9a-f]{64}', identity['settings_sha256'])):
        raise ValueError('Exact original instance identity required')
    return hashlib.sha256(files.canonical(identity)).hexdigest()


def _json(path):
    path = files.check_path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Original control JSON exceeds its finite parse bound')
    return json.loads(path.read_bytes())


def local_identity(root):
    from . import contracts as c
    root = files.check_path(Path(root).absolute())
    settings = c.settings(root)
    return dict(goal_id=settings['goal_id'], settings_sha256=files.hash_file(root / 'settings.json')['sha256'])


def _manifest(root):
    root = files.check_path(Path(root).absolute())
    path = root / MANIFEST
    if not path.exists():
        return None
    value = _json(path)
    if value.get('version') != 1 or value.get('kind') != 'scientific_fork':
        raise ValueError('Unsupported fork source manifest')
    return value


@dataclass(frozen=True)
class SourceSnapshot:
    entries: tuple
    index: dict
    origins: dict
    native_records: dict
    legacy_compilations: dict


def _snapshot(manifest):
    index = _entry_index(manifest['entries'])
    entries = tuple(row for row in manifest['entries'] if row.get('visibility') != 'private_snapshot')
    return SourceSnapshot(entries, index, manifest['origins'], manifest.get('native_records', {}),
        manifest.get('legacy_compilations', {}))


def _entry_index(entries):
    index = {}
    seen = set()
    for row in entries:
        key = (identity_key(row['origin_identity']), relative(row['path']))
        versions = index.setdefault(key, [])
        exact = (*key, row['sha256'], row['size_bytes'])
        if exact in seen:
            raise ValueError('Repeated original identity in source binding')
        seen.add(exact)
        versions.append(row)
    return {key: tuple(rows) for key, rows in index.items()}


@dataclass(frozen=True)
class NodeSource:
    origin_identity: dict
    step: int
    original_root: str
    storage_root: Path
    entries: tuple[dict, ...] | None = None
    native_record: str | None = None
    snapshot: SourceSnapshot | None = field(default=None, repr=False, compare=False)
    _index: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self):
        if self.snapshot is not None:
            object.__setattr__(self, 'entries', self.snapshot.entries)
            object.__setattr__(self, '_index', self.snapshot.index)
            return
        if self.entries is not None:
            object.__setattr__(self, '_index', _entry_index(self.entries))

    @property
    def inherited(self):
        return self.entries is not None

    @property
    def logical_workspace(self):
        return Path(self.original_root) / 'project/nodes' / f'{self.step:03d}'


def _source(root, manifest, identity, step, *, snapshot=None):
    snapshot = _snapshot(manifest) if snapshot is None else snapshot
    return _snapshot_source(Path(root) / FORK_DIRECTORY, snapshot, identity, step)


def _snapshot_source(storage_root, snapshot, identity, step):
    key = identity_key(identity)
    origin = snapshot.origins.get(key)
    if origin is None or origin.get('identity') != identity:
        raise ValueError('Fork original has no exact origin binding')
    return NodeSource(identity, step, origin['original_root'], storage_root,
        snapshot.entries, snapshot.native_records.get(f'{key}:{step}'), snapshot)


def node(root, step):
    if type(step) is not int or step < 1:
        raise ValueError('Exact positive logical step required')
    root = files.check_path(Path(root).absolute())
    local = root / 'jobs' / f'{step:03d}.json'
    if local.exists():
        return NodeSource(local_identity(root), step, str(root), root)
    manifest = _manifest(root)
    matches = ([] if manifest is None else [row for row in manifest['prefix_entries'] if row['step'] == step])
    if len(matches) != 1:
        raise ValueError('Scientific node has no unique visible original')
    return _source(root, manifest, matches[0]['origin_identity'], step)


def selected_source(root):
    manifest = _manifest(root)
    if manifest is None:
        raise ValueError('Instance has no frozen fork source')
    value = manifest['source']
    return _source(root, manifest, value['origin_identity'], value['step'])


def for_origin(root, identity, *, step=1):
    root = Path(root).absolute()
    if (root / 'settings.json').exists() and identity == local_identity(root):
        return NodeSource(identity, step, str(root), root)
    manifest = _manifest(root)
    if manifest is None:
        raise ValueError('Original instance is not bound in this fork')
    return _source(root, manifest, identity, step)


def inherited(root, step):
    manifest = _manifest(root)
    return (manifest is not None and any(row['step'] == step for row in manifest['prefix_entries'])
        and not (Path(root) / 'jobs' / f'{step:03d}.json').exists())


def dependency(source, step, *, seal_sha256=None):
    if source.entries is None:
        return node(source.storage_root, step)
    path = f'native-evidence/{step:03d}/result-identity.json'
    matches = [row for row in source.entries if row['path'] == path
        and (seal_sha256 is None or row['sha256'] == seal_sha256)]
    same = [row for row in matches if row['origin_identity'] == source.origin_identity]
    if same:
        matches = same
    if len(matches) != 1:
        raise ValueError('Historical dependency has no unique exact original')
    return _snapshot_source(source.storage_root, source.snapshot, matches[0]['origin_identity'], step)


def source_for_ref(source, ref):
    identity = ref.get('origin_identity', source.origin_identity)
    if identity == source.origin_identity:
        return source
    if source.entries is None:
        return for_origin(source.storage_root, identity, step=source.step)
    return _snapshot_source(source.storage_root, source.snapshot, identity, source.step)


def at_step(source, step):
    if source.inherited:
        return _snapshot_source(source.storage_root, source.snapshot, source.origin_identity, step)
    return NodeSource(source.origin_identity, step, source.original_root, source.storage_root)


def legacy_compilation(source):
    """The frozen complete legacy compiler namespace; never infer missing facts."""
    if source.snapshot is None:
        raise ValueError('Legacy compilation inventory is only retained for inherited originals')
    key = f'{identity_key(source.origin_identity)}:{source.step}'
    value = source.snapshot.legacy_compilations.get(key)
    if value is None:
        raise ValueError('Original legacy compiler inventory is not retained')
    if set(value) != {'compiler_paths', 'author_successor', 'output_upgrade'}:
        raise ValueError('Original legacy compiler inventory is malformed')
    paths = value['compiler_paths']
    if not isinstance(paths, list) or paths != sorted(set(paths)):
        raise ValueError('Original legacy compiler index is ambiguous')
    base = f'native-evidence/{source.step:03d}/compiled-implementations/'
    for path in paths:
        if not path.startswith(base) or len(PurePosixPath(path).parts) != 5 or not path.endswith('/compiler.json'):
            raise ValueError('Original legacy compiler escaped its namespace')
        resolve_ref(source, dict(path=path))
    for name, suffix in [('author_successor', 'successor'), ('output_upgrade', 'output-upgrade')]:
        ref = value[name]
        if ref is not None:
            if ref.get('path') != f'host-recovery/author-closeout/{source.step:03d}-{suffix}.json':
                raise ValueError('Original legacy author chain escaped its namespace')
            resolve_ref(source_for_ref(source, ref), ref)
    return value


def resolve_root_ref(root, ref):
    if 'origin_identity' in ref:
        return resolve_ref(for_origin(root, ref['origin_identity']), ref)
    path = relative(ref['path'])
    actual = Path(root) / path
    if actual.exists() or _manifest(root) is None:
        proof = files.hash_file(actual)
        if any(field in ref and proof[field] != ref[field] for field in ('sha256', 'size_bytes')):
            raise ValueError('Original bytes changed: ' + path)
        return actual
    parts = PurePosixPath(path).parts
    step = None
    if len(parts) >= 2 and parts[0] in {'native-evidence', 'jobs'}:
        candidate = parts[1].split('.')[0]
        if candidate.isdecimal():
            step = int(candidate)
    elif len(parts) >= 3 and parts[:2] == ('project', 'nodes') and parts[2].isdecimal():
        step = int(parts[2])
    if step is not None:
        return resolve_ref(node(root, step), ref)
    manifest = _manifest(root)
    matches = [row for row in manifest['entries'] if row['path'] == path
        and all(field not in ref or row[field] == ref[field] for field in ('sha256', 'size_bytes'))]
    if len(matches) != 1:
        raise ValueError('Original reference requires an explicit origin identity')
    return resolve_ref(for_origin(root, matches[0]['origin_identity']), matches[0])


def material_path(root, material):
    """Resolve storage while retaining the material's original source string."""
    source = Path(material['source'])
    if not source.is_absolute():
        return resolve_root_ref(root, dict(path=relative(material['source']),
            **{field: material[field] for field in ('sha256', 'size_bytes') if field in material}))
    root = Path(root).absolute()
    if source.is_relative_to(root):
        return resolve_root_ref(root, dict(path=source.relative_to(root).as_posix(),
            **{field: material[field] for field in ('sha256', 'size_bytes') if field in material}))
    manifest = _manifest(root)
    if manifest is None:
        return source
    matches = []
    for origin in manifest['origins'].values():
        logical = Path(origin['original_root'])
        if source.is_relative_to(logical):
            relative_path = source.relative_to(logical).as_posix()
            entries = [row for row in manifest['entries'] if row['origin_identity'] == origin['identity']
                and row['path'] == relative_path and row['sha256'] == material['sha256']]
            matches.extend(entries)
    if len(matches) != 1:
        raise ValueError('Scientific material has no unique exact inherited origin')
    return resolve_ref(for_origin(root, matches[0]['origin_identity']), matches[0])


def material_owner(source, material):
    logical = Path(material['source'])
    if not logical.is_absolute():
        return source, relative(material['source'])
    own_root = Path(source.original_root)
    if logical.is_relative_to(own_root):
        return source, logical.relative_to(own_root).as_posix()
    root = source.storage_root.parents[1] if source.inherited else source.storage_root
    manifest = _manifest(root)
    if manifest is None:
        raise ValueError('Material is outside its original instance')
    matches = []
    for origin in manifest['origins'].values():
        logical_root = Path(origin['original_root'])
        if logical.is_relative_to(logical_root):
            path = logical.relative_to(logical_root).as_posix()
            matches.extend(row for row in manifest['entries'] if row['origin_identity'] == origin['identity']
                and row['path'] == path and row['sha256'] == material['sha256'])
    if len(matches) != 1:
        raise ValueError('Material has no unique exact original origin')
    return for_origin(root, matches[0]['origin_identity'], step=source.step), matches[0]['path']


def has_original(source, path):
    """Host-side existence in retained storage, independent of science indexing.

    This checks current retained presence; it does not attest historical absence
    or add a private original to a task's scientific input bindings.
    """
    path = relative(path)
    if source.inherited:
        if not source._index.get((identity_key(source.origin_identity), path)):
            return False
    elif not (source.storage_root / path).exists():
        return False
    resolve_ref(source, dict(path=path))
    return True


def resolve_ref(source, ref):
    if not isinstance(source, NodeSource) or not isinstance(ref, dict):
        raise ValueError('Exact original handle and reference required')
    origin = ref.get('origin_identity', source.origin_identity)
    key = identity_key(origin)
    path = relative(ref['path'])
    if origin != source.origin_identity:
        raise ValueError('Cross-origin reference requires its own original handle')
    expected = dict(ref)
    if source.entries is None:
        actual = files.check_path(source.storage_root / path)
    else:
        versions = source._index.get((key, path), ())
        matches = [row for row in versions if all(field not in ref or ref[field] == row[field]
            for field in ('sha256', 'size_bytes'))]
        if (not matches and isinstance(ref.get('sha256'), str)
                and re.fullmatch('[0-9a-f]{64}', ref['sha256'])
                and type(ref.get('size_bytes')) is int and ref['size_bytes'] >= 0):
            # Old frozen receipts can bind a cleaned/reused locator while the
            # exact bytes remain in the original owner's content archive. This
            # resolves that explicit identity; it never discovers a new source,
            # changes the logical receipt, or bypasses a damaged matching inode.
            archived = 'host-history/objects/' + ref['sha256']
            matches = [row for row in source._index.get((key, archived), ())
                if row['sha256'] == ref['sha256'] and row['size_bytes'] == ref['size_bytes']]
        if not matches and not versions:
            raise ValueError('Original is outside the frozen fork closure: ' + key + ':' + path)
        if len(matches) != 1:
            raise ValueError('Original reference has no unique exact frozen binding: ' + key + ':' + path)
        entry = matches[0]
        stored_path = relative(entry['path'])
        if entry['storage_path'] not in {f'inherited/{key}/{stored_path}',
                f'inherited/{key}/versions/{entry["sha256"]}/{entry["size_bytes"]}/{stored_path}'}:
            raise ValueError('Original storage binding changed')
        for field in ('sha256', 'size_bytes'):
            if field in ref and ref[field] != entry[field]:
                raise ValueError('Original reference differs from its frozen binding')
        expected = entry
        actual = files.check_path(source.storage_root / entry['storage_path'])
    from . import contracts as c
    files.check_path(actual)
    binding = None
    for attempt in range(3):
        generation = c._digest_generation(actual)
        if generation is None or binding is not None and generation[:-1] != binding:
            raise ValueError('Original changed while reading its binding')
        binding = generation[:-1]
        checked = dict(sha256=c.digest(c.regular(actual)), size_bytes=actual.stat().st_size)
        after = c._digest_generation(actual)
        if after != generation:
            if attempt < 2 and c._readonly_metadata_change(generation, after):
                continue
            raise ValueError('Original changed while reading its binding')
        for field in ('sha256', 'size_bytes'):
            if field in expected and checked[field] != expected[field]:
                raise ValueError('Original bytes changed: ' + key + ':' + path)
        return actual


def original_ref(source, path):
    path = relative(path)
    actual = resolve_ref(source, dict(path=path))
    proof = files.hash_file(actual)
    return dict(origin_identity=source.origin_identity, path=path,
        sha256=proof['sha256'], size_bytes=proof['size_bytes'])


def source_job(source):
    value = _json(resolve_ref(source, dict(path=f'jobs/{source.step:03d}.json')))
    if value.get('step') != source.step:
        raise ValueError('Original job changed its logical identity')
    return value


def source_settings(source):
    from . import contracts as c
    job = source_job(source)
    if source.entries is None:
        return c.settings(source.storage_root, job)
    path = resolve_ref(source, dict(path='settings.json', sha256=source.origin_identity['settings_sha256']))
    initial = _json(path)
    if initial.get('goal_id') != source.origin_identity['goal_id']:
        raise ValueError('Original settings lost its instance identity')
    revision = job.get('configuration_revision')
    if revision is None:
        return initial
    number = revision['revision']
    predecessor = dict(path='settings.json', sha256=source.origin_identity['settings_sha256'])
    selected = None
    for index in range(1, number + 1):
        relative_path = f'host-control/configuration/revisions/{index:06d}.json'
        proof = original_ref(source, relative_path)
        current = _json(resolve_ref(source, proof))
        identity = _json(resolve_ref(source, dict(path=f'host-control/configuration/identities/{index:06d}.json')))
        if (identity != dict(sha256=proof['sha256']) or current.get('revision') != index
                or current.get('predecessor') != predecessor):
            raise ValueError('Original configuration predecessor changed')
        predecessor = dict(path=relative_path, sha256=proof['sha256'])
        selected = current['settings']
    if revision != dict(revision=number, **predecessor):
        raise ValueError('Original job configuration binding changed')
    return selected


def source_record(source, name):
    name = relative(name)
    if '/' in name:
        raise ValueError('Native original name must be one declared member')
    record = (f'native-evidence/{source.step:03d}' if name in {
        'result-identity.json', 'result.json', 'native-authorship.json', 'input-provenance.json',
        'provenance.json', 'observation.json', 'formal-review.json'} else source.native_record)
    if record is None:
        if source.entries is None:
            from . import execution
            record = execution.record(source.storage_root, source.step).relative_to(source.storage_root).as_posix()
        else:
            record = f'native-evidence/{source.step:03d}'
    return resolve_ref(source, dict(path=record + '/' + name))


def source_raw(source):
    return source_record(source, 'science-output.json')


def source_sequence(root, before_step, *, source_cutoff=None, extra_steps=()):
    if type(before_step) is not int or before_step < 1:
        raise ValueError('Exact history boundary required')
    root = files.check_path(Path(root).absolute())
    manifest = _manifest(root)
    inherited = [] if manifest is None else manifest['prefix_entries']
    steps = {row['step'] for row in inherited if row['step'] < before_step}
    for path in (root / 'jobs').glob('*.json'):
        if path.stem.isdecimal() and int(path.stem) < before_step:
            step = int(path.stem)
            if (root / 'native-evidence' / f'{step:03d}' / 'result-identity.json').exists():
                steps.add(step)
    if source_cutoff is not None:
        if (type(source_cutoff) is not int or not 0 <= source_cutoff < before_step
                or list(extra_steps) != sorted(set(extra_steps))
                or any(type(step) is not int or not source_cutoff < step < before_step for step in extra_steps)):
            raise ValueError('Invalid exact scientific cutoff')
        steps = {step for step in steps if step <= source_cutoff} | set(extra_steps)
    elif extra_steps:
        raise ValueError('Extra nodes require an exact cutoff')
    snapshot = _snapshot(manifest) if manifest is not None else None
    inherited_by_step = {row['step']: row for row in inherited}
    identity = None
    result = []
    for step in sorted(steps):
        if (root / 'jobs' / f'{step:03d}.json').exists():
            if identity is None:
                identity = local_identity(root)
            result.append(NodeSource(identity, step, str(root), root))
        else:
            row = inherited_by_step.get(step)
            if row is None:
                raise ValueError('Exact scientific sequence member is missing')
            result.append(_source(root, manifest, row['origin_identity'], step, snapshot=snapshot))
    return result


def history_source(source, job):
    """Unchanged selected-task history retains its exact original author."""
    binding=job.get('history',{});owner=job;seen=set()
    while owner.get('fork_source') is not None:
        ref=owner['fork_source']['job']
        source=source_for_ref(source,ref)
        key=(identity_key(source.origin_identity),ref['sha256'])
        if key in seen:raise ValueError('Frozen history has a cyclic task origin')
        seen.add(key)
        owner=_json(resolve_ref(source,ref))
        if owner.get('history',{})!=binding:
            raise ValueError('Frozen history differs from its exact original task')
    return source


def recovery_refs(source, job):
    owner=history_source(source,job)
    refs=job.get('history',{}).get('execution_recoveries',[])
    return ([dict(ref,origin_identity=ref.get('origin_identity',owner.origin_identity)) for ref in refs]
        if owner.inherited else refs)


def frozen_recovery_refs(root, before_step):
    manifest = _manifest(root)
    rows = [] if manifest is None else list(manifest['cut'].get('execution_recoveries', []))
    for path in sorted((Path(root) / 'jobs').glob('*.json')):
        if path.stem.isdecimal() and int(path.stem) < before_step:
            for value in recovery_refs(node(root,int(path.stem)),_json(path)):
                if value not in rows:
                    rows.append(value)
    return rows
