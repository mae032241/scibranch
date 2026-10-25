"""Kernel readonly views of exact shared inputs, without changing native policy.

Same-user host processes and pre-existing writable descriptors remain trusted.
Only the assigned workspace and explicit framework mappings are inventoried.
"""
import json
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
    if (work / 'inputs').exists():
        mappings[str(target / 'inputs')] = work / 'inputs'
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
        mappings[str(destination)] = Path(source)
        protected_sources.append(Path(source))
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
            if not any(Path(destination).is_relative_to(Path(base)) for base in mappings):
                mappings[destination] = path
    if private:
        for path in host_aliases:mappings[str(path)]=path
        for name in ('host-history/objects', 'project/global', 'source-provenance/originals',
                'host-runtime/fork','host-control/skills/objects'):
            path = root / name
            if path.exists():
                mappings[str(path)] = _exact(path)
    dynamic_root(root, work, DYNAMIC_ROOTS[0])
    arguments = []
    for destination, source in mappings.items():
        arguments.extend(['--ro-bind', str(source), destination])
    if context is not None:context.remaining_seconds()
    return arguments


def _probe(program, generation, timeout):
    with tempfile.TemporaryDirectory(prefix='scid-readonly-probe-') as temporary:
        base = Path(temporary); protected = base / 'input'; protected.write_text('original')
        source = ('import os,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            'exec("for action in (lambda: p.chmod(0o600), lambda: p.write_text(\'changed\')):\\n'
            ' try: action()\\n except OSError: pass\\n else: raise SystemExit(31)"); '
            '(p.parent/"writable").write_text("ok")')
        try:
            result = subprocess.run([program, '--unshare-user', '--die-with-parent', '--bind', '/', '/',
                '--ro-bind', str(protected), str(protected), '--', sys.executable, '-I', '-c', source, str(protected)],
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
    return [program, '--unshare-user', '--unshare-pid', '--die-with-parent', '--bind', '/', '/', '--proc', '/proc',
        *ancestors, *workspace_arguments(root, work), *mount_arguments(root, job, work, private=True, context=context),
        '--ro-bind', str(holder), str(holder), '--', *argv]


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
