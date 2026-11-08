"""Exact local and inherited originals; storage location is not authorship.

This module reads exact originals and transported controls. It grants no qualification, authority,
budget, recovery, or native-session continuation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path, PurePosixPath

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
    if (not isinstance(identity, dict) or set(identity) != {'goal_id', 'settings_ref'}
            or not isinstance(identity['settings_ref'], dict) or set(identity['settings_ref']) != {'path'}
            or not Path(identity['settings_ref']['path']).is_absolute()):
        raise ValueError('Exact original instance identity required')
    return files.safe_id(identity['goal_id'])

def _json(path):
    path = files.check_path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Original control JSON exceeds its finite parse bound')
    return json.loads(path.read_bytes())


def local_identity(root):
    from . import contracts as c
    root = files.check_path(Path(root).absolute())
    settings = c.settings(root)
    return dict(goal_id=settings['goal_id'], settings_ref=c.record_ref(root / 'settings.json'))


def _manifest(root):
    from . import handoff_state
    state=handoff_state.current(root)
    if state is not None and state.fork_manifest is not None:
        return state.fork_manifest
    root = files.check_path(Path(root).absolute())
    path = root / MANIFEST
    if not path.exists():
        return None
    value = _json(path)
    if value.get('version') != 2 or value.get('kind') != 'scientific_fork':
        raise ValueError('Unsupported fork source manifest')
    if state is not None:
        # Publication closes this binding for the controller scope. Absence is
        # never cached: a new fork can still publish its manifest in this scope.
        state.fork_manifest=value
        for row in value.get('prefix_entries',[]):
            state.fork_prefix.setdefault(row['step'],[]).append(row)
    return value


@dataclass(frozen=True)
class SourceSnapshot:
    entries: tuple
    index: dict
    origins: dict
    native_records: dict


def _snapshot(manifest):
    index = _entry_index(manifest['entries'])
    entries = tuple(row for row in manifest['entries'] if row.get('visibility') != 'private_snapshot')
    return SourceSnapshot(entries, index, manifest['origins'], manifest.get('native_records', {}))


def _entry_index(entries):
    index = {}
    seen = set()
    for row in entries:
        key = (identity_key(row['origin_identity']), relative(row['path']))
        versions = index.setdefault(key, [])
        exact = key
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


def context_root(source):
    """Return the consuming instance from its declared original-storage binding."""
    root=Path(source.storage_root)
    if source.inherited:
        for component in reversed(PurePosixPath(FORK_DIRECTORY).parts):
            if root.name!=component:
                raise ValueError('Inherited source escaped its declared fork storage namespace')
            root=root.parent
    return root


def _source_snapshot(root, manifest):
    from . import handoff_state
    state=handoff_state.current(root)
    if state is not None and state.fork_manifest is manifest:
        if state.fork_snapshot is None:
            state.fork_snapshot=_snapshot(manifest)
        return state.fork_snapshot
    return _snapshot(manifest)


def _prefix_rows(root, manifest, step):
    if manifest is None:return ()
    from . import handoff_state
    state=handoff_state.current(root)
    if state is not None and state.fork_manifest is manifest:
        return state.fork_prefix.get(step,())
    return [row for row in manifest['prefix_entries'] if row['step']==step]


def _source(root, manifest, identity, step, *, snapshot=None):
    snapshot = _source_snapshot(root,manifest) if snapshot is None else snapshot
    return _snapshot_source(Path(root) / FORK_DIRECTORY, snapshot, identity, step)


def _snapshot_source(storage_root, snapshot, identity, step):
    key = identity_key(identity)
    origin = snapshot.origins.get(key)
    if origin is None or origin.get('identity') != identity:
        raise ValueError('Fork original has no exact origin binding')
    return NodeSource(identity, step, origin['original_root'], storage_root,
        snapshot.entries, snapshot.native_records.get(f'{key}:{step}'), snapshot)


def node(root, step):
    from . import handoff_state
    state=handoff_state.current(root)
    if state is not None and step in state.handles:
        return state.handles[step]
    source=_node(root,step)
    if state is not None:state.handles[step]=source
    return source


def node_from_base(root, step, *, base):
    """Select a source using the operation owner's already accepted local identity."""
    from . import handoff_state
    root = files.check_path(Path(root).absolute())
    state = handoff_state.current(root)
    if state is not None and step in state.handles:
        return state.handles[step]
    if type(step) is not int or step < 1:
        raise ValueError('Exact positive logical step required')
    if (root / 'jobs' / f'{step:03d}.json').exists():
        source = NodeSource(dict(goal_id=base['goal_id'],
            settings_ref=dict(path=str(root / 'settings.json'))), step, str(root), root)
    else:
        source = _node(root, step)
    if state is not None:
        state.handles[step] = source
    return source


