"""Pi capsule: readonly host, private instance hidden, declared workspace access.

The framework-owned workspace root stays readonly, including absent publication
names. workspace-write opens only scientific work/artifacts/experiments slots;
exact readonly originals override these grants. Runtime/session and temporary
exceptions remain separate. No host final-output/publication exception.
This is a same-user soft boundary, not confidentiality or network isolation.
"""
import os
from pathlib import Path
import shutil
import stat
import sys

TEMPORARY = (Path('/tmp'), Path('/var/tmp'))
METADATA = ('.git', '.agents', '.codex', '.aws', '.pi')
# These are the existing generated-material and collected-deliverable scopes,
# not a grant to arbitrary parents of a requested file. Local/projects need their
# own explicit slots. Controls already published inside work/ are overlaid RO;
# bind mounts cannot reserve an absent child of that writable directory.
SCIENTIFIC_SLOTS = ('work', 'artifacts', 'experiments')
CONTROL_FILES = ('science-task.json', 'conclusion.json', 'science-result.schema.json',
    'work/experiment-config.json', 'work/science-resume.json',
    'work/author-closeout-science.json', 'work/author-closeout-facts.json',
    'author-closeout-facts.json', 'work/.scid-budget-notice.py')


def _unsupported(message):
    from .experiments import UnsupportedExecution
    return UnsupportedExecution('Pi filesystem namespace unsupported: ' + message)


def overlaps(left, right):
    left, right = Path(left), Path(right)
    return left.is_relative_to(right) or right.is_relative_to(left)


def agent_directory():
    return Path(os.environ.get('PI_CODING_AGENT_DIR') or str(Path.home() / '.pi/agent')).expanduser()


def _ordinary(path, *, directory):
    from .shared_input_views import _exact
    try:
        path = _exact(path); mode = path.lstat().st_mode
    except OSError as error: raise _unsupported('an exact runtime path is missing') from error
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise _unsupported('runtime paths must be ordinary exact files/directories')
    return path


def project_policy(settings):
    """Pure Pi permission projection; no Codex configuration or native defaults."""
    from .pi_backend import validate_configuration, validate_launcher
    validate_launcher(settings.get('native_launcher_argv'))
    options=validate_configuration(settings.get('native_options'))
    if (options['workspace']!='workspace-write'
            or settings.get('native_capabilities',{}).get('native_launcher_contract') is not None):
        raise _unsupported('projects require an explicit workspace-write policy without a custom launcher contract')
    return options


def project_boundary(project, *, root, work, protected):
    from . import project_layout, shared_input_views as views
    project_layout.code_roots(project,work)
    base=views._exact(project['base_root'])
    reserved=[root,work,*protected,*[Path(p) for p in
        ('/usr','/bin','/sbin','/lib','/lib64','/etc','/dev','/proc','/sys','/run','/task-native','/task-tmp')]]
    if Path.home().is_relative_to(base) or any(overlaps(base,path) for path in reserved):
        raise _unsupported('project base overlaps private control, authentication or runtime')
    for target in views.project_targets(project,writable=True):
        if any(overlaps(target,base/name) for name in METADATA):
            raise _unsupported('project write grants overlap protected native metadata')


def execution_projection(*, observer_source=None, private_metadata_sources=()):
    """Provider facts and pure argv projection for the existing Local closure."""
    from functools import partial
    return dict(prepare=partial(_prepare_execution_projection, observer_source=observer_source,
        private_metadata_sources=private_metadata_sources),validate_runtime=_validate_execution_runtime,
        mounts=_load_execution_mounts,project=execution_arguments_from_facts)


