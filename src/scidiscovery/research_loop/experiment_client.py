"""Readable execution clients resolve to existing, privately verified originals.

This is a default presentation boundary, not protection from same-user access.
"""
from pathlib import Path
import json
import re

VERSION = 1


def record_directory(root, step):
    """Keep new control receipts outside the scientific working directory."""
    from . import contracts as c
    root = Path(root).absolute()
    job_path = root / 'jobs' / f'{step:03d}.json'
    job = c.read(c.regular(job_path))
    if type(job.get('worker_view_version')) is int and job['worker_view_version'] == VERSION:
        return root / 'host-runtime/experiment-control' / f'{step:03d}' / 'records'
    return c.workspace(root, step) / 'work/lineage'


def root_for_configuration(config, *, allow_standalone=False):
    """Resolve only the old or current canonical execution record namespace."""
    from .experiments import IntegrityError
    if not isinstance(config,dict) or not isinstance(config.get('record_dir'),str):
        raise IntegrityError('Execution record namespace is malformed')
    directory = Path(config['record_dir'])
    if (len(directory.parents) >= 5 and directory.parts[-5:-3] == ('project', 'nodes')
            and directory.parts[-2:] == ('work', 'lineage')):
        root = directory.parents[4]
    elif (len(directory.parents) >= 4 and directory.parts[-4:-2] == ('host-runtime', 'experiment-control')
            and directory.name == 'records'):
        root = directory.parents[3]
    else:
        if allow_standalone:return None
        raise IntegrityError('Execution record namespace is unavailable')
    scope=config.get('scope')
    if (not directory.is_absolute() or directory.resolve()!=directory or not isinstance(scope,dict)
            or type(scope.get('step')) is not int or scope['step']<=0):
        raise IntegrityError('Execution record namespace or scientific scope changed')
    if directory != record_directory(root, config['scope']['step']):
        raise IntegrityError('Execution record namespace differs from its frozen scientific task')
    return root


def stub(job, attempt):
    return dict(worker_view_version=VERSION, step=job['step'], attempt=attempt)


def location(path):
    """Only a direct work-directory client can select its canonical task."""
    from .experiments import IntegrityError
    path = Path(path).absolute()
    if (len(path.parents) < 5 or path.resolve() != path or path.parent.name != 'work'
            or path.parents[3].name != 'project' or path.parents[2].name != 'nodes'
            or not path.parents[1].name.isdecimal() or path.suffix != '.json'):
        raise IntegrityError('Execution client requires its canonical task work directory')
    return path.parents[4], int(path.parents[1].name)


def binding_path(root, job, path):
    from . import contracts as c
    return (root / 'host-runtime/experiment-control' / f'{job["step"]:03d}'
            / 'clients' / Path(path).name)


def resolve(path, value):
    from . import contracts as c
    from .experiments import IntegrityError, proof
    from .protocol import stage_scope
    root, step = location(path)
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    if (type(job.get('worker_view_version')) is not int or job['worker_view_version'] != VERSION
            or not isinstance(value, dict) or set(value) != {'worker_view_version', 'step', 'attempt'}
            or type(value.get('worker_view_version')) is not int or value['worker_view_version'] != VERSION
            or type(value.get('step')) is not int or value['step'] != step
            or type(value.get('attempt')) is not int or not 0 <= value['attempt'] < 10000):
        raise IntegrityError('Execution client differs from its frozen scientific task')
    binding = c.read(c.regular(binding_path(root, job, path)))
    if not isinstance(binding, dict) or set(binding) != {'client', 'configuration'} or binding['client'] != value:
        raise IntegrityError('Execution client differs from its host binding')
    attempt = value['attempt']
    origin = root / 'native-evidence' / f'{step:03d}'
    record = origin if attempt == 0 else origin / 'continuations' / f'{attempt:03d}'
    host_path = record / 'experiment-config.json'
    if proof(host_path) != binding['configuration']:
        raise IntegrityError('Execution client original configuration changed')
    config = c.read(c.regular(host_path))
    intent = c.read(c.regular(record / 'native-intent.json'))
    task = c.record_ref(root / 'jobs' / f'{step:03d}.json')
    if intent.get('task_ref') != task or attempt and intent.get('attempt') != attempt:
        raise IntegrityError('Execution client has no exact accepted native task')
    expected_attempt = dict(number=attempt, execution_key=c.record_ref(record / 'native-intent.json') if attempt else task)
    if (config.get('scope') != stage_scope(root, job) or config.get('native_attempt') != expected_attempt
            or config.get('record_dir') != str(record_directory(root, step))):
        raise IntegrityError('Execution client original scope or native attempt changed')
    projection = root / 'host-runtime/experiment-control' / f'{step:03d}' / 'interfaces' / f'{attempt:03d}.json'
    if c.read(c.regular(projection)) != config:
        raise IntegrityError('Execution client original host interface changed')
    return config


def payload_path(config, path):
    """Resolve only an exact host-bound compiled implementation client."""
    from . import contracts as c
    from .experiments import IntegrityError, proof, read, MAX_ASSET
    path=Path(path).absolute()
    if config.get('worker_view_version')!=VERSION:return path
    try:value=json.loads(read(path,MAX_ASSET))
    except (UnicodeError,ValueError):return path
    if not isinstance(value,dict) or 'worker_implementation_version' not in value:return path
    root=root_for_configuration(config);work=c.workspace(root,config['scope']['step'])
    if (set(value)!={'worker_implementation_version','implementation_ref'}
            or type(value['worker_implementation_version']) is not int or value['worker_implementation_version']!=VERSION
            or not isinstance(value['implementation_ref'],str) or re.fullmatch(r'I[0-9]{3,}',value['implementation_ref']) is None):
        raise IntegrityError('Scientific implementation reference is malformed')
    from . import execution_inputs
    job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
    materials={json.dumps(item,sort_keys=True):item for item in execution_inputs._bound(job)
        if path in (work/item['path'],Path(item['source']))}
    if materials:
        if len(materials)!=1:raise IntegrityError('Scientific implementation input is ambiguous')
        material=next(iter(materials.values()));actual=proof(path)
        if any(actual[key]!=material[key] for key in ('size_bytes',)):
            raise IntegrityError('Scientific implementation input differs from its bound original')
        package=execution_inputs.compiled_payload(root,material)
        if proof(package['path'])!=package:
            raise IntegrityError('Scientific implementation package differs from its compiled original')
        return Path(package['path'])
    if not path.is_relative_to(work/'artifacts'):
        raise IntegrityError('Scientific implementation is outside this task bound inputs')
    directory=Path(config['record_dir']).parent/'implementations'/value['implementation_ref']
    binding=c.read(c.regular(directory/'client.json'))
    package=directory/'package.json'
    if (not isinstance(binding,dict) or set(binding)!={'client','public_path','package'} or binding['client']!=value
            or binding['public_path']!=str(path) or binding['package']!=proof(package)):
        raise IntegrityError('Scientific implementation differs from its exact compiled original')
    return package
