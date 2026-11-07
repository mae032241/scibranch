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
_UNLOADED_AUTHOR = object()


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


def _load_dynamic_binding(root, work):
    """Own one declaration read at a preparation or publication boundary."""
    from . import atomic_files
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
    _observe_dynamic_binding(root, work, value)
    return value


def _observe_dynamic_binding(root, work, value):
    """Observe current directory identities against the already read declaration."""
    paths = [work, work / 'work', *[work / 'work' / item for item in DYNAMIC_ROOTS]]
    expected = dict(workspace=str(work), directories=[dict(path=str(p), identity=_identity(p)) for p in paths])
    if (value.get('workspace')!=expected['workspace'] or value.get('directories')!=expected['directories']
            or set(value)-{'workspace','directories','holders'}):
        raise _unsupported('a bound workspace or retained root was replaced; publication refused')
    for holder,identity in value.get('holders',{}).items():
        if _identity(Path(holder))!=identity:
            raise _unsupported('a bound private original holder was replaced; publication refused')


def dynamic_root(root, work, name):
    """Create the fixed roots once; later publication must use the same inodes."""
    if name not in DYNAMIC_ROOTS:
        raise ValueError('Unknown shared input publication root')
    work = Path(work)
    _load_dynamic_binding(root, work)
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
    from . import atomic_files
    value=_load_dynamic_binding(root,work)
    path=_binding(root,work)
    identities=dict(value.get('holders',{}))
    for holder in holders:identities[str(holder)]=_identity(holder)
    if identities!=value.get('holders',{}):
        value['holders']=identities
        atomic_files.replace_bytes(path,(json.dumps(value,sort_keys=True)+'\n').encode(),mode=0o400)
    return value


def _host_aliases(root, work, inventory, job, record, binding):
    """Protect the current job's framework aliases and publication holders.

    Historical nodes retain their sealed bindings. Only sources actually selected
    for this launch enter the mount plan; old jobs and attempts are not reopened.
    """
    from . import contracts as c
    aliases=[];holders=[]
    def existing(path):
        inventory.check()
        if path.exists() or path.is_symlink():
            aliases.append(_exact(path))
    if 'step' not in job:return aliases,binding
    step=job['step']
    node=c.workspace(root,step)
    for relative in ('inputs','conclusion.json',*[f'work/{name}' for name in DYNAMIC_ROOTS]):
        existing(node/relative)
    origin=c.host_record(node)
    for current in dict.fromkeys((origin,record or origin)):
        for name in ('science-result.schema.json','science-output.json','author-science-context'):
            existing(current/name)
    for number in ('001','002'):
        existing(root/'host-recovery/author-context'/f'{step:03d}'/number/'author-science-context')
    if job.get('role')=='W':
        record_directory=(root/'host-runtime/experiment-control'/f'{step:03d}'/'records'
            if type(job.get('worker_view_version')) is int and job['worker_view_version']==1
            else node/'work/lineage')
        private=root/'host-runtime/experiment-control'/f'{step:03d}'
        for holder in (record_directory,private/'scientific-output-views',
                private/'implementations',origin/'compiled-implementations'):
            inventory.check();_exact(holder).mkdir(parents=True,exist_ok=True,mode=0o700)
            _identity(holder);aliases.append(holder);holders.append(holder)
        # Legacy outputs belong only to this current job's compiler inventory.
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
    binding=_pin_holders(root,work,holders)
    return aliases,binding