def _prepare_execution_projection(value,args,*,root,record,work,home,job,settings,resume_position,
        observer_source=None, private_metadata_sources=()):
    from . import shared_input_views as views
    from .pi_backend import validate_configuration, validate_launcher
    validate_launcher(settings['native_launcher_argv'])
    options=validate_configuration(settings['native_options'])
    if options['workspace']!='workspace-write':
        raise _unsupported('managed Local cannot add write grants to a read-only native policy')
    if job.get('project') is not None:project_policy(settings)
    auth=_ordinary(agent_directory(),directory=True)
    if any(overlaps(auth,path) for path in (root,work,home)):
        raise _unsupported('Local authentication overlaps instance/session storage')
    value.update(policy=options,permission_sources=[],runtime_programs=[],private_roots=[str(auth),str(home)],
        private_observer_source=observer_source, private_metadata_sources=tuple(private_metadata_sources),
        bwrap=dict(path=views.ensure_capability()),python=str(Path(sys.executable).absolute()),
        runtime_roots=list(dict.fromkeys((sys.base_prefix,sys.prefix))),workspace_readonly=True,
        metadata=list(METADATA),configuration_mounts=[],applied_native_argv=list(args),
        environment=dict(PATH='/usr/bin:/bin',HOME='/task-tmp',TMPDIR='/task-tmp',
            XDG_RUNTIME_DIR='/task-tmp',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1'))
    _validate_execution_runtime(value,[Path(p) for p in
        ('/usr','/bin','/lib','/lib64','/etc/resolv.conf','/etc/hosts','/etc/ssl/certs')])


def _validate_execution_runtime(value,paths):
    for path in paths:
        if any(overlaps(path,private) for private in value['private_roots']):
            raise _unsupported('scientific runtime/public mount overlaps native authentication or private sessions')


def execution_arguments_from_facts(value, command, *, policy_cwd, project, cwd):
    """Pure scientific argv: no Pi executable, auth, CODEX_HOME or native query."""
    from .pi_backend import validate_configuration
    if validate_configuration(value['policy'])['workspace'] != 'workspace-write':
        raise _unsupported('scientific permissions differ from the frozen native declaration')
    if project is not None:
        return ['/bin/sh','-c','cd "$1" && shift && exec "$@"','scid-project-cwd',cwd,*command]
    return list(command)


def _load_execution_mounts(root,job,work,*,context,source_facts,selected_code,mounts,
        code_root,cwd,metadata_root,output,output_mounts,value):
    """Actual effect boundary: observe mutable aliases and accepted output slots."""
    from . import shared_input_views as views
    protected=[];private_metadata=[]
    observer_source = value.get('private_observer_source')
    private_sources = [*value.get('private_metadata_sources', ())]
    if observer_source is not None: private_sources.append(observer_source)
    for private_source in dict.fromkeys(private_sources):
        # These exact current originals must never become captured scientific
        # code or a Local work/output alias. No session-directory inventory or
        # metadata content read. Same-user deliberate copies remain trusted.
        source = _ordinary(private_source, directory=False)
        if source.stat().st_nlink != 1:
            raise _unsupported('private observer source has a scientific/runtime alias')
    for name in CONTROL_FILES:
        path=work/name
        if path.exists() or path.is_symlink():protected.append(_ordinary(path,directory=False))
    for name in METADATA:
        path=metadata_root/name
        path=_ordinary(path,directory=True)
        (protected if path.is_relative_to(work) else private_metadata).append(path)
        # Preserve protected metadata under independent runtime/output aliases.
        if cwd.is_relative_to(work):mounts=[*mounts,(path,cwd/name)]
    project=job.get('project');base_facts=None
    if project is not None:
        project_boundary(project,root=root,work=work,protected=[*value['private_roots'],
            Path(job['public_root']),*[Path(p) for p in value['runtime_roots']]])
        base_facts=views.load_project_base_facts(project,writable=False,code_tree=code_root,context=context)
    originals=views.load_execution_original_facts(root,job,work,native=source_facts['native'],base_facts=base_facts)
    originals['protected_sources']=(*originals['protected_sources'],*private_metadata)
    facts=views.load_pi_mount_facts(root,job,work,context=context,source_facts=source_facts,
        selected_code=selected_code,mounts=mounts,protected_paths=protected,
        metadata_exclude=() if code_root is None else (code_root,),output_artifacts=output,
        writable_output_mounts=output_mounts,**originals)
    for destination,source in facts['mappings'].items():
        _validate_execution_runtime(value,[source])
    facts['workspace_writes']=[argument for name in SCIENTIFIC_SLOTS
        for argument in ('--bind',str(_ordinary(work/name,directory=True)),str(work/name))]
    facts['base_facts']=base_facts
    return facts


def load_facts(root, job, work, *, policy, native_home, native_argv, context,
        selected_code, source_facts, observation=None):
    from . import shared_input_views as views
    from .pi_backend import validate_configuration, validate_launcher
    options = validate_configuration(policy.get('native_options'))
    validate_launcher(policy.get('native_launcher_argv'))
    if source_facts is None or selected_code is None:
        raise _unsupported('the operation owner must supply accepted source and selected-code facts')
    if source_facts.get('policy') != policy:
        raise _unsupported('resource and mount facts differ from the owner-loaded frozen policy')
    if (job.get('role') not in {'H', 'W', 'A', 'E'}
            or policy.get('experiment_mode') not in {'local','external'}
            or job.get('project')!=policy.get('project')):
        raise _unsupported('role, experiment mode or frozen project changed')
    project=job.get('project')
    if project is not None:project_policy(policy)
    root, work = _ordinary(root, directory=True), _ordinary(work, directory=True)
    home = _ordinary(native_home, directory=True)
    sessions = _ordinary(home / 'sessions', directory=True)
    auth = _ordinary(agent_directory(), directory=True)
    if (not work.is_relative_to(root) or work == root
            or root.is_relative_to(home) or Path.home().is_relative_to(home) or overlaps(home, work)
            or overlaps(auth, root) or overlaps(auth, home) or Path.home().is_relative_to(auth)
            or any(path.is_relative_to(auth) for path in TEMPORARY)
            or any(overlaps(auth, path) for path in (Path('/proc'), Path('/dev'), Path('/usr'), Path('/etc')))):
        raise _unsupported('session or inherited runtime directory overlaps control/runtime roots')
    for path in (Path('/proc'), Path('/dev'), *TEMPORARY):
        if root == path or path.is_relative_to(root) or home == path:
            raise _unsupported('instance/session storage covers an isolated runtime root')
    from .pi_transport import load_resource_settings
    # Current mutable SDK settings and all four fixed inherited system-prompt
    # candidates are observed once at native admission, even for empty Skills,
    # before capability probes, projection writes, or ANY native process.
    # No prompt-content read, ancestor/package discovery or auth/config copying.
    resource_policy = load_resource_settings(auth, work, mode=options.get('resource_mode', 'closed'))
    entry = shutil.which(native_argv[0])
    if entry is None: raise _unsupported('selected launcher unavailable')
    launcher = _ordinary(Path(entry).resolve(strict=True), directory=False)
    python_argument = Path(sys.executable).absolute()
    python = _ordinary(python_argument.resolve(strict=True), directory=False)
    runtime = [(_ordinary(Path(__file__).resolve().parents[2], directory=True), True),
        *[(_ordinary(Path(p).resolve(), directory=True), True) for p in dict.fromkeys((sys.prefix, sys.base_prefix))],
        (python, False), (launcher, False)]
    if launcher.suffix in {'.js', '.mjs', '.cjs'}:
        modules = next((p for p in reversed(launcher.parents) if p.name == 'node_modules'), None)
        if modules is not None: runtime.append((_ordinary(modules, directory=True), True))
        elif launcher.parent.name in {'bin', 'dist'}:
            runtime.append((_ordinary(launcher.parent.parent, directory=True), True))
    mounts = []
    for path, directory in runtime:
        if (overlaps(path, root) or overlaps(path, auth) or overlaps(path, home)
                or path in (*TEMPORARY, Path('/'), Path.home())):
            raise _unsupported('runtime path overlaps private control or authentication')
        if any(path.is_relative_to(t) for t in TEMPORARY): mounts.append((path, path))
    if any(python_argument.is_relative_to(t) for t in TEMPORARY):
        if any(overlaps(python_argument, p) for p in (root, auth, home)):
            raise _unsupported('Python alias overlaps private control')
        if not any(directory and python_argument.is_relative_to(p) for p, directory in runtime):
            mounts.append((python, python_argument))
    node = shutil.which('node')
    if node is not None:
        alias = Path(node).absolute(); node = _ordinary(alias.resolve(strict=True), directory=False)
        for path in dict.fromkeys((node, alias)):
            if any(path.is_relative_to(t) for t in TEMPORARY):
                if any(overlaps(path, p) for p in (root, auth, home)): raise _unsupported('Node alias overlaps private control')
                mounts.append((node, path))
    program = views.ensure_capability(context=context)
    for name in SCIENTIFIC_SLOTS:
        path = views._exact(work / name)
        path.mkdir(mode=0o700, exist_ok=True)
        _ordinary(path, directory=True)
    protected = []
    for name in METADATA:
        path = views._exact(work / name)
        if not path.exists():path.mkdir(mode=0o700)
        protected.append(path)
    for name in CONTROL_FILES:
        path = work / name
        if path.exists() or path.is_symlink():
            protected.append(_ordinary(path, directory=False))
    code_writes=[];project_mounts=[];base_facts=None
    writable=job.get('role')=='W' and policy['native_options']['workspace']=='workspace-write'
    if project is not None:
        project_boundary(project,root=root,work=work,protected=[home,auth,*[p for p,_ in runtime]])
        base_facts=views.load_project_base_facts(project,writable=writable,context=context)
        project_mounts=views.project_arguments(project,work,artifacts=work/'artifacts',
            writable=writable,context=context,base_facts=base_facts)
    if writable:
        from . import project_layout
        code=views._exact(project_layout.code_roots(project,work)['root'])
        code.mkdir(mode=0o700,exist_ok=True);_ordinary(code,directory=True)
        code_writes=['--bind',str(code),str(code)]
    from . import skill_supply
    resources = (skill_supply.load_selected_resources(root, policy, job)
        if 'step' in job else dict(resources=(), mounts=()))
    resource_roots = [path for path, _ in resources['mounts'] if not path.is_relative_to(work)]
    if any(overlaps(path, private) for path in resource_roots for private in (root, auth, home)):
        raise _unsupported('selected installation resources overlap private control or authentication')
    originals=views.load_execution_original_facts(root,job,work,native=source_facts['native'],
        base_facts=base_facts) if 'step' in job else {}
    observer_sources = ()
    if observation is not None:
        from .pi_transport import observation_proof
        observation = observation_proof(observation)
        if (observation['path'] != str(sessions / 'pi-session.jsonl')
                or observation['workspace'] != str(work)):
            raise _unsupported('observer differs from the exact session/work binding')
        observer_sources = (_ordinary(observation['extension'], directory=False),)
        originals['protected_sources'] = (*originals.get('protected_sources', ()), *observer_sources)
    mappings = views.load_pi_mount_facts(root, job, work,
        context=context, selected_code=selected_code, source_facts=source_facts,
        mounts=resources['mounts'], protected_paths=protected,**originals)
    # No mapping may reopen a private path hidden by this capsule. The producer
    # already binds the logical input destinations, not their canonical aliases.
    for target, source in mappings['mappings'].items():
        if not Path(target).is_relative_to(work) and Path(target) not in resource_roots:
            raise _unsupported('input mapping escapes exact workspace or selected installation resource')
        if any(Path(source).is_relative_to(writable) for writable in (auth, sessions)):
            raise _unsupported('an original overlaps a writable auth/session exception')
    return dict(program=program, mounts=mappings, root=root, work=work, home=home,
        sessions=sessions, auth=auth, runtime=tuple(dict.fromkeys(mounts)),
        readonly=policy['native_options']['workspace'] == 'read-only',
        code_writes=code_writes,project_mounts=project_mounts, resources=resources['resources'],
        resource_policy=resource_policy, observer_sources=observer_sources,
        launcher=launcher, launcher_argument=native_argv[0],
        rpc_prefix=(sys.executable, '-m', 'scidiscovery.research_loop.pi_transport'))


def arguments_from_facts(facts):
    root, work = facts['root'], facts['work']
    args = ['--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
        '--die-with-parent', '--new-session', '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev']
    for path in TEMPORARY: args += ['--tmpfs', str(path)]
    for source, target in facts['runtime']: args += ['--ro-bind', str(source), str(target)]
    mode = '--ro-bind' if facts.get('readonly', True) else '--bind'
    # A readonly parent reserves absent top-level controls without creating
    # placeholders on either the host or in the scientific namespace.
    args += ['--tmpfs', str(root), '--ro-bind', str(work), str(work),
        mode, str(work / 'work'), str(work / 'work')]
    if not facts.get('readonly', True):
        for name in SCIENTIFIC_SLOTS[1:]:
            args += ['--bind', str(work / name), str(work / name)]
    args += facts.get('code_writes',[])
    args += facts.get('project_mounts',[])
    for path in (facts['sessions'], facts['auth']): args += ['--bind', str(path), str(path)]
    for path in facts.get('observer_sources', ()): args += ['--ro-bind', str(path), str(path)]
    return args


def wrap_from_facts(facts, argv):
    from .shared_input_views import _BwrapCommand, mount_arguments_from_facts
    command = list(argv)
    prefix = list(facts['rpc_prefix'])
    if command[:len(prefix)] != prefix or len(command) <= len(prefix):
        raise _unsupported('launch differs from the owned RPC bridge')
    slot = len(prefix)
    if command[slot] == '--resume-proof':
        slot += 2
    if slot >= len(command) or command[slot] != '--':
        raise _unsupported('launch differs from the owned RPC bridge controls')
    slot += 1
    if slot >= len(command) or command[slot] not in {facts['launcher_argument'], str(facts['launcher'])}:
        raise _unsupported('launch differs from the selected original executable')
    command[slot] = str(facts['launcher'])
    # Pure consumption of the launch owner's live source/projection facts. A
    # separate bridge proof binds native get_commands metadata before prompt.
    # The controlled observer has its own exact private-source proof slot;
    # selected Skills confer no arbitrary user-extension grant.
    import json
    from .pi_transport import runtime_resource_mode, resource_proof
    runtime_resource_mode(facts.get('resource_policy'))
    if 'resources' not in facts:
        raise _unsupported('launch owner must supply explicit resource facts, including empty selection')
    resources = resource_proof(list(facts['resources']))
    paths = []
    for resource in resources:
        path = resource['path']
        if (str(Path(path)) != path or not Path(path).is_absolute()
                or Path(path).name != 'SKILL.md'
                or facts['mounts']['mappings'].get(str(Path(path).parent)) != Path(path).parent):
            raise _unsupported('selected Skill differs from its readonly source facts')
        paths.append(path)
    if (len(paths) != len(set(paths)) or any(flag in command
            for flag in ('--skill', '--resource-proof', '--resource-policy'))):
        raise _unsupported('duplicate native resource binding')
    observation = facts.get('observation')
    if observation is not None:
        extension = Path(observation['extension'])
        helper = Path(facts['work']) / 'work' / '.scid-budget-notice.py'
        if (facts.get('observer_sources') != (extension,)
                or facts['mounts']['mappings'].get(str(helper)) != helper
                or '--extension' in command or '--observation-proof' in command):
            raise _unsupported('controlled observer differs from readonly source facts')
        command[len(prefix):len(prefix)] = ['--observation-proof', json.dumps(observation,
            ensure_ascii=True, separators=(',', ':'))]
        command.extend(['--extension', str(extension)])
    command[len(prefix):len(prefix)] = ['--resource-policy', json.dumps(facts['resource_policy'],
        ensure_ascii=True, separators=(',', ':')), '--resource-proof', json.dumps(list(resources),
        ensure_ascii=True, separators=(',', ':'))]
    command.extend(part for path in paths for part in ('--skill', path))
    controls = [facts['program'], *arguments_from_facts(facts),
        *mount_arguments_from_facts(facts['mounts']), '--remount-ro', str(facts['root']),
        '--chdir', str(facts['work'])]
    return _BwrapCommand([*controls, '--', *command], command_index=len(controls))