def _node(root, step):
    if type(step) is not int or step < 1:
        raise ValueError('Exact positive logical step required')
    root = files.check_path(Path(root).absolute())
    local = root / 'jobs' / f'{step:03d}.json'
    if local.exists():
        return NodeSource(local_identity(root), step, str(root), root)
    manifest = _manifest(root)
    matches = _prefix_rows(root,manifest,step)
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
    from . import handoff_state
    state=handoff_state.current(root)
    if state is not None and step in state.handles:
        return state.handles[step].inherited
    manifest = _manifest(root)
    return (bool(_prefix_rows(root,manifest,step))
        and not (Path(root) / 'jobs' / f'{step:03d}.json').exists())


def dependency(source, step, *, seal_ref=None):
    if source.entries is None:
        return node(source.storage_root, step)
    path = f'native-evidence/{step:03d}/result-identity.json'
    matches = []
    for row in source.entries:
        if row['path'] != path:
            continue
        if seal_ref is not None:
            identity = row['origin_identity']
            origin = source.snapshot.origins.get(identity_key(identity))
            if origin is None or origin.get('identity') != identity:
                raise ValueError('Historical dependency has no exact origin binding')
            if ('origin_identity' in seal_ref and seal_ref['origin_identity'] != identity
                    or seal_ref.get('path') not in {path, str(Path(origin['original_root']) / path)}
                    or 'size_bytes' in seal_ref and seal_ref['size_bytes'] != row['size_bytes']):
                continue
        matches.append(row)
    if seal_ref is None or 'origin_identity' not in seal_ref:
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


def _check_identity(path, ref, *, context=None):
    """Compare a saved ordinary identity without upgrading legacy references."""
    saved = ref.get('file_stat')
    if saved is not None and (type(saved) is not list or len(saved) != 4
            or any(type(value) is not int for value in saved)
            or saved[2] != ref.get('size_bytes')):
        raise ValueError('Original file fingerprint is malformed')
    actual = files.stat_file(path, context=context)
    if 'size_bytes' in ref and actual['size_bytes'] != ref['size_bytes']:
        raise ValueError('Original size changed: ' + str(path))
    if saved is not None:
        device, inode, modified, _ = actual['source_stat']
        if saved != [device, inode, actual['size_bytes'], modified]:
            raise ValueError('Original file identity changed: ' + str(path))
    return path


