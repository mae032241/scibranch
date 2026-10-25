"""The default native attempt's scientific subprocess namespace.

This only supplies argv/cwd to LocalExecution. Acceptance, clocks, process
identity, cleanup and collection remain with the existing execution owner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
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


def prepare(root, step, record, work, home, args, *, standard, resume_position=None, native_options=None):
    """Freeze one host-only attempt; unsupported launchers still support reads."""
    from . import contracts as c, execution, experiments as e
    snapshot=record/'native-execution-permissions.json'
    value=dict(version=1,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        native_attempt=dict(number=len(execution.records(root,step))-1,execution_key=execution.identity(root,step)),
        intent=e.proof(record/'native-intent.json'),
        native_argv=list(args),home=str(home),workspace=str(work))
    job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
    project=job.get('project')
    if project is not None:
        frozen=c.settings(root,job)
        if frozen.get('project')!=project or frozen.get('native_options')!=native_options:
            raise e.IntegrityError('Project native policy differs from its frozen job')
        project_policy(frozen)
        value['project']=project
    public=Path(job['public_root'])
    native_args=list(args)
    try:
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
        helper=_native_program(args[:1])
        bwrap=shutil.which('bwrap')
        if bwrap is None:raise e.UnsupportedExecution('The native scientific namespace wrapper is unavailable')
        value.update(policy=policy,permission_sources=originals,native=dict(path=str(helper)),bwrap=dict(path=str(Path(bwrap).resolve())),
            python=str(Path(sys.executable).absolute()),runtime_roots=list(dict.fromkeys((sys.base_prefix,sys.prefix))))
        _public_root(public,root,work,home,[Path(path) for path in
            ('/usr','/bin','/lib','/lib64','/etc/resolv.conf','/etc/hosts','/etc/ssl/certs',
             *value['runtime_roots'],value['native']['path'],value['python'])])
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
    except e.UnsupportedExecution as error:
        if project is not None:raise
        value['unsupported']=str(error)
    with snapshot.open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    snapshot.chmod(0o400)
    original=e.proof(snapshot)

    def launch(config,accepted,command,cwd):
        if e.proof(snapshot)!=original or c.read(c.regular(snapshot))!=value:
            raise e.IntegrityError('Original native permissions changed')
        if (e.proof(record/'native-intent.json')!=value['intent']
                or c.record_ref(root/'jobs'/f'{step:03d}.json')!=value['task_ref']
                or accepted.get('native_attempt')!=value['native_attempt']
                or accepted.get('scope')!=config['scope']
                or config['scope']['job_ref']!=value['task_ref']):
            raise e.IntegrityError('Scientific launch differs from its original native attempt')
        if 'unsupported' in value:raise e.UnsupportedExecution(value['unsupported'])
        for key in ('backend_configuration',):
            if e.proof(value[key]['path'])!=value[key]:
                raise e.IntegrityError('Original native sandbox implementation changed')
        return _launch(value,config,accepted,command,cwd,public=public,root=root)
    return native_args,launch


def _launch(value,config,accepted,command,cwd,*,public,root):
    from . import experiments as e, shared_input_views, storage_policy
    from . import contracts as c
    from . import worker_execution_bridge, experiment_client
    context=storage_policy.Deadline(config['max_seconds'],upstream=worker_execution_bridge.operation_context())
    shared_input_views.ensure_capability(context=context)
    job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
    project=job.get('project')
    if value.get('project')!=project:
        raise e.IntegrityError('Scientific project differs from its frozen native attempt')
    if config['execution_interface']['executor']!='local_execution:command':
        raise e.UnsupportedExecution('Selected executor has no default scientific subprocess namespace')
    directory=e.Records(config).directory(accepted['call_id'])
    work=Path(value['workspace']);source=Path(accepted['source_script'])
    command=list(command);bindings=[];code_root=None;final_mounts=[]
    if 'code_version' in accepted:
        from . import code_versions
        if e._code_version_binding(config,accepted)!=root:
            raise e.IntegrityError('Scientific code belongs to another exact instance')
        version=accepted['code_version'];code_root=work/version['logical_root']
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
                reference=experiment_client.input_reference(config,logical)
                if any(reference[key]!=item.get(key) for key in ('path','size_bytes','file_stat')):
                    raise e.IntegrityError('Scientific input differs from its accepted original')
                bindings.append(('--ro-bind',Path(item['path']),logical))
            artifacts=target/'artifacts'
            if artifacts.resolve()!=artifacts or artifacts.exists() and not artifacts.is_dir():
                raise e.UnsupportedExecution('Scientific artifacts require an ordinary output directory')
            from . import local_experiments
            bindings.append(('--bind',local_experiments.artifact_directory(config,accepted['call_id']),artifacts))
        elif accepted.get('snapshot_files'):
            raise e.IntegrityError('Scientific code snapshots require their accepted experiment directory')
        command[accepted['script_index']]=str(Path(project['base_root'])/version['entry'] if project is not None else source)
    else:
        if source.resolve()!=source or not source.is_relative_to(work):
            raise e.UnsupportedExecution('Scientific source is outside its assigned workspace')
        bound_script=('entry' not in accepted and accepted['script']['path']!=str(directory/'source.py'))
        if bound_script:
            resolved=experiment_client.payload_path(config,source)
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
    project_writes=shared_input_views.project_targets(project,writable=False)
    if _profile(value['policy'],policy_cwd,'/task-tmp',virtual=True,project_writes=project_writes)!=value['backend_permission_profile']['configuration']:
        raise e.IntegrityError('Accepted scientific cwd differs from its native permission projection')
    input_mounts=None
    if code_root is not None:
        input_mounts=shared_input_views.mount_arguments(root,job,work,include_skills=False,context=context)
        targets=[Path(input_mounts[index+2]) for index,argument in enumerate(input_mounts)
            if argument in ('--bind','--ro-bind')]
        if any(target.is_relative_to(code_root) or code_root.is_relative_to(target) for target in targets):
            raise e.UnsupportedExecution('Scientific input mount overlaps the selected code directory')
        try:
            export=code_versions.materialize(root,step=config['scope']['step'],call_id=accepted['call_id'],
                code_version=version,context=context)
        except (OSError,ValueError) as error:
            raise e.IntegrityError('Accepted code version original changed or is unavailable') from error
        bindings.insert(0,('--ro-bind',export,code_root))
        if accepted.get('experiment_directory') is not None:
            (Path(cwd)/'artifacts').mkdir(exist_ok=True)
    backend=Path('/task-native')
    wrapped=[value['bwrap']['path'],'--unshare-user','--unshare-pid','--unshare-ipc','--unshare-uts',
        '--die-with-parent','--new-session','--clearenv','--proc','/proc','--dev','/dev','--tmpfs','/tmp']
    runtime_roots=[Path(path) for path in ('/usr','/bin','/lib','/lib64')]
    for path in ('/usr','/bin','/lib','/lib64','/etc/resolv.conf','/etc/hosts','/etc/ssl/certs'):
        if Path(path).exists():wrapped.extend(['--ro-bind',path,path])
    if command[0]==value['python']:
        for path in value['runtime_roots']:
            if not Path(path).is_relative_to('/usr'):
                if (Path(path) in (Path('/'),Path.home()) or Path(value['home']).is_relative_to(path)
                        or directory.is_relative_to(path)):
                    raise e.UnsupportedExecution('Python runtime overlaps private native control')
                wrapped.extend(['--ro-bind',path,path])
                runtime_roots.append(Path(path))
    # Runtime binaries outside the system trees are exact read-only files;
    # neither their user's home nor the control source tree is mounted.
    for path in dict.fromkeys((value['native']['path'],str(Path(command[0]).absolute()))):
        if not any(Path(path).is_relative_to(base) for base in runtime_roots):
            wrapped.extend(['--ro-bind',path,path])
    _public_root(public,root,work,Path(value['home']),[*runtime_roots,
        *[Path(path) for path in ('/etc/resolv.conf','/etc/hosts','/etc/ssl/certs',
            value['native']['path'],str(Path(command[0]).absolute()))]])
    wrapped.extend(['--ro-bind',str(public),str(public)])
    wrapped.extend(shared_input_views.workspace_arguments(root,work))
    wrapped.extend(['--ro-bind',value['backend_configuration']['path'],str(backend/'config.toml'),
        '--tmpfs',str(work/'work/experiment'),'--tmpfs','/task-tmp','--chdir',cwd])
    # Codex protects these metadata directories even when they were absent at
    # preparation. Supply them in the host workspace before inner sandboxing.
    metadata_root=directory/'inputs' if code_root is None and 'entry' in accepted else policy_cwd
    for name in ('.git','.agents','.codex','.aws'):
        path=metadata_root/name
        if not path.exists():path.mkdir(mode=0o700)
    for mode,source_path,target in bindings:wrapped.extend([mode,str(source_path),str(target)])
    if project is not None:
        if code_root is None:
            raise e.UnsupportedExecution('Project local execution requires an exact project code version')
        from . import local_experiments
        wrapped.extend(shared_input_views.project_arguments(project,work,code_tree=export,
            artifacts=local_experiments.artifact_directory(config,accepted['call_id']),writable=False,context=context))
        shared_input_views.project_overlap(project,[(Path(input_mounts[index+1]),Path(input_mounts[index+2]))
            for index,argument in enumerate(input_mounts) if argument in ('--bind','--ro-bind')])
    wrapped.extend(input_mounts if input_mounts is not None else
        shared_input_views.mount_arguments(root,job,work,mounts=final_mounts,include_skills=False,context=context))
    environment=dict(PATH='/usr/bin:/bin',HOME='/task-tmp',CODEX_HOME=str(backend),
        TMPDIR='/task-tmp',XDG_RUNTIME_DIR='/task-tmp',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1')
    for key,content in environment.items():wrapped.extend(['--setenv',key,content])
    bootstrap='umask 077; chmod 700 /task-tmp; exec "$@"'
    if project is not None:
        command=['/bin/sh','-c','cd "$1" && shift && exec "$@"','scid-project-cwd',cwd,*command]
    scientific=['/bin/sh','-c',bootstrap,'scid-scientific-sandbox',value['native']['path'],
        'sandbox','-P',PERMISSION_PROFILE,'-C',str(policy_cwd),'--',*command]
    return shared_input_views._BwrapCommand(wrapped+scientific,command_index=len(wrapped)),None,None
