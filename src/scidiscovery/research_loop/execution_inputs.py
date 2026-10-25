"""Exact native scientific inputs and immutable compiler provenance; no effects."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import uuid

from . import contracts as c, qualification as q, execution, experiment_client

SOURCE_SCHEMA = 'scidiscovery.declared-input-sources.v1'
SOURCE_CONTRACT = dict(version=1, coverage='all_adapter_declared_inputs_and_contributors',
    origins=['global', 'producer', 'current_author', 'unknown'],
    scope='framework_declared_inputs_only')


def source_contract():
    """One optional interpretation, distinct from Local's snapshot contract."""
    from . import experiments as e
    raw = e.encoded(SOURCE_CONTRACT)
    return dict(key=e.INPUT_SOURCES_KEY, kind='source_provenance', schema_id=SOURCE_SCHEMA,
        payload_schema_version=1, media_type='application/json', content=raw.decode('utf-8'),
        size_bytes=len(raw))


def declared_sources(root, job):
    """Only immediate frozen inputs; a trusted adapter selects from this inventory."""
    rows = [dict(name=row['path'], origin=dict(kind='global', material=row)) for row in job['global_materials']]
    seen = set()
    for material in _bound(job):
        key = json.dumps(material, sort_keys=True)
        if key not in seen:
            rows.append(dict(name=material['path'], origin=dict(kind='producer', original=_producer(root, material))))
            seen.add(key)
    rows.append(dict(name='current_scientific_author', origin=dict(kind='current_author', step=job['step'])))
    return rows


def validate_source_view(root, job, view, *, source=None, controls_only=False):
    """Verified direct edges and contributor membership, never an adapter call."""
    if (not isinstance(view, dict) or set(view) != {'version','complete','sources'}
            or type(view['version']) is not int or view['version'] != 1
            or type(view['complete']) is not bool or not isinstance(view['sources'], list)):
        raise q.IntegrityError('Declared source view has no exact coverage contract')
    if not controls_only:
        if source is None:q.validate_inputs(root, job)
        else:q._original_inputs(source, job)
    if job.get('source_provenance') is None:
        return dict(complete=False, contributions=[])
    edges = q._read(root, job['source_provenance'], source=source)['edges']
    complete = view['complete']; contributions = []; seen = set()
    for origin in view['sources']:
        if not isinstance(origin, dict):
            raise q.IntegrityError('Declared input origin is malformed')
        key = json.dumps(origin, sort_keys=True)
        if key in seen:
            raise q.IntegrityError('Declared input origin is duplicated')
        seen.add(key)
        if origin.get('kind') == 'producer' and set(origin) == {'kind','original'}:
            original = origin['original']; material = resolve_native_original(root, original, compiler_job=job,
                source=source, controls_only=controls_only)
            if not any(edge['kind'] == 'producer' and edge['step'] == original['producer_step']
                    and edge['seal'] == original['producer_seal'] for edge in edges):
                raise q.IntegrityError('Declared producer is absent from frozen source edges')
            seal = q._read(root, original['producer_seal'], source=source)
            contributions.append(dict(origin_step=original['producer_step'], origin_result_ref=seal['result_ref'], material=material))
        elif origin.get('kind') == 'global' and set(origin) == {'kind','material'}:
            if not any(edge['kind'] == 'global' and edge['material'] == origin['material'] for edge in edges):
                raise q.IntegrityError('Declared global is absent from frozen source edges')
            material = origin['material']
            logical = Path(source.original_root) if source is not None else Path(root)
            if not controls_only:
                q._check(dict(path=str(logical / material['source']), size_bytes=material['size_bytes']), source=source)
        elif origin.get('kind') == 'current_author' and set(origin) == {'kind','step'}:
            if type(origin['step']) is not int or origin['step'] != job['step']:
                raise q.IntegrityError('Declared current author belongs to another job')
        elif origin == {'kind':'unknown'}:
            complete = False
        else:
            raise q.IntegrityError('Declared input origin is unsupported')
    return dict(complete=complete, contributions=contributions)


def validate_exact_input_sources(root, job, accepted, *, source=None, controls_only=False):
    """Consume an actual adapter's frozen source view without parsing its domain."""
    from . import experiments as e
    original = accepted.get('input_sources')
    if original is None or original.get('contract') != source_contract():
        return None
    declared = [row for row in accepted['interface'].get('public_capabilities', []) if row['key'] == e.INPUT_SOURCES_KEY]
    contract = source_contract()
    if len(declared) != 1 or any(declared[0].get(key) != contract[key] for key in
            ('key','kind','schema_id','payload_schema_version','media_type','size_bytes')):
        raise q.IntegrityError('Declared input view has no unique frozen capability')
    view = original['view']
    if view is None:
        return None
    if not isinstance(view, dict) or set(view) != {'job','configuration','native_intent','prepared','evidence','sources'}:
        raise q.IntegrityError('Declared input view changed its host owner')
    root = Path(source.original_root) if source is not None else Path(root).resolve()
    attempt = accepted.get('native_attempt') or {}
    projection = root / 'host-runtime/experiment-control' / f'{accepted["scope"]["step"]:03d}' / 'interfaces' / f'{attempt["number"]:03d}.json'
    config = q._read(root, view['configuration'], source=source)
    native_record = (root / f'native-evidence/{job["step"]:03d}' /
        f'continuations/{attempt["number"]:03d}' if attempt['number'] else root / f'native-evidence/{job["step"]:03d}')
    if not controls_only:native_record = _native_record(root, job['step'], attempt['number'], source=source)
    if (view['job'] != q._ref(root, root / 'jobs' / f'{job["step"]:03d}.json', source=source)
            or q._read(root, view['job'], source=source) != job or job['role'] != 'W'
            or view['configuration'] != q._ref(root, projection, source=source)
            or config['scope'] != accepted['scope'] or config['native_attempt'] != attempt
            or config['execution_interface'] != accepted['interface']
            or view['prepared'] != accepted['prepared']
            or view['native_intent'] != q._ref(root, native_record / 'native-intent.json', source=source)
            or q._read(root, view['native_intent'], source=source)['task_ref'] != dict(path=str(root/view['job']['path']))
            or accepted['interface']['config_record'] != {key:value for key,value in config.items() if key != 'execution_interface'}):
        raise q.IntegrityError('Declared input view differs from its original native owner')
    if not isinstance(view['evidence'], list) or not view['evidence']:
        raise q.IntegrityError('Declared input view omitted its actual preparation evidence')
    if not controls_only:
        for ref in view['evidence']:
            if Path(ref['path']).is_absolute() or q._ref(root, root / ref['path'], source=source) != ref:
                raise q.IntegrityError('Declared preparation original changed')
    return validate_source_view(root, job, view['sources'], source=source, controls_only=controls_only)


def managed_source_contributions(root, job, accepted, *, source=None, controls_only=False):
    value = validate_exact_input_sources(root, job, accepted, source=source, controls_only=controls_only)
    if value is not None:
        return value
    local = validate_local_input_sources(root, job, accepted, source=source, controls_only=controls_only)
    return None if local is None else dict(complete=True, contributions=local)


def _bound(job):
    return [*job['direct_materials'], *[row['material'] for row in job.get('selected_inputs', [])]]


def _sealed_material(root, seal, material, *, source=None, controls_only=False):
    """Check one selected private descriptor before comparing its public row."""
    matches = [row for row in seal['materials']
        if {key: value for key, value in row.items() if key != 'file_stat'} == material]
    if len(matches) != 1:
        raise q.IntegrityError('Scientific input is not its exact producer material')
    if not controls_only and c._public_materials(root, matches, source=source) != [material]:
        raise q.IntegrityError('Scientific input is not its exact producer material')
    return material


def _native_record(root, step, attempt, *, source=None):
    if source is None:
        return execution.record(root, step, attempt)
    if type(attempt) is not int or not 0 <= attempt < 10000:
        raise q.IntegrityError('Original native attempt has no exact finite identity')
    origin = Path(source.original_root) / f'native-evidence/{step:03d}'
    return origin / f'continuations/{attempt:03d}' if attempt else origin