def _logical_input(root, path, *, source=None, context=None):
    """Resolve one declared task path without requiring a host-side alias."""
    parts = PurePosixPath(path).parts
    if (len(parts)<5 or parts[:2]!=('project','nodes') or not parts[2].isdecimal()
            or parts[2]!=f'{int(parts[2]):03d}' or parts[3] not in {'inputs','work'}):
        return None
    step=int(parts[2]);root=Path(root)
    if source is None:
        job_path=root/'jobs'/f'{step:03d}.json'
        if not job_path.exists():return None
        job=_json(job_path)
    else:
        source=at_step(source,step)
        job=source_job(source)
    logical='/'.join(parts[3:])
    material_parts = PurePosixPath(logical).parts
    materials = []
    if len(material_parts) >= 5 and material_parts[:2] == ('inputs', 'parents') and material_parts[3] == 'artifacts':
        from . import handoff_state, material_references
        producers = {}
        handoff_state.load_material_sources(root,job['material_refs'],sources=producers,source=source)
        materials=[row for row in material_references.materials(job['material_refs'],sources=producers)
            if row['path']==logical]
    if materials:
        if any(row!=materials[0] for row in materials):raise ValueError('Logical input has conflicting originals')
        producer = producers[int(PurePosixPath(materials[0]['path']).parts[2])]
        return bound_material_path(root,materials[0],source=producer['owner'],seal=producer['seal'])
    rows=[row for row in [*job.get('global_materials',[]),*job.get('history',{}).get('inputs',[]),
        *job.get('history',{}).get('execution_recovery_materials',[])] if row['path']==logical]
    if rows:
        if any(row!=rows[0] for row in rows):raise ValueError('Logical input has conflicting originals')
        if 'source' not in rows[0]:raise ValueError('Legacy logical input original is unavailable')
        selected=Path(rows[0]['source'])
        if root/selected==root/path:
            # Frozen generated pages name their actual original, not an alias.
            # Only exact scientific membership permits this inherited read.
            entries=(source._index.get((identity_key(source.origin_identity),path),())
                if source is not None and source.inherited else ())
            if len(entries)!=1 or entries[0].get('visibility') not in {'scientific_input','historical_science'}:
                raise ValueError('Selected scientific input original is unavailable')
        if source is None:return resolve_root_ref(root,rows[0],context=context)
        actual=selected if selected.is_absolute() else Path(source.original_root)/selected
        return _check_identity(files.check_path(actual),rows[0],context=context)
    direct=[row for row in job.get('direct_refs',[]) if row['path']==logical]
    if len(direct)==1:
        producer=int(PurePosixPath(logical).parts[2])
        owner=dependency(source,producer) if source is not None else node(root,producer)
        seal=_json(source_record(owner,'result-identity.json'))
        raw=resolve_ref(owner,seal['raw_ref'],context=context)
        from . import contracts as c, candidate_refs
        original=c.read_scientific_output(raw)
        public=c.scientific_projection(candidate_refs.public_science_fields(original,
            candidate_refs.for_job(job)),str(PurePosixPath(logical).parent/'scientific-original.json'))
        if public!=original:raise ValueError('Selected public scientific projection is unavailable')
        return _check_identity(raw,direct[0],context=context)
    return None


def _declared_science(source, actual, ref, *, context=None):
    """Resolve only a source explicitly named by this original task/declarations."""
    actual=Path(actual)
    if source.inherited and not source._index.get((identity_key(source.origin_identity),f'jobs/{source.step:03d}.json')):
        raise ValueError('Scientific path has no exact declared original membership')
    from . import handoff_state, material_references
    job=source_job(source)
    producers = {}
    handoff_state.load_material_sources(Path(source.original_root),job['material_refs'],sources=producers,source=source)
    materials = material_references.materials(job['material_refs'], sources=producers)
    rows=[*job.get('global_materials',[]),*materials,
        *job.get('history',{}).get('inputs',[]),
        *job.get('history',{}).get('execution_recovery_materials',[])]
    proofs=[]
    for row in rows:
        if 'source' not in row:continue
        path=Path(row['source'])
        if (path if path.is_absolute() else Path(source.original_root)/path)==actual:
            proofs.append(row)
    settings=source_settings(source)
    declaration=settings.get('source_provenance')
    if declaration is not None:
        document=_json(resolve_ref(source_for_ref(source,declaration),declaration,context=context))
        for row in document.get('declarations',[]):
            for name in ('origin','retained'):
                proof=row.get(name) or {}
                path=Path(proof.get('path',''))
                if (path if path.is_absolute() else Path(source.original_root)/path)==actual:
                    proofs.append(proof)
    if not proofs:
        raise ValueError('Scientific path has no exact declared original membership')
    if any(proof.get('size_bytes')!=proofs[0].get('size_bytes') for proof in proofs):
        raise ValueError('Scientific path has conflicting declared identities')
    for proof in proofs:
        _check_identity(files.check_path(actual),proof,context=context)
    return _check_identity(actual,ref,context=context)