def _load_native_facts(root, job, *, native=None, input_policy=None):
    """Load an unknown accepted chain once, or consume the caller's exact chain.

    A prepared node without an initial intent or continuation candidate has no
    wake or author context and needs no historical accepted-chain traversal.
    """
    from . import accepted_budget, contracts as c, execution
    if 'step' not in job:return dict(record=None, intent={}, records=(), input_policy=input_policy)
    origin = execution.origin(root, job['step'])
    if native is not None:
        record, intent = Path(native['record']), native['intent']
        attempt = intent.get('attempt', 0)
        expected = origin if attempt == 0 else origin / 'continuations' / f'{attempt:03d}'
        if (record != expected or intent.get('task_ref') != dict(path=str(Path(root)/'jobs'/f'{job["step"]:03d}.json'))
                or intent.get('step', job['step']) != job['step']):
            raise _unsupported('native facts belong to another task or attempt')
        records = tuple(origin if number == 0 else origin/'continuations'/f'{number:03d}'
            for number in range(attempt + 1))
        return dict(record=record, intent=intent, records=records, input_policy=input_policy)
    initial = (origin/'native-intent.json').exists()
    candidates = False
    if not initial:
        directory = origin/'continuations'
        if directory.exists():
            if directory.resolve() != directory:
                raise ValueError('Native continuation directory changed')
            candidates = any(directory.iterdir())
    if not initial and not candidates:
        if input_policy is not None and accepted_budget.native_attempt(root, job['step'], origin,
                head=input_policy['head']) is not None:
            raise _unsupported('an accepted native intent is missing')
        return dict(record=origin, intent={}, records=(origin,), input_policy=input_policy)
    if input_policy is None:
        base = c.settings(root)
        head = accepted_budget.load(root, base=base)
        policy = c.settings_for_job(root, job, base=base, head=head)
        input_policy = dict(base=base, policy=policy, head=head)
    rows = tuple(execution.accepted_intents_from_facts(root, job['step'], job=job,
        task_ref=c.record_ref(Path(root)/'jobs'/f'{job["step"]:03d}.json'), **input_policy))
    if not rows:
        if initial:raise _unsupported('an existing native intent has no accepted owner')
        return dict(record=origin, intent={}, records=(origin,), input_policy=input_policy)
    records = tuple(path for path, _ in rows)
    seal = origin/'result-identity.json'
    if seal.exists() and c.read(c.regular(seal)).get('execution_ref') != execution._reference(records):
        raise ValueError('Sealed native execution reference changed')
    return dict(record=records[-1], intent=rows[-1][1], records=records, input_policy=input_policy)


def _load_wake_facts(root, job, native):
    from . import contracts as c, execution, experiments as e, originals
    mappings, documents, resolved = [], {}, []
    def resolve(reference):
        for declaration, path in resolved:
            if declaration == reference:return path
        path = originals.resolve_root_ref(root, reference)
        resolved.append((reference, path))
        return path
    def document(path):
        if path not in documents:documents[path] = c.read(path)
        return documents[path]
    def original(reference):
        return resolve({key: reference[key]
            for key in ('path', 'size_bytes', 'file_stat') if key in reference})
    # The same W may consume several batches before yielding its result. Keep
    # every selected wake's original outputs available in later turns, including
    # reentry and temporary native recovery, using the existing exact chain.
    references=[]
    for record in native['records']:
        intent=(native['intent'] if record==native['record'] else
            document(c.regular(record/'native-intent.json')))
        for reference in (intent.get('execution_wake') or {}).get('observations', []):
            if reference not in references:references.append(reference)
    for reference in references:
        observed = document(resolve(reference))
        accepted = document(resolve(observed['accepted']))
        attempt = accepted.get('native_attempt', {}).get('number', 0)
        record = execution.record_from_chain(native['records'], attempt)
        config = document(c.regular(record / 'experiment-config.json'))
        if accepted['scope'] != config['scope'] or accepted['interface'] != config['execution_interface']:
            raise _unsupported('a selected wake original changed its accepted owner')
        prefix = Path('work/execution-results') / accepted['call_id']
        if job.get('worker_view_version') == 1:
            for output in e.scientific_outputs(config, observed):
                mappings.append((Path(output.local_path), prefix / 'outputs' / output.name))
        else:
            for output in observed['outputs']:
                mappings.append((original(output), prefix / 'outputs' / output['name']))
            for index, proof in enumerate(observed['proofs'], 1):
                mappings.append((original(proof), prefix / 'originals' / f'{index:03d}'))
            mappings.append((resolve(observed['completed']), prefix / 'result.json'))
    return mappings