def _producer(root, material, *, source=None, controls_only=False):
    from . import originals
    if source is not None:
        job = originals.source_job(source)
        matches = []
        edges = (q._read(root, job['source_provenance'], source=source)['edges'] if controls_only
            else q._original_inputs(source, job)['edges'])
        for edge in edges:
            if edge['kind'] != 'producer':continue
            owner = originals.dependency(source, edge['step'], seal_ref=edge['seal'])
            seal = q._read(root, edge['seal'], source=owner)
            if any({key: value for key, value in row.items() if key != 'file_stat'} == material
                    for row in seal['materials']):
                _sealed_material(root, seal, material, source=owner, controls_only=controls_only)
                matches.append(dict(instance_id=owner.origin_identity['goal_id'], producer_step=owner.step,
                    producer_seal=edge['seal'], material_path=material['path'],
                    size_bytes=material['size_bytes']))
        if len(matches) != 1:
            raise q.IntegrityError('Original scientific input has no exact unique producer')
        return matches[0]
    if (Path(root) / originals.MANIFEST).exists():
        parts = Path(material['path']).parts
        if len(parts) >= 4 and parts[:2] == ('inputs', 'parents') and parts[2].isdecimal():
            source = originals.node(root, int(parts[2]))
            if source.inherited:
                c.validate_original(source, controls_only=controls_only)
                seal = c.read(originals.source_record(source, 'result-identity.json'))
                _sealed_material(root, seal, material, source=source, controls_only=controls_only)
                if not controls_only:originals.material_path(root, material)
                return dict(instance_id=source.origin_identity['goal_id'], producer_step=source.step,
                    producer_seal=originals.original_ref(source, f'native-evidence/{source.step:03d}/result-identity.json'),
                    material_path=material['path'], size_bytes=material['size_bytes'])
    parts = Path(material['path']).parts
    if (len(parts)<5 or parts[:2]!=('inputs','parents') or not parts[2].isdecimal()
            or parts[3]!='artifacts'):
        raise ValueError('Scientific input needs an exact sealed instance original')
    step=int(parts[2])
    if step<=0 or parts[2]!=f'{step:03d}':
        raise q.IntegrityError('Scientific input producer namespace changed')
    path = c.host_record(c.workspace(root, step)) / 'result-identity.json'
    seal = c.read(c.regular(path))
    _sealed_material(root, seal, material, controls_only=controls_only)
    record = c.host_record(c.workspace(root, step))
    if (seal['job_ref'] != c.record_ref(root / 'jobs' / f'{step:03d}.json')
            or seal['result_ref'] != c.file_ref(record / 'result.json')):
        raise q.IntegrityError('Scientific input producer or original bytes changed')
    if not controls_only:
        if originals.resolve_root_ref(root, seal['raw_ref']) != c._execution_raw(root, step):
            raise q.IntegrityError('Scientific input producer original bytes changed')
        originals.material_path(root, material)
    return dict(instance_id=c.settings(root)['goal_id'], producer_step=step,
        producer_seal=q._ref(root, path), material_path=material['path'],
        size_bytes=material['size_bytes'])


def resolve_native_original(root, original, *, compiler_job, source=None, controls_only=False):
    """Membership and origin are exact; a matching size never selects a source."""
    root = Path(root).resolve()
    if (not isinstance(original, dict) or type(original.get('producer_step')) is not int
            or type(original.get('size_bytes')) is not int):
        raise q.IntegrityError('Native original requires exact integer producer/size declarations')
    if source is not None:
        return _original_input(source, original, compiler_job, controls_only=controls_only)
    found = [row for row in _bound(compiler_job) if row['path'] == original.get('material_path')]
    unique = {json.dumps(row, sort_keys=True): row for row in found}
    if len(unique) != 1:
        raise ValueError('Native input is missing or ambiguous in its compiler task')
    material = next(iter(unique.values()))
    if (json.dumps(original, sort_keys=True) != json.dumps(_producer(root, material, controls_only=controls_only), sort_keys=True)
            or original['producer_step'] >= compiler_job['step']):
        raise q.IntegrityError('Native input differs from its frozen compiler source')
    return material


def _directory(root, step):
    return c.host_record(c.workspace(root, step)) / 'compiled-implementations'


def _private_compile_output(root, job, config, output, *, source=None):
    if source is not None:
        from . import originals
        job_ref = originals.original_ref(source, f'jobs/{job["step"]:03d}.json')
        directory = Path(source.original_root) / 'host-runtime/experiment-control' / f'{job["step"]:03d}' / 'records'
    else:
        directory = experiment_client.record_directory(root, job['step'])
    return (type(config.get('worker_view_version')) is int and config['worker_view_version'] == 1
        and config.get('record_dir') == str(directory)
        and output.parent.parent == directory.parent / 'implementations'
        and re.fullmatch(r'I[0-9]{3,}', output.parent.name) is not None and output.name == 'package.json')


def _implementation_client(material, *, root=None, source=None):
    if material['size_bytes'] > 4096:
        return None
    from . import originals
    if source is not None:
        owner, path = originals.material_owner(source, material)
        actual = originals.resolve_ref(owner, dict(material, path=path))
    else:
        actual = Path(material['source']) if root is None else originals.material_path(root, material)
    raw = c.regular(actual).read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or 'worker_implementation_version' not in value:
        return None
    if (set(value) != {'worker_implementation_version', 'implementation_ref'}
            or type(value['worker_implementation_version']) is not int or value['worker_implementation_version'] != 1
            or not isinstance(value['implementation_ref'], str)
            or re.fullmatch(r'I[0-9]{3,}', value['implementation_ref']) is None):
        raise q.IntegrityError('Native implementation client is malformed')
    return value


def _legacy_authorization(root, job, compiler_ref, original_material, permit_ref, attempt, *, require_intent, source=None):
    from . import author_closeout
    if 'worker_view_version' in job or type(attempt) is not int or attempt not in {1, 2}:
        raise q.IntegrityError('Legacy compiler clients require an explicit new author context')
    expected = (root / 'host-recovery/author-closeout' / f'{job["step"]:03d}.json'
                if attempt == 1 else author_closeout.successor_path(root, job['step']))
    expected_ref = q._ref(root, expected) if source is None else author_closeout._ref(root, expected, source=source)
    if permit_ref != expected_ref:
        raise q.IntegrityError('Legacy compiler client changed its exact author authorization')
    def read(proof):
        return q._read(root, proof) if source is None else c.read(author_closeout._check(root, proof, source=source))
    permit = read(permit_ref)
    if attempt == 1:
        author_closeout.validate(root, permit, **(dict(source=source) if source is not None else {}))
        original = permit
    else:
        author_closeout.validate_successor(root, permit, **(dict(source=source) if source is not None else {}))
        original = read(permit['original_permit'])
    compiler_path = author_closeout._declared_ref(root,compiler_ref)
    if (compiler_path.name != 'compiler.json' or compiler_path.parent.parent != _directory(root,job['step'])
            or original['step'] != job['step'] or read(original['job']) != job):
        raise q.IntegrityError('Legacy compiler client differs from the authorized original task')
    if require_intent:
        path = root / 'native-evidence' / f'{job["step"]:03d}' / 'continuations' / f'{attempt:03d}' / 'native-intent.json'
        intent = c.read(c.regular(path)) if source is None else author_closeout._read_original(root, path, source)
        if (type(intent.get('attempt')) is not int or intent['attempt'] != attempt
                or intent.get('task_ref') != dict(path=str(root/original['job']['path']))
                or intent.get('author_closeout') != permit_ref or intent.get('continuation_kind') != 'author_closeout'
                or intent.get('context_mode') != 'new_context'
                or type(intent.get('worker_view_version')) is not int or intent['worker_view_version'] != 1):
            raise q.IntegrityError('Legacy compiler client has no exact accepted author context')
    return original


def _legacy_author_eligibility(root, job, compiler_ref, original_material, permit_ref, attempt, *, require_intent, source=None):
    """Verify scientific compiler provenance independently of author permission."""
    original = _legacy_authorization(root,job,compiler_ref,original_material,permit_ref,attempt,require_intent=require_intent,source=source)
    compiler = (verify_compilation(root,compiler_ref,material=original_material) if source is None else
                verify_original_compilation(source,compiler_ref,material=original_material))
    if compiler['job'] != original['job'] or compiler['native_intent'] != original['original_intent']:
        raise q.IntegrityError('Legacy compiler client differs from the authorized original task')
    return compiler


def verify_legacy_author_client(root, job, binding_ref, *, require_intent=True, source=None):
    """Verify a new client separately from an unchanged legacy package and permit."""
    from . import author_closeout, originals
    root = Path(root).resolve() if source is None else Path(source.original_root)
    binding = q._read(root, binding_ref) if source is None else c.read(
        originals.resolve_ref(source, binding_ref))
    if not isinstance(binding, dict) or set(binding) != {'client', 'public_path', 'package', 'legacy_authorization'}:
        raise q.IntegrityError('Legacy author client binding is malformed')
    grant = binding['legacy_authorization']
    if not isinstance(grant, dict) or set(grant) != {'permit', 'compiler', 'material', 'attempt'}:
        raise q.IntegrityError('Legacy author client authorization is malformed')
    compiler = _legacy_author_eligibility(root, job, grant['compiler'], grant['material'], grant['permit'], grant['attempt'],
                                     require_intent=require_intent, source=source)
    expected_binding = root / grant['compiler']['path']
    expected_binding = expected_binding.parent / f'author-client-{grant["attempt"]:03d}.json'
    compilers = (sorted(_directory(root, job['step']).glob('*/compiler.json')) if source is None or not source.inherited else
                 [root / path for path in originals.legacy_compilation(source)['compiler_paths']])
    expected_ref = q._ref(root, expected_binding) if source is None else author_closeout._ref(root, expected_binding, source=source)
    bare_binding = binding_ref if source is None else {
        key:value for key,value in binding_ref.items() if key != 'origin_identity'}
    if (root / grant['compiler']['path']) not in compilers or bare_binding != expected_ref:
        raise q.IntegrityError('Legacy author client changed its original compiler namespace')
    reference = f'I{compilers.index(root / grant["compiler"]["path"]) + 1:03d}'
    public_source = c.workspace(root, job['step']) / 'artifacts/scientific-implementations' / (reference + '.json')
    expected_client = dict(worker_implementation_version=1, implementation_ref=reference)
    client_material = (dict(q._identity(public_source), source=str(public_source)) if source is None else
                       dict(originals.original_ref(source, public_source.relative_to(root).as_posix()), source=str(public_source)))
    if (binding['public_path'] != str(public_source) or binding['package'] != compiler['output']
            or not isinstance(binding['client'], dict)
            or type(binding['client'].get('worker_implementation_version')) is not int
            or binding['client'] != expected_client or _implementation_client(client_material, source=source) != expected_client):
        raise q.IntegrityError('Legacy author client differs from its actual scientific file and original package')
    return binding, compiler


