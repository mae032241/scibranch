"""Explicit project declarations and bounded Git HEAD input views.

Only Git's tracked index/tree names are enumerated. Worktree data is never
walked, copied or persisted as a second manifest. Returned stat anchors detect
ordinary replacement races; same-user concurrent writers remain trusted.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess

from .storage_policy import Deadline, StorageQuotaExceeded

MAX_ROOTS = 64
MAX_ENTRIES = 100_000
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_PATH_BYTES = 4096
SECONDS = 65
_FIELDS = {'base_root', 'editable', 'scratch', 'outputs'}


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\0' in value
            or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))
            or any(part.lower() == '.git' for part in value.split('/'))
            or PurePosixPath(value).as_posix() != value
            or len(os.fsencode(value)) >= MAX_PATH_BYTES):
        raise ValueError('Project path must be a canonical relative path without .git')
    return value


def _absolute(value):
    if (not isinstance(value, (str, Path)) or not str(value) or '\0' in str(value)
            or not Path(value).is_absolute() or str(Path(value)) != str(value)
            or '..' in Path(value).parts):
        raise ValueError('Project paths require exact absolute paths')
    return Path(value)


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def configuration(value):
    """Parse public project fields without resolving aliases or reading bytes."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) - _FIELDS
            or not {'base_root', 'editable'} <= set(value)):
        raise ValueError('project requires base_root and editable with supported fields')
    result = dict(base_root=str(_absolute(value['base_root'])))
    declared = []
    for key in ('editable', 'scratch', 'outputs'):
        roots = value.get(key, [])
        if not isinstance(roots, list) or len(roots) > MAX_ROOTS or (key == 'editable' and not roots):
            raise ValueError('project.' + key + ' requires a finite directory array')
        result[key] = [_relative(item) for item in roots]
        for item in result[key]:
            path = PurePosixPath(item)
            if any(_overlap(path, previous) for previous in declared):
                raise ValueError('Project editable/scratch/outputs paths overlap')
            declared.append(path)
    if len(declared) > MAX_ROOTS:
        raise ValueError('Project directory declarations exceed the capability limit')
    return result


def _frozen(value):
    if not isinstance(value, dict) or set(value) != _FIELDS | {'base_identity'}:
        raise ValueError('Frozen project identity is missing or malformed')
    result = configuration({key: value[key] for key in _FIELDS})
    identity = value['base_identity']
    if (not isinstance(identity, dict) or set(identity) != {'object_format', 'commit'}
            or identity.get('object_format') not in ('sha1', 'sha256')
            or not isinstance(identity.get('commit'), str)
            or not re.fullmatch('[0-9a-f]{' + str(40 if identity.get('object_format') == 'sha1' else 64) + '}', identity['commit'])):
        raise ValueError('Frozen project Git identity is invalid')
    result['base_identity'] = dict(identity)
    return result


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _lineage(path):
    anchors = {}
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Project requires real directory ancestors: ' + str(parent))
        anchors[parent] = (info.st_dev, info.st_ino, info.st_mode)
    return anchors


def _unchanged(anchors):
    for path, expected in anchors.items():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino, info.st_mode) != expected:
            raise ValueError('Project directory ancestor changed: ' + str(path))


def _control(root):
    anchors = _lineage(root)
    repository = root / '.git'
    anchors.update(_lineage(repository))  # linked worktrees/.git files are unsupported
    anchors.update(_lineage(repository / 'objects'))
    anchors.update(_lineage(repository / 'refs'))
    for name in ('objects/info', 'objects/pack', 'info', 'refs/heads', 'refs/tags'):
        path = repository / name
        if path.exists() or path.is_symlink():
            anchors.update(_lineage(path))
    controls = {}
    for name in ('HEAD', 'config', 'index', 'packed-refs', 'shallow'):
        path = repository / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            if name in ('HEAD', 'config', 'index'):
                raise ValueError('Project requires an ordinary Git ' + name) from None
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Project Git control must be a regular file: ' + name)
        controls[path] = _signature(info)
    for name in ('commondir', 'objects/info/alternates', 'objects/info/http-alternates'):
        path = repository / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError('Project Git shared/alternate object directories are unsupported')
    return anchors, controls


def _controls_unchanged(anchors, controls):
    _unchanged(anchors)
    if any(_signature(path.lstat()) != signature for path, signature in controls.items()):
        raise ValueError('Project Git control changed during verification')


