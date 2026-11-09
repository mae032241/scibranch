"""The default native attempt's scientific subprocess namespace.

This only supplies argv/cwd to LocalExecution. Acceptance, clocks, process
identity, cleanup and collection remain with the existing execution owner.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tomllib

PERMISSION_PROFILE = 'scid-local-attempt'


def _toml(value):
    if isinstance(value, str):return json.dumps(value)
    if type(value) is bool:return str(value).lower()
    if isinstance(value, list):return '['+', '.join(_toml(item) for item in value)+']'
    if isinstance(value, dict):
        return '{'+', '.join(json.dumps(key)+' = '+_toml(item) for key,item in value.items())+'}'
    raise ValueError('Unsupported native permission configuration value')


def _merge(target, source):
    for key,value in source.items():
        if isinstance(value,dict) and isinstance(target.get(key),dict):_merge(target[key],value)
        else:target[key]=value


def project_policy(settings):
    """Fail before submission when the frozen native policy cannot project."""
    from .experiments import UnsupportedExecution
    options = settings.get('native_options')
    launcher = settings.get('native_launcher_argv', [])
    contract = settings.get('native_capabilities', {}).get('native_launcher_contract')
    if (settings.get('native_backend', 'codex') != 'codex' or len(launcher) != 1
            or Path(launcher[0]).name != 'codex' or contract is not None
            or not isinstance(options, dict) or options.get('workspace') != 'workspace-write'):
        raise UnsupportedExecution('Project namespaces require an explicit frozen standard Codex workspace-write policy; inherited, readonly and custom native policies have no supported project projection')
    if (Path('/etc/codex/managed_config.toml').exists() or Path('/etc/codex/requirements.toml').exists()):
        raise UnsupportedExecution('Managed native permissions have no supported project namespace projection')
    policy = dict(writable_roots=[], network_access=options['network'],
        exclude_tmpdir_env_var=False, exclude_slash_tmp=False)
    policy.update(options.get('sandbox_workspace_write', {}))
    if policy['writable_roots']:
        raise UnsupportedExecution('Additional native writable roots have no exact project namespace mapping; use a supported frozen policy without extra roots')
    return policy


def _permissions(home, work):
    """Capture relevant existing legacy declarations, then override both users.

    Named/managed policies need their original effective permission state;
    guessing their translation to legacy workspace-write would widen access.
    """
    from .experiments import UnsupportedExecution
    value={}
    candidates=[Path('/etc/codex/config.toml'),home/'config.toml']
    originals=[]
    from . import experiments as e
    def layer(path):
        if path.exists():
            original=e.proof(path);data=tomllib.loads(e.read(path).decode())
            if e.proof(path)!=original:raise e.IntegrityError('Native permissions changed during capture')
            originals.append(original);_merge(value,data)
    for path in dict.fromkeys(candidates):layer(path)
    # Codex loads trusted project layers only from its project root to cwd.
    # A user's unrelated ~/.codex above that root is not another project layer.
    ancestors=(work,*work.parents)
    markers=value.get('project_root_markers',['.git'])
    if (not isinstance(markers,list) or not markers
            or any(not isinstance(item,str) or not item or '/' in item for item in markers)):
        raise UnsupportedExecution('Native project-root declarations cannot be resolved')
    boundary=next((index for index,parent in enumerate(ancestors)
        if any((parent/marker).exists() for marker in markers)),0)
    projects=value.get('projects',{})
    trust=next((projects[str(parent)].get('trust_level') for parent in ancestors
        if isinstance(projects.get(str(parent)),dict)),None)
    if trust=='trusted':
        for parent in reversed(ancestors[:boundary+1]):layer(parent/'.codex/config.toml')
    if (value.get('default_permissions') is not None or value.get('permissions')
            or value.get('profile') is not None
            or value.get('network') or value.get('features',{}).get('network_proxy') is True
            or Path('/etc/codex/managed_config.toml').exists()
            or Path('/etc/codex/requirements.toml').exists()):
        raise UnsupportedExecution('This native permission policy requires its original effective sandbox state; managed local launch is unsupported')
    declared=value.get('sandbox_workspace_write',{})
    if not isinstance(declared,dict):raise UnsupportedExecution('Native workspace permission declaration is unavailable')
    policy=dict(writable_roots=[],network_access=False,exclude_tmpdir_env_var=False,exclude_slash_tmp=False)
    if set(declared)-set(policy):
        raise UnsupportedExecution('This native workspace policy has no supported scientific projection')
    policy.update(declared)
    if policy['writable_roots']:
        raise UnsupportedExecution('Additional native writable roots have no declared scientific namespace mapping')
    if any(type(policy[key]) is not bool for key in ('network_access','exclude_tmpdir_env_var','exclude_slash_tmp')):
        raise UnsupportedExecution('Native workspace permission declaration is invalid')
    _toml(policy)
    return policy, originals


def _profile(policy, workspace, tmpdir, *, virtual=False, project_writes=()):
    # Extending the native built-in retains its protected workspace metadata.
    # Exclusion removes an additional temporary write grant, never a write
    # already supplied by the workspace or the other temporary root.
    workspace=Path(workspace)
    temporary=Path(tmpdir) if tmpdir else None
    if temporary is not None:
        if not temporary.is_absolute():temporary=workspace/temporary
        if not virtual:temporary=temporary.resolve()
    slash_tmp=Path('/tmp') if virtual else Path('/tmp').resolve()
    writable=[workspace, *[Path(path) for path in project_writes]]
    if not policy['exclude_slash_tmp']:writable.append(slash_tmp)
    if not policy['exclude_tmpdir_env_var'] and temporary is not None:writable.append(temporary)
    filesystem={str(path): 'write' for path in project_writes}
    for excluded,name,path in ((policy['exclude_tmpdir_env_var'],':tmpdir',temporary),
            (policy['exclude_slash_tmp'],':slash_tmp',slash_tmp)):
        if excluded and path is not None and not any(path.is_relative_to(root) for root in writable):
            filesystem[name]='read'
    return dict(extends=':workspace',filesystem=filesystem,network=dict(enabled=policy['network_access']))


def _native_program(launcher):
    """Resolve the selected installation, including the official npm wrapper.

    A running Codex session's temporary arg0 aliases are deliberately irrelevant
    to an ordinary CLI process. An ELF entry, or the selected npm package's own
    installed optional binary, owns the same sandbox implementation.
    """
    from .experiments import UnsupportedExecution
    entry=shutil.which(launcher[0])
    if entry is None:raise UnsupportedExecution('The selected native program is unavailable')
    entry=Path(entry).resolve(strict=True)
    candidates=[entry]
    platform={'x86_64':('x64','x86_64-unknown-linux-musl'),
              'aarch64':('arm64','aarch64-unknown-linux-musl')}.get(os.uname().machine)
    if sys.platform=='linux' and platform and entry.name=='codex.js' and entry.parent.name=='bin':
        architecture,target=platform;package=entry.parent.parent
        vendor=Path('vendor')/target/'bin/codex'
        candidates.extend((package/'node_modules/@openai'/('codex-linux-'+architecture)/vendor,
            package.parent/('codex-linux-'+architecture)/vendor,package/vendor))
    for path in candidates:
        try:
            native=path.resolve(strict=True)
            with native.open('rb') as stream:magic=stream.read(4)
            if sys.platform=='linux' and magic==b'\x7fELF':return native
        except OSError:continue
    raise UnsupportedExecution('The selected native installation has no supported scientific sandbox binary')


def _public_root(public, root, work, home, runtime_roots):
    from .experiments import UnsupportedExecution
    if not public.is_absolute() or public.resolve()!=public or not public.is_dir():
        raise UnsupportedExecution('Declared public root is no longer an original directory')
    protected=[root,work,home,Path('/etc/codex'),Path('/task-native'),Path('/task-tmp'),
        Path('/proc'),Path('/dev'),*runtime_roots]
    if (Path('/tmp').is_relative_to(public) or any(public.is_relative_to(path.resolve())
            or path.resolve().is_relative_to(public) for path in protected)):
        raise UnsupportedExecution('Declared public root overlaps private control or a scientific namespace mount')


def _experiment_python(settings, control_python):
    """Capture mutable interpreter installation facts, not configuration again."""
    from . import python_environment
    selected=settings.get('execution_config')
    if selected is None or selected['executor']!='local_execution:command':return None
    argv=selected['configuration'].get('run_argv')
    if (not isinstance(argv,list) or not argv or argv[0]==control_python
            or not python_environment.is_python_interpreter(argv[0])):return None
    return dict(run_argv=list(argv),runtime_roots=python_environment.runtime_roots(argv[0]))


def load_native_facts(record, *, task_ref):
    """Acquire the intent body and its proof from one exact original byte read."""
    from . import contracts as c, experiments as e
    path=Path(c.record_path(Path(record)/'native-intent.json'))
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(descriptor,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise e.IntegrityError('Native intent is not an original regular file')
        data=stream.read()
        after=os.fstat(stream.fileno())
    if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
            or len(data)!=after.st_size):
        raise e.IntegrityError('Native intent changed during capture')
    intent=json.loads(data)
    if not isinstance(intent,dict) or intent.get('task_ref')!=task_ref:
        raise e.IntegrityError('Native execution task changed')
    proof=dict(path=str(path),size_bytes=len(data),
        file_stat=[after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns],
        sha256=hashlib.sha256(data).hexdigest())
    return dict(record=Path(record),intent=intent,task_ref=task_ref,intent_proof=proof)


def prepare(root, step, record, work, home, args, *, standard, resume_position=None, native_options=None,
            job=None, intent=None, settings=None, intent_proof=None):
    """Independent legacy preparation owner; native runners use explicit facts."""
    from . import contracts as c, shared_input_views, configuration_updates
    producers={}
    if job is None:job=c.read_job(root/'jobs'/f'{step:03d}.json',sources=producers)
    if settings is None:settings=configuration_updates.for_job(root,job)
    if intent is None:
        if intent_proof is not None:raise ValueError('Intent proof requires its owner-loaded body')
        native=load_native_facts(record,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'))
        intent=native['intent']
    else:
        if intent_proof is None:raise ValueError('Owner-loaded intent requires its original proof')
        native=dict(record=record,intent=intent,task_ref=intent['task_ref'],intent_proof=intent_proof)
    sources=shared_input_views.load_source_facts(root,job,work,
        native=native,policy=settings,material_sources=producers)
    selected=shared_input_views.materialize_selected_code(root,shared_input_views.selected_code_from_facts(sources))
    return prepare_from_facts(root,step,record,work,home,args,resume_position=resume_position,
        job=job,intent=intent,settings=settings,source_facts=sources,selected_code=selected,
        material_sources=producers,projection=codex_projection(standard,native_options))


def prepare_from_facts(root, step, record, work, home, args, *, resume_position, job, intent,
                       settings, source_facts, selected_code, material_sources, projection):
    """Existing bridge closure, with an explicit provider projection, not an executor.

    The runner owns immutable inputs once. Only the mount producer observes
    changing filesystem state at each accepted Local effect boundary.
    """
    from . import execution, experiments as e
    validate_source_facts(root,step,record,work,job,intent,settings,source_facts)
    snapshot=record/'native-execution-permissions.json'
    attempt=intent.get('attempt',0)
    if type(attempt) is not int or attempt<0:
        raise e.IntegrityError('Native preparation requires an exact attempt number')
    expected=execution.origin(root,step)
    if attempt:expected=expected/'continuations'/f'{attempt:03d}'
    native=source_facts['native']
    try:task_ref=native['task_ref'];intent_proof=native['intent_proof']
    except KeyError:
        raise e.IntegrityError('Native preparation requires its owner-loaded task reference and intent proof') from None
    if (not root.is_absolute() or '..' in root.parts
            or record!=expected
            or task_ref!=dict(path=str(root/'jobs'/f'{step:03d}.json'))
            or intent.get('task_ref')!=task_ref
            or intent_proof.get('path')!=str(record/'native-intent.json')):
        raise e.IntegrityError('Native preparation belongs to another task or attempt')
    value=dict(version=1,task_ref=task_ref,
        native_attempt=dict(number=attempt,execution_key=dict(path=intent_proof['path']) if attempt
            else task_ref),
        intent=intent_proof,
        native_argv=list(args),home=str(home),workspace=str(work))
    project=job.get('project')
    if project is not None:
        if settings.get('project')!=project:
            raise e.IntegrityError('Project native policy differs from its frozen job')
        value['project']=project
    public=Path(job['public_root'])
    native_args=list(args)
    try:
        projection['prepare'](value,native_args,root=root,record=record,work=work,home=home,
            job=job,settings=settings,resume_position=resume_position)
        from scidiscovery.plugin_configuration import PluginConfigurationError
        try:experiment_python=_experiment_python(settings,value['python'])
        except PluginConfigurationError:
            raise e.UnsupportedExecution('The selected experiment Python runtime could not be captured for this native attempt') from None
        if experiment_python is not None:value['experiment_python']=experiment_python
        _public_root(public,root,work,home,[Path(path) for path in
            ('/usr','/bin','/lib','/lib64','/etc/resolv.conf','/etc/hosts','/etc/ssl/certs',
             *value['runtime_roots'],*(experiment_python or {}).get('runtime_roots',[]),
             *value['runtime_programs'],*value['private_roots'],value['python'])])
        projection['validate_runtime'](value,[public,work,*value['runtime_roots'],
            *(experiment_python or {}).get('runtime_roots',[])])
    except e.UnsupportedExecution as error:
        if project is not None:raise
        value['unsupported']=str(error)
    with snapshot.open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    snapshot.chmod(0o400)

    def check_available():
        if 'unsupported' in value:raise e.UnsupportedExecution(value['unsupported'])

    def launch(config,accepted,command,cwd):
        if (accepted.get('native_attempt')!=value['native_attempt']
                or accepted.get('scope')!=config['scope']
                or config['scope']['job_ref']!=value['task_ref']):
            raise e.IntegrityError('Scientific launch differs from its original native attempt')
        check_available()
        return _launch_from_facts(value,config,accepted,command,cwd,public=public,root=root,
            source_facts=source_facts,selected_code=selected_code,material_sources=material_sources,
            projection=projection)
    launch.check_available=check_available
    return native_args,launch


def validate_source_facts(root,step,record,work,job,intent,settings,facts):
    """Pure scope validation; never reload the runner's originals."""
    from .experiments import IntegrityError
    if (facts['root']!=root or facts['work']!=work or facts['target']!=work
            or facts['job']!=job or facts['policy']!=settings
            or facts['native']['record']!=record or facts['native']['intent']!=intent
            or job.get('step')!=step):
        raise IntegrityError('Scientific input facts belong to another native attempt')