def create_legacy_author_client(root, job, compiler_ref, original_material, permit_ref, attempt, public_path):
    """Trusted new-author publication; never rewrite an old package or task marker."""
    root = Path(root).resolve()
    _legacy_authorization(root,job,compiler_ref,original_material,permit_ref,attempt,require_intent=False)
    from . import author_closeout
    permit = q._read(root,permit_ref)
    if attempt == 1:author_closeout.require_original_eligibility(root,permit)
    else:author_closeout.require_successor_eligibility(root,permit)
    compiler = _legacy_author_eligibility(root, job, compiler_ref, original_material, permit_ref, attempt, require_intent=False)
    paths = sorted(_directory(root, job['step']).glob('*/compiler.json'))
    compiler_path = root / compiler_ref['path']
    if compiler_path not in paths:
        raise q.IntegrityError('Legacy author client has no original compiler owner')
    reference = f'I{paths.index(compiler_path) + 1:03d}'
    expected = c.workspace(root, job['step']) / 'artifacts/scientific-implementations' / (reference + '.json')
    public_path = Path(public_path).absolute()
    if public_path != expected or public_path.resolve() != public_path:
        raise q.IntegrityError('Legacy author client requires its actual new scientific path')
    client = dict(worker_implementation_version=1, implementation_ref=reference)
    binding_path = compiler_path.parent / f'author-client-{attempt:03d}.json'
    if (not binding_path.exists()
            and (c.host_record(c.workspace(root, job['step'])) / 'result-identity.json').exists()):
        raise q.IntegrityError('Legacy author clients cannot change an already sealed original')
    if public_path.exists():
        if c.read(c.regular(public_path)) != client:
            raise q.IntegrityError('Legacy author client cannot replace another scientific original')
    else:
        c.write(public_path, client)
        public_path.chmod(0o400)
    binding = dict(client=client, public_path=str(public_path), package=compiler['output'],
        legacy_authorization=dict(permit=permit_ref, compiler=compiler_ref, material=original_material, attempt=attempt))
    proof = q._save(root, binding_path, binding)
    verify_legacy_author_client(root, job, proof, require_intent=False)
    return proof


def verify_compilation(root, ref, *, material=None, client_binding=None, source=None, controls_only=False):
    if source is not None:
        return verify_original_compilation(source, ref, material=material, client_binding=client_binding,
            controls_only=controls_only)
    """Shared seal/review/run validator; it never recompiles reviewed bytes."""
    if 'origin_identity' in ref:
        from . import originals
        return verify_original_compilation(originals.for_origin(root, ref['origin_identity']), ref,
            material=material, client_binding=client_binding, controls_only=controls_only)
    root = Path(root).resolve(); value = q._read(root, ref)
    if value.get('version') != 1 or type(value.get('version')) is not int:
        raise q.IntegrityError('Compiler original version changed')
    job = q._read(root, value['job']); step = job['step']
    if (value['job'] != q._ref(root, root / 'jobs' / f'{step:03d}.json')
            or job['role'] != 'W' or Path(root / ref['path']).parent.parent != _directory(root, step)):
        raise q.IntegrityError('Compiler original has no exact W owner')
    config = q._read(root, value['configuration'])
    attempt = config['native_attempt']['number']
    if type(attempt) is not int or not 0 <= attempt < 10000:
        raise q.IntegrityError('Compiler native attempt is malformed')
    # Validate the frozen original directly. Re-entering records() here would
    # recursively validate this same author's scientific context. Launch/seal
    # callers independently validate the complete accepted execution chain.
    native_record = execution.origin(root, step)
    if attempt:
        native_record = native_record / 'continuations' / f'{attempt:03d}'
    expected = root / 'host-runtime/experiment-control' / f'{config["scope"]["step"]:03d}' / 'interfaces' / f'{attempt:03d}.json'
    if (value['configuration'] != q._ref(root, expected)
            or value['native_intent'] != q._ref(root, native_record / 'native-intent.json')
            or q._read(root, value['native_intent'])['task_ref'] != dict(path=str(root/value['job']['path']))
            or config['scope']['step'] != step or config['scope']['job_ref'] != dict(path=str(root/value['job']['path']))):
        raise q.IntegrityError('Compiler native task/configuration original changed')
    if value['implementation'] != config['execution_interface']:
        raise q.IntegrityError('Compiler implementation differs from its frozen interface')
    declared = [item for item in value['implementation'].get('public_capabilities', [])
                if item['key'] == 'implementation_authoring']
    if len(declared) != 1 or value['contract'] != declared[0]:
        raise q.IntegrityError('Compiler public contract changed')
    if any(value['capability'][key] != value['capability_source'][key] for key in ('size_bytes',)):
        raise q.IntegrityError('Compiler retained capability differs from its operator original')
    q._read(root, value['capability'])
    if 'code_version' in value:
        _verify_compiler_code(root, job, ref, value, verify_pin=not controls_only)
    if controls_only:
        seen = set()
        for item in value['inputs']:
            original = resolve_native_original(root, item['original'], compiler_job=job, controls_only=True)
            if (item['semantic_name'] in seen or item['alias'] != original['path']
                    or type(item['size_bytes']) is not int or item['size_bytes'] != original['size_bytes']
                    or not isinstance(item['media_type'], str)):
                raise q.IntegrityError('Compiler input alias/metadata changed')
            seen.add(item['semantic_name'])
        if 'code_version' not in value:
            author = value['author']; origin = Path(value['author_origin']['path'])
            if (not origin.is_relative_to(c.workspace(root, step))
                    or author['size_bytes'] != value['author_origin']['size_bytes']):
                raise q.IntegrityError('Compiler author snapshot differs from its declared source')
            if value.get('author_input') is not None:
                original = resolve_native_original(root, value['author_input'], compiler_job=job, controls_only=True)
                if origin != c.workspace(root, step) / original['path'] or author['size_bytes'] != original['size_bytes']:
                    raise q.IntegrityError('Compiler author input differs from its exact bound original')
        if material is not None:
            private = type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1
            if private:
                path = Path(value['output']['path']).parent / 'client.json'
                if client_binding != q._ref(root, path):
                    raise q.IntegrityError('Compiler client binding is not its sealed original')
                binding = q._read(root, client_binding)
                if (set(binding) != {'client', 'public_path', 'package'}
                        or binding['client'] != dict(worker_implementation_version=1, implementation_ref=path.parent.name)
                        or binding['package'] != value['output']
                        or binding['public_path'] != material.get('original_source', material['source'])
                        or not _private_compile_output(root, job, config, Path(value['output']['path']))):
                    raise q.IntegrityError('Compiler client changed its exact material/package binding')
            elif client_binding is not None:
                binding = q._read(root, client_binding); grant = binding.get('legacy_authorization') or {}
                if (grant.get('compiler') != ref or grant.get('material') != material
                        or binding.get('package') != value['output']
                        or binding.get('public_path') != material.get('original_source', material['source'])):
                    raise q.IntegrityError('Legacy compiler client changed its original authorization binding')
            elif any(value['output'].get(key) != item for key, item in dict(
                    path=material.get('original_source', material['source']), size_bytes=material['size_bytes']).items()):
                raise q.IntegrityError('Compiler output is not the exact sealed material')
        return value
    if 'code_version' not in value:
        q._read(root, value['author'])
        author_origin = Path(value['author_origin']['path'])
        if (not author_origin.is_relative_to(c.workspace(root, step))
                or author_origin.resolve() != author_origin
                or any(value['author'][key] != value['author_origin'][key] for key in ('size_bytes',))):
            raise q.IntegrityError('Compiler author snapshot differs from its declared source')
        author_input = value.get('author_input')
        if author_input is not None:
            author_material = resolve_native_original(root, author_input, compiler_job=job)
            if (author_origin != c.workspace(root, step) / author_material['path']
                    or any(value['author'][key] != author_material[key] for key in ('size_bytes',))):
                raise q.IntegrityError('Compiler author input differs from its exact bound original')
        elif author_origin.is_relative_to(c.workspace(root, step) / 'inputs'):
            raise q.IntegrityError('Compiler author input has no declared original contribution')
    seen = set()
    for item in value['inputs']:
        if item['semantic_name'] in seen:
            raise q.IntegrityError('Compiler input slots are ambiguous')
        seen.add(item['semantic_name'])
        source = resolve_native_original(root, item['original'], compiler_job=job)
        if (item['alias'] != source['path'] or type(item['size_bytes']) is not int or item['size_bytes'] != source['size_bytes']
                or not isinstance(item['media_type'], str)):
            raise q.IntegrityError('Compiler input alias/metadata changed')
    q._check({key:value['output'][key] for key in ('path','size_bytes')})
    if 'file_stat' in value['output']:
        from . import experiments as e
        if e.proof(value['output']['path'])!=value['output']:
            raise q.IntegrityError('Compiler output original changed')
    private_output = type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1
    if private_output:
        output = Path(value['output']['path'])
        if not _private_compile_output(root, job, config, output) or output.resolve() != output:
            raise q.IntegrityError('Compiler output escaped its exact host namespace')
    if material is not None:
        if private_output:
            path = Path(value['output']['path']).parent / 'client.json'
            if client_binding is None or client_binding != q._ref(root, path):
                raise q.IntegrityError('Compiler client binding is not its sealed original')
            binding = q._read(root, client_binding)
            expected = dict(worker_implementation_version=1, implementation_ref=path.parent.name)
            source = Path(material.get('original_source',material['source']))
            if (not isinstance(binding, dict) or set(binding) != {'client', 'public_path', 'package'}
                    or not isinstance(binding['client'], dict)
                    or type(binding['client'].get('worker_implementation_version')) is not int
                    or binding['client'] != expected or binding['package'] != value['output']
                    or binding['public_path'] != str(source) or not source.is_relative_to(c.workspace(root, step) / 'artifacts')
                    or _implementation_client(material, root=root) != expected):
                raise q.IntegrityError('Compiler client differs from its exact scientific material and private package')
            from . import originals
            originals.material_path(root, material)
        elif client_binding is not None:
            binding, _ = verify_legacy_author_client(root, job, client_binding)
            if (binding['legacy_authorization']['compiler'] != ref or binding['public_path'] != material.get('original_source',material['source'])
                    or _implementation_client(material, root=root) != binding['client']):
                raise q.IntegrityError('Legacy compiler client is not the exact sealed scientific material')
            from . import originals
            originals.material_path(root, material)
        elif any(value['output'].get(key)!=item for key,item in dict(
                path=material.get('original_source',material['source']),size_bytes=material['size_bytes']).items()):
            raise q.IntegrityError('Compiler output is not the exact sealed material')
    return value


