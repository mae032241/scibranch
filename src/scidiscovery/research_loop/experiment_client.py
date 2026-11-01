"""Readable execution clients resolve to existing, privately verified originals.

This is a default presentation boundary, not protection from same-user access.
"""
from pathlib import Path
import json
import re
import stat

VERSION = 1


def record_directory(root, step):
    """Keep new control receipts outside the scientific working directory."""
    from . import contracts as c
    root = Path(root).absolute()
    job_path = root / 'jobs' / f'{step:03d}.json'
    job = c.read(c.regular(job_path))
    return _record_directory_from_facts(root,step,job)


def _record_directory_from_facts(root,step,job):
    from . import contracts as c
    if type(job.get('worker_view_version')) is int and job['worker_view_version'] == VERSION:
        return root / 'host-runtime/experiment-control' / f'{step:03d}' / 'records'
    return c.workspace(root, step) / 'work/lineage'


def root_for_configuration(config, *, allow_standalone=False):
    """Resolve only the old or current canonical execution record namespace."""
    from .experiments import IntegrityError
    root=_configuration_root(config,allow_standalone=allow_standalone)
    if root is None:return None
    if Path(config['record_dir']) != record_directory(root, config['scope']['step']):
        raise IntegrityError('Execution record namespace differs from its frozen scientific task')
    return root


def _configuration_root(config, *, allow_standalone):
    """Observe the configured namespace without loading its task record."""
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
    return _observe_payload_path(config,path,facts=load_client_facts(config))


def _observe_payload_path(config, path, *, facts):
    """Acquire a payload using this operation's already loaded task bindings."""
    from . import contracts as c
    from .experiments import IntegrityError, proof, read, MAX_ASSET
    requested=Path(path).absolute();reference=_observe_input_reference(config,requested,facts=facts);path=Path(reference['path'])
    if config.get('worker_view_version')!=VERSION:return path
    try:value=json.loads(read(path,MAX_ASSET))
    except (UnicodeError,ValueError):return path
    if not isinstance(value,dict) or 'worker_implementation_version' not in value:return path
    root,work,job=facts['root'],facts['work'],facts['job']
    if root is None:root_for_configuration(config)
    if (set(value)!={'worker_implementation_version','implementation_ref'}
            or type(value['worker_implementation_version']) is not int or value['worker_implementation_version']!=VERSION
            or not isinstance(value['implementation_ref'],str) or re.fullmatch(r'I[0-9]{3,}',value['implementation_ref']) is None):
        raise IntegrityError('Scientific implementation reference is malformed')
    from . import execution_inputs
    materials={json.dumps(item,sort_keys=True):item for item in execution_inputs._bound(job)
        if requested in (work/item['path'],root/Path(item['source']))}
    if materials:
        if len(materials)!=1:raise IntegrityError('Scientific implementation input is ambiguous')
        material=next(iter(materials.values()));actual=reference
        if any(actual[key]!=material[key] for key in ('size_bytes',)):
            raise IntegrityError('Scientific implementation input differs from its bound original')
        package=execution_inputs.compiled_payload(root,material)
        package_proof=proof(package['path'])
        if any(package_proof[key]!=package[key] for key in package if key in {'path','size_bytes','file_stat'}):
            raise IntegrityError('Scientific implementation package differs from its compiled original')
        return Path(package['path'])
    if not path.is_relative_to(work/'artifacts'):
        raise IntegrityError('Scientific implementation is outside this task bound inputs')
    directory=Path(config['record_dir']).parent/'implementations'/value['implementation_ref']
    binding=c.read(c.regular(directory/'client.json'))
    package=directory/'package.json'
    package_proof=proof(package)
    if (not isinstance(binding,dict) or set(binding)!={'client','public_path','package'} or binding['client']!=value
            or binding['public_path']!=str(path)
            or any(package_proof.get(key)!=item for key,item in binding['package'].items())):
        raise IntegrityError('Scientific implementation differs from its exact compiled original')
    return package


def _input_rows(job):
    return [*job.get('global_materials',[]),*job.get('direct_refs',[]),*job.get('direct_materials',[]),
        *job.get('history',{}).get('inputs',[]),
        *job.get('history',{}).get('execution_recovery_materials',[]),
        *[row['material'] for row in job.get('selected_inputs',[])]]


