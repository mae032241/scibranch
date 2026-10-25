"""Kernel readonly views of exact shared inputs, without changing native policy.

Same-user host processes and pre-existing writable descriptors remain trusted.
Only the assigned workspace and explicit framework mappings are inventoried.
"""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

DYNAMIC_ROOTS = ('retained-experiments', 'execution-results', 'retained-implementations')
# Launch capability bounds, independent of optional storage quotas. No bytes are read.
ALIAS_MAX_ENTRIES = 100_000
ALIAS_SECONDS = 15
_CAPABILITIES = set()


class _BwrapCommand(list):
    """Owned bwrap argv with its constructor's exact control/command boundary."""

    def __init__(self, arguments, *, command_index):
        super().__init__(arguments)
        if type(command_index) is not int or not 1 <= command_index <= len(self):
            raise ValueError('An owned bwrap command requires an exact command boundary')
        self.command_index = command_index


@contextmanager
def popen_arguments(command):
    """Keep owned bwrap control arguments outside execve's argv size limit.

    The descriptor lives only across Popen. bwrap consumes and closes its
    --args descriptor before executing the scientific child. Other launchers
    retain their original argv and receive no inherited descriptors.
    """
    if not isinstance(command, _BwrapCommand):
        yield command, ()
        return
    if not command:
        raise ValueError('An owned bwrap command requires its executable')
    if b'\0' in os.fsencode(command[0]):
        raise ValueError('bwrap arguments must not contain NUL bytes')
    with tempfile.TemporaryFile() as arguments:
        for index in range(1, len(command)):
            encoded = os.fsencode(command[index])
            if b'\0' in encoded:
                raise ValueError('bwrap arguments must not contain NUL bytes')
            if index < command.command_index:
                arguments.write(encoded)
                arguments.write(b'\0')
        arguments.seek(0)
        descriptor = arguments.fileno()
        yield [command[0], '--args', str(descriptor), *command[command.command_index:]], (descriptor,)


def _unsupported(message):
    from .experiments import UnsupportedExecution
    return UnsupportedExecution('Shared input readonly view unsupported: ' + message)