def codex_projection(standard,native_options):
    """Codex keeps its actual native sandbox, never a name-selected framework branch."""
    def prepare_projection(value,native_args,*,root,record,work,home,job,settings,resume_position):
        from . import experiments as e, shared_input_views
        project=job.get('project')
        if project is not None:project_policy(settings)
        if not standard:
            raise e.UnsupportedExecution('This custom native launcher has no declared scientific subprocess sandbox; managed local launch is unsupported')
        if native_options is None:
            policy, originals=_permissions(home,work)
        else:
            if native_options['workspace']!='workspace-write':
                raise e.UnsupportedExecution('Managed scientific subprocess projection currently supports only the Codex workspace-write declaration')
            if (Path('/etc/codex/managed_config.toml').exists() or Path('/etc/codex/requirements.toml').exists()):
                raise e.UnsupportedExecution('Managed native policy requires its original effective sandbox state')
            policy=dict(writable_roots=[],network_access=native_options['network'],
                        exclude_tmpdir_env_var=False,exclude_slash_tmp=False)
            policy.update(native_options.get('sandbox_workspace_write',{}))
            if policy['writable_roots']:
                raise e.UnsupportedExecution('Additional native writable roots have no declared scientific namespace mapping')
            originals=[]
        helper=_native_program(native_args[:1])
        bwrap=shutil.which('bwrap')
        if bwrap is None:raise e.UnsupportedExecution('The native scientific namespace wrapper is unavailable')
        value.update(policy=policy,permission_sources=originals,native=dict(path=str(helper)),bwrap=dict(path=str(Path(bwrap).resolve())),
            python=str(Path(sys.executable).absolute()),runtime_roots=list(dict.fromkeys((sys.base_prefix,sys.prefix))))
        value.update(runtime_programs=[str(helper)],private_roots=[],workspace_readonly=False,
            metadata=['.git','.agents','.codex','.aws'])
        backend=record/'scientific-native-home';backend.mkdir(mode=0o700)
        tmpdir=os.environ.get('TMPDIR')
        from . import shared_input_views
        project_writes=shared_input_views.project_targets(project,writable=False)
        projection=_profile(policy,work,tmpdir,project_writes=project_writes)
        # A node workspace and every accepted cwd below it cannot cover either
        # top-level virtual temporary root; recheck the actual cwd at launch.
        backend_projection=_profile(policy,work,'/task-tmp',virtual=True,project_writes=project_writes)
        value['permission_environment']=dict(TMPDIR=tmpdir)
        value['permission_profile']=dict(name=PERMISSION_PROFILE,configuration=projection)
        value['backend_permission_profile']=dict(name=PERMISSION_PROFILE,configuration=backend_projection)
        content='default_permissions='+json.dumps(PERMISSION_PROFILE)+'\npermissions='+_toml({PERMISSION_PROFILE:backend_projection})+'\n'
        (backend/'config.toml').write_text(content);(backend/'config.toml').chmod(0o400)
        value['backend_configuration']=e.proof(backend/'config.toml')
        # Exec retains its supported legacy flags. Only the sandbox subcommand
        # accepts -P; it receives the equivalent declaration in its namespace.
        index=native_args.index('--sandbox')
        if native_args[index+1]!='workspace-write':raise e.IntegrityError('Original native workspace policy changed')
        if resume_position is not None and (type(resume_position) is not int
                or not 2<=resume_position<len(native_args) or native_args[resume_position]!='resume'):
            raise e.IntegrityError('Original native resume command position changed')
        position=resume_position if resume_position is not None else len(native_args)-1
        worker_policy=dict(policy)
        if project is not None:
            worker_policy['writable_roots']=[str(path) for path in shared_input_views.project_targets(project,writable=True)]
            value['worker_permission_policy']=worker_policy
            value['project_writable_targets']=[str(path) for path in project_writes]
        native_args[position:position]=['-c','sandbox_workspace_write='+_toml(worker_policy)]
        value['applied_native_argv']=list(native_args)
        value['configuration_mounts']=[['--ro-bind',value['backend_configuration']['path'],'/task-native/config.toml']]
        value['environment']=dict(PATH='/usr/bin:/bin',HOME='/task-tmp',CODEX_HOME='/task-native',
            TMPDIR='/task-tmp',XDG_RUNTIME_DIR='/task-tmp',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1')
    return dict(prepare=prepare_projection,validate_runtime=lambda value,paths:None,
        mounts=_codex_mount_facts,project=_codex_scientific_arguments)


def _codex_mount_facts(root,job,work,*,context,source_facts,selected_code,mounts,value,**layout):
    from . import shared_input_views, experiments as e
    # The actual mount producer observes the installed configuration inode at
    # the effect boundary; it never reloads the runner's permission snapshot.
    original=value['backend_configuration']
    if e.proof(original['path'])!=original:
        raise e.IntegrityError('Original native sandbox implementation changed')
    return shared_input_views.load_mount_facts(root,job,work,include_skills=False,
        context=context,source_facts=source_facts,selected_code=selected_code,mounts=mounts)


def _codex_scientific_arguments(value,command,*,policy_cwd,project,cwd):
    from . import experiments as e, shared_input_views
    if _profile(value['policy'],policy_cwd,'/task-tmp',virtual=True,
            project_writes=shared_input_views.project_targets(project,writable=False))!=value['backend_permission_profile']['configuration']:
        raise e.IntegrityError('Accepted scientific cwd differs from its native permission projection')
    if project is not None:
        command=['/bin/sh','-c','cd "$1" && shift && exec "$@"','scid-project-cwd',cwd,*command]
    return [value['native']['path'],'sandbox','-P',PERMISSION_PROFILE,'-C',str(policy_cwd),'--',*command]


def _launch(value,config,accepted,command,cwd,*,public,root):
    """Independent legacy argv inspection owner (not used by the native bridge)."""
    from . import contracts as c, shared_input_views
    producers={};job=c.read_job(root/'jobs'/f'{config["scope"]["step"]:03d}.json',sources=producers)
    sources=shared_input_views.load_source_facts(root,job,Path(value['workspace']),material_sources=producers)
    selected=shared_input_views.materialize_selected_code(root,shared_input_views.selected_code_from_facts(sources))
    value=dict(value,runtime_programs=[value['native']['path']],private_roots=[],workspace_readonly=False,
        metadata=['.git','.agents','.codex','.aws'],
        configuration_mounts=[['--ro-bind',value['backend_configuration']['path'],'/task-native/config.toml']],
        environment=dict(PATH='/usr/bin:/bin',HOME='/task-tmp',CODEX_HOME='/task-native',TMPDIR='/task-tmp',
            XDG_RUNTIME_DIR='/task-tmp',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1'))
    return _launch_from_facts(value,config,accepted,command,cwd,public=public,root=root,
        source_facts=sources,selected_code=selected,material_sources=producers,projection=codex_projection(True,None))


def _launch_from_facts(value,config,accepted,command,cwd,*,public,root,source_facts,selected_code,
                       material_sources,projection):
    from . import experiments as e, shared_input_views, storage_policy
    from . import contracts as c
    from . import worker_execution_bridge, experiment_client, local_experiments
    context=storage_policy.Deadline(config['max_seconds'],upstream=worker_execution_bridge.operation_context())
    shared_input_views.ensure_capability(context=context)
    job=source_facts['job']
    if (source_facts['root']!=root or source_facts['work']!=Path(value['workspace'])
            or source_facts['target']!=Path(value['workspace'])
            or job.get('step')!=config['scope']['step']
            or Path(config['record_dir'])!=experiment_client._record_directory_from_facts(root,job['step'],job)
            or list(command)!=accepted['command'] or cwd!=accepted.get('cwd')):
        raise e.IntegrityError('Scientific launch differs from its owner-loaded bindings')
    project=job.get('project')
    if value.get('project')!=project:
        raise e.IntegrityError('Scientific project differs from its frozen native attempt')
    if config['execution_interface']['executor']!='local_execution:command':
        raise e.UnsupportedExecution('Selected executor has no default scientific subprocess namespace')
    directory=e.Records(config).directory(accepted['call_id'])
    work=Path(value['workspace']);source=Path(accepted['source_script'])
    input_sources=source_facts
    if input_sources['native']['intent'].get('attempt',0)!=value['native_attempt']['number']:
        raise e.IntegrityError('Scientific input facts belong to another native attempt')
    client_facts=experiment_client.client_bindings_from_facts(root,work,job,
        material_paths=input_sources['material_paths'],material_sources=material_sources)
    command=list(command);bindings=[];code_root=None;final_mounts=[];output=None
    if 'code_version' in accepted:
        from . import code_versions, project_layout
        try:version=project_layout.validate_code(project,accepted['code_version'],purpose='run')
        except (ValueError,KeyError,TypeError) as error:
            raise e.IntegrityError('Scientific code differs from its frozen project') from error
        if (source!=work/version['logical_root']/version['entry']
                or accepted.get('base_identity')!=(project['base_identity'] if project is not None else None)
                or any(key in accepted for key in ('entry','source_reference','source_input'))):
            raise e.IntegrityError('Scientific code differs from its owner-loaded logical source')
        code_root=work/version['logical_root']
        cwd=cwd or str(Path(project['base_root']) if project is not None else work)
        if (code_root.resolve()!=code_root or not code_root.is_dir()
                or Path(cwd).is_relative_to(code_root)):
            raise e.UnsupportedExecution('Scientific working directory must remain outside the ordinary code directory')
        if accepted.get('experiment_directory') is not None:
            target=Path(accepted['experiment_directory'])
            if (target.resolve()!=target or not target.is_dir() or not target.is_relative_to(work)
                    or str(target)!=cwd):
                raise e.UnsupportedExecution('Scientific experiment is outside its accepted working directory')
            for item in accepted['snapshot_files']:
                logical=target/item['name']
                reference=experiment_client._observe_input_reference(config,logical,facts=client_facts)
                if any(reference[key]!=item.get(key) for key in ('path','size_bytes','file_stat')):
                    raise e.IntegrityError('Scientific input differs from its accepted original')
                bindings.append(('--ro-bind',Path(item['path']),logical))
            artifacts=target/'artifacts'
            if artifacts.resolve()!=artifacts or artifacts.exists() and not artifacts.is_dir():
                raise e.UnsupportedExecution('Scientific artifacts require an ordinary output directory')
            output=local_experiments.artifact_directory_from_facts(config,accepted['call_id'],
                root=root,work=work,job=job)
            bindings.append(('--bind',output,artifacts))
        elif accepted.get('snapshot_files'):
            raise e.IntegrityError('Scientific code snapshots require their accepted experiment directory')
        command[accepted['script_index']]=str(Path(project['base_root'])/version['entry'] if project is not None else source)
    else:
        if source.resolve()!=source or not source.is_relative_to(work):
            raise e.UnsupportedExecution('Scientific source is outside its assigned workspace')
        bound_script=('entry' not in accepted and accepted['script']['path']!=str(directory/'source.py'))
        if bound_script:
            resolved=experiment_client._observe_payload_path(config,source,facts=client_facts)
            if (resolved==source or accepted['script']['path']!=str(resolved)
                    or any(e.proof(resolved).get(key)!=item for key,item in accepted['script'].items())):
                raise e.IntegrityError('Scientific bound package differs from its accepted original')
            final_mounts.append((resolved,source))
            command[accepted['script_index']]=str(source);cwd=cwd or str(work)
        elif 'entry' in accepted:
            target=Path(accepted['experiment_directory'])
            if target.resolve()!=target or not target.is_relative_to(work):
                raise e.UnsupportedExecution('Scientific experiment is outside its assigned workspace')
            bindings=[('--ro-bind',directory/'inputs',target),
                ('--bind',directory/'inputs/artifacts',target/'artifacts')]
            command[accepted['script_index']]=str(target/accepted['entry']);cwd=str(target)
        else:
            bindings=[('--ro-bind',directory/'source.py',source)]
            command[accepted['script_index']]=str(source);cwd=cwd or str(work)
    if (Path(cwd).resolve()!=Path(cwd) or not (Path(cwd).is_relative_to(work)
            or project is not None and Path(cwd)==Path(project['base_root']))):
        raise e.UnsupportedExecution('Scientific working directory is outside its assigned workspace')
    policy_cwd=work if project is not None else Path(cwd)
    scientific=projection['project'](value,command,policy_cwd=policy_cwd,project=project,cwd=cwd)
    if code_root is not None:
        try:
            export=code_versions.materialize(root,step=config['scope']['step'],call_id=accepted['call_id'],
                code_version=version,context=context)
        except (OSError,ValueError) as error:
            raise e.IntegrityError('Accepted code version original changed or is unavailable') from error
        bindings.insert(0,('--ro-bind',export,code_root))
        if accepted.get('experiment_directory') is not None:
            (Path(cwd)/'artifacts').mkdir(exist_ok=True)
    if project is not None and output is None:
        output=local_experiments.artifact_directory_from_facts(config,accepted['call_id'],
            root=root,work=work,job=job)
    wrapped=[value['bwrap']['path'],'--unshare-user','--unshare-pid','--unshare-ipc','--unshare-uts',
        '--die-with-parent','--new-session','--clearenv','--proc','/proc','--dev','/dev','--tmpfs','/tmp']
    runtime_roots=[Path(path) for path in ('/usr','/bin','/lib','/lib64')]
    for path in ('/usr','/bin','/lib','/lib64','/etc/resolv.conf','/etc/hosts','/etc/ssl/certs'):
        if Path(path).exists():wrapped.extend(['--ro-bind',path,path])
    python_roots=[]
    experiment_python=value.get('experiment_python')
    if experiment_python is not None:
        argv=experiment_python['run_argv']
        if config.get('run_argv')!=argv or command[:len(argv)]!=argv:
            raise e.IntegrityError('Scientific Python command differs from its frozen native attempt')
        python_roots=experiment_python['runtime_roots']
    elif command[0]==value['python']:
        python_roots=value['runtime_roots']
    if python_roots:
        for path in python_roots:
            if not Path(path).is_relative_to('/usr'):
                if (Path(path) in (Path('/'),Path.home()) or Path(value['home']).is_relative_to(path)
                        or directory.is_relative_to(path) or Path(path).is_relative_to(root)
                        or root.is_relative_to(path)):
                    raise e.UnsupportedExecution('Python runtime overlaps private native control')
                wrapped.extend(['--ro-bind',path,path])
                runtime_roots.append(Path(path))
    # Runtime binaries outside the system trees are exact read-only files;
    # neither their user's home nor the control source tree is mounted.
    projection['validate_runtime'](value,[*runtime_roots,Path(command[0]).absolute(),public,work])
    for path in dict.fromkeys((*value['runtime_programs'],str(Path(command[0]).absolute()))):
        if not any(Path(path).is_relative_to(base) for base in runtime_roots):
            wrapped.extend(['--ro-bind',path,path])
    _public_root(public,root,work,Path(value['home']),[*runtime_roots,
        *[Path(path) for path in ('/etc/resolv.conf','/etc/hosts','/etc/ssl/certs',
            *value['runtime_programs'],*value['private_roots'],str(Path(command[0]).absolute()))]])
    wrapped.extend(['--ro-bind',str(public),str(public)])
    wrapped.extend(shared_input_views.workspace_arguments(root,work,readonly=value['workspace_readonly']))
    for mount in value['configuration_mounts']:wrapped.extend(mount)
    wrapped.extend(['--tmpfs',str(work/'work/experiment'),'--tmpfs','/task-tmp','--chdir',cwd])
    # Codex protects these metadata directories even when they were absent at
    # preparation. Supply them in the host workspace before inner sandboxing.
    metadata_root=directory/'inputs' if code_root is None and 'entry' in accepted else policy_cwd
    for name in value['metadata']:
        path=metadata_root/name
        if not path.exists():path.mkdir(mode=0o700)
    input_facts=projection['mounts'](root,job,work,context=context,source_facts=input_sources,
        selected_code=selected_code,mounts=[*final_mounts,
            *[(source,target) for mode,source,target in bindings if mode=='--ro-bind']],
        code_root=code_root,cwd=Path(cwd),metadata_root=metadata_root,output=output,
        output_mounts=[(source,target) for mode,source,target in bindings if mode=='--bind'],
        value=value)
    input_mounts=shared_input_views.mount_arguments_from_facts(input_facts)
    if code_root is not None:
        # The exact captured export itself is expected; unrelated originals may
        # never be hidden under it or replaced by the code overlay.
        targets=[Path(target) for target,source_path in input_facts['mappings'].items()
            if (Path(source_path),Path(target))!=(export,code_root)]
        if any(target.is_relative_to(code_root) or code_root.is_relative_to(target) for target in targets):
            raise e.UnsupportedExecution('Scientific input mount overlaps the selected code directory')
    wrapped.extend(input_facts.get('workspace_writes',()))
    for mode,source_path,target in bindings:wrapped.extend([mode,str(source_path),str(target)])
    if project is not None:
        if code_root is None:
            raise e.UnsupportedExecution('Project local execution requires an exact project code version')
        wrapped.extend(shared_input_views.project_arguments(project,work,code_tree=export,
            artifacts=output,writable=False,context=context,base_facts=input_facts.get('base_facts')))
    wrapped.extend(input_mounts)
    for key,content in value['environment'].items():wrapped.extend(['--setenv',key,content])
    bootstrap='umask 077; chmod 700 /task-tmp; exec "$@"'
    scientific=['/bin/sh','-c',bootstrap,'scid-scientific-sandbox',*scientific]
    return shared_input_views._BwrapCommand(wrapped+scientific,command_index=len(wrapped)),None,None