def _original_input(source, original, compiler_job, *, controls_only=False):
    from . import originals
    matches = [row for row in _bound(compiler_job) if row['path'] == original.get('material_path')]
    if len({json.dumps(row, sort_keys=True) for row in matches}) != 1:
        raise q.IntegrityError('Original compiler input is missing or ambiguous')
    material = matches[0]
    step = original.get('producer_step')
    if type(step) is not int or not 0 < step < compiler_job['step']:
        raise q.IntegrityError('Original compiler input crossed its producer boundary')
    seal_ref = original['producer_seal']
    owner = originals.dependency(source, step, seal_ref=seal_ref)
    seal = c.read(originals.resolve_ref(owner, seal_ref))
    if (original.get('instance_id') != owner.origin_identity['goal_id']
            or seal_ref['path'] != f'native-evidence/{step:03d}/result-identity.json'
            or any(original.get(field) != material[field] for field in ('size_bytes',))):
        raise q.IntegrityError('Original compiler input changed its exact producer/material')
    _sealed_material(Path(owner.original_root), seal, material, source=owner, controls_only=controls_only)
    return material


def verify_original_compilation(source, ref, *, material=None, client_binding=None, controls_only=False):
    """The same compiler ownership relations interpreted at the original root."""
    from . import originals
    source = originals.source_for_ref(source, ref)
    def read_ref(value):
        owner = originals.source_for_ref(source, value)
        return c.read(originals.resolve_ref(owner, value))
    value = read_ref(ref)
    job = read_ref(value['job'])
    step = job['step']
    source = originals.at_step(source, step)
    root = Path(source.original_root)
    if (value.get('version') != 1 or type(value['version']) is not int or job['role'] != 'W'
            or value['job']['path'] != f'jobs/{step:03d}.json'
            or Path(ref['path']).parent.parent != Path(f'native-evidence/{step:03d}/compiled-implementations')):
        raise q.IntegrityError('Original compiler has no exact W owner')
    job_ref = originals.original_ref(source, value['job']['path'])
    if any(value['job'][field] != job_ref[field] for field in ('size_bytes',)):
        raise q.IntegrityError('Original compiler task identity changed')
    config = read_ref(value['configuration'])
    attempt = config['native_attempt']['number']
    if type(attempt) is not int or not 0 <= attempt < 10000:
        raise q.IntegrityError('Original compiler native attempt changed')
    record = f'native-evidence/{step:03d}' + (f'/continuations/{attempt:03d}' if attempt else '')
    expected_config = f'host-runtime/experiment-control/{step:03d}/interfaces/{attempt:03d}.json'
    if (value['configuration']['path'] != expected_config or value['native_intent']['path'] != record + '/native-intent.json'
            or read_ref(value['native_intent'])['task_ref'] != dict(path=str(root/job_ref['path']))
            or config['scope']['step'] != step or config['scope']['job_ref'] != dict(path=str(root/job_ref['path']))
            or value['implementation'] != config['execution_interface']):
        raise q.IntegrityError('Original compiler lost its frozen native configuration')
    declarations = [item for item in value['implementation'].get('public_capabilities', []) if item['key'] == 'implementation_authoring']
    if len(declarations) != 1 or value['contract'] != declarations[0]:
        raise q.IntegrityError('Original compiler capability contract changed')
    if any(value['capability'][field] != value['capability_source'][field] for field in ('size_bytes',)):
        raise q.IntegrityError('Original compiler capability snapshot changed')
    originals.resolve_ref(originals.source_for_ref(source, value['capability']), value['capability'])
    if 'code_version' in value:
        holder=source.storage_root.parents[1] if source.inherited else root
        _verify_compiler_code(root, job, ref, value, verify_pin=not controls_only, git_holder=holder)
    if controls_only:
        seen = set()
        for item in value['inputs']:
            original = _original_input(source, item['original'], job, controls_only=True)
            if (item['semantic_name'] in seen or item['alias'] != original['path']
                    or type(item['size_bytes']) is not int or item['size_bytes'] != original['size_bytes']
                    or not isinstance(item['media_type'], str)):
                raise q.IntegrityError('Original compiler input alias or metadata changed')
            seen.add(item['semantic_name'])
        if 'code_version' not in value:
            author = value['author']; origin = Path(value['author_origin']['path'])
            if not origin.is_relative_to(source.logical_workspace) or author['size_bytes'] != value['author_origin']['size_bytes']:
                raise q.IntegrityError('Original compiler author snapshot changed its source')
            if value.get('author_input') is not None:
                original = _original_input(source, value['author_input'], job, controls_only=True)
                if origin != source.logical_workspace / original['path'] or author['size_bytes'] != original['size_bytes']:
                    raise q.IntegrityError('Original compiler author input changed')
        if material is not None and job.get('worker_view_version') == 1:
            expected = Path(value['output']['path']).parent / 'client.json'
            if client_binding is None or client_binding['path'] != expected.relative_to(root).as_posix():
                raise q.IntegrityError('Original compiler client has no exact sealed binding')
            binding = read_ref(client_binding)
            if (set(binding) != {'client', 'public_path', 'package'}
                    or binding['client'] != dict(worker_implementation_version=1, implementation_ref=expected.parent.name)
                    or binding['package'] != value['output']
                    or binding['public_path'] != material.get('original_source', material['source'])
                    or not _private_compile_output(root, job, config, Path(value['output']['path']), source=source)):
                raise q.IntegrityError('Original compiler client changed its reviewed material/package binding')
        elif material is not None and client_binding is not None:
            binding = read_ref(client_binding)
            grant = binding.get('legacy_authorization') or {}
            original_material = grant.get('material') or {}
            expected_compiler = {key: value for key, value in ref.items() if key != 'origin_identity'}
            if (grant.get('compiler') != expected_compiler or binding.get('package') != value['output']
                    or binding.get('public_path') != material.get('original_source', material['source'])
                    or any(value['output'].get(key) != item for key, item in dict(
                        path=original_material.get('original_source', original_material.get('source')),
                        size_bytes=original_material.get('size_bytes')).items())):
                raise q.IntegrityError('Legacy compiler client is not the exact sealed scientific material')
        elif material is not None and any(value['output'].get(key) != item for key, item in dict(
                path=material.get('original_source', material['source']), size_bytes=material['size_bytes']).items()):
            raise q.IntegrityError('Original compiler output is not its exact sealed material')
        return value
    if 'code_version' not in value:
        originals.resolve_ref(originals.source_for_ref(source, value['author']), value['author'])
        author = Path(value['author_origin']['path'])
        if (not author.is_relative_to(source.logical_workspace)
                or any(value['author'][field] != value['author_origin'][field] for field in ('size_bytes',))):
            raise q.IntegrityError('Original compiler author snapshot changed its source')
        if value.get('author_input') is not None:
            author_material = _original_input(source, value['author_input'], job)
            if author != source.logical_workspace / author_material['path'] or any(
                    value['author'][field] != author_material[field] for field in ('size_bytes',)):
                raise q.IntegrityError('Original compiler author input changed')
        elif author.is_relative_to(source.logical_workspace / 'inputs'):
            raise q.IntegrityError('Original compiler author input has no declared producer')
    seen = set()
    for item in value['inputs']:
        original = _original_input(source, item['original'], job)
        if (item['semantic_name'] in seen or item['alias'] != original['path']
                or type(item['size_bytes']) is not int or item['size_bytes'] != original['size_bytes']
                or not isinstance(item['media_type'], str)):
            raise q.IntegrityError('Original compiler input alias or metadata changed')
        seen.add(item['semantic_name'])
    output = value['output']
    output_owner, output_path = originals.material_owner(source, dict(output, source=output['path']))
    actual_output=originals.resolve_ref(output_owner, dict(output, path=output_path))
    if 'file_stat' in output and not source.inherited:
        from . import experiments as e
        if e.proof(actual_output)['file_stat']!=output['file_stat']:
            raise q.IntegrityError('Compiler output original changed')
    private = job.get('worker_view_version') == 1
    if private and not _private_compile_output(root, job, config, Path(output['path']), source=source):
        raise q.IntegrityError('Original compiler output escaped its private namespace')
    if material is not None:
        material_owner, material_path = originals.material_owner(source, material)
        originals.resolve_ref(material_owner, dict(material, path=material_path))
        if private:
            expected_path = Path(output['path']).parent / 'client.json'
            expected_relative = expected_path.relative_to(root).as_posix()
            if client_binding is None or client_binding['path'] != expected_relative:
                raise q.IntegrityError('Original compiler client has no exact sealed binding')
            binding = read_ref(client_binding)
            expected = dict(worker_implementation_version=1, implementation_ref=expected_path.parent.name)
            if (set(binding) != {'client', 'public_path', 'package'} or binding['client'] != expected
                    or binding['package'] != output or binding['public_path'] != material.get('original_source',material['source'])
                    or not Path(material.get('original_source',material['source'])).is_relative_to(source.logical_workspace / 'artifacts')
                    or _implementation_client(material, source=source) != expected):
                raise q.IntegrityError('Original compiler client changed its reviewed material/package')
        elif client_binding is not None:
            binding, _ = verify_legacy_author_client(root, job, client_binding, source=source)
            expected_compiler = {key:value for key,value in ref.items() if key != 'origin_identity'}
            if (binding['legacy_authorization']['compiler'] != expected_compiler
                    or binding['public_path'] != material.get('original_source',material['source'])
                    or _implementation_client(material, source=source) != binding['client']):
                raise q.IntegrityError('Legacy compiler client is not the exact sealed scientific material')
        elif any(output.get(key)!=item for key,item in dict(
                path=material.get('original_source',material['source']),size_bytes=material['size_bytes']).items()):
            raise q.IntegrityError('Original compiler output is not its exact sealed material')
    return value