def resolve_root_ref(root, ref, *, context=None):
    if context is not None:context.remaining_seconds()
    if 'origin_identity' in ref:
        return resolve_ref(for_origin(root, ref['origin_identity']), ref, context=context)
    root = Path(root).absolute()
    reference_path = Path(ref.get('source', ref['path']))
    if reference_path.is_absolute():
        if not reference_path.is_relative_to(root):
            if 'source' not in ref and 'file_stat' not in ref:
                raise ValueError('External original requires an exact bound source')
            manifest=_manifest(root)
            if manifest is not None:
                origins=[row for row in manifest['origins'].values()
                    if reference_path.is_relative_to(row['original_root'])]
                if len(origins)==1:
                    owner=for_origin(root,origins[0]['identity'])
                    return resolve_ref(owner,ref,context=context)
                return _declared_science(selected_source(root),reference_path,ref,context=context)
            return _check_identity(files.check_path(reference_path), ref, context=context)
        path = relative(reference_path.relative_to(root).as_posix())
    else:
        path = relative(str(reference_path))
    actual = root / path
    if not actual.exists():
        bound=_logical_input(root,path,context=context)
        if bound is not None:return _check_identity(bound,ref,context=context)
    if actual.exists() or _manifest(root) is None:
        return _check_identity(actual, ref, context=context)
    parts = PurePosixPath(path).parts
    step = None
    if len(parts) >= 2 and parts[0] in {'native-evidence', 'jobs'}:
        candidate = parts[1].split('.')[0]
        if candidate.isdecimal():step = int(candidate)
    elif len(parts) >= 3 and parts[:2] == ('project', 'nodes') and parts[2].isdecimal():
        step = int(parts[2])
    if step is not None:
        return resolve_ref(node(root, step), dict(ref, path=path), context=context)
    manifest = _manifest(root)
    matches = [row for row in manifest['entries'] if row['path'] == path
        and all(field not in ref or row.get(field) == ref[field] for field in ('size_bytes',))]
    if len(matches) != 1:
        raise ValueError('Original reference requires an explicit origin identity')
    return resolve_ref(for_origin(root, matches[0]['origin_identity']), dict(matches[0],
        **{key: ref[key] for key in ('file_stat',) if key in ref}), context=context)


def material_path(root, material):
    """Resolve the actual bound source and verify its producer's saved identity."""
    root = Path(root).absolute()
    saved = material
    parts = PurePosixPath(material.get('path', '')).parts
    if ('file_stat' not in material and len(parts) >= 5
            and parts[:2] == ('inputs', 'parents') and parts[2].isdecimal()
            and parts[2] == f'{int(parts[2]):03d}' and parts[3] == 'artifacts'):
        owner = node(root, int(parts[2]))
        seal_path = owner.storage_root / f'native-evidence/{owner.step:03d}/result-identity.json'
        if owner.inherited or seal_path.exists():
            seal = _json(source_record(owner, 'result-identity.json'))
            matches = [row for row in seal.get('materials', [])
                if {key: value for key, value in row.items() if key != 'file_stat'} == material]
            if len(matches) != 1:
                raise ValueError('Scientific material differs from its exact producer seal')
            saved = matches[0]
        else:
            local=PurePosixPath(*parts[4:])
            own=owner.logical_workspace/(str(local) if local.parts[0]=='experiments' else 'artifacts/'+str(local))
            if Path(material['source'])!=own:
                # A stopped managed writer already saved this exact output in
                # the existing same-call observation before the node seal.
                if len(local.parts)<4 or local.parts[0]!='experiments' or local.parts[2]!='artifacts':
                    raise ValueError('Unsealed material has no exact current producer original')
                from . import experiment_client, experiments
                call=local.parts[1]
                if not experiments.ID.fullmatch(call):raise ValueError('Unsealed managed material call changed')
                observation=_json(experiment_client.record_directory(root,owner.step)/'calls'/call/'execution-observation.json')
                experiments.verify_observation(observation,lambda proof:Path(proof['path']),
                    interface=observation['interface'],verify_files=False)
                matches=[item for item in observation.get('scientific_outputs',observation['outputs'])
                    if item['name']=='/'.join(local.parts[3:]) and item['path']==material['source']
                    and item['size_bytes']==material['size_bytes']]
                if len(matches)!=1:raise ValueError('Unsealed managed material differs from its saved original')
                saved=dict(material,**{key:matches[0][key] for key in ('file_stat',) if key in matches[0]})
    return _material_path(root, saved)


