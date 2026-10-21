"""Host-owned source facts and direct edges, never review or execution authority.

Operator declarations share prepare's configuration trust boundary. These proofs
cover declared/frozen inputs and outputs, not arbitrary native shell reads or an
adversarial writer with access to host records. No scientific payload grants facts.
"""
import hashlib
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


def _identity(path):
    path = _file(path)
    return dict(path=str(path), sha256=c.digest(path), size_bytes=path.stat().st_size)


def execution_payload(root, material):
    """Exact native bytes for an unchanged sealed scientific subject."""
    from . import execution_inputs
    return execution_inputs.compiled_payload(root,material)


def _check(identity):
    if _identity(identity['path']) != identity:
        raise IntegrityError('Source original identity changed')


def _ref(root, path):
    root, path = Path(root).resolve(), Path(path).absolute()
    identity = _identity(path)
    return {**identity, 'path': path.relative_to(root).as_posix()}


def _read(root, ref):
    root = Path(root).resolve()
    path = root / ref['path']
    if Path(ref['path']).is_absolute() or not path.is_relative_to(root):
        raise IntegrityError('Source receipt is outside its original instance')
    _check({**ref, 'path': str(path)})
    return c.read(path)


def _save(root, path, value):
    if path.exists():
        if c.read(c.regular(path)) != value:
            raise IntegrityError('Frozen source receipt changed')
    else:
        c.write(path, value)
        path.chmod(0o400)
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
        if (not isinstance(row, dict) or set(row) != {'source', 'sha256', 'restriction'}
                or not isinstance(row['source'], str) or not isinstance(row['sha256'], str)
                or row['restriction'] not in ('clear', 'restricted')):
            raise ValueError('Source declaration requires source, sha256 and clear/restricted restriction')
        source = _file(row['source'])
        if str(source) not in allowed and not source.is_relative_to(public_root):
            raise ValueError('Declared source is not an original global input or public material')
        if str(source) in seen:
            raise ValueError('Source declaration is ambiguous')
        seen.add(str(source))
        identity = _identity(source)
        if identity['sha256'] != row['sha256']:
            raise IntegrityError('Declared source digest differs from the exact original')
        result.append(dict(origin=identity, restriction=row['restriction']))
    return result


def freeze_sources(root, configured, originals, globals_):
    """Retain declared bytes and the explicit original-to-global-copy mapping."""
    root = Path(root).resolve()
    declarations = []
    for index, row in enumerate(configured):
        path = root / 'source-provenance' / 'originals' / f'{index:06d}'
        _check(row['origin'])
        c._copy_bound(Path(row['origin']['path']), path, row['origin']['sha256'])
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
    path = c.host_record(c.workspace(root, step)) / 'result-identity.json'
    seal = c.read(c.regular(path))
    return dict(kind='producer', step=step, seal=_ref(root, path),
                missing_reason=None if seal.get('source_provenance') else 'legacy_producer_without_source_proof')


def _inputs(root, job):
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
        seal = c.read(c.regular(c.host_record(c.workspace(root, step)) / 'result-identity.json'))
        if ref['sha256'] != seal['raw_sha256']:
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
    seals = [(edge['step'], c.read(root / edge['seal']['path'])) for edge in edges if edge['kind'] == 'producer']
    for material in job['direct_materials']:
        matches = [(step, seal) for step, seal in seals if material in seal['materials']]
        same_origin = [(step, row) for step, seal in seals for row in seal['materials'] if row['source'] == material['source']]
        for step, row in same_origin:
            if row == material:continue
            if (len(matches) != 1 or row['path'] == material['path']
                    or Path(material['source']) != root / 'host-history/objects' / material['sha256']):
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
            if c.digest(c.regular(root / material['source'])) != material['sha256']:
                raise IntegrityError('Bound global original changed')
            states.append(_declaration(root, sources, edge['declaration'], memo))
        elif edge['kind'] == 'declaration':
            states.append(_declaration(root, sources, edge['declaration'], memo, public=True))
        elif edge['kind'] == 'producer':
            seal = _read(root, edge['seal'])
            step = edge['step']
            job_path = root / 'jobs' / f'{step:03d}.json'
            record = c.host_record(c.workspace(root, step))
            if (seal['job_sha256'] != c.digest(c.regular(job_path))
                    or seal['sha256'] != c.digest(c.regular(record / 'result.json'))
                    or seal['raw_sha256'] != c.digest(c.regular(c._execution_raw(root, step)))):
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
            state = _restriction(root, _read(root, inputs), active, memo, sources) if inputs else 'unknown'
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


def _output(root, job, seal, *, managed_coverage=None):
    additional_inputs = False
    if seal.get('observations') is not None:
        root = Path(root).resolve()
        receipt = seal['observations']
        path = root / 'host-history' / 'observations' / f'{job["step"]:03d}.json'
        if receipt['path'] != path.relative_to(root).as_posix() or c.digest(_file(path)) != receipt['sha256']:
            raise IntegrityError('Source observation receipt changed')
        observation = c.read(path)
        observer_unknown = False
        if configuration_updates.for_job(root, job).get('history_observer') is not None:
            from . import history
            covered = history.observer_input_sources(root, job, observation)
            observer_unknown = covered is None or not covered['complete']
        additional_inputs = bool(observation.get('managed_calls')) or observer_unknown
        if managed_coverage is not None:
            from . import history
            history.validate_input_coverage(root, job, observation.get('managed_calls', []), managed_coverage)
            additional_inputs = not all(row['complete'] for row in managed_coverage) or observer_unknown
    elif managed_coverage is not None:
        raise IntegrityError('Managed input coverage has no original observation')
    return dict(version=1, step=job['step'], job_sha256=seal['job_sha256'],
                result_sha256=seal['sha256'], raw_sha256=seal['raw_sha256'],
                materials_sha256=hashlib.sha256(json.dumps(seal['materials'], sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest(),
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


def validate_outputs(root, job, seal):
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
    ref = job.get('source_provenance')
    if ref is None:
        return dict(restriction='unknown', scope=SCOPE)
    validate_inputs(root, job)
    document = _read(root, ref)
    return dict(restriction=_restriction(root, document, set(), {}, _sources(root)), direct_input_count=len(document['edges']), scope=SCOPE)


def output_status(root, step, material=None):
    """Read exact sealed subject facts; this is not an execution admission gate."""
    root = Path(root).resolve()
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