def validate_original(source, job, seal, *, controls_only=False):
    rows = seal.get('compiled_implementations', [])
    for row in rows:
        if not any({key: value for key, value in item.items() if key != 'file_stat'} == row['material']
                for item in seal['materials']):
            raise q.IntegrityError('Original compiler seal names an unavailable material')
        verify_original_compilation(source, row['compiler'], material=row['material'],
            client_binding=row.get('client_binding'), controls_only=controls_only)


def capture(root, job, materials):
    """Only delivered compiler outputs enter this node's existing seal."""
    root = Path(root).resolve(); result = []; seen = set()
    for path in sorted(_directory(root, job['step']).glob('*/compiler.json')):
        ref = q._ref(root, path); value = q._read(root, ref)
        client_binding = None
        if type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1:
            client_path = Path(value['output']['path']).parent / 'client.json'
            if not client_path.exists():
                continue  # An unpublished compiler result is not a delivered material.
            client_binding = q._ref(root, client_path)
            client = q._read(root, client_binding)
            source = client.get('public_path')
        else:
            source = value['output']['path']
            clients = sorted(path.parent.glob('author-client-*.json'))
            matching = [item for item in clients if c.read(c.regular(item)).get('public_path') in {row.get('original_source',row['source']) for row in materials}]
            if matching:
                client_binding = q._ref(root, matching[-1])
                client, _ = verify_legacy_author_client(root, job, client_binding)
                source = client['public_path']
                if any(row.get('original_source',row['source']) == value['output']['path'] for row in materials):
                    raise q.IntegrityError('Legacy machine package must remain separate from its new scientific client')
        matches = [row for row in materials if row.get('original_source',row['source']) == source]
        if not matches:
            continue
        if len(matches) != 1 or matches[0]['source'] in seen:
            raise q.IntegrityError('Compiled material has ambiguous provenance')
        material = c._public_materials(root, matches)[0]
        verify_compilation(root, ref, material=material, client_binding=client_binding); seen.add(material['source'])
        result.append(dict(material=material, compiler=ref,
            **(dict(client_binding=client_binding) if client_binding is not None else {})))
    from . import originals
    for saved in materials:
        row = {key: value for key, value in saved.items() if key != 'file_stat'}
        if row['source'] in seen or _implementation_client(row, root=root) is None:
            continue
        parts = Path(row['path']).parts
        if len(parts) < 4 or parts[:2] != ('inputs', 'parents') or not parts[2].isdecimal():
            raise q.IntegrityError('Native implementation client has no exact sealed compiler provenance')
        if not originals.inherited(root, int(parts[2])):
            raise q.IntegrityError('Native implementation client has no exact sealed compiler provenance')
        source = originals.node(root, int(parts[2]))
        seal = c.read(originals.source_record(source, 'result-identity.json'))
        matches = [item for item in seal.get('compiled_implementations', []) if item['material'] == row]
        if len(matches) != 1:
            raise q.IntegrityError('Inherited implementation client lacks its exact sealed compiler')
        item = matches[0]
        compiler = dict(item['compiler'], origin_identity=item['compiler'].get('origin_identity', source.origin_identity))
        client = item.get('client_binding')
        if client is not None:
            client = dict(client, origin_identity=client.get('origin_identity', source.origin_identity))
        verify_compilation(root, compiler, material=row, client_binding=client)
        result.append(dict(material=row, compiler=compiler, **(dict(client_binding=client) if client is not None else {})))
    return result


def compiled_payload(root, material, *, source=None):
    """Resolve a sealed science client to its separately bound private package."""
    from . import originals
    if source is not None:
        if _implementation_client(material, source=source) is None:
            identity = dict(path=material['source'], size_bytes=material['size_bytes'])
            owner, path = originals.material_owner(source, material)
            originals.resolve_ref(owner, dict(identity, path=path))
            return identity
        native = _producer(root, material, source=source)
        owner = originals.dependency(source, native['producer_step'], seal_ref=native['producer_seal'])
        seal = q._read(root, native['producer_seal'], source=owner)
        found = [row for row in seal.get('compiled_implementations', []) if row['material'] == material]
        if len(found) != 1:
            raise q.IntegrityError('Original native client has no exact sealed compiler')
        return verify_compilation(root, found[0]['compiler'], material=material,
            client_binding=found[0].get('client_binding'), source=owner)['output']
    try:
        if _implementation_client(material, root=root) is None:
            identity = q._identity(originals.material_path(root, material))
            if any(identity[field] != material[field] for field in ('size_bytes',)):
                raise q.IntegrityError('Execution material differs from its exact source')
            return identity
    except q.IntegrityError:
        raise
    except ValueError as exc:
        raise q.IntegrityError('Execution material differs from its exact source') from exc
    original = _producer(Path(root).resolve(), material)
    seal = q._read(root, original['producer_seal'])
    found = [row for row in seal.get('compiled_implementations', []) if row['material'] == material]
    if len(found) != 1:
        raise q.IntegrityError('Native implementation client needs its exact sealed compiler provenance')
    value = verify_compilation(root, found[0]['compiler'], material=material,
                               client_binding=found[0].get('client_binding'))
    output = value['output']
    source = originals.node(root, original['producer_step'])
    if source.inherited:
        owner, path = originals.material_owner(source, dict(output, source=output['path']))
        actual = originals.resolve_ref(owner, dict(output, path=path))
        return dict(output, path=str(actual))
    return output


def validate_seal(root, job, seal, *, source=None):
    if source is not None:
        return validate_original(source, job, seal, controls_only=True)
    for row in seal.get('compiled_implementations', []):
        if not any({key: value for key, value in item.items() if key != 'file_stat'} == row['material']
                for item in seal['materials']):
            raise q.IntegrityError('Sealed compiler names an unavailable material')
        verify_compilation(root, row['compiler'], material=row['material'],
            client_binding=row.get('client_binding'), controls_only=True)


def execution_input_coverage(root, job, subject):
    """Prove coverage by existing selected/compiler edges, without adding edges."""
    root = Path(root).resolve()
    seal = q._read(root, subject['producer_seal'])
    selected = [row for row in job.get('selected_inputs', [])
        if row['origin_step'] == subject['producer_step'] and row['material'] == subject['material']
        and row['origin_result_ref'] == seal['result_ref']]
    if not selected:
        return None
    found = [row for row in seal.get('compiled_implementations', []) if row['material'] == subject['material']]
    if len(found) != 1:
        return None
    compiler = verify_compilation(root, found[0]['compiler'], material=subject['material'],
                                  client_binding=found[0].get('client_binding'))
    compiler_job = q._read(root, compiler['job'])

    def covers(owner, originals):
        if owner.get('source_provenance') is None:
            return False
        q.validate_inputs(root, owner)
        edges = q._read(root, owner['source_provenance'])['edges']
        return all(any(edge['kind'] == 'producer' and edge['step'] == original['producer_step']
            and edge['seal'] == original['producer_seal'] for edge in edges) for original in originals)

    if not covers(job, [subject]):
        return None
    originals = [row['original'] for row in compiler['inputs']]
    if compiler.get('author_input') is not None:
        originals.append(compiler['author_input'])
    # These same originals are already traversed by authors(compiler producer).
    # verify_compilation proves their exact membership in compiler_job.
    if not covers(compiler_job, originals):
        return None
    return found[0]['compiler']


