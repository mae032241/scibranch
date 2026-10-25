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
    if any(proof(host_path).get(key)!=item for key,item in binding['configuration'].items()):
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
    requested=Path(path).absolute();reference=input_reference(config,requested);path=Path(reference['path'])
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
        if requested in (work/item['path'],root/Path(item['source']))}
    if materials:
        if len(materials)!=1:raise IntegrityError('Scientific implementation input is ambiguous')
        material=next(iter(materials.values()));actual=proof(path)
        if any(actual[key]!=material[key] for key in ('size_bytes',)):
            raise IntegrityError('Scientific implementation input differs from its bound original')
        package=execution_inputs.compiled_payload(root,material)
        if any(proof(package['path'])[key]!=package[key] for key in package if key in {'path','size_bytes','file_stat'}):
            raise IntegrityError('Scientific implementation package differs from its compiled original')
        return Path(package['path'])
    if not path.is_relative_to(work/'artifacts'):
        raise IntegrityError('Scientific implementation is outside this task bound inputs')
    directory=Path(config['record_dir']).parent/'implementations'/value['implementation_ref']
    binding=c.read(c.regular(directory/'client.json'))
    package=directory/'package.json'
    if (not isinstance(binding,dict) or set(binding)!={'client','public_path','package'} or binding['client']!=value
            or binding['public_path']!=str(path)
            or any(proof(package).get(key)!=item for key,item in binding['package'].items())):
        raise IntegrityError('Scientific implementation differs from its exact compiled original')
    return package


def _input_rows(job):
    return [*job.get('global_materials',[]),*job.get('direct_refs',[]),*job.get('direct_materials',[]),
        *job.get('history',{}).get('files',[]),
        *job.get('history',{}).get('execution_recovery_materials',[]),
        *[row['material'] for row in job.get('selected_inputs',[])]]



def code_selection(config, path, *, resolved=None, experiment_directory=None):
    """Select dedicated current-W code; saved scientific inputs stay direct."""
    from . import contracts as c, shared_input_views
    from .experiments import CallerError, IntegrityError
    if config.get('scope',{}).get('role')!='W':return None
    root=root_for_configuration(config,allow_standalone=True)
    if root is None:return None
    path=Path(path).absolute()
    work=c.workspace(root,config['scope']['step'])
    job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
    rows=_input_rows(job)
    bound=any(path in (work/item['path'],root/Path(item.get('source',work/item['path'])))
        for item in rows)
    if bound or resolved is not None and Path(resolved)!=path:
        if experiment_directory is not None:
            raise CallerError('Choose a current-W code entry for --experiment-dir; bound scientific inputs and compiled packages remain direct inputs.')
        return None
    if path.resolve()!=path:
        raise IntegrityError('Code selection requires an exact ordinary path')
    if any(path.is_relative_to(work/'work'/name) for name in shared_input_views.DYNAMIC_ROOTS):
        input_reference(config,path)
        return None
    code_root=work/'code'
    if not path.is_relative_to(code_root) or path==code_root:
        raise CallerError('Move a new code entry into code/; --experiment-dir selects only the Local runtime working directory.')
    if experiment_directory is not None:
        directory=Path(experiment_directory).absolute()
        relative=directory.relative_to(work) if directory.is_relative_to(work) else Path()
        if (config.get('execution_interface',{}).get('executor')!='local_execution:command'
                or len(relative.parts)!=2 or relative.parts[0]!='experiments'
                or directory.resolve()!=directory or not directory.is_dir()
                or directory.is_relative_to(code_root)):
            raise CallerError('Choose an explicit current-W experiments/<name> runtime directory outside code/.')
        selection='directory';selected=code_root
        entry=path.relative_to(code_root).as_posix()
    else:
        code_root=path.parent;selection='file';selected=path;entry=path.name
    for item in rows:
        for material in (work/item['path'],root/Path(item.get('source',work/item['path']))):
            if selected.is_relative_to(material) or material.is_relative_to(selected):
                raise CallerError('Selected code overlaps a bound scientific input; keep data outside the selected code scope.')
    return dict(code_root=code_root,entry=entry,selection=selection,
        logical_root=code_root.relative_to(work).as_posix())



def input_reference(config, path):
    """Resolve the exact task binding before fingerprinting its actual source."""
    from . import contracts as c, originals
    from .experiments import IntegrityError, proof
    path=Path(path).absolute()
    if path.resolve()!=path:
        raise IntegrityError('Scientific input requires an exact ordinary path')
    root=root_for_configuration(config,allow_standalone=True)
    if root is None:return proof(path)
    work=c.workspace(root,config['scope']['step'])
    job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
    matches={json.dumps(item,sort_keys=True):item for item in _input_rows(job)
        if path in (work/item['path'],root/Path(item.get('source',work/item['path'])))}
    if matches:
        if len(matches)!=1:raise IntegrityError('Scientific input binding is ambiguous')
        material=next(iter(matches.values()))
        original=originals.material_path(root,dict(material,source=material.get('source',str(work/material['path']))))
        reference=proof(original)
        if reference['size_bytes']!=material['size_bytes']:
            raise IntegrityError('Scientific input differs from its original bound size')
        return reference
    if not path.is_relative_to(work) or path==work or path.is_relative_to(work/'inputs'):
        raise IntegrityError('Scientific input is outside this task bound inputs or current workspace files')
    from . import shared_input_views
    if any(path.is_relative_to(work/'work'/name) for name in shared_input_views.DYNAMIC_ROOTS):
        original=shared_input_views.logical_original(root,job,path)
        if original is not None:return proof(original)
    return proof(path)