def load_source_facts(root, job, work, *, target_work=None, native=None, input_policy=None,
                      policy=None, include_review=True, include_wake=True,
                      author_facts=_UNLOADED_AUTHOR, material_sources=None):
    """One producer owns this operation's native, wake and author originals."""
    root, work = Path(root), Path(work)
    target = Path(target_work) if target_work is not None else work
    from . import handoff_state
    if job.get('material_refs') and 'direct_materials' not in job:
        raise _unsupported('the input owner must supply its resolved current material facts')
    producers = {} if material_sources is None else material_sources
    handoff_state.load_material_sources(root, job.get('material_refs', []), sources=producers)
    material_paths = handoff_state.material_paths(root, job, sources=producers)
    native = _load_native_facts(root, job, native=native, input_policy=input_policy)
    author = None
    if native['intent'].get('author_context_version') == 1:
        from . import accepted_budget, author_closeout, contracts as c
        proof = native['intent']['author_closeout_science_context']
        if author_facts is _UNLOADED_AUTHOR:
            if native['input_policy'] is None:
                base = c.settings(root)
                head = accepted_budget.load(root, base=base)
                native['input_policy'] = dict(base=base, head=head,
                    policy=c.settings_for_job(root, job, base=base, head=head) if policy is None else policy)
            author = author_closeout.load_science_context_facts(root, job, proof,
                input_policy=native['input_policy'])
        else:
            author = author_facts
            if (author is None or author['root'] != root or author['job'] != job
                    or author['proof'] != proof
                    or author['manifest_path'] != author_closeout._declared_ref(root, proof)):
                raise _unsupported('author facts belong to another exact context')
    elif author_facts is not _UNLOADED_AUTHOR and author_facts is not None:
        raise _unsupported('author facts have no selected context')
    facts = dict(root=root, job=job, work=work, target=target, native=native,
        author=author, wake=_load_wake_facts(root, job, native) if include_wake else [], review_code=[],
        material_paths=material_paths,
        policy=policy if policy is not None else
            (native['input_policy']['policy'] if native['input_policy'] is not None else None))
    if include_review and job.get('formal_review'):
        from . import formal_review, execution_inputs, qualification as q
        bound = formal_review.selected_compiler(root, job['formal_review'])
        if bound is not None and 'code_version' in bound[1]:
            ref, compiler = bound
            owner = q._read(root, compiler['job'])
            call_id = execution_inputs._compiler_call_id(root, owner['step'], ref)
            facts['review_code'].append((owner['step'], call_id, compiler['code_version'],
                target / 'inputs/review/scientific-implementation.json', True))
    return facts


def wake_mappings_from_facts(facts):
    return [(source, facts['target'] / destination) for source, destination in facts['wake']]


def selected_code_from_facts(facts):
    """Select accepted code identities without reading or materializing them."""
    root, target, author = facts['root'], facts['target'], facts['author']
    selected = list(facts['review_code'])
    if author is not None:
        for row in author['entries']:
            item = row['item']
            if 'code_version' not in item:continue
            step, call_id = row['code_identity']
            destination = root / item['display']['path']
            selected.append((step, call_id, item['code_version'],
                target / destination.relative_to(author['directory']),
                item['kind'] == 'compiler_author' or item['code_version']['selection'] == 'file'))
    return selected


def materialize_selected_code(root, selected, *, context=None, exports=None):
    """The existing export writer owns physical code acceptance and writes."""
    from . import code_versions
    mappings = []
    for step, call_id, version, destination, file_entry in selected:
        if exports is not None:exports.append((step, call_id))
        export = code_versions.materialize(root, step=step, call_id=call_id,
            code_version=version, context=context)
        mappings.append((export / version['entry'] if file_entry else export, destination, step, call_id))
    return mappings


