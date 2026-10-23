"""Host-owned source facts and direct edges, never review or execution authority.

Operator declarations share prepare's configuration trust boundary. These proofs
cover declared/frozen inputs and outputs, not arbitrary native shell reads or an
adversarial writer with access to host records. No scientific payload grants facts.
"""
import copy
import json
from pathlib import Path

from . import configuration_updates
from ..operations.provenance_rules import dependency_sources, inherited_restriction
from . import contracts as c

SCOPE = ('Source restrictions of framework-declared/frozen inputs and outputs only; '
         'clear is not scientific review or execution permission. Arbitrary native '
         'external reads and host-record writes remain a soft isolation boundary.')


class IntegrityError(ValueError):
    """An exact retained source, binding or producer identity changed."""


def _file(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink() or not path.is_file():
        raise IntegrityError('Source must be an exact canonical regular original')
    return path


def _source_path(source, ref):
    from . import originals
    owner = originals.source_for_ref(source, ref)
    path = Path(ref['path'])
    if path.is_absolute():
        path = path.relative_to(owner.original_root)
    return originals.resolve_ref(owner, dict(ref, path=path.as_posix()))


def _identity(path, *, source=None):
    if source is not None:
        actual = _source_path(source, dict(path=str(path)))
        return dict(path=str(path), size_bytes=actual.stat().st_size)
    path = _file(path)
    return dict(path=str(path), size_bytes=path.stat().st_size)


def control_reference(root, ref, *, source=None):
    """Name the original control record, preserving its logical instance root."""
    logical_root = Path(source.original_root) if source is not None else Path(root)
    path = Path(ref['path'])
    return dict(path=str(path if path.is_absolute() else logical_root / path))


def execution_payload(root, material):
    """Exact native bytes for an unchanged sealed scientific subject."""
    from . import execution_inputs
    return execution_inputs.compiled_payload(root,material)


def _check(identity, *, source=None):
    if source is not None:
        _source_path(source, identity)
        return
    if _identity(identity['path']) != identity:
        raise IntegrityError('Source original identity changed')


def _ref(root, path, *, source=None):
    if source is not None:
        logical = Path(path)
        relative = logical.relative_to(source.original_root) if logical.is_absolute() else logical
        identity = _identity(logical, source=source)
        return dict(identity, path=relative.as_posix())
    root, path = Path(root).resolve(), Path(path).absolute()
    from . import originals
    if not path.exists() and (root / originals.MANIFEST).exists():
        relative = path.relative_to(root).as_posix()
        actual = originals.resolve_root_ref(root, dict(path=relative))
        manifest = originals._manifest(root)
        matches = [row for row in manifest['entries'] if root / originals.FORK_DIRECTORY / row['storage_path'] == actual]
        if len(matches) != 1:
            raise IntegrityError('Source receipt has no unique original identity')
        return {field: matches[0][field] for field in ('origin_identity', 'path', 'size_bytes')}
    identity = _identity(path)
    return {**identity, 'path': path.relative_to(root).as_posix()}


def _read(root, ref, *, source=None):
    if source is not None:
        return c.read(_source_path(source, ref))
    root = Path(root).resolve()
    from . import originals
    if 'origin_identity' in ref:
        return c.read(originals.resolve_root_ref(root, ref))
    path = root / ref['path']
    if Path(ref['path']).is_absolute() or not path.is_relative_to(root):
        raise IntegrityError('Source receipt is outside its original instance')
    if not path.exists() and (root / originals.MANIFEST).exists():
        return c.read(originals.resolve_root_ref(root, ref))
    _check({**ref, 'path': str(path)})
    return c.read(path)


def _save(root, path, value):
    from . import atomic_files
    payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    try:
        atomic_files.publish_bytes(path, payload)
    except FileExistsError:
        if c.read(c.regular(path)) != value:
            raise IntegrityError('Frozen source receipt changed')
    return _ref(root, path)


def source_configuration(public_root, declarations, originals):
    """Validate prepare's operator configuration, before creating the instance."""
    if declarations is None:
        return []
    if not isinstance(declarations, list):
        raise ValueError('Source restrictions must be a JSON array')
    allowed = {str(Path(path).resolve()) for path in originals}
    public_root = Path(public_root).resolve()
    result, seen = [], set()
    for row in declarations:
        if (not isinstance(row, dict) or set(row) != {'source', 'restriction'}
                or not isinstance(row['source'], str)
                or row['restriction'] not in ('clear', 'restricted')):
            raise ValueError('Source declaration requires source and clear/restricted restriction')
        source = _file(row['source'])
        if str(source) not in allowed and not source.is_relative_to(public_root):
            raise ValueError('Declared source is not an original global input or public material')
        if str(source) in seen:
            raise ValueError('Source declaration is ambiguous')
        seen.add(str(source))
        identity = _identity(source)
        result.append(dict(origin=identity, restriction=row['restriction']))
    return result


def freeze_sources(root, configured, originals, globals_, *, policy, context, source_kind):
    """Retain declared bytes and the explicit original-to-global-copy mapping."""
    root = Path(root).resolve()
    declarations = []
    for index, row in enumerate(configured):
        path = root / 'source-provenance' / 'originals' / f'{index:06d}'
        _check(row['origin'])
        c._bind_original(Path(row['origin']['path']), path, policy=policy, context=context, root=root, size_bytes=row['origin']['size_bytes'], source_kind=source_kind)
        declarations.append({**row, 'retained': _ref(root, path)})
    globals_map = []
    for original, material in zip(originals, globals_):
        original = Path(original).resolve()
        if original.read_text(encoding='utf-8') != (root / material['source']).read_text(encoding='utf-8'):
            raise IntegrityError('Original global text changed before its explicit copy mapping froze')
        globals_map.append(dict(origin=_identity(original), material=material))
    return _save(root, root / 'source-provenance' / 'declarations.json',
                 dict(version=1, declarations=declarations, globals=globals_map, scope=SCOPE))


def _sources(root):
    ref = c.settings(root).get('source_provenance')
    return _read(root, ref) if ref is not None else dict(declarations=[], globals=[])


def _producer(root, step):
    from . import originals
    if originals.inherited(root, step):
        source = originals.node(root, step)
        path = originals.source_record(source, 'result-identity.json')
        seal = c.read(path)
        return dict(kind='producer', step=step,
            seal=originals.original_ref(source, f'native-evidence/{step:03d}/result-identity.json'),
            missing_reason=None if seal.get('source_provenance') else 'legacy_producer_without_source_proof')
    path = c.host_record(c.workspace(root, step)) / 'result-identity.json'
    seal = c.read(c.regular(path))
    return dict(kind='producer', step=step, seal=_ref(root, path),
                missing_reason=None if seal.get('source_provenance') else 'legacy_producer_without_source_proof')


def _inputs(root, job):
    from . import originals
    if job.get('fork_source') is not None:
        source = originals.selected_source(root)
        original = originals.source_job(source)
        document = _original_inputs(source, original)
        for edge in document['edges']:
            if edge['kind'] == 'producer':
                producer = originals.dependency(source, edge['step'], seal_ref=control_reference(source.original_root, edge['seal'], source=source))
                edge['seal'] = dict(edge['seal'], origin_identity=producer.origin_identity)
        return document
    sources = _sources(root)
    edges = []
    for material in job['global_materials']:
        mappings = [row for row in sources['globals'] if row['material'] == material]
        origin = mappings[0]['origin'] if len(mappings) == 1 else None
        matches = [index for index, row in enumerate(sources['declarations']) if row['origin'] == origin]
        edges.append(dict(kind='global', material=material, origin=origin,
                          declaration=matches[0] if len(matches) == 1 else None,
                          missing_reason=None if len(matches) == 1 else 'undeclared_or_ambiguous_original'))
    global_origins = [row['origin'] for row in sources['globals']]
    edges.extend(dict(kind='declaration', declaration=index) for index, row in enumerate(sources['declarations'])
                 if row['origin'] not in global_origins)
    steps = {int(Path(ref['path']).parts[2]) for ref in job['direct_refs']}
    for ref in job.get('direct_originals',job['direct_refs']):
        step = int(Path(ref['path']).parts[2])
        source = originals.node(root, step)
        seal = c.read(originals.source_record(source, 'result-identity.json'))
        if seal['raw_ref'].get('path',) != str(Path(source.original_root) / (source.native_record or f'native-evidence/{source.step:03d}') / 'science-output.json'):
            raise IntegrityError('Direct source does not name its exact producer original')
    steps.update(row['origin_step'] for row in job.get('selected_inputs', []))
    steps.update(row['step'] for row in job.get('delivery_sources', {}).get('source_nodes', []))
    review_binding=None
    if job.get('formal_review'):
        from . import formal_review
        review_binding=formal_review.input_binding(root,job['formal_review'])
        steps.update(review_binding.get('scientific_source_steps',[]))
    repair = job.get('engineering_repair')
    if repair is not None:
        steps.add(repair['source']['step'])
    for step in sorted(steps):
        if not 0 < step < job['step']:
            raise IntegrityError('Source producer is not an exact preceding node')
        edges.append(_producer(root, step))
    # A copied material needs membership in an exact bound producer seal. Equal
    # bytes from another path do not establish that membership or its origin.
    seals = [(edge['step'], _read(root, edge['seal'])) for edge in edges if edge['kind'] == 'producer']
    for material in job['direct_materials']:
        matches = [(step, seal) for step, seal in seals if material in seal['materials']]
        same_origin = [(step, row) for step, seal in seals for row in seal['materials'] if row['source'] == material['source']]
        for step, row in same_origin:
            if row == material:continue
            if (len(matches) != 1 or row['path'] == material['path']
                    or not Path(material['source']).is_relative_to(root / 'host-history/objects')):
                raise IntegrityError('Bound material differs from its exact producer original')
            # Shared archive bytes do not merge the logical material identities
            # or their producers. Verify each alias against its bound owner.
            c.material_name(c.workspace(root, matches[0][0]), material)
            c.material_name(c.workspace(root, step), row)
        if len(matches) != 1:
            if review_binding is not None and material in review_binding.get('compiler_scientific_materials',[]):
                continue  # Exact compiler author snapshot, verified by input_binding.
            edges.append(dict(kind='unknown', reason='missing_or_ambiguous_material_origin', material=material))
    return dict(version=1, step=job['step'], edges=edges, scope=SCOPE)


def _declaration(root, sources, index, memo, *, public=False):
    if index is None:
        return 'unknown'
    key = ('declaration', index, public)
    if key in memo:
        return memo[key]
    row = sources['declarations'][index]
    retained = root / row['retained']['path']
    _check({**row['retained'], 'path': str(retained)})
    if public:
        _check(row['origin'])  # Native reads this original, not its retained copy.
    memo[key] = row['restriction']
    return row['restriction']


def _restriction(root, document, active, memo, sources):
    key = document['step']
    if key in active:
        return 'unknown'
    if key in memo:
        return memo[key]
    active = {*active, key}
    states = []
    for edge in dependency_sources(None, document['edges']):
        if edge['kind'] == 'global':
            material = edge['material']
            if c.regular(root / material['source']).stat().st_size != material['size_bytes']:
                raise IntegrityError('Bound global original changed')
            states.append(_declaration(root, sources, edge['declaration'], memo))
        elif edge['kind'] == 'declaration':
            states.append(_declaration(root, sources, edge['declaration'], memo, public=True))
        elif edge['kind'] == 'producer':
            from . import originals
            if 'origin_identity' in edge['seal'] or originals.inherited(root, edge['step']):
                source = originals.for_origin(root, edge['seal']['origin_identity'], step=edge['step']) if 'origin_identity' in edge['seal'] else originals.node(root, edge['step'])
                c.validate_original(source)
                states.append(original_status(source)['restriction'])
                continue
            seal = _read(root, edge['seal'])
            step = edge['step']
            job_path = root / 'jobs' / f'{step:03d}.json'
            record = c.host_record(c.workspace(root, step))
            if (seal['job_ref'] != c.record_ref(c.regular(job_path))
                    or seal['result_ref'] != c.file_ref(c.regular(record / 'result.json'))
                    or seal['raw_ref'] != c.file_ref(c.regular(c._execution_raw(root, step)))):
                raise IntegrityError('Source producer differs from its exact scientific seal')
            parent = c.read(job_path)
            ref = seal.get('source_provenance')
            if ref is None:
                states.append('unknown')
                continue
            proof = _read(root, ref)
            expected = _output(root, parent, seal, managed_coverage=proof.get('managed_input_coverage'))
            if proof != expected:
                raise IntegrityError('Source proof differs from its original producer')
            inputs = parent.get('source_provenance')
            validate_inputs(root, parent)
            state = (status(root, parent)['restriction'] if parent.get('fork_source') is not None else
                _restriction(root, _read(root, inputs), active, memo, sources) if inputs else 'unknown')
            states.append(inherited_restriction([state, proof['additional_input_restriction']]))
        else:
            states.append('unknown')
    result = inherited_restriction(states)
    memo[key] = result
    return result


def bind_inputs(root, job):
    root = Path(root).resolve()
    return _save(root, c.host_record(c.workspace(root, job['step'])) / 'input-provenance.json', _inputs(root, job))


def validate_inputs(root, job):
    root = Path(root).resolve()
    ref = job.get('source_provenance')
    if ref is not None and _read(root, ref) != _inputs(root, job):
        raise IntegrityError('Frozen direct source binding changed')


def _source_read(source, ref):
    from . import originals
    return c.read(originals.resolve_ref(originals.source_for_ref(source, ref), ref))


def _original_inputs(source, job, *, _active=None):
    """Check frozen provenance edges against their portable input originals."""
    from . import originals
    active = set() if _active is None else _active
    identity = (originals.identity_key(source.origin_identity), source.step)
    if identity in active:
        raise IntegrityError('Original fork input provenance cycle')
    if job.get('source_provenance') is None:
        return dict(version=1, step=job['step'], edges=[], scope=SCOPE)
    document = _source_read(source, job['source_provenance'])
    if job.get('fork_source') is not None:
        ref = job['fork_source']['job']
        original = originals.source_for_ref(source, ref)
        originals.resolve_ref(original, ref)
        original_job = originals.source_job(original)
        expected = _original_inputs(original, original_job, _active={*active, identity})
        for edge in expected['edges']:
            if edge['kind'] == 'producer':
                producer = originals.dependency(original, edge['step'], seal_ref=control_reference(source.original_root, edge['seal'], source=source))
                edge['seal'] = dict(edge['seal'], origin_identity=producer.origin_identity)
        if document != expected:
            raise IntegrityError('Original fork provenance changed its exact selected source')
        return expected
    if (set(document) != {'version', 'step', 'edges', 'scope'} or document['version'] != 1
            or document['step'] != job['step'] or document['scope'] != SCOPE):
        raise IntegrityError('Original input provenance changed its task identity')
    settings = originals.source_settings(source)
    source_ref = settings.get('source_provenance')
    sources = _source_read(source, source_ref) if source_ref is not None else dict(globals=[], declarations=[])
    expected_globals = []
    for material in job['global_materials']:
        mappings = [row for row in sources['globals'] if row['material'] == material]
        origin = mappings[0]['origin'] if len(mappings) == 1 else None
        matches = [index for index, row in enumerate(sources['declarations']) if row['origin'] == origin]
        expected_globals.append(dict(kind='global', material=material, origin=origin,
            declaration=matches[0] if len(matches) == 1 else None,
            missing_reason=None if len(matches) == 1 else 'undeclared_or_ambiguous_original'))
    if [row for row in document['edges'] if row['kind'] == 'global'] != expected_globals:
        raise IntegrityError('Original global provenance differs from its frozen inputs')
    global_origins = [row['origin'] for row in sources['globals']]
    expected_declarations = [dict(kind='declaration', declaration=index) for index, row in enumerate(sources['declarations'])
        if row['origin'] not in global_origins]
    if [row for row in document['edges'] if row['kind'] == 'declaration'] != expected_declarations:
        raise IntegrityError('Original public declarations changed')
    required = {int(Path(ref['path']).parts[2]) for ref in job['direct_refs']}
    required.update(row['origin_step'] for row in job.get('selected_inputs', []))
    required.update(row['step'] for row in job.get('delivery_sources', {}).get('source_nodes', []))
    if job.get('engineering_repair') is not None:
        required.add(job['engineering_repair']['source']['step'])
    producers = [row for row in document['edges'] if row['kind'] == 'producer']
    if not required.issubset({row['step'] for row in producers}) or len({row['step'] for row in producers}) != len(producers):
        raise IntegrityError('Original source proof omits or duplicates its producers')
    bound_seals = []
    for edge in producers:
        if not 0 < edge['step'] < job['step']:
            raise IntegrityError('Original producer is outside its task input boundary')
        parent = originals.dependency(source, edge['step'], seal_ref=control_reference(source.original_root, edge['seal'], source=source))
        seal = _source_read(parent, edge['seal'])
        job_ref = originals.original_ref(parent, f'jobs/{edge["step"]:03d}.json')
        if seal['job_ref'] != control_reference(parent.original_root, job_ref, source=parent):
            raise IntegrityError('Original provenance producer lost its exact task')
        missing = None if seal.get('source_provenance') else 'legacy_producer_without_source_proof'
        if edge.get('missing_reason') != missing:
            raise IntegrityError('Original producer provenance availability changed')
        bound_seals.append(seal)
    unknown = [row for row in document['edges'] if row['kind'] == 'unknown']
    for material in job['direct_materials']:
        matches = [seal for seal in bound_seals if material in seal['materials']]
        if len(matches) != 1 and not any(row.get('material') == material for row in unknown) and not job.get('formal_review'):
            raise IntegrityError('Original material has no exact declared producer')
    if any(row['kind'] not in {'global', 'declaration', 'producer', 'unknown'} for row in document['edges']):
        raise IntegrityError('Original provenance has an unsupported edge')
    return copy.deepcopy(document)


def validate_original(source, job, seal):
    """Verify original relationships without issuing a new qualification."""
    _original_inputs(source, job)
    ref = seal.get('source_provenance')
    if job.get('source_provenance') is not None and ref is None:
        raise IntegrityError('Original output source proof is missing')
    if ref is None:
        return
    proof = _source_read(source, ref)
    expected = _output(source.storage_root, job, seal,
        managed_coverage=proof.get('managed_input_coverage'), source=source)
    if proof != expected:
        raise IntegrityError('Original output provenance differs from its exact sealed subject')


def _original_input_status(source, *, active=None):
    from . import originals
    active = set() if active is None else active
    key = (originals.identity_key(source.origin_identity), source.step)
    if key in active:
        raise IntegrityError('Original source provenance cycle')
    job = originals.source_job(source)
    if job.get('fork_source') is not None:
        original = originals.source_for_ref(source, job['fork_source']['job'])
        originals.resolve_ref(original, job['fork_source']['job'])
        return _original_input_status(original, active={*active, key})
    settings = originals.source_settings(source)
    source_ref = settings.get('source_provenance')
    sources = _source_read(source, source_ref) if source_ref is not None else dict(declarations=[])
    states = []
    for edge in _original_inputs(source, job)['edges']:
        if edge['kind'] in {'global', 'declaration'}:
            index = edge['declaration']
            if index is None:
                states.append('unknown')
            else:
                declaration = sources['declarations'][index]
                retained = originals.resolve_ref(source, declaration['retained'])
                actual = c.file_ref(retained)
                if any(actual[field] != declaration['origin'][field] for field in ('size_bytes',)):
                    raise IntegrityError('Original public snapshot differs from its declaration')
                states.append(declaration['restriction'])
        elif edge['kind'] == 'producer':
            parent = originals.dependency(source, edge['step'], seal_ref=control_reference(source.original_root, edge['seal'], source=source))
            states.append(original_status(parent, active={*active, key})['restriction'])
        else:
            states.append('unknown')
    return dict(restriction=inherited_restriction(states), scope=SCOPE)


def original_status(source, *, active=None):
    from . import originals
    job = originals.source_job(source)
    seal = c.read(originals.source_record(source, 'result-identity.json'))
    validate_original(source, job, seal)
    input_status = _original_input_status(source, active=active)
    states = [input_status['restriction']]
    output = _source_read(source, seal['source_provenance']) if seal.get('source_provenance') else None
    if output is not None:
        states.append(output['additional_input_restriction'])
    return dict(restriction=inherited_restriction(states), scope=SCOPE)


def _output(root, job, seal, *, managed_coverage=None, source=None):
    additional_inputs = False
    if seal.get('observations') is not None:
        root = Path(root).resolve()
        receipt = seal['observations']
        path = f'host-history/observations/{job["step"]:03d}.json'
        if receipt['path'] != path:
            raise IntegrityError('Source observation receipt changed')
        actual = _source_path(source, receipt) if source is not None else _file(root / path)
        if 'size_bytes' in receipt and actual.stat().st_size != receipt['size_bytes']:
            raise IntegrityError('Source observation receipt changed')
        observation = c.read(actual)
        observer_unknown = False
        from . import originals
        policy = originals.source_settings(source) if source is not None else configuration_updates.for_job(root, job)
        if policy.get('history_observer') is not None:
            from . import history
            covered = history.observer_input_sources(root, job, observation, source=source)
            observer_unknown = covered is None or not covered['complete']
        additional_inputs = bool(observation.get('managed_calls')) or observer_unknown
        if managed_coverage is not None:
            from . import history
            history.validate_input_coverage(root, job, observation.get('managed_calls', []), managed_coverage, source=source)
            additional_inputs = not all(row['complete'] for row in managed_coverage) or observer_unknown
    elif managed_coverage is not None:
        raise IntegrityError('Managed input coverage has no original observation')
    return dict(version=1, step=job['step'], job_ref=seal['job_ref'],
                result_ref=seal['result_ref'], raw_ref=seal['raw_ref'],
                materials=copy.deepcopy(seal['materials']),
                inputs=job.get('source_provenance'), derivation='all_frozen_task_inputs',
                observations=seal.get('observations'),
                additional_input_restriction='unknown' if additional_inputs else 'clear',
                missing_reason='observation_input_provenance_unsupported' if additional_inputs else None,
                **(dict(managed_input_coverage=managed_coverage) if managed_coverage is not None else {}),
                scope=SCOPE)


def seal_outputs(root, job, seal):
    root = Path(root).resolve()
    path = c.host_record(c.workspace(root, job['step'])) / 'provenance.json'
    coverage = None
    if path.exists():
        coverage = c.read(_file(path)).get('managed_input_coverage')
    elif seal.get('observations') is not None:
        from . import history
        observation = history.read_observation(root, job, seal)
        if observation.get('managed_calls'):
            coverage = history.managed_input_coverage(root, job, observation['managed_calls'])
    return _save(root, path, _output(root, job, seal, managed_coverage=coverage))


def validate_outputs(root, job, seal, *, source=None):
    if source is not None:
        return validate_original(source, job, seal)
    ref = seal.get('source_provenance')
    if ref is not None:
        proof = _read(root, ref)
        if proof != _output(root, job, seal, managed_coverage=proof.get('managed_input_coverage')):
            raise IntegrityError('Frozen output source proof changed')
    if job.get('source_provenance') is not None and ref is None:
        raise IntegrityError('Output source proof is missing')


def status(root, job):
    """On-demand compact view; never duplicate ancestor edges in task/context."""
    root = Path(root).resolve()
    if job.get('fork_source') is not None:
        from . import originals
        validate_inputs(root, job)
        result = _original_input_status(originals.selected_source(root))
        return dict(result, direct_input_count=len(_inputs(root, job)['edges']))
    ref = job.get('source_provenance')
    if ref is None:
        return dict(restriction='unknown', scope=SCOPE)
    validate_inputs(root, job)
    document = _read(root, ref)
    return dict(restriction=_restriction(root, document, set(), {}, _sources(root)), direct_input_count=len(document['edges']), scope=SCOPE)


def output_status(root, step, material=None):
    """Read exact sealed subject facts; this is not an execution admission gate."""
    root = Path(root).resolve()
    from . import originals
    if originals.inherited(root, step):
        source = originals.node(root, step)
        c.validate_original(source)
        seal = c.read(originals.source_record(source, 'result-identity.json'))
        if material is not None and material not in seal['materials']:
            raise IntegrityError('Material is not an exact original of the named producer')
        return dict(original_status(source), missing_reason=None)
    work = c.workspace(root, step)
    c.validate(work)
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    seal = c.read(c.host_record(work) / 'result-identity.json')
    if material is not None and material not in seal['materials']:
        raise IntegrityError('Material is not an exact original of the named producer')
    ref = seal.get('source_provenance')
    if ref is None:
        return dict(restriction='unknown', missing_reason='legacy_producer_without_source_proof', scope=SCOPE)
    proof = _read(root, ref)
    state = inherited_restriction([status(root, job)['restriction'], proof['additional_input_restriction']])
    return dict(restriction=state, missing_reason=proof['missing_reason'], scope=SCOPE)