def _git(root, arguments, *, context, output_limit=4096, worktree=True):
    """Run read-only worktree Git with isolated environment and bounded pipes."""
    binary = shutil.which('git', path=os.defpath)
    if binary is None:
        raise ValueError('Project inputs require host Git')
    env = dict(PATH=os.defpath, HOME=os.devnull, XDG_CONFIG_HOME=os.devnull,
        LC_ALL='C', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT='0', GIT_ATTR_NOSYSTEM='1',
        GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0',
        GIT_ALLOW_PROTOCOL='', GIT_NO_LAZY_FETCH='1')
    command = [binary, '--no-pager', '--no-optional-locks', '--git-dir=' + str(root / '.git')]
    if worktree:
        command.append('--work-tree=' + str(root))
    for setting in (f'core.hooksPath={os.devnull}', f'core.attributesFile={os.devnull}',
                    f'core.excludesFile={os.devnull}',
                    'core.fsmonitor=false', 'core.untrackedCache=false',
                    'core.splitIndex=false', 'maintenance.auto=false', 'gc.auto=0'):
        command.extend(('-c', setting))
    command.extend(arguments)
    deadline = Deadline(10, upstream=context)
    deadline.remaining_seconds()
    output, error = bytearray(), bytearray()
    process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            for stream, buffer, limit in ((process.stdout, output, output_limit),
                                          (process.stderr, error, 4096)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (buffer, limit))
            while selector.get_map():
                for key, _ in selector.select(min(.1, deadline.remaining_seconds())):
                    buffer, limit = key.data
                    chunk = os.read(key.fd, min(65536, limit - len(buffer) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        buffer.extend(chunk)
                        if len(buffer) > limit:
                            raise StorageQuotaExceeded('Project Git metadata capability limit exceeded')
            result = process.wait(timeout=deadline.remaining_seconds())
        if result:
            raise ValueError('Project Git verification failed: ' + arguments[0])
        return bytes(output)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _identity(root, context):
    if _git(root, ('rev-parse', '--is-bare-repository'), context=context, worktree=False).strip() != b'false':
        raise ValueError('Project base_root must be a Git worktree root')
    if _git(root, ('rev-parse', '--show-toplevel'), context=context, worktree=False).rstrip(b'\n') != os.fsencode(root):
        raise ValueError('Project base_root must be the exact Git worktree root')
    object_format = _git(root, ('rev-parse', '--show-object-format'), context=context).strip().decode('ascii')
    if object_format not in ('sha1', 'sha256'):
        raise ValueError('Project Git object format is unsupported')
    commit = _git(root, ('rev-parse', '--verify', 'HEAD^{commit}'), context=context).strip().decode('ascii')
    if not re.fullmatch('[0-9a-f]{' + str(40 if object_format == 'sha1' else 64) + '}', commit):
        raise ValueError('Project HEAD must identify an ordinary Git commit')
    return dict(object_format=object_format, commit=commit)


def _listing(raw, *, index, object_format, context):
    result = {}
    if raw and not raw.endswith(b'\0'):
        raise ValueError('Project Git listing was truncated')
    for record in raw.split(b'\0')[:-1]:
        context.remaining_seconds()
        if len(result) >= MAX_ENTRIES:
            raise StorageQuotaExceeded('Project tracked entries exceed the metadata capability limit')
        try:
            metadata, raw_name = record.split(b'\t', 1)
            if index:
                mode, oid, stage = metadata.split()
            else:
                mode, stage, oid = metadata.split()
            name = _relative(os.fsdecode(raw_name))
        except (ValueError, IndexError) as error:
            raise ValueError('Project Git contains an unsupported tracked path') from error
        if (mode not in (b'100644', b'100755') or stage != (b'0' if index else b'blob')
                or not re.fullmatch(b'[0-9a-f]{' + str(40 if object_format == 'sha1' else 64).encode() + b'}', oid)
                or name in result):
            raise ValueError('Project tracked symlink/gitlink, unmerged or unsupported entry: ' + name)
        result[name] = (int(mode, 8), oid.decode('ascii'))
    return result


def _source(root, name, mode, object_id):
    """Anchor an exact mount source without opening or hashing its contents."""
    source = root / name
    ancestors = _lineage(source.parent)
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or bool(before.st_mode & 0o111) != (mode == 0o100755):
        raise ValueError('Project tracked file type/executable mode changed: ' + name)
    signature = _signature(before)
    _unchanged(ancestors)
    return dict(source=source, relative=name, mode=mode, object_id=object_id,
                signature=signature, ancestors=ancestors)


def files(project, *, context=None, exclude=()):
    """Verify HEAD/index and ordinary Git worktree state for this view's files.

    ``exclude`` may hide declared mount roots at actual consumption, so their
    current physical bytes cannot substitute for the selected code commit.
    Git's normal filters and index flags apply; same-user writers are trusted.
    """
    project = _frozen(project)
    context = Deadline(SECONDS, upstream=context)
    declared = project['editable'] + project['scratch'] + project['outputs']
    exclude = tuple(exclude)
    if any(item not in declared for item in exclude) or len(set(exclude)) != len(exclude):
        raise ValueError('Project exclusions must be exact declared directory roots')
    root = Path(project['base_root'])
    # Validate ordinary Git controls before invoking Git. Its normal worktree
    # check may refresh index stat-cache metadata, so anchor controls afterward.
    _control(root)
    pathspec = ('.', *(':(top,literal,exclude)' + item for item in exclude))
    dirty = _git(root, ('diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv', '--', *pathspec),
                 context=context, output_limit=MAX_METADATA_BYTES)
    anchors, controls = _control(root)
    identity = _identity(root, context)
    if identity != project['base_identity']:
        raise ValueError('Project HEAD changed from the frozen base identity')
    index = _listing(_git(root, ('ls-files', '--stage', '-z'), context=context,
                         output_limit=MAX_METADATA_BYTES), index=True,
                     object_format=identity['object_format'], context=context)
    tree = _listing(_git(root, ('ls-tree', '-r', '-z', '--full-tree', identity['commit']),
                        context=context, output_limit=MAX_METADATA_BYTES), index=False,
                    object_format=identity['object_format'], context=context)
    if index != tree:
        raise ValueError('Project Git index differs from frozen HEAD; staged changes are unsupported')
    result = []
    for name, (mode, object_id) in tree.items():
        context.remaining_seconds()
        if any(name == item or name.startswith(item + '/') for item in exclude):
            continue
        result.append(_source(root, name, mode, object_id))
    if dirty:
        raise ValueError('Project worktree differs from frozen HEAD')
    # Catch ordinary source/parent replacements while building this mount view.
    for row in result:
        context.remaining_seconds()
        _unchanged(row['ancestors'])
        if _signature(row['source'].lstat()) != row['signature']:
            raise ValueError('Project tracked file changed during verification: ' + row['relative'])
    _controls_unchanged(anchors, controls)
    if _identity(root, context) != identity:
        raise ValueError('Project HEAD changed during verification')
    return result


def prepare(value, *, protected, existing=None, context=None):
    """Freeze new identity, or retain an existing identity without base-data IO."""
    declaration = configuration(value)
    if existing is not None:
        frozen = _frozen(existing)
        if declaration != {key: frozen[key] for key in _FIELDS}:
            raise ValueError('Project declaration is immutable for an existing instance')
    elif declaration is None:
        return None
    else:
        frozen = declaration
    root = Path(frozen['base_root'])
    for value in protected:
        path = _absolute(value).resolve()
        if _overlap(root, path):
            raise ValueError('Project base overlaps a protected instance/runtime path: ' + str(path))
    if existing is not None:
        return frozen
    context = Deadline(SECONDS, upstream=context)
    _control(root)
    frozen['base_identity'] = _identity(root, context)
    # Explicit declarations are directories, including empty editable roots.
    for name in frozen['editable'] + frozen['scratch'] + frozen['outputs']:
        path = root / name
        if path.exists() or path.is_symlink():
            _lineage(path)
        elif name in frozen['editable']:
            raise ValueError('Project editable directory is missing: ' + name)
        else:
            # Absent scratch/output roots are namespace-created mountpoints.
            for parent in path.parents:
                if parent.exists() or parent.is_symlink():
                    _lineage(parent)
                    break
    files(frozen, context=context)
    return frozen


def code_roots(project, work):
    """Map exact declared draft roots without inspecting draft contents."""
    work = Path(work)
    if project is None:
        root = work / 'code'
        return dict(root=root, logical_root='code',
                    editable=[dict(relative='.', source=root, target=root)])
    project = _frozen(project)
    root = work / 'project'
    return dict(root=root, logical_root='project', editable=[
        dict(relative=name, source=root / name, target=Path(project['base_root']) / name)
        for name in project['editable']])


def validate_code(project, version, *, purpose, paths=None):
    """Validate code metadata and optionally its caller-supplied exact tree names."""
    from . import code_versions
    version = code_versions._version(version)
    if purpose not in ('run', 'compiler'):
        raise ValueError('Unknown project code purpose')
    if project is None:
        logical = version['logical_root']
        if logical != 'code' and not logical.startswith('code/'):
            raise ValueError('Legacy code version must select the code draft root')
        return version
    project = _frozen(project)
    if version['logical_root'] != 'project' or (purpose == 'run' and version['selection'] != 'directory'):
        raise ValueError('Project run requires one directory code version rooted at project')
    def declared(name):
        name = _relative(name)
        if not any(name.startswith(root + '/') for root in project['editable']):
            raise ValueError('Project code path is outside declared editable directories: ' + name)
    declared(version['entry'])
    if paths is not None:
        seen = set()
        for name in paths:
            declared(name)
            if name in seen:
                raise ValueError('Duplicate project code tree path')
            seen.add(name)
        if version['entry'] not in seen:
            raise ValueError('Project code entry is absent from its exact tree')
    return version