def load_client_facts(config):
    """Own one operation's task expansion and exact material locations."""
    from . import contracts as c, handoff_state
    root=_configuration_root(config,allow_standalone=True)
    if root is None:return client_bindings_from_facts(None,None,None,material_paths={},material_sources={})
    work=c.workspace(root,config['scope']['step'])
    sources={}
    job=c.read_job(root/'jobs'/f'{config["scope"]["step"]:03d}.json',sources=sources)
    from .experiments import IntegrityError
    if Path(config['record_dir'])!=_record_directory_from_facts(root,config['scope']['step'],job):
        raise IntegrityError('Execution record namespace differs from its frozen scientific task')
    paths=handoff_state.material_paths(root,job,sources=sources)
    return client_bindings_from_facts(root,work,job,material_paths=paths,material_sources=sources)


def client_bindings_from_facts(root,work,job,*,material_paths,material_sources):
    """Share one owner's loaded bindings without acquiring another record."""
    return dict(root=root,work=work,job=job,material_paths=material_paths,material_sources=material_sources)


def _input_matches_from_facts(facts,path):
    """Select declared aliases without loading or probing their originals."""
    root,work,job=facts['root'],facts['work'],facts['job']
    return {json.dumps(item,sort_keys=True):item for item in _input_rows(job)
        if path in (work/item['path'],root/Path(item.get('source',work/item['path'])))}


def _code_path_from_facts(facts,path):
    """Map one code alias using only the operation's loaded task bindings."""
    from . import project_layout
    from .experiments import CallerError
    root,work,job=facts['root'],facts['work'],facts['job']
    if _input_matches_from_facts(facts,path):return path,False
    project=job.get('project')
    if project is None:return path,False
    layout=project_layout.code_roots(project,work)
    for alias in (Path(project['base_root']),layout['root']):
        if not path.is_relative_to(alias):continue
        relative=path.relative_to(alias)
        if '..' in relative.parts or not any(
                relative.is_relative_to(row['relative']) and relative!=Path(row['relative'])
                for row in layout['editable']):
            raise CallerError('Choose a code entry inside a declared project editable directory.')
        return layout['root']/relative,True
    return path,False


def _observe_code_path(path, *, facts):
    from .experiments import IntegrityError
    path=Path(path).absolute()
    selected,ordinary=_code_path_from_facts(facts,path)
    if ordinary and selected.resolve()!=selected:
        raise IntegrityError('Code selection requires an exact ordinary project draft path')
    return selected



def code_path(config, path):
    """Map declared original-path aliases to the exact current project draft."""
    path = Path(path).absolute()
    if config.get('scope', {}).get('role') != 'W':return path
    facts=load_client_facts(config)
    if facts['root'] is None:return path
    return _observe_code_path(path,facts=facts)


def code_selection(config, path, *, resolved=None, experiment_directory=None):
    """Select dedicated current-W code; saved scientific inputs stay direct."""
    if config.get('scope',{}).get('role')!='W':return None
    facts=load_client_facts(config)
    if facts['root'] is None:return None
    return _observe_code_selection(config,path,resolved=resolved,experiment_directory=experiment_directory,facts=facts)


def _observe_code_selection(config,path,*,resolved,experiment_directory,facts):
    from . import project_layout, shared_input_views
    from .experiments import CallerError, IntegrityError
    root,work,job=facts['root'],facts['work'],facts['job']
    path=_observe_code_path(path,facts=facts)
    if resolved is not None:resolved=_observe_code_path(resolved,facts=facts)
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
        _observe_input_reference(config,path,facts=facts)
        return None
    project=job.get('project')
    layout=project_layout.code_roots(project,work)
    code_root=layout['root']
    if not path.is_relative_to(code_root) or path==code_root:
        logical_root=code_root.relative_to(work).as_posix()+'/'
        raise CallerError(f'Move a new code entry into the declared {logical_root} code scope; --experiment-dir selects only the Local runtime working directory.')
    if experiment_directory is not None:
        directory=Path(experiment_directory).absolute()
        relative=directory.relative_to(work) if directory.is_relative_to(work) else Path()
        if (config.get('execution_interface',{}).get('executor')!='local_execution:command'
                or len(relative.parts)!=2 or relative.parts[0]!='experiments'
                or directory.resolve()!=directory or directory.exists() and not directory.is_dir()
                or directory.parent.exists() and not directory.parent.is_dir()
                or directory.is_relative_to(code_root)):
            raise CallerError('Choose an explicit current-W experiments/<name> runtime directory outside code/.')
        selection='directory';selected=code_root
        entry=path.relative_to(code_root).as_posix()
    elif project is not None:
        selection='directory';selected=code_root;entry=path.relative_to(code_root).as_posix()
    else:
        code_root=path.parent;selection='file';selected=path;entry=path.name
    for item in rows:
        for material in (work/item['path'],root/Path(item.get('source',work/item['path']))):
            scopes=[row[key] for row in layout['editable'] for key in ('source','target')] if project is not None else [selected]
            if any(scope.is_relative_to(material) or material.is_relative_to(scope) for scope in scopes):
                raise CallerError('Selected code overlaps a bound scientific input; keep data outside the selected code scope.')
    result=dict(code_root=code_root,entry=entry,selection=selection,
        logical_root=code_root.relative_to(work).as_posix())
    if project is not None:result['roots']=[row['relative'] for row in layout['editable']]
    return result