def bound_material_path(root, material, *, source, seal):
    """Consume an owner's exact producer seal without reopening that seal."""
    public = {key: value for key, value in material.items() if key != 'file_stat'}
    matches = [row for row in seal.get('materials', [])
        if {key: value for key, value in row.items() if key != 'file_stat'} == public]
    if len(matches) != 1:
        raise ValueError('Scientific material differs from its exact producer seal')
    if source.step != int(PurePosixPath(public['path']).parts[2]):
        raise ValueError('Scientific material belongs to another producer')
    return _material_path(Path(root).absolute(), matches[0])


def _material_path(root, saved):
    source = Path(saved['source'])
    parts = PurePosixPath(saved.get('path', '')).parts
    ref = dict(path=str(source), **{field: saved[field]
        for field in ('size_bytes', 'file_stat') if field in saved})
    if not source.is_absolute() or source.is_relative_to(root):
        return resolve_root_ref(root, ref)
    manifest = _manifest(root)
    if manifest is None:
        return _check_identity(files.check_path(source), ref)
    matches = []
    for origin in manifest['origins'].values():
        logical = Path(origin['original_root'])
        if source.is_relative_to(logical):
            relative_path = source.relative_to(logical).as_posix()
            matches.append((origin,relative_path))
    if not matches:
        return _declared_science(selected_source(root),source,ref)
    if len(matches) != 1:
        raise ValueError('Scientific material has no unique exact inherited origin')
    origin,path=matches[0]
    owner_step=int(parts[2]) if len(parts)>=3 and parts[:2]==('inputs','parents') and parts[2].isdecimal() else 1
    return resolve_ref(for_origin(root, origin['identity'],step=owner_step),dict(ref,path=path))


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
            matches.append((origin,path))
    if len(matches) != 1:
        raise ValueError('Material has no unique exact original origin')
    origin,path=matches[0]
    return for_origin(root, origin['identity'], step=source.step), path


def has_original(source, path):
    """Host-side existence in retained storage, independent of science indexing.

    This checks current retained presence; it does not attest historical absence
    or add a private original to a task's scientific input bindings.
    """
    path = relative(path)
    if source.inherited:
        if not source._index.get((identity_key(source.origin_identity), path)):
            try:
                resolve_ref(source,dict(path=path))
            except (OSError,ValueError):
                return False
            return True
    elif not (source.storage_root / path).exists():
        return False
    resolve_ref(source, dict(path=path))
    return True