def _exact(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or any(p.is_symlink() for p in (path, *path.parents)):
        raise _unsupported('an exact ordinary path changed')
    return path


def _identity(path):
    try:
        info = _exact(path).stat()
    except FileNotFoundError as error:
        raise _unsupported('a bound input directory is missing or replaced') from error
    if not stat.S_ISDIR(info.st_mode):
        raise _unsupported('a fixed input directory changed')
    return [info.st_dev, info.st_ino]


def _binding(root, work):
    root, work = Path(root).absolute(), Path(work).absolute()
    if not work.is_relative_to(root):
        raise _unsupported('workspace is outside its exact instance')
    return root / 'host-runtime/shared-input-views' / work.relative_to(root) / 'binding.json'


def dynamic_root(root, work, name):
    """Create the fixed roots once; later publication must use the same inodes."""
    from . import atomic_files
    if name not in DYNAMIC_ROOTS:
        raise ValueError('Unknown shared input publication root')
    root, work = _exact(root), _exact(work)
    path = _binding(root, work)
    paths = [work, work / 'work', *[work / 'work' / item for item in DYNAMIC_ROOTS]]
    if not path.exists():
        for directory in paths:
            _exact(directory).mkdir(exist_ok=True)
        value = dict(workspace=str(work), directories=[dict(path=str(p), identity=_identity(p)) for p in paths])
        atomic_files.publish_bytes(path, (json.dumps(value, sort_keys=True) + '\n').encode(), existing_ok=True)
    from . import contracts as c
    value = c.read(c.regular(_exact(path)))
    expected = dict(workspace=str(work), directories=[dict(path=str(p), identity=_identity(p)) for p in paths])
    if (value.get('workspace')!=expected['workspace'] or value.get('directories')!=expected['directories']
            or set(value)-{'workspace','directories','holders'}):
        raise _unsupported('a bound workspace or retained root was replaced; publication refused')
    for holder,identity in value.get('holders',{}).items():
        if _identity(Path(holder))!=identity:
            raise _unsupported('a bound private original holder was replaced; publication refused')
    return work / 'work' / name


def workspace_arguments(root, work, *, target_work=None, readonly=False):
    """Install parent mountpoints before a launcher's context/remapping overlays."""
    work = _exact(work)
    target = Path(target_work) if target_work is not None else work
    dynamic_root(root, work, DYNAMIC_ROOTS[0])
    mode = '--ro-bind' if readonly else '--bind'
    return [mode, str(work), str(target), mode, str(work / 'work'), str(target / 'work')]


def project_targets(project, *, writable):
    """Exact namespace grants; the base itself is never a writable root."""
    if project is None:return []
    base = Path(project['base_root'])
    names = [*(project['editable'] if writable else []), *project['scratch'], *project['outputs']]
    return [base / name for name in names]


def project_overlap(project, mappings):
    """Reject exact scientific-input aliases intersecting a project write view."""
    if project is None:return
    targets = project_targets(project, writable=True)
    for source, destination in mappings:
        for path in (Path(source), Path(destination)):
            if any(path.is_relative_to(target) or target.is_relative_to(path) for target in targets):
                raise _unsupported('a declared project directory overlaps an exact scientific input')


def _project_readonly_aliases(project, work, target, mappings, *, context=None):
    """Project writable bind aliases must receive every final readonly overlay."""
    if project is None:return []
    from . import project_layout
    layout = project_layout.code_roots(project, work)
    backings = [(target / entry['source'].relative_to(work), entry['target'])
        for entry in layout['editable']]
    backings.extend((target / 'artifacts/project' / name, Path(project['base_root']) / name)
        for name in project['outputs'])
    aliases = {}
    for destination, source in mappings.items():
        if context is not None:context.remaining_seconds()
        destination, source = Path(destination), Path(source)
        for backing, visible in backings:
            if destination.is_relative_to(backing):
                aliases[str(visible / destination.relative_to(backing))] = source
            elif backing.is_relative_to(destination):
                # A readonly parent overlay also replaces the corresponding
                # writable subtree; do not leave its pre-overlay base alias.
                original = _exact(source / backing.relative_to(destination))
                if not original.exists():
                    raise _unsupported('a readonly parent view omits a declared project backing directory')
                aliases[str(visible)] = original
    return [(source, Path(destination)) for destination, source in aliases.items()]


def _project_directory(path):
    path = Path(path)
    for parent in reversed((path, *path.parents)):
        _exact(parent)
        if not parent.exists():parent.mkdir(mode=0o700)
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise _unsupported('a project backing directory is not ordinary')
    return path


def project_arguments(project, work, *, code_tree=None, artifacts, writable, context=None):
    """Whole readonly base, hidden root .git, and declared directory overlays.

    A run consumes an exact materialized commit, including absent empty roots;
    it never reads the current editable directories in the physical base. Host
    preparation may create missing empty directories for declared mountpoints;
    readonly host bases must already contain those directories.
    """
    if project is None:return []
    from . import project_layout
    base = _exact(project['base_root'])
    work, artifacts = _exact(work), _exact(artifacts)
    if writable and code_tree is not None:
        raise _unsupported('a writable project cannot use a retained code export')
    layout = project_layout.code_roots(project, work)
    code_view = writable or code_tree is not None
    excluded = [*(project['editable'] if code_view else []), *project['scratch'], *project['outputs']]
    project_layout.verify_base(project, context=context, exclude=excluded)
    targets = [base / name for name in excluded]
    for protected in (work, artifacts, *([Path(code_tree)] if code_tree is not None else [])):
        if protected.is_relative_to(base) or base.is_relative_to(protected):
            raise _unsupported('project base overlaps its private backing directories')
    for target in targets:
        if context is not None:context.remaining_seconds()
        try:
            _project_directory(target)
        except OSError as error:
            raise _unsupported('readonly host project bases require precreated declared mountpoint directories') from error
    repository = _exact(base / '.git')
    if not repository.is_dir():
        raise _unsupported('project Git control requires an ordinary directory')
    arguments = ['--ro-bind', str(base), str(base),
        '--tmpfs', str(repository), '--remount-ro', str(repository)]
    for entry in layout['editable'] if code_view else []:
        source = Path(code_tree) / entry['relative'] if code_tree is not None else entry['source']
        destination = entry['target']
        if code_tree is not None and not source.exists():
            arguments.extend(['--tmpfs', str(destination), '--remount-ro', str(destination)])
        else:
            source = _exact(source)
            if not source.is_dir():raise _unsupported('an exact project code directory is missing')
            arguments.extend(['--bind' if writable else '--ro-bind', str(source), str(destination)])
    for name in project['scratch']:
        arguments.extend(['--tmpfs', str(base / name)])
        if not writable and code_tree is None:arguments.extend(['--remount-ro', str(base / name)])
    for name in project['outputs']:
        source = _project_directory(artifacts / 'project' / name)
        arguments.extend(['--bind' if writable or code_tree is not None else '--ro-bind', str(source), str(base / name)])
    if context is not None:context.remaining_seconds()
    return arguments


class _Inventory:
    def __init__(self, context=None):
        from .storage_policy import Deadline
        self.context = Deadline(ALIAS_SECONDS, upstream=context)
        self.count = 0

    def check(self):
        self.context.remaining_seconds()
        self.count += 1
        if self.count > ALIAS_MAX_ENTRIES:
            raise _unsupported('workspace alias inventory exceeds the launch metadata capability limit')

    def walk(self, path):
        self.check()
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            yield path, (info.st_dev, info.st_ino)
        elif stat.S_ISDIR(info.st_mode):
            # Streaming metadata only: no recursive host/cache/extra-root scan.
            with os.scandir(path) as entries:
                for entry in entries:
                    yield from self.walk(Path(entry.path))
        elif not stat.S_ISLNK(info.st_mode):
            raise _unsupported('workspace alias inventory contains an unsupported file type')


def _pin_holders(root, work, holders):
    """Extend the one ephemeral launch binding; existing roots never rebind."""
    from . import atomic_files, contracts as c
    dynamic_root(root,work,DYNAMIC_ROOTS[0])
    path=_binding(root,work);value=c.read(c.regular(path))
    identities=dict(value.get('holders',{}))
    for holder in holders:identities[str(holder)]=_identity(holder)
    if identities!=value.get('holders',{}):
        value['holders']=identities
        atomic_files.replace_bytes(path,(json.dumps(value,sort_keys=True)+'\n').encode(),mode=0o400)


def _host_aliases(root, work, inventory):
    """Known framework aliases only; never recursively search another workspace.

    Private call/compilation holders are readonly to the Worker. Their existing
    host owners publish outside its namespace; managed children receive only the
    exact accepted artifacts exception through their independent launch view.
    """
    from . import contracts as c, experiment_client
    aliases=[];holders=[]
    def existing(path):
        inventory.check()
        if path.exists() or path.is_symlink():
            aliases.append(_exact(path))
    jobs=root/'jobs'
    if not jobs.exists():return aliases
    with os.scandir(_exact(jobs)) as entries:
        for entry in entries:
            inventory.check()
            path=Path(entry.path)
            if path.suffix!='.json':continue
            if not path.stem.isdecimal() or path.name!=f'{int(path.stem):03d}.json':
                raise _unsupported('the known job namespace changed')
            declared=c.read(c.regular(_exact(path),limit=32*1024*1024))
            step=declared.get('step')
            if type(step) is not int or step<=0 or path.name!=f'{step:03d}.json':
                raise _unsupported('a known job identity changed')
            node=c.workspace(root,step)
            for relative in ('inputs','conclusion.json',*[f'work/{name}' for name in DYNAMIC_ROOTS]):
                existing(node/relative)
            origin=c.host_record(node)
            records=[origin]
            continued=origin/'continuations'
            if continued.exists():
                with os.scandir(_exact(continued)) as attempts:
                    for attempt in attempts:
                        inventory.check();record=Path(attempt.path)
                        if not record.name.isdecimal() or record.name!=f'{int(record.name):03d}':
                            raise _unsupported('the known continuation namespace changed')
                        _identity(record);records.append(record)
            for record in records:
                for name in ('science-result.schema.json','science-output.json','author-science-context'):
                    existing(record/name)
            for number in ('001','002'):
                existing(root/'host-recovery/author-context'/f'{step:03d}'/number/'author-science-context')
            if declared.get('role')!='W':continue
            record_directory=experiment_client.record_directory(root,step)
            private=root/'host-runtime/experiment-control'/f'{step:03d}'
            for holder in (record_directory,private/'scientific-output-views',
                    private/'implementations',origin/'compiled-implementations'):
                inventory.check();_exact(holder).mkdir(parents=True,exist_ok=True,mode=0o700)
                _identity(holder);aliases.append(holder);holders.append(holder)
            # Legacy package aliases have an explicit compiler output inventory;
            # do not inventory arbitrary scientific artifacts or scratch trees.
            with os.scandir(origin/'compiled-implementations') as implementations:
                for implementation in implementations:
                    inventory.check();directory=_exact(Path(implementation.path))
                    compiler=directory/'compiler.json'
                    if not compiler.exists():continue
                    value=c.read(c.regular(compiler,limit=32*1024*1024))
                    output=Path(value['output']['path'])
                    if not output.is_relative_to(root):
                        raise _unsupported('a retained compiler alias escaped its instance')
                    existing(output)
    _pin_holders(root,work,holders)
    return aliases


def _native_intent(root, job):
    from . import contracts as c, execution
    record = execution.record(root, job['step'])
    intent = record / 'native-intent.json'
    return c.read(c.regular(intent)) if intent.exists() else {}


def _author_context(root, job):
    from . import author_closeout, contracts as c
    value = _native_intent(root, job)
    if value.get('author_context_version') != 1:return None
    proof = value['author_closeout_science_context']
    directory = author_closeout.validate_science_context(root, job, proof)
    return directory, c.read(author_closeout._check(root, proof))


def _wake_mappings(root, job, target):
    from . import contracts as c, execution, experiments as e, originals
    mappings = []
    def original(reference):
        return originals.resolve_root_ref(root, {key: reference[key]
            for key in ('path', 'size_bytes', 'file_stat') if key in reference})
    for number, reference in enumerate((_native_intent(root, job).get('execution_wake') or {}).get('observations', []), 1):
        observed = c.read(originals.resolve_root_ref(root, reference))
        accepted = c.read(originals.resolve_root_ref(root, observed['accepted']))
        attempt = accepted.get('native_attempt', {}).get('number', 0)
        config = c.read(c.regular(execution.record(root, job['step'], attempt) / 'experiment-config.json'))
        if accepted['scope'] != config['scope'] or accepted['interface'] != config['execution_interface']:
            raise _unsupported('a selected wake original changed its accepted owner')
        prefix = target / 'work/execution-results' / f'{number:03d}'
        if job.get('worker_view_version') == 1:
            for output in e.scientific_outputs(config, observed):
                mappings.append((Path(output.local_path), prefix / 'outputs' / output.name))
        else:
            for output in observed['outputs']:
                mappings.append((original(output), prefix / 'outputs' / output['name']))
            for index, proof in enumerate(observed['proofs'], 1):
                mappings.append((original(proof), prefix / 'originals' / f'{index:03d}'))
            mappings.append((originals.resolve_root_ref(root, observed['completed']), prefix / 'result.json'))
    return mappings


def selected_code_mounts(root, job, work, *, target_work=None, context=None, exports=None):
    """Materialize only exact selected review/context pins for this launch."""
    from . import code_versions, contracts as c
    root, work = Path(root), Path(work)
    target = Path(target_work) if target_work is not None else work
    selected = []
    if job.get('formal_review'):
        from . import formal_review, execution_inputs, qualification as q
        bound = formal_review.selected_compiler(root, job['formal_review'])
        if bound is not None and 'code_version' in bound[1]:
            ref, compiler = bound
            owner = q._read(root, compiler['job'])
            call_id = execution_inputs._compiler_call_id(root, owner['step'], ref)
            selected.append((owner['step'], call_id, compiler['code_version'],
                target / 'inputs/review/scientific-implementation.json', True))
    author = _author_context(root, job)
    if author is not None:
        from . import author_closeout
        directory, manifest = author
        for item in manifest['entries']:
            if 'code_version' not in item:continue
            step, call_id = author_closeout._context_code(root, job, item, controls_only=False)
            destination = root / item['display']['path']
            selected.append((step, call_id, item['code_version'], target / destination.relative_to(directory),
                item['kind'] == 'compiler_author' or item['code_version']['selection'] == 'file'))
    mappings = []
    for step, call_id, version, destination, file_entry in selected:
        if exports is not None:exports.append((step, call_id))
        export = code_versions.materialize(root, step=step, call_id=call_id,
            code_version=version, context=context)
        mappings.append((export / version['entry'] if file_entry else export, destination, step, call_id))
    return mappings


def _dynamic_mountpoints(work, target, selected, *, context=None):
    """Empty mountpoints only; fixed publication roots keep their real inodes."""
    for destination, source in selected.items():
        if context is not None:context.remaining_seconds()
        destination, source = Path(destination), Path(source)
        if (not destination.is_absolute() or '..' in destination.parts
                or not destination.is_relative_to(target)):
            raise _unsupported('a selected scientific view escaped its task')
        relative = destination.relative_to(target)
        if len(relative.parts) < 3 or relative.parts[0] != 'work' or relative.parts[1] not in DYNAMIC_ROOTS:
            continue
        actual = _exact(work / relative)
        if actual == source:continue
        parent = work / 'work' / relative.parts[1]
        _identity(parent)
        for component in relative.parts[2:-1]:
            parent = _exact(parent / component)
            parent.mkdir(exist_ok=True)
            _identity(parent)
        if source.is_dir():
            actual.mkdir(exist_ok=True)
            _identity(actual)
        else:
            try:
                descriptor = os.open(actual, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            except FileExistsError:
                if not stat.S_ISREG(actual.lstat().st_mode):
                    raise _unsupported('a selected file mountpoint changed type')
            else:
                os.close(descriptor)


def logical_original(root, job, path):
    """Host reads of an existing selected view use its source, not a mountpoint."""
    from . import author_closeout, code_versions, contracts as c
    work = c.workspace(root, job['step']); path = Path(path)
    for source, destination in _wake_mappings(root, job, work):
        if path == destination:return source
    author = _author_context(root, job)
    if author is None:return None
    directory, manifest = author
    for item in manifest['entries']:
        destination = work / (Path(root) / item['display']['path']).relative_to(directory)
        if 'code_version' in item:
            file_entry = item['kind'] == 'compiler_author' or item['code_version']['selection'] == 'file'
            if (path != destination if file_entry else not path.is_relative_to(destination)):
                continue
            step, call_id = author_closeout._context_code(root, job, item, controls_only=False)
            export = code_versions.materialize(root, step=step, call_id=call_id, code_version=item['code_version'])
            return export / (item['code_version']['entry'] if file_entry else path.relative_to(destination))
        if path == destination:
            return (author_closeout._check(root, item['display']) if item['kind'] in {'task', 'framework'}
                else author_closeout._context_source(root, item['source']))
    return None


def mount_arguments(root, job, work, *, target_work=None, mounts=(), private=False, include_skills=True, context=None):
    """Final ro overlays after ALL writable/context mappings.

    Explicit readonly (source,target) mappings override the default same target.
    Private canonical roots are only appropriate to the full-host generic outer;
    mapped scientific namespaces must leave private=False.
    """
    work, root = _exact(work), _exact(root)
    target = Path(target_work) if target_work is not None else work
    dynamic_root(root, work, DYNAMIC_ROOTS[0])
    mappings = {}
    for name in DYNAMIC_ROOTS:
        mappings[str(target / 'work' / name)] = work / 'work' / name
    skill_mounts = []
    if include_skills:
        from . import skill_supply
        skill_mounts = skill_supply.native_mounts(root, job)
        for source, destination in skill_mounts:
            destination = Path(destination)
            if destination.is_relative_to(work):
                destination = target / destination.relative_to(work)
            mappings[str(destination)] = Path(source)
    protected_sources=list(mappings.values())
    for source, destination in mounts:
        if Path(destination) != target / 'inputs':
            mappings[str(destination)] = Path(source)
        protected_sources.append(Path(source))
    # Logical task paths need no host file. The frozen job selects their actual
    # sources; these final overlays follow wider context and backend mappings.
    from . import contracts as c, experiment_client, originals
    declared = {}
    for item in experiment_client._input_rows(job):
        logical = originals.relative(item['path'])
        destination = str(target / logical)
        bound = dict(item, source=item.get('source', str(work / logical)))
        source = originals.material_path(root, bound)
        if destination in declared and declared[destination] != source:
            raise _unsupported('a declared logical input has ambiguous sources')
        declared[destination] = source
    if job.get('delivery_sources', {}).get('catalogue'):
        catalogue = c.regular(work / 'inputs/delivery-candidates.json')
        declared[str(target / 'inputs/delivery-candidates.json')] = catalogue
    for source, destination in _wake_mappings(root, job, target):
        declared[str(destination)] = source
    author = _author_context(root, job)
    if author is not None:
        from . import author_closeout
        directory, manifest = author
        for item in manifest['entries']:
            if 'code_version' in item:continue
            display = root / item['display']['path']
            source = (author_closeout._check(root, item['display']) if item['kind'] in {'task', 'framework'}
                else author_closeout._context_source(root, item['source']))
            declared[str(target / display.relative_to(directory))] = source
    for source, destination, _, _ in selected_code_mounts(root, job, work,
            target_work=target, context=context):
        declared[str(destination)] = source
    for destination, source in declared.items():
        mappings[destination] = source
        protected_sources.append(source)
    # Trusted launchers already verify exact accepted source identities. Their
    # explicit file mappings take priority at this same final overlay seam.
    for source, destination in mounts:
        if Path(destination) != target / 'inputs':
            mappings[str(destination)] = Path(source)
            if str(destination) in declared:declared[str(destination)] = Path(source)
    project_overlap(job.get('project'), [(source, Path(destination)) for destination, source in mappings.items()])
    _dynamic_mountpoints(work, target, declared, context=context)
    inventory = _Inventory(context)
    host_aliases=_host_aliases(root,work,inventory) if private else []
    protected = set()
    for path in host_aliases:
        inventory.check();info=path.lstat()
        if stat.S_ISREG(info.st_mode):protected.add((info.st_dev,info.st_ino))
    for source in dict.fromkeys(protected_sources):
        _exact(source)
        protected.update(identity for _, identity in inventory.walk(source))
    workspace_files = list(inventory.walk(work))
    # Recheck every exact entry before handing the plan to Popen. A disappearing
    # or replaced alias fails closed, never silently drops its protection.
    for path, identity in workspace_files:
        info = path.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino) != identity or not stat.S_ISREG(info.st_mode):
            raise _unsupported('workspace alias changed during launch inventory')
        if identity in protected:
            destination = str(target / path.relative_to(work))
            if Path(destination).is_relative_to(target / 'inputs') and destination not in declared:
                continue
            if not any(Path(destination).is_relative_to(Path(base)) for base in mappings):
                mappings[destination] = path
    if private:
        for path in host_aliases:
            if path != work / 'inputs':mappings[str(path)]=path
        for name in ('host-history/objects', 'project/global', 'source-provenance/originals',
                'host-runtime/fork'):
            path = root / name
            if path.exists():
                mappings[str(path)] = _exact(path)
    dynamic_root(root, work, DYNAMIC_ROOTS[0])
    arguments = []
    inputs = target / 'inputs'
    input_mappings = [(Path(destination), source) for destination, source in mappings.items()
        if Path(destination).is_relative_to(inputs)]
    for destination, source in mappings.items():
        if Path(destination).is_relative_to(inputs):continue
        arguments.extend(['--ro-bind', str(source), destination])
    # Build missing logical mountpoints only in this namespace. Freezing this
    # inputs tree leaves the already-bound dynamic publication roots intact.
    if input_mappings:
        _exact(work / 'inputs').mkdir(exist_ok=True)
        arguments.extend(['--tmpfs', str(inputs)])
        directories = {parent for destination, _ in input_mappings for parent in destination.parents
            if parent != inputs and parent.is_relative_to(inputs)}
        for directory in sorted(directories, key=lambda path: (len(path.parts), str(path))):
            arguments.extend(['--dir', str(directory)])
        for destination, source in sorted(input_mappings, key=lambda item: len(item[0].parts)):
            arguments.extend(['--ro-bind', str(source), str(destination)])
        arguments.extend(['--remount-ro', str(inputs)])
    if job.get('role') == 'W':
        for source, destination in _project_readonly_aliases(job.get('project'), work, target, mappings, context=context):
            arguments.extend(['--ro-bind', str(source), str(destination)])
    if context is not None:context.remaining_seconds()
    return arguments


def _probe(program, generation, timeout):
    with tempfile.TemporaryDirectory(prefix='scid-readonly-probe-') as temporary:
        base = Path(temporary); protected = base / 'input'; protected.write_text('original')
        source = ('import os,pathlib,pty,sys; p=pathlib.Path(sys.argv[1]); '
            'exec("for action in (lambda: p.chmod(0o600), lambda: p.write_text(\'changed\')):\\n'
            ' try: action()\\n except OSError: pass\\n else: raise SystemExit(31)"); '
            '(p.parent/"writable").write_text("ok"); '
            'master,slave=pty.openpty(); os.close(master); os.close(slave)')
        try:
            controls = [program, '--unshare-user', '--die-with-parent', '--bind', '/', '/',
                '--dev', '/dev', '--ro-bind', str(protected), str(protected)]
            command = _BwrapCommand([*controls, '--', sys.executable, '-I', '-c', source, str(protected)],
                command_index=len(controls))
            with popen_arguments(command) as (argv, pass_fds):
                result = subprocess.run(argv, pass_fds=pass_fds,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _unsupported('Linux readonly mount capability probe failed') from error
        if result.returncode or protected.read_text() != 'original' or not (base / 'writable').exists():
            raise _unsupported('Linux user/mount namespaces cannot enforce readonly inputs')
    return program


def ensure_capability(*, context=None):
    """Actually exercise the kernel mount capability before a model submission."""
    if sys.platform != 'linux':
        raise _unsupported('Linux readonly mounts are required')
    program = shutil.which('bwrap')
    if program is None:
        raise _unsupported('bwrap is unavailable')
    program = str(Path(program).resolve(strict=True)); info = Path(program).stat()
    timeout = min(10, context.remaining_seconds()) if context is not None else 10
    key=(program,info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size)
    if key not in _CAPABILITIES:
        _probe(program,key[1:],timeout)
        _CAPABILITIES.add(key)
    if context is not None:context.remaining_seconds()
    return program


def wrap(root, job, work, argv, *, context=None):
    """All local native argv, including opaque protocol/custom launchers."""
    program = ensure_capability(context=context)
    holder, ancestors = _private_git_arguments(root)
    project = job.get('project')
    project_mounts = []
    if project is not None:
        from . import contracts as c, native_execution
        settings = c.settings(root, job)
        if settings.get('project') != project:
            raise _unsupported('project namespace differs from its frozen job')
        policy = native_execution.project_policy(settings)
        worker = job.get('role') == 'W'
        if worker:
            policy = dict(policy, writable_roots=[str(path) for path in project_targets(project, writable=True)])
            declaration = 'sandbox_workspace_write='+native_execution._toml(policy)
            if not any(argument == declaration and argv[index-1] == '-c'
                    for index, argument in enumerate(argv) if index):
                argv = list(argv)
                position = argv.index('resume') if 'resume' in argv else len(argv)-1
                argv[position:position] = ['-c', declaration]
        project_mounts = project_arguments(project, work, artifacts=work/'artifacts', writable=worker, context=context)
    controls = [program, '--unshare-user', '--unshare-pid', '--die-with-parent', '--bind', '/', '/', '--proc', '/proc',
        '--dev', '/dev',
        *ancestors, *workspace_arguments(root, work), *project_mounts,
        *mount_arguments(root, job, work, private=True, context=context),
        '--ro-bind', str(holder), str(holder)]
    return _BwrapCommand([*controls, '--', *argv], command_index=len(controls))


def _private_git_arguments(root):
    """Prepare the planned holder and pin every renameable ancestor mount."""
    try:
        root = _exact(root)
        for directory in (root, *root.parents):
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise _unsupported('private code requires ordinary directory ancestors')
        holder = root / 'host-runtime/code-versions'
        for directory in (holder.parent, holder):
            _exact(directory).mkdir(mode=0o700, exist_ok=True)
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise _unsupported('private code requires an ordinary directory holder')
    except OSError as error:
        raise _unsupported('private code directory is missing or not ordinary') from error
    arguments = []
    for directory in reversed(holder.parents):
        if directory != Path('/'):
            arguments.extend(['--bind', str(directory), str(directory)])
    return holder, arguments