def _local_source(root, job, item, authoring, *, source=None, logical=None):
    """Exact aliases first; remaining declared authoring bytes belong to this W."""
    path = Path(item['path']); work = c.workspace(root, job['step'])
    matches = {}
    for material in job['global_materials']:
        if (logical==work/material['path'] if logical is not None else path in {root/material['source'],work/material['path']}):
            origin = dict(kind='global', material=material)
            matches[json.dumps(origin, sort_keys=True)] = origin
    for material in _bound(job):
        if (logical==work/material['path'] if logical is not None else path in {Path(material['source']),work/material['path']}):
            origin = dict(kind='producer', original=_producer(root, material, source=source))
            matches[json.dumps(origin, sort_keys=True)] = origin
    if len(matches) > 1:
        raise q.IntegrityError('Local input has ambiguous exact origins')
    if matches:
        origin = next(iter(matches.values()))
        material = origin.get('material', origin.get('original'))
        if any(item[key] != material[key] for key in ('size_bytes',)):
            raise q.IntegrityError('Local read copy differs from its exact original')
        return origin
    if authoring is not None and path.is_relative_to(authoring):
        return dict(kind='author', step=job['step'])
    return dict(kind='unknown')


def _local_authoring(work, directory):
    directory = Path(directory)
    try:
        relative = directory.relative_to(work / 'experiments')
    except ValueError:
        return None
    return directory if len(relative.parts) == 1 and directory.resolve() == directory else None


def local_input_sources(config, interface, payload, metadata):
    """Called by the actual adapter before acceptance; no scientific inference."""
    from . import experiments as e
    fixed_code = 'code_version' in metadata
    if (not isinstance(interface, dict) or interface.get('version') != e.VERSION
            or ('experiment_directory' not in metadata if fixed_code else 'entry' not in metadata)):
        return None
    root = experiment_client.root_for_configuration(config, allow_standalone=True)
    if root is None:
        return None  # Standalone commands have no current scientific-task owner.
    step = config['scope']['step']; work = c.workspace(root, step)
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    attempt = config.get('native_attempt') or {}
    if type(attempt.get('number')) is not int:
        return None
    projection = root / 'host-runtime/experiment-control' / f'{config["scope"]["step"]:03d}' / 'interfaces' / f'{attempt["number"]:03d}.json'
    frozen = q._read(root, q._ref(root, projection))
    from .protocol import stage_scope
    if (job['role'] != 'W' or config['scope'] != stage_scope(root, job)
            or frozen != dict(config, execution_interface=interface)):
        raise q.IntegrityError('Local authoring differs from its frozen host task')
    intent = q._ref(root, execution.record(root, step, attempt['number']) / 'native-intent.json')
    if q._read(root, intent)['task_ref'] != c.record_ref(root / 'jobs' / f'{step:03d}.json'):
        raise q.IntegrityError('Local authoring has no exact native intent')
    directory = Path(metadata['experiment_directory']); authoring = _local_authoring(work, directory)
    if fixed_code:
        try:
            e._code_version_binding(dict(config, execution_interface=interface),
                dict(metadata, scope=config['scope']))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise q.IntegrityError('Local input view lost its host-owned code selection') from error
        names = {str((work / item['path']).relative_to(directory))
            for item in experiment_client._input_rows(job)
            if (work / item['path']).is_relative_to(directory)
                and not (work / item['path']).is_relative_to(directory / 'artifacts')}
        if (authoring is None or metadata.get('cwd') != str(directory)
                or not isinstance(metadata.get('snapshot_files'), list)
                or {item.get('name') for item in metadata['snapshot_files']} != names
                or len(metadata['snapshot_files']) != len(names)):
            raise q.IntegrityError('Local code input view changed its frozen scientific inventory')
        for item in metadata['snapshot_files']:
            reference = experiment_client.input_reference(config, directory / item['name'])
            if (set(item) != {'name','path','size_bytes','file_stat'}
                    or any(item.get(key) != reference[key] for key in ('path','size_bytes','file_stat'))):
                raise q.IntegrityError('Local code input view requires exact frozen scientific inputs')
    return dict(job=q._ref(root, root / 'jobs' / f'{step:03d}.json'), native_intent=intent,
        configuration=q._ref(root, projection), prepared=payload.model_dump(mode='json'),
        files=[dict(name=item['name'], origin=_local_source(root, job, item, authoring,
            logical=directory/item['name']))
               for item in metadata['snapshot_files']])


def _local_source_metadata(root, job, accepted, view, *, source=None):
    """Read saved Local contributor relations without reopening retained bytes."""
    from . import code_versions
    fixed = 'code_version' in accepted
    directory = Path(accepted['experiment_directory'])
    authoring = _local_authoring(c.workspace(root, job['step']), directory)
    files = accepted.get('snapshot_files')
    if not isinstance(view['files'], list) or not isinstance(files, list) or len(view['files']) != len(files):
        raise q.IntegrityError('Local input view changed its frozen file inventory')
    if fixed:
        code_versions._version(accepted['code_version'])
    else:
        records = Path(accepted['config_record']['record_dir'])
        snapshot = records / 'calls' / accepted['call_id']
        if (accepted.get('cwd') != str(snapshot / 'inputs')
                or accepted['source_script'] != str(directory / accepted['entry'])
                or accepted['script']['path'] != str(snapshot / 'source.py')):
            raise q.IntegrityError('Local input view changed its original call binding')
    if job.get('source_provenance') is None:return None
    edges = q._read(root, job['source_provenance'], source=source)['edges']
    contributions = []; seen = set(); complete = True
    work = c.workspace(root, job['step'])
    for declared, item in zip(view['files'], files):
        name = item['name']; relative = Path(name)
        if (declared.get('name') != name or not relative.parts or relative.is_absolute()
                or '..' in relative.parts or relative.as_posix() != name or name in seen):
            raise q.IntegrityError('Local input view changed a declared scientific file')
        seen.add(name); origin = declared['origin']
        path = directory / relative if fixed else Path(item['path'])
        if origin.get('kind') == 'producer':
            original = origin['original']
            material = resolve_native_original(root, original, compiler_job=job, source=source, controls_only=True)
            if (path not in {work / material['path'], Path(material['source'])}
                    or item['size_bytes'] != material['size_bytes']
                    or not any(edge['kind'] == 'producer' and edge['step'] == original['producer_step']
                        and edge['seal'] == original['producer_seal'] for edge in edges)):
                raise q.IntegrityError('Local input view changed its frozen producer relation')
            seal = q._read(root, original['producer_seal'], source=source)
            contributions.append(dict(origin_step=original['producer_step'], origin_result_ref=seal['result_ref'], material=material))
        elif origin.get('kind') == 'global':
            material = origin['material']
            if (material not in job['global_materials'] or item['size_bytes'] != material['size_bytes']
                    or path not in {work / material['path'], root / material['source']}
                    or not any(edge['kind'] == 'global' and edge['material'] == material for edge in edges)):
                raise q.IntegrityError('Local input view changed its frozen global relation')
        elif origin.get('kind') == 'author':
            if origin.get('step') != job['step'] or authoring is None or not path.is_relative_to(authoring):
                raise q.IntegrityError('Local input view changed its original author')
            if fixed:complete = False
        elif origin == {'kind': 'unknown'}:
            complete = False
        else:
            raise q.IntegrityError('Local input view has an unsupported frozen origin')
    if not fixed and accepted['entry'] not in seen:
        raise q.IntegrityError('Local input view omitted its entrypoint')
    return contributions if complete else None