def resolve_ref(source, ref, *, context=None):
    from . import handoff_state
    if handoff_state.loading_sealed():
        return sealed_path(source, ref)
    if context is not None: context.remaining_seconds()
    if not isinstance(source, NodeSource) or not isinstance(ref, dict):
        raise ValueError('Exact original handle and reference required')
    origin = ref.get('origin_identity', source.origin_identity)
    key = identity_key(origin)
    raw_path = ref.get('source', ref['path'])
    if origin != source.origin_identity:
        raise ValueError('Cross-origin reference requires its own original handle')
    if Path(raw_path).is_absolute() and not Path(raw_path).is_relative_to(Path(source.original_root)):
        if source.inherited:
            return _declared_science(source,Path(raw_path),ref,context=context)
        if 'file_stat' in ref:
            return _check_identity(files.check_path(Path(raw_path)), ref, context=context)
        return _declared_science(source,Path(raw_path),ref,context=context)
    if Path(raw_path).is_absolute():
        original = Path(raw_path)
        if not original.is_relative_to(Path(source.original_root)):
            raise ValueError('Original reference escaped its declared origin')
        path = relative(original.relative_to(source.original_root).as_posix())
    else:
        path = relative(raw_path)
    expected = ref
    if source.entries is None:
        actual = files.check_path(source.storage_root / path)
        if not actual.exists():
            bound=_logical_input(source.storage_root,path,source=source,context=context)
            if bound is not None:actual=bound
    else:
        matches = source._index.get((key, path), ())
        if len(matches) != 1:
            bound=_logical_input(Path(source.original_root),path,source=source,context=context)
            if bound is not None:return _check_identity(bound,ref,context=context)
            return _declared_science(source,Path(source.original_root)/path,ref,context=context)
        entry = matches[0]
        if entry['storage_path'] != f'inherited/{key}/{path}':
            raise ValueError('Original storage binding changed')
        if 'size_bytes' in ref and ref['size_bytes'] != entry['size_bytes']:
            raise ValueError('Original reference differs from its frozen binding')
        expected = entry
        if entry.get('visibility') in {'scientific_input','historical_science'}:
            bound=_logical_input(Path(source.original_root),path,source=source,context=context)
            actual=(files.check_path(Path(source.original_root)/path) if bound is None else bound)
        else:
            actual = files.check_path(source.storage_root / entry['storage_path'])
            # A copied control's source fingerprint is not the identity of its
            # child inode. Its bounded immutable entry binds the transported
            # control; scientific references still check their real originals.
    _check_identity(actual, expected, context=context)
    if expected is not ref and 'file_stat' in ref and entry.get('visibility') in {'scientific_input','historical_science'}:
        _check_identity(actual, ref, context=context)
    return actual

def sealed_path(source, ref):
    """Locate an accepted sealed binding without reopening its verification."""
    raw = Path(ref.get('source', ref['path']))
    own = Path(source.original_root)
    if raw.is_absolute() and not raw.is_relative_to(own):
        return raw
    path = relative(raw.relative_to(own).as_posix() if raw.is_absolute() else str(raw))
    if source.entries is None:
        return source.storage_root / path
    entries = source._index.get((identity_key(source.origin_identity), path), ())
    if len(entries) != 1:
        return own / path
    entry = entries[0]
    return (own / path if entry.get('visibility') in {'scientific_input', 'historical_science'}
        else source.storage_root / entry['storage_path'])


def original_ref(source, path):
    path = relative(path)
    actual = resolve_ref(source, dict(path=path))
    proof = files.stat_file(actual)
    return dict(origin_identity=source.origin_identity, path=path,
        size_bytes=proof['size_bytes'])


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
    initial = _json(resolve_ref(source, dict(path='settings.json')))
    if initial.get('goal_id') != source.origin_identity['goal_id']:
        raise ValueError('Original settings lost its instance identity')
    revision = job.get('configuration_revision')
    if revision is None: return initial
    number = revision['revision']
    predecessor = dict(path='settings.json')
    selected = None
    for index in range(1, number + 1):
        relative_path = f'host-control/configuration/revisions/{index:06d}.json'
        current = _json(resolve_ref(source, dict(path=relative_path)))
        identity = _json(resolve_ref(source, dict(path=f'host-control/configuration/identities/{index:06d}.json')))
        expected = dict(path=str(Path(source.original_root) / relative_path))
        if identity != expected or current.get('revision') != index or current.get('predecessor') != predecessor:
            raise ValueError('Original configuration predecessor changed')
        predecessor = dict(path=relative_path)
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
    snapshot = _source_snapshot(root,manifest) if manifest is not None else None
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
        key=(identity_key(source.origin_identity),ref['path'])
        if key in seen:raise ValueError('Frozen history has a cyclic task origin')
        seen.add(key)
        owner=_json(resolve_ref(source,ref))
        if owner.get('history',{})!=binding:
            raise ValueError('Frozen history differs from its exact original task')
    return source


def recovery_refs(source, job):
    refs=job.get('history',{}).get('execution_recoveries',[])
    # This legacy list does not own modern accepted-search recovery bindings.
    if not refs:return []
    owner=history_source(source,job)
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