def selected_code_mounts(root, job, work, *, target_work=None, context=None, exports=None):
    """Independent code-selection operation; real launches pass source facts."""
    facts = load_source_facts(root, job, work, target_work=target_work, include_wake=False)
    return materialize_selected_code(root, selected_code_from_facts(facts), context=context, exports=exports)


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


def logical_original_from_facts(facts, path, *, code_mounts=()):
    """Resolve one logical name using only this lookup's accepted facts."""
    path = Path(path)
    for source, destination in wake_mappings_from_facts(facts):
        if path == destination:return source
    author = facts['author']
    if author is None:return None
    for row in author['entries']:
        item = row['item']
        destination = facts['target'] / (facts['root'] / item['display']['path']).relative_to(author['directory'])
        if 'code_version' in item:
            file_entry = item['kind'] == 'compiler_author' or item['code_version']['selection'] == 'file'
            if (path != destination if file_entry else not path.is_relative_to(destination)):
                continue
            step, call_id = row['code_identity']
            for source, target, owner, call in code_mounts:
                if (target, owner, call) == (destination, step, call_id):
                    return source if file_entry else source / path.relative_to(destination)
            continue
        if path == destination:
            return row['display'] if item['kind'] in {'task', 'framework'} else row['source']
    return None


def logical_original(root, job, path):
    """Independent lookup: collect once, then resolve without reopening views."""
    from . import contracts as c
    facts = load_source_facts(root, job, c.workspace(root, job['step']), include_review=False)
    selected = [item for item in selected_code_from_facts(facts)
        if item[4] and Path(path) == item[3]]
    # Directory selections may resolve any descendant; no unrelated export is
    # created merely because a host asks about another logical original.
    selected.extend(item for item in selected_code_from_facts(facts)
        if not item[4] and Path(path).is_relative_to(item[3]))
    code_mounts = materialize_selected_code(root, selected)
    return logical_original_from_facts(facts, path, code_mounts=code_mounts)