def validate_local_input_sources(root, job, accepted, *, source=None, controls_only=False):
    """Only the frozen view/retained bytes; never construct or call an adapter."""
    from . import experiments as e
    original = accepted.get('input_sources')
    if original is None:
        return None
    expected = dict(key=e.INPUT_SOURCES_KEY,kind='source_provenance',schema_id=e.INPUT_SOURCES_SCHEMA,
        payload_schema_version=1,media_type='application/json',content=e.encoded(e.INPUT_SOURCES_CONTRACT).decode('utf-8'),
        size_bytes=len(e.encoded(e.INPUT_SOURCES_CONTRACT)))
    if original['contract'] != expected:
        return None  # Another contract has no implicit interpretation here.
    declared=[item for item in accepted['interface'].get('public_capabilities',[]) if item['key']==e.INPUT_SOURCES_KEY]
    if (len(declared)!=1 or any(declared[0].get(key)!=expected[key] for key in
            ('key','kind','schema_id','payload_schema_version','media_type','size_bytes'))):
        raise q.IntegrityError('Local input view has no unique frozen source capability')
    view = original['view']
    if view is None:
        return None
    root = Path(source.original_root) if source is not None else Path(root).resolve()
    work = c.workspace(root, job['step'])
    config = q._read(root, view['configuration'], source=source); attempt = accepted.get('native_attempt') or {}
    projection = root / 'host-runtime/experiment-control' / f'{accepted["scope"]["step"]:03d}' / 'interfaces' / f'{attempt["number"]:03d}.json'
    native_record = (root / f'native-evidence/{job["step"]:03d}' /
        f'continuations/{attempt["number"]:03d}' if attempt['number'] else root / f'native-evidence/{job["step"]:03d}')
    if not controls_only:native_record = _native_record(root, job['step'], attempt['number'], source=source)
    if (set(view) != {'job','native_intent','configuration','prepared','files'}
            or view['job'] != q._ref(root, root / 'jobs' / f'{job["step"]:03d}.json', source=source)
            or q._read(root, view['job'], source=source) != job or job['role'] != 'W'
            or view['configuration'] != q._ref(root, projection, source=source)
            or config['scope'] != accepted['scope'] or config['native_attempt'] != attempt
            or config['execution_interface'] != accepted['interface']
            or accepted.get('local_observation_version') != 1
            or view['prepared'] != accepted['prepared']
            or view['native_intent'] != q._ref(root, native_record / 'native-intent.json', source=source)
            or q._read(root, view['native_intent'], source=source)['task_ref'] != dict(path=str(root/view['job']['path']))
            or accepted['config_record'] != {key:value for key,value in config.items() if key != 'execution_interface'}):
        raise q.IntegrityError('Local input view lost its exact host owner')
    if controls_only:
        return _local_source_metadata(root, job, accepted, view, source=source)
    if 'code_version' in accepted:
        from . import code_versions
        if source is not None and source.inherited:
            raise q.IntegrityError('Inherited Local code requires its exact NodeSource Git holder')
        try:
            if config['scope']['step'] != job['step'] or e._code_version_binding(config, accepted) != root:
                raise ValueError('Local code belongs to another original root')
            code_versions.verify(root, step=job['step'], call_id=accepted['call_id'],
                code_version=accepted['code_version'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise q.IntegrityError('Local code version lost its exact selected source or original pin') from error
        directory = Path(accepted['experiment_directory']); authoring = _local_authoring(work, directory)
        if (authoring is None or accepted.get('cwd') != str(directory)
                or not isinstance(view['files'], list)
                or not isinstance(accepted.get('snapshot_files'), list)
                or len(view['files']) != len(accepted['snapshot_files'])):
            raise q.IntegrityError('Local code input view changed its scientific inventory')
        if source is None:q.validate_inputs(root, job)
        else:q._original_inputs(source, job)
        edges = q._read(root, job['source_provenance'], source=source)['edges']
        names = {str((work / item['path']).relative_to(directory))
            for item in experiment_client._input_rows(job)
            if (work / item['path']).is_relative_to(directory)
                and not (work / item['path']).is_relative_to(directory / 'artifacts')}
        seen = set(); complete = True; contributions = []
        for declared, item in zip(view['files'], accepted['snapshot_files']):
            name = item['name']; relative = Path(name)
            if (set(declared) != {'name','origin'} or set(item) != {'name','path','size_bytes','file_stat'}
                    or relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                    or not relative.parts or name in seen or name not in names or declared['name'] != name):
                raise q.IntegrityError('Local code input view changed a declared scientific file')
            seen.add(name)
            retained = {key:item[key] for key in ('path','size_bytes','file_stat')}
            q._check({key:retained[key] for key in ('path','size_bytes')}, source=source)
            if e.proof(retained['path'])!=retained:
                raise q.IntegrityError('Local code input original changed')
            reference = experiment_client.input_reference(config, directory / relative)
            if retained != reference:
                raise q.IntegrityError('Local code input lost its exact scientific binding')
            origin = declared['origin']
            if origin != _local_source(root,job,item,authoring,source=source,logical=directory/relative):
                raise q.IntegrityError('Local code input source differs from its frozen exact origin')
            if origin['kind'] == 'producer':
                native = origin['original']; material = resolve_native_original(root, native, compiler_job=job, source=source)
                if not any(edge['kind'] == 'producer' and edge['step'] == native['producer_step']
                        and edge['seal'] == native['producer_seal'] for edge in edges):
                    raise q.IntegrityError('Local code input producer is absent from original source edges')
                seal = q._read(root, native['producer_seal'], source=source)
                contributions.append(dict(origin_step=native['producer_step'], origin_result_ref=seal['result_ref'], material=material))
            elif origin['kind'] == 'global':
                if not any(edge['kind'] == 'global' and edge['material'] == origin['material'] for edge in edges):
                    raise q.IntegrityError('Local code global input is absent from original source edges')
            else:
                complete = False
        if seen != names:
            raise q.IntegrityError('Local code input view omitted a frozen scientific file')
        return contributions if complete else None
    directory = Path(accepted['experiment_directory']); authoring = _local_authoring(work, directory)
    if source is not None:
        records = (root / 'host-runtime/experiment-control' / f'{accepted["scope"]["step"]:03d}' / 'records'
            if type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1 else work / 'work/lineage')
    else:
        records = experiment_client.record_directory(root, job['step'])
    snapshot = records / 'calls' / accepted['call_id']
    if (accepted.get('cwd') != str(snapshot / 'inputs')
            or Path(accepted['source_script']) != directory / accepted['entry']
            or accepted['script']['path'] != str(snapshot / 'source.py')
            or len(view['files']) != len(accepted['snapshot_files'])):
        raise q.IntegrityError('Local input view changed its snapshot inventory')
    q._check(accepted['script'], source=source); complete = True; contributions = []
    if source is None:q.validate_inputs(root, job)
    else:q._original_inputs(source, job)
    edges = q._read(root, job['source_provenance'], source=source)['edges']
    seen = set()
    for declared, item in zip(view['files'], accepted['snapshot_files']):
        name = item['name']; relative = Path(name)
        if (set(declared) != {'name','origin'} or relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                or name in seen or declared['name'] != name or Path(item['path']) != directory / relative):
            raise q.IntegrityError('Local input view changed a declared file')
        seen.add(name)
        q._check(dict(path=str(snapshot / 'inputs' / relative), size_bytes=item['size_bytes']), source=source)
        origin = declared['origin']
        # Verify the published classification, not a fresh adapter-generated view.
        if origin != _local_source(root, job, item, authoring, source=source):
            raise q.IntegrityError('Local input source differs from its frozen exact origin')
        if origin['kind'] == 'producer':
            native = origin['original']; material = resolve_native_original(root, native, compiler_job=job, source=source)
            if not any(edge['kind'] == 'producer' and edge['step'] == native['producer_step']
                    and edge['seal'] == native['producer_seal'] for edge in edges):
                raise q.IntegrityError('Local input producer is absent from original source edges')
            seal = q._read(root, native['producer_seal'], source=source)
            contributions.append(dict(origin_step=native['producer_step'], origin_result_ref=seal['result_ref'], material=material))
        elif origin['kind'] == 'global':
            if not any(edge['kind'] == 'global' and edge['material'] == origin['material'] for edge in edges):
                raise q.IntegrityError('Local global input is absent from original source edges')
        elif origin['kind'] == 'unknown':
            complete = False
    if accepted['entry'] not in seen:
        raise q.IntegrityError('Local input view omitted its entrypoint')
    return contributions if complete else None


class NativeInputHost:
    """A non-JSON service created only after the original host context is checked."""

    def __init__(self, config):
        from . import execution_authority as authority
        self.root, self.job, self.intent = authority._host_context(config)
        self.config = config

    def compile(self, source, output, *, slots, aliases, capability, contract, builder):
        from . import execution_authority as authority, experiments as e
        from . import shared_originals, storage_policy as storage
        _, remaining=authority._remaining(self.root, self.config)
        context=storage.Deadline(remaining)
        policy=c.settings(self.root,self.job).get('storage_policy')
        work = c.workspace(self.root, self.job['step']); source = Path(source).absolute(); output = Path(output).absolute()
        private_output = type(self.job.get('worker_view_version')) is int and self.job['worker_view_version'] == 1
        if not private_output:
            from . import shared_input_views
            if shared_input_views._binding(self.root,work).exists():
                from .experiments import UnsupportedExecution
                raise UnsupportedExecution('Legacy public compile cannot publish shared originals into writable artifacts after readonly view binding')
        if private_output:
            output_allowed = _private_compile_output(self.root, self.job, self.config, output)
        else:
            output_allowed = output.is_relative_to(work / 'artifacts')
        if (source.resolve() != source or not source.is_relative_to(work)
                or output.resolve() != output or not output_allowed
                or any(parent.is_symlink() for parent in output.parents)):
            raise ValueError('Compile source/output must be original files in this task and its host-owned output namespace'
                             if private_output else 'Compile source/output must be original files in this task and artifacts')
        from . import atomic_files, code_versions
        logical_source=source
        if set(aliases) != {slot['semantic_name'] for slot in slots}:
            raise ValueError('Select one exact bound scientific-file alias for every slot')
        inputs = []
        for slot in slots:
            alias = aliases[slot['semantic_name']]
            found = {json.dumps(row, sort_keys=True): row for row in _bound(self.job) if row['path'] == alias}
            if len(found) != 1:
                raise ValueError('Scientific file alias is missing or ambiguous; choose an exact listed task input')
            material = next(iter(found.values())); original = _producer(self.root, material)
            if original['producer_step'] >= self.job['step']:
                raise ValueError('Compile inputs must be preceding sealed originals')
            inputs.append(dict(slot, alias=alias, original=original, size_bytes=material['size_bytes']))
        if c.output_quota_exceeded(c.settings(self.root, self.job), (item['size_bytes'] for item in inputs)):
            raise ValueError('Scientific inputs exceed the explicit frozen handoff quota')
        attempt = self.config['native_attempt']['number']
        projection = self.root / 'host-runtime/experiment-control' / f'{self.config["scope"]["step"]:03d}' / 'interfaces' / f'{attempt:03d}.json'
        invocation = dict(job=q._ref(self.root, self.root / 'jobs' / f'{self.job["step"]:03d}.json'),
            native_intent=self.intent, configuration=q._ref(self.root, projection),
            implementation=self.config['execution_interface'], contract=contract,
            capability_source=capability, inputs=inputs)
        existing = None
        if output.exists() or output.is_symlink():
            matches=[]
            for receipt in _directory(self.root,self.job['step']).glob('*/compiler.json'):
                value=q._read(self.root,q._ref(self.root,receipt))
                if value.get('output',{}).get('path')==str(output):matches.append((receipt,value))
            if len(matches)!=1:
                raise q.IntegrityError('Existing compiled output has no unique original call')
            receipt, existing = matches[0]
            proof = q._ref(self.root, receipt)
            verify_compilation(self.root, proof)
            if any(existing.get(key) != value for key, value in invocation.items()):
                raise q.IntegrityError('Existing compiled output differs from its original invocation')
        identity = (_compiler_call_id(self.root, self.job['step'], proof)
                    if existing is not None and 'code_version' in existing else uuid.uuid4().hex)
        directory = _directory(self.root, self.job['step']) / identity
        if directory.resolve() != directory:
            raise q.IntegrityError('Compiler original directory changed')
        code_version = None; cleanup_code = False
        try:
            # Existing pre-Git records keep their saved interpretation. A new
            # mutable author must use the explicit current-W code selection.
            selection = (experiment_client.code_selection(self.config, logical_source)
                         if existing is None or 'code_version' in existing else None)
            if selection is not None:
                cleanup_code = True
                code_version = code_versions.capture(self.root, step=self.job['step'], call_id=identity,
                    **selection, context=context)
                if existing is not None:
                    if code_version != existing['code_version']:
                        raise q.IntegrityError('Existing compiled output selected another exact code source')
                else:
                    export = code_versions.materialize(self.root, step=self.job['step'], call_id=identity,
                        code_version=code_version, context=context)
                    source = export / code_version['entry']
            else:
                if existing is not None and 'code_version' in existing:
                    raise q.IntegrityError('Existing compiled output requires its original current-W code selection')
                reference = experiment_client.input_reference(self.config, logical_source)
                source = Path(reference['path'])
                source_identity = {key: reference[key] for key in ('path', 'size_bytes')}
                author_origin = dict(source_identity, path=str(logical_source)); author_input = None
                if logical_source.is_relative_to(work / 'inputs'):
                    found = {json.dumps(row, sort_keys=True): row for row in _bound(self.job)
                             if work / row['path'] == logical_source}
                    if len(found) != 1:
                        raise ValueError('Author input must name one exact frozen scientific document')
                    material = next(iter(found.values())); author_input = _producer(self.root, material)
                    if any(source_identity[key] != material[key] for key in ('size_bytes',)):
                        raise q.IntegrityError('Author input differs from its original scientific document')
            if existing is not None:
                if code_version is None and (existing.get('author_origin') != author_origin
                        or existing.get('author_input') != author_input):
                    raise q.IntegrityError('Existing compiled output differs from its original author source')
                return dict(status='compiled',path=str(output) if private_output else str(output.relative_to(work)),
                    qualification='Unreviewed compiled material; no execution permission')
            source_generation = atomic_files._generation(c.regular(source).lstat())
            authored = source.read_bytes()
            if (atomic_files._generation(source.lstat()) != source_generation
                    or code_version is None and (q._identity(source) != source_identity
                        or len(authored) != source_identity['size_bytes'])):
                raise q.IntegrityError('Compiler author changed while reading its fixed source')
            raw = builder(authored, [{key: item[key] for key in item if key != 'alias'} for item in inputs])
            if type(raw) is not bytes or atomic_files._generation(source.lstat()) != source_generation:
                raise q.IntegrityError('Compiler author original changed during compilation')
            if c.output_quota_exceeded(c.settings(self.root, self.job), [len(raw)]):
                raise ValueError('Compiled package exceeds explicit output quota')
            directory.mkdir(parents=True, exist_ok=True)
            q._check(capability)
            author_binding = dict(code_version=code_version, author_input=None)
            from . import originals
            if code_version is None:
                q._check(source_identity)
                author_path=c._bind_original(source,directory/'author.json',policy=policy,context=context,
                    root=self.root,size_bytes=source_identity['size_bytes'],source_kind='mutable')
                author_ref=q._ref(self.root,author_path)
                originals.resolve_root_ref(self.root,author_ref)
                author_binding = dict(author=author_ref, author_origin=author_origin, author_input=author_input)
            capability_path=c._bind_original(Path(capability['path']),directory/'capability.json',policy=policy,
                context=context,root=self.root,size_bytes=capability['size_bytes'],source_kind='mutable')
            snapshot=q._ref(self.root,capability_path)
            originals.resolve_root_ref(self.root,snapshot)
            if output.exists() or output.is_symlink():
                raise ValueError('Choose a new output; sealed packages cannot be overwritten')
            frozen=shared_originals.publish_bytes(self.root,raw,policy=policy,context=context)
            shared_originals.link(frozen,output,size_bytes=len(raw),root=self.root,policy=policy,context=context)
            value = dict(version=1, **invocation, **author_binding,
                capability=snapshot, output=e.proof(output,context=context))
            proof = q._save(self.root, directory / 'compiler.json', value)
            verify_compilation(self.root, proof)
            return dict(status='compiled', path=str(output) if private_output else str(output.relative_to(work)),
                        qualification='Unreviewed compiled material; no execution permission')
        finally:
            if cleanup_code:
                try:
                    code_versions.cleanup_export(self.root, step=self.job['step'], call_id=identity, context=context)
                except (OSError, ValueError) as error:
                    from . import experiments as e
                    e._diagnostic(error, 'Compiler code export cleanup failed', config=self.config)

    def for_payload(self, payload, resolved_inputs):
        from . import execution_authority as authority
        subject, _, _ = authority._selected_subject(self.root, self.job, payload)
        seal = q._read(self.root, subject['producer_seal'])
        found = [row for row in seal.get('compiled_implementations', []) if row['material'] == subject['material']]
        if len(found) != 1:
            raise ValueError('Native package needs its exact sealed compiler provenance')
        value = verify_compilation(self.root, found[0]['compiler'], material=subject['material'],
                                   client_binding=found[0].get('client_binding'))
        compiler_job = q._read(self.root, value['job'])
        expected = [{key: row[key] for key in row if key != 'alias'} for row in value['inputs']]
        if json.dumps([item.model_dump(mode='json') for item in resolved_inputs], sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise q.IntegrityError('Native package slots differ from their sealed compiler provenance')

        def resolve(item):
            declared = item.model_dump(mode='json')
            matches = [row for row in value['inputs'] if {key: row[key] for key in row if key != 'alias'} == declared]
            if len(matches) != 1:
                raise q.IntegrityError('Native slot differs from its exact reviewed compiler binding')
            return resolve_native_original(self.root, declared['original'], compiler_job=compiler_job)

        def metadata(item):
            material = resolve(item)
            return material['size_bytes'], item.media_type

        @contextmanager
        def open_original(item):
            material = resolve(item)
            from . import originals
            with c.regular(originals.material_path(self.root, material)).open('rb') as stream:
                yield stream

        return metadata, open_original


def _compiler_call_id(root, step, ref):
    path = Path(root) / ref['path']; identity = path.parent.name
    if (re.fullmatch(r'[0-9a-f]{32}', identity) is None
            or path != _directory(root, step) / identity / 'compiler.json'):
        raise q.IntegrityError('Compiler code version has no exact original call')
    return identity



def _verify_compiler_code(root, job, ref, value, *, verify_pin=True, git_holder=None):
    from . import code_versions
    if ('author' in value or 'author_origin' in value or value.get('author_input') is not None):
        raise q.IntegrityError('Compiler code version is mixed with an author snapshot or scientific input')
    identity = _compiler_call_id(root, job['step'], ref)
    version = value['code_version']
    try:
        code_versions._version(version)
        if verify_pin:
            code_versions.verify(root if git_holder is None else git_holder,
                step=job['step'], call_id=identity, code_version=version)
        if (version['selection'] != 'file' or Path(version['logical_root']).parts[0] != 'code'
                or Path(version['entry']).name != version['entry']):
            raise ValueError('Compiler code must select one current-W code entry')
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise q.IntegrityError('Compiler code version lost its exact selected source or original pin') from error