def _selected_reference(path, logical='script or payload'):
    """Validate author-owned files only, after original binding resolution."""
    from .experiments import CallerError, proof
    if path.resolve()!=path:
        raise CallerError(f'The selected scientific input {logical!r} must use an ordinary task path without links or parent directories.',error='scientific_input_unusable')
    try:
        metadata=path.lstat()
    except (FileNotFoundError,NotADirectoryError):
        raise CallerError(f'The selected scientific input {logical!r} is missing. Write or select its task file; script paths are relative to the task workspace, independently of --experiment-dir.',error='scientific_input_not_found') from None
    except PermissionError:
        raise CallerError(f'The selected scientific input {logical!r} cannot be read. Select an accessible ordinary task file.',error='scientific_input_unusable') from None
    if not stat.S_ISREG(metadata.st_mode):
        raise CallerError(f'The selected scientific input {logical!r} must be an ordinary file, not a directory or special file.',error='scientific_input_unusable')
    try:return proof(path)
    except (FileNotFoundError,NotADirectoryError,PermissionError):
        raise CallerError(f'The selected scientific input {logical!r} is unavailable. Write or select an accessible ordinary task file.',error='scientific_input_unusable') from None


def input_reference(config, path):
    """Resolve the exact task binding before fingerprinting its actual source."""
    return _observe_input_reference(config,path,facts=load_client_facts(config))


def _observe_input_reference(config,path,*,facts):
    """Acquire selected-file facts without reloading this operation's task."""
    from . import originals
    from .experiments import IntegrityError, proof
    path=Path(path).absolute()
    root,work,job=facts['root'],facts['work'],facts['job']
    if root is None:return _selected_reference(path)
    matches=_input_matches_from_facts(facts,path)
    if matches:
        if len(matches)!=1:raise IntegrityError('Scientific input binding is ambiguous')
        material=next(iter(matches.values()))
        try:
            if path.resolve()!=path:raise IntegrityError('Scientific input requires an exact ordinary path')
            if material in job['direct_materials']:
                original=facts['material_paths'][material['path']]
            else:
                original=originals.material_path(root,dict(material,source=material.get('source',str(work/material['path']))))
            reference=proof(original)
        except (OSError,ValueError) as error:
            raise IntegrityError('Scientific input original is unavailable or changed') from error
        if reference['size_bytes']!=material['size_bytes']:
            raise IntegrityError('Scientific input differs from its original bound size')
        return reference
    if not path.is_relative_to(work) or path==work or path.is_relative_to(work/'inputs'):
        raise IntegrityError('Scientific input is outside this task bound inputs or current workspace files')
    from . import shared_input_views
    if any(path.is_relative_to(work/'work'/name) for name in shared_input_views.DYNAMIC_ROOTS):
        try:
            if path.resolve()!=path:raise IntegrityError('Scientific input requires an exact ordinary path')
            original=shared_input_views.logical_original(root,job,path)
            if original is not None:return proof(original)
        except (OSError,ValueError) as error:
            raise IntegrityError('Retained scientific input original is unavailable or changed') from error
    # Unbound workspace files are author-owned; original failures above retain
    # their integrity meaning and must never become a request correction.
    return _selected_reference(path,path.relative_to(work).as_posix())