def load_mount_facts(root, job, work, *, target_work=None, mounts=(), private=False,
                     include_skills=True, context=None, selected_code=None, source_facts=None):
    """Produce exact source and live metadata facts for one final mount seam.

    Explicit readonly (source,target) mappings override the default same target.
    Private canonical roots are only appropriate to the full-host generic outer;
    mapped scientific namespaces must leave private=False. Dedicated writers
    prepare roots, exports and empty mountpoints before the final inventory.
    """
    work, root = _exact(work), _exact(root)
    target = Path(target_work) if target_work is not None else work
    sources = (load_source_facts(root, job, work, target_work=target)
        if source_facts is None else source_facts)
    if (sources['root'], sources['job'], sources['work'], sources['target']) != (root, job, work, target):
        raise _unsupported('source facts belong to another task or namespace')
    binding=_load_dynamic_binding(root, work)
    mappings = {}
    for name in DYNAMIC_ROOTS:
        mappings[str(target / 'work' / name)] = work / 'work' / name
    skill_mounts = []
    if include_skills:
        from . import skill_supply
        skill_mounts = (skill_supply.native_mounts(root, job) if sources['policy'] is None else
            skill_supply.native_mounts_from_policy(root, job, policy=sources['policy']))
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
    historical_sources = set()
    current_sources = set()
    from . import accepted_prefix
    history_files = {item['path']:item for item in accepted_prefix.input_rows(job)}
    if not job.get('formal_review'):
        public=accepted_prefix.visibility(root,accepted_prefix.for_job(job))
        declared[str(target/'inputs/history')]=public
        historical_sources.add(public)
    actual_paths = {item['path'] for item in [*job.get('global_materials', []),
        *job.get('direct_refs', []), *job.get('direct_materials', []),
        *job.get('history', {}).get('execution_recovery_materials', []),
        *[row['material'] for row in job.get('selected_inputs', [])]]}
    for item in experiment_client._input_rows(job):
        logical = originals.relative(item['path'])
        destination = str(target / logical)
        bound = dict(item, source=item.get('source', str(work / logical)))
        # These are published navigation/control pages, already bound by the
        # job. Actual direct/global/recovery inputs still use the exact resolver.
        historical = item['path'] not in actual_paths and history_files.get(item['path']) == item
        if item['path'] in sources['material_paths']:
            source = sources['material_paths'][item['path']]
        else:
            source = (Path(bound['source']) if historical else originals.material_path(root, bound))
        (historical_sources if historical else current_sources).add(source)
        if destination in declared and declared[destination] != source:
            raise _unsupported('a declared logical input has ambiguous sources')
        declared[destination] = source
    if job.get('delivery_sources', {}).get('catalogue'):
        catalogue = c.regular(work / 'inputs/delivery-candidates.json')
        declared[str(target / 'inputs/delivery-candidates.json')] = catalogue
        current_sources.add(catalogue)
    for source, destination in wake_mappings_from_facts(sources):
        declared[str(destination)] = source
        current_sources.add(source)
    author = sources['author']
    if author is not None:
        for row in author['entries']:
            item = row['item']
            if 'code_version' in item:continue
            display = root / item['display']['path']
            source = row['display'] if item['kind'] in {'task', 'framework'} else row['source']
            declared[str(target / display.relative_to(author['directory']))] = source
            current_sources.add(source)
    code_selection = selected_code_from_facts(sources)
    code_mounts = (materialize_selected_code(root, code_selection, context=context)
        if selected_code is None else selected_code)
    if [(destination, step, call_id) for _, destination, step, call_id in code_mounts] != [
            (destination, step, call_id) for step, call_id, _, destination, _ in code_selection]:
        raise _unsupported('selected code mounts belong to another task or namespace')
    for source, destination, _, _ in code_mounts:
        declared[str(destination)] = source
        current_sources.add(source)
    for destination, source in declared.items():
        mappings[destination] = source
        if source not in historical_sources or source in current_sources:
            protected_sources.append(source)
    if private:
        # The full-host view exposes canonical aliases too. Mount the saved
        # history paths readonly without reopening their old inode proofs.
        for source in sorted(historical_sources, key=str):
            mappings[str(source)] = source
    # Trusted launchers already verify exact accepted source identities. Their
    # explicit file mappings take priority at this same final overlay seam.
    for source, destination in mounts:
        if Path(destination) != target / 'inputs':
            mappings[str(destination)] = Path(source)
            if str(destination) in declared:declared[str(destination)] = Path(source)
    project_overlap(job.get('project'), [(source, Path(destination)) for destination, source in mappings.items()])
    _dynamic_mountpoints(work, target, declared, context=context)
    inventory = _Inventory(context)
    host_aliases=[]
    if private:
        host_aliases,binding=_host_aliases(root,work,inventory,job,sources['native']['record'],binding)
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
    c.regular(_exact(_binding(root,work)))
    _observe_dynamic_binding(root,work,binding)
    input_mappings = [(Path(destination), source) for destination, source in mappings.items()
        if Path(destination).is_relative_to(target / 'inputs')]
    if input_mappings:_exact(work / 'inputs').mkdir(exist_ok=True)
    project_aliases = (_project_readonly_aliases(job.get('project'), work, target, mappings, context=context)
        if job.get('role') == 'W' else [])
    if context is not None:context.remaining_seconds()
    return dict(root=root, job=job, work=work, target=target, sources=sources,
        mappings=mappings, input_mappings=input_mappings, project_aliases=project_aliases,
        code_mounts=code_mounts)


def mount_arguments_from_facts(facts):
    """Pure final readonly argv projection; no original or metadata is read."""
    mappings, target = facts['mappings'], facts['target']
    arguments = []
    inputs = target / 'inputs'
    input_mappings = facts['input_mappings']
    for destination, source in mappings.items():
        if Path(destination).is_relative_to(inputs):continue
        arguments.extend(['--ro-bind', str(source), destination])
    # Build missing logical mountpoints only in this namespace. Freezing this
    # inputs tree leaves the already-bound dynamic publication roots intact.
    if input_mappings:
        arguments.extend(['--tmpfs', str(inputs)])
        directories = {parent for destination, _ in input_mappings for parent in destination.parents
            if parent != inputs and parent.is_relative_to(inputs)}
        for directory in sorted(directories, key=lambda path: (len(path.parts), str(path))):
            arguments.extend(['--dir', str(directory)])
        for destination, source in sorted(input_mappings, key=lambda item: len(item[0].parts)):
            arguments.extend(['--ro-bind', str(source), str(destination)])
        arguments.extend(['--remount-ro', str(inputs)])
    for source, destination in facts['project_aliases']:
        arguments.extend(['--ro-bind', str(source), str(destination)])
    return arguments


def mount_arguments(root, job, work, *, target_work=None, mounts=(), private=False,
                    include_skills=True, context=None, selected_code=None):
    """Independent mount operation, with one producer and a pure argv consumer."""
    facts = load_mount_facts(root, job, work, target_work=target_work, mounts=mounts,
        private=private, include_skills=include_skills, context=context, selected_code=selected_code)
    return mount_arguments_from_facts(facts)


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


def load_wrap_facts(root, job, work, *, context=None, selected_code=None,
                    source_facts=None, policy=None):
    """Produce one full-host namespace using dedicated preparation writers."""
    root, work = Path(root), Path(work)
    program = ensure_capability(context=context)
    holder, ancestors = _private_git_arguments(root)
    sources = (load_source_facts(root, job, work, policy=policy)
        if source_facts is None else source_facts)
    project = job.get('project')
    project_mounts, worker_policy = [], None
    if project is not None:
        from . import contracts as c, native_execution
        settings = (sources['native']['input_policy']['policy']
            if policy is None and sources['native']['input_policy'] is not None else policy)
        if settings is None:settings = c.settings(root, job)
        if settings.get('project') != project:
            raise _unsupported('project namespace differs from its frozen job')
        if sources['policy'] is None:sources = dict(sources, policy=settings)
        projected = native_execution.project_policy(settings)
        worker = job.get('role') == 'W'
        if worker:
            worker_policy = dict(projected,
                writable_roots=[str(path) for path in project_targets(project, writable=True)])
        project_mounts = project_arguments(project, work, artifacts=work/'artifacts', writable=worker, context=context)
    workspace = workspace_arguments(root, work)
    mounts = load_mount_facts(root, job, work, private=True, context=context,
        selected_code=selected_code, source_facts=sources)
    return dict(program=program, holder=holder, ancestors=ancestors, workspace=workspace,
        project_mounts=project_mounts, worker_policy=worker_policy, mounts=mounts)


def wrap_from_facts(facts, argv):
    """Pure native argv assembly from this launch's freshly produced plan."""
    if facts['worker_policy'] is not None:
        from . import native_execution
        declaration = 'sandbox_workspace_write='+native_execution._toml(facts['worker_policy'])
        if not any(argument == declaration and argv[index-1] == '-c'
                for index, argument in enumerate(argv) if index):
            argv = list(argv)
            position = argv.index('resume') if 'resume' in argv else len(argv)-1
            argv[position:position] = ['-c', declaration]
    program, holder = facts['program'], facts['holder']
    controls = [program, '--unshare-user', '--unshare-pid', '--die-with-parent', '--bind', '/', '/', '--proc', '/proc',
        '--dev', '/dev',
        *facts['ancestors'], *facts['workspace'], *facts['project_mounts'],
        *mount_arguments_from_facts(facts['mounts']),
        '--ro-bind', str(holder), str(holder)]
    return _BwrapCommand([*controls, '--', *argv], command_index=len(controls))


def wrap(root, job, work, argv, *, context=None, selected_code=None):
    """Independent full-host launch-plan operation, including opaque argv."""
    return wrap_from_facts(load_wrap_facts(root, job, work, context=context,
        selected_code=selected_code), argv)


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
