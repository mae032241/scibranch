"""Private Git snapshots with count/deadline bounds, without execution authority.

The call ref pins code bytes only. The accepted original record, owned by the
caller, must keep the full code_version binding immutable. Paths and metadata
checks detect ordinary replacement races; same-user host access is a soft boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile

from .storage_policy import Deadline, Inventory, StorageQuotaExceeded


DEFAULT_LIMITS = dict(max_file_bytes=1048576, max_total_bytes=52428800,
                      max_files=4096, max_scan_entries=8192)
_HEX = re.compile(r'[0-9a-f]{40}\Z')
_CALL = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
_MESSAGE = b'SciDiscovery code version\n'
_PATH_BYTES = 4096


def _limits(value):
    if value is not None and (not isinstance(value, dict) or set(value) - set(DEFAULT_LIMITS)):
        raise ValueError('Unknown code capture limit')
    result = {**DEFAULT_LIMITS, **(value or {})}
    if any(type(number) is not int or number <= 0 for number in result.values()):
        raise ValueError('Code capture limits must be positive integers')
    return dict(version=1, enabled=True, **result)


def _relative(value, *, git_path=False):
    if (not isinstance(value, str) or not value or '\\' in value or '\0' in value
            or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))
            or PurePosixPath(value).as_posix() != value
            or len(os.fsencode(value)) >= _PATH_BYTES):
        raise ValueError('Code path must be a canonical relative path')
    if git_path and any(part.lower() == '.git' for part in value.split('/')):
        raise ValueError('Git cannot represent a .git code path component')
    return value


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _lineage(path):
    result = {}
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Code namespace requires real directory ancestors')
        result[part] = (info.st_dev, info.st_ino)
    return result


def _unchanged(anchors):
    for path, identity in anchors.items():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
            raise ValueError('Code namespace ancestor changed')


def _extend(anchors, additions):
    _unchanged(anchors)
    if any(path in anchors and anchors[path] != identity for path, identity in additions.items()):
        raise ValueError('Code namespace ancestor changed')
    anchors.update(additions)
    _unchanged(anchors)


def _directory(value):
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts or
            (isinstance(value, str) and str(path) != value)):
        raise ValueError('Code namespace requires an exact absolute directory')
    return path, _lineage(path)


def _open_directory(path, *, dir_fd=None, expected=None):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    info = os.fstat(fd)
    if expected is not None and (info.st_dev, info.st_ino) != expected:
        os.close(fd)
        raise ValueError('Code directory changed before opening')
    return fd


def _mkdir(path, anchors):
    _unchanged(anchors)
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Private code path must be a real directory')
    _extend(anchors, {path: (info.st_dev, info.st_ino)})


@dataclass
class _Private:
    holder: Path
    repository: Path
    ref: str
    export: Path
    anchors: dict


def _private(root, step, call_id, *, create, context, repository=True):
    if type(step) is not int or step < 0:
        raise ValueError('Code step must be a nonnegative integer')
    if (not isinstance(call_id, str) or not _CALL.fullmatch(call_id)
            or call_id.startswith('.') or call_id.endswith('.') or '..' in call_id
            or call_id.endswith('.lock')):
        raise ValueError('Invalid code call identity')
    root, anchors = _directory(root)
    holder = root / 'host-runtime/code-versions'
    for directory in (root / 'host-runtime', holder):
        if create:
            _mkdir(directory, anchors)
        else:
            _extend(anchors, _lineage(directory))
    private = _Private(holder, holder / 'repository.git',
                       f'refs/scidiscovery/steps/{step:03d}/calls/{call_id}',
                       holder / f'exports/{step:03d}/{call_id}/tree', anchors)
    if not repository:
        return private
    try:
        info = private.repository.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise ValueError('Private code repository must be a real directory')
    if create and info is None:
        _git(private, ('init', '--bare', '--quiet', '--object-format=sha1', '--template='),
             context=context)
    _extend(private.anchors, _lineage(private.repository))
    if (_git(private, ('rev-parse', '--show-object-format'), context=context).strip() != b'sha1'
            or _git(private, ('rev-parse', '--is-bare-repository'), context=context).strip() != b'true'):
        raise ValueError('Private code repository must be bare SHA-1 Git')
    return private


class _GitError(ValueError):
    pass


def _git(private, arguments, *, context, data=b'', index=None, input_limit=1048576,
         output_limit=4096, allowed=(0,), output_stream=None):
    """Isolated Git with chunked pipes and bounded compact control responses.

    File-like input/output streams carry code bytes without a byte quota or an
    aggregate in-memory payload. Numeric limits describe compact control data
    or an exact previously observed blob size, never configured file quotas.
    """
    streaming = hasattr(data, 'read')
    if not streaming and input_limit is not None and len(data) > input_limit:
        raise StorageQuotaExceeded('Code Git input limit exceeded')
    _unchanged(private.anchors)
    binary = shutil.which('git', path=os.defpath)
    if binary is None:
        raise ValueError('Code versions require host Git')
    env = dict(PATH=os.defpath, HOME=os.devnull, XDG_CONFIG_HOME=os.devnull, LC_ALL='C',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT='0', GIT_ATTR_NOSYSTEM='1',
               GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0',
               GIT_AUTHOR_NAME='SciDiscovery', GIT_AUTHOR_EMAIL='code@example.invalid',
               GIT_COMMITTER_NAME='SciDiscovery', GIT_COMMITTER_EMAIL='code@example.invalid',
               GIT_AUTHOR_DATE='2000-01-01T00:00:00+00:00',
               GIT_COMMITTER_DATE='2000-01-01T00:00:00+00:00')
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    command = [binary, '--no-pager', f'--git-dir={private.repository}']
    for setting in (f'core.hooksPath={os.devnull}', f'core.attributesFile={os.devnull}',
                    f'core.excludesFile={os.devnull}', 'core.autocrlf=false',
                    'core.fsmonitor=false', 'core.untrackedCache=false', 'core.splitIndex=false',
                    'core.logAllRefUpdates=false', 'commit.gpgSign=false', 'gc.auto=0', 'gc.autoPackLimit=0',
                    'maintenance.auto=false'):
        command.extend(('-c', setting))
    command.extend(arguments)
    deadline = Deadline(10, upstream=context)
    output, error = bytearray(), bytearray()
    output_count = 0
    process = subprocess.Popen(command, cwd=private.holder, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            for stream, buffer, limit in ((process.stdout, output, output_limit),
                                          (process.stderr, error, 4096)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (buffer, limit))
            offset, pending = 0, memoryview(b'')
            if streaming or data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while selector.get_map():
                for key, _ in selector.select(min(.1, deadline.remaining_seconds())):
                    if key.fileobj is process.stdin:
                        if streaming and not pending:
                            pending = memoryview(data.read(65536))
                            if not pending:
                                selector.unregister(process.stdin)
                                process.stdin.close()
                                continue
                        try:
                            if streaming:
                                pending = pending[os.write(process.stdin.fileno(), pending):]
                            else:
                                offset += os.write(process.stdin.fileno(), memoryview(data)[offset:offset + 65536])
                        except BrokenPipeError:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                            continue
                        if not streaming and offset == len(data):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        buffer, limit = key.data
                        stdout = key.fileobj is process.stdout
                        chunk = os.read(key.fd, 65536 if not stdout or limit is None else
                                        min(65536, limit - output_count + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            if stdout:
                                output_count += len(chunk)
                                if output_stream is not None:
                                    output_stream.write(chunk)
                                else:
                                    buffer.extend(chunk)
                            else:
                                buffer.extend(chunk[:max(0, limit - len(buffer))])
                            if stdout and limit is not None and output_count > limit:
                                raise StorageQuotaExceeded('Code Git output limit exceeded')
            result = process.wait(timeout=deadline.remaining_seconds())
        _unchanged(private.anchors)
        if result not in allowed:
            raise _GitError('Private code Git command failed: ' + arguments[0])
        return bytes(output)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def _scan(code_root, entry, selection, policy, context, *, inventory=None):
    inventory = inventory if inventory is not None else Inventory(policy, context=context)
    info = code_root.lstat()
    root_fd = _open_directory(code_root, expected=(info.st_dev, info.st_ino))
    files, directories = {}, {'.': _signature(os.fstat(root_fd))}
    opened, stack = [root_fd], []
    try:
        if selection == 'file':
            parent_fd, components = root_fd, entry.split('/')
            for number, component in enumerate(components[:-1], 1):
                inventory.entry()
                info = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                child = _open_directory(component, dir_fd=parent_fd, expected=(info.st_dev, info.st_ino))
                opened.append(child)
                directories['/'.join(components[:number])] = _signature(os.fstat(child))
                parent_fd = child
            inventory.entry()
            info = os.stat(components[-1], dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Selected code entry must be a regular file')
            inventory.file(info.st_size)
            files[entry] = _signature(info)
        else:
            stack.append((root_fd, os.scandir(root_fd), ''))
            while stack:
                context.remaining_seconds()
                fd, entries, prefix = stack[-1]
                item = next(entries, None)
                if item is None:
                    entries.close()
                    stack.pop()
                    os.close(opened.pop())
                    continue
                inventory.entry()
                name = _relative(prefix + item.name, git_path=True)
                info = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = _open_directory(item.name, dir_fd=fd, expected=(info.st_dev, info.st_ino))
                    opened.append(child)
                    directories[name] = _signature(os.fstat(child))
                    stack.append((child, os.scandir(child), name + '/'))
                elif stat.S_ISREG(info.st_mode):
                    inventory.file(info.st_size)
                    files[name] = _signature(info)
                else:
                    raise ValueError('Selected code must contain only regular files and directories')
    finally:
        for _, entries, _ in stack:
            entries.close()
        for fd in opened:
            os.close(fd)
    if entry is not None and entry not in files:
        raise ValueError('Selected code entry is absent from the captured tree')
    return files, directories


def _declared_roots(value, *, entry, selection, logical_root):
    if value is None:
        return None
    if (not isinstance(value, (list, tuple)) or not value
            or selection != 'directory' or logical_root != 'project'):
        raise ValueError('Declared code roots require a project directory selection')
    roots = tuple(_relative(name, git_path=True) for name in value)
    if any(left == right or left.startswith(right + '/') or right.startswith(left + '/')
           for number, left in enumerate(roots) for right in roots[number + 1:]):
        raise ValueError('Declared code roots must be distinct and nonoverlapping')
    if not any(entry.startswith(name + '/') for name in roots):
        raise ValueError('Selected code entry is outside the declared code roots')
    return roots


def _scan_roots(code_root, entry, roots, policy, context):
    """Visit only explicit editable directories with one aggregate inventory."""
    inventory = Inventory(policy, context=context)
    anchors = _lineage(code_root)
    files, directories = {}, {'.': _signature(code_root.lstat())}
    for relative in roots:
        directory = code_root / relative
        _extend(anchors, _lineage(directory))
        for parent in reversed(Path(relative).parents):
            name = parent.as_posix()
            if name not in directories:
                inventory.entry()
                directories[name] = _signature((code_root / parent).lstat())
        inventory.entry()
        found, observed = _scan(directory, None, 'directory', policy, context,
                                inventory=inventory)
        files.update((relative + '/' + name, info) for name, info in found.items())
        directories.update((relative if name == '.' else relative + '/' + name, info)
                           for name, info in observed.items())
    if entry not in files:
        raise ValueError('Selected code entry is absent from the captured tree')
    _unchanged(anchors)
    return files, directories


def _source_parents(code_root, name, directories):
    for parent in reversed(Path(name).parents):
        key = parent.as_posix()
        if _signature((code_root / parent).lstat()) != directories[key]:
            raise ValueError('Selected code directory changed before reading')


def _read_file(path, expected, inventory, *, output_stream=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or _signature(info) != expected:
            raise ValueError('Selected code changed before reading')
        inventory.file(0)
        result, count = bytearray(), 0
        while True:
            inventory.limit('max_file_bytes', count)
            chunk = os.read(fd, min(65536, expected[3] - count + 1))
            if not chunk:
                break
            count += len(chunk)
            if output_stream is None:
                result.extend(chunk)
            else:
                output_stream.write(chunk)
            inventory.total += len(chunk)
            inventory.limit('max_file_bytes', count)
            inventory.limit('max_total_bytes', inventory.total)
            if count > expected[3]:
                raise ValueError('Selected code grew while reading')
        if count != expected[3] or _signature(os.fstat(fd)) != expected:
            raise ValueError('Selected code changed while reading')
        return bytes(result)
    finally:
        os.close(fd)


def _oid(output):
    value = output.strip().decode('ascii')
    if not _HEX.fullmatch(value):
        raise ValueError('Invalid private Git object identity')
    return value


@contextmanager
def _index(private):
    _unchanged(private.anchors)
    fd, name = tempfile.mkstemp(prefix='capture-', suffix='.index', dir=private.holder)
    os.close(fd)
    path = Path(name)
    path.unlink()
    try:
        yield path
    finally:
        _unchanged(private.anchors)
        for candidate in (path, Path(name + '.lock')):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Private code index changed type')
            candidate.unlink()


def _ref_path(private):
    """Loose ref ancestors must be directories; absent/packed refs need no aliases."""
    path = private.repository
    for component in private.ref.split('/')[:-1]:
        path = path / component
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Original code ref has a non-directory ancestor')
        _extend(private.anchors, {path: (info.st_dev, info.st_ino)})
    try:
        info = (private.repository / private.ref).lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Original code ref must be a regular Git ref')


def _pin(private, commit, context):
    # --no-deref prevents a substituted symbolic call ref from redirecting writes.
    _ref_path(private)
    if _git(private, ('symbolic-ref', '--quiet', private.ref), context=context, allowed=(0, 1)):
        raise ValueError('Original code ref must not be symbolic')
    try:
        _git(private, ('update-ref', '--no-deref', private.ref, commit, '0' * 40), context=context)
    except _GitError:
        if _git(private, ('symbolic-ref', '--quiet', private.ref), context=context, allowed=(0, 1)):
            raise ValueError('Original code ref must not be symbolic') from None
        if _git(private, ('show-ref', '--verify', '--hash', private.ref), context=context).strip() != commit.encode('ascii'):
            raise ValueError('Original call already pins different code') from None
    _ref_path(private)


def capture(root, *, step, call_id, code_root, entry, selection, logical_root,
            roots=None, limits=None, context=None) -> dict:
    """Capture selected raw files and create the immutable original call ref once."""
    context = Deadline(65, upstream=context)
    policy = _limits(limits)
    entry = _relative(entry, git_path=True)
    logical_root = _relative(logical_root)
    if selection not in ('file', 'directory'):
        raise ValueError('Code selection must be file or directory')
    if isinstance(roots, (list, tuple)) and len(roots) > policy['max_scan_entries']:
        raise StorageQuotaExceeded('Code root entry limit exceeded')
    roots = _declared_roots(roots, entry=entry, selection=selection, logical_root=logical_root)
    code_root, source_anchors = _directory(code_root)
    def scan():
        if roots is None:
            return _scan(code_root, entry, selection, policy, context)
        return _scan_roots(code_root, entry, roots, policy, context)
    before = scan()
    private = _private(root, step, call_id, create=True, context=context)
    actual = Inventory(policy, context=context)
    with _index(private) as index:
        selected = sorted(before[0].items(), key=lambda item: os.fsencode(item[0]))
        # Length-delimited raw blobs use in-memory marks; no paths or refs enter this stream.
        framing = [
            (f'blob\nmark :{number}\ndata {info[3]}\n'.encode('ascii'),
             f'\nget-mark :{number}\n'.encode('ascii'))
            for number, (_, info) in enumerate(selected, 1)
        ]
        with tempfile.TemporaryFile(dir=private.holder) as payload:
            for (name, info), (prefix, suffix) in zip(selected, framing):
                _unchanged(source_anchors)
                _source_parents(code_root, name, before[1])
                payload.write(prefix)
                _read_file(code_root / name, info, actual, output_stream=payload)
                payload.write(suffix)
            payload.write(b'done\n')
            payload.seek(0)
            output = _git(private, ('fast-import', '--quiet', '--done'), data=payload,
                          input_limit=None, output_limit=41 * len(selected),
                          index=index, context=context)
        if (len(output) != 41 * len(selected)
                or not re.fullmatch(rb'(?:[0-9a-f]{40}\n)+', output)):
            raise ValueError('Invalid private Git blob identities')
        del framing
        records = bytearray()
        for (name, info), blob in zip(selected, output.splitlines()):
            mode = b'100755' if info[2] & 0o111 else b'100644'
            records.extend(mode + b' ' + blob + b'\t' + os.fsencode(name) + b'\0')
        _git(private, ('update-index', '-z', '--index-info'), data=records,
             input_limit=len(records), index=index, context=context)
        tree = _oid(_git(private, ('write-tree',), index=index, context=context))
        commit = _oid(_git(private, ('commit-tree', tree), data=_MESSAGE,
                           index=index, context=context))
        if scan() != before:
            raise ValueError('Selected code changed during capture')
        _unchanged(source_anchors)
        _pin(private, commit, context)
    return dict(version=1, object_format='sha1', commit=commit, tree_path='.',
                selection=selection, entry=entry, logical_root=logical_root)


def _version(value):
    keys = {'version', 'object_format', 'commit', 'tree_path', 'selection', 'entry', 'logical_root'}
    if (not isinstance(value, dict) or set(value) != keys or type(value.get('version')) is not int
            or value.get('version') != 1 or value.get('object_format') != 'sha1'
            or not isinstance(value.get('commit'), str) or not _HEX.fullmatch(value['commit'])
            or value.get('tree_path') != '.' or value.get('selection') not in ('file', 'directory')):
        raise ValueError('Invalid code_version schema')
    _relative(value['entry'], git_path=True)
    _relative(value['logical_root'])
    return value


def _tree(private, version, policy, context, *, objects=None):
    _ref_path(private)
    if _git(private, ('symbolic-ref', '--quiet', private.ref), context=context, allowed=(0, 1)):
        raise ValueError('Original code ref must not be symbolic')
    pinned = _git(private, ('show-ref', '--verify', '--hash', private.ref), context=context).strip()
    if pinned != version['commit'].encode('ascii'):
        raise ValueError('Code version does not match the exact original call ref')
    if _git(private, ('cat-file', '-t', version['commit']), context=context).strip() != b'commit':
        raise ValueError('Code version must identify a commit')
    listing = _git(private, ('ls-tree', '-r', '-t', '-z', '-l', '--full-tree', version['commit']),
                   output_limit=policy['max_scan_entries'] * (_PATH_BYTES + 128), context=context)
    inventory = Inventory(policy, context=context)
    files, directories, seen = {}, set(), set()
    for record in listing.split(b'\0'):
        if not record:
            continue
        inventory.entry()
        metadata, raw_name = record.split(b'\t', 1)
        mode, kind, oid, size = metadata.split()
        name = _relative(os.fsdecode(raw_name), git_path=True)
        if name in seen:
            raise ValueError('Duplicate private code tree path')
        seen.add(name)
        identity = _oid(oid)
        if objects is not None:
            objects.add(identity)
        if mode == b'040000' and kind == b'tree' and size == b'-':
            directories.add(name)
        elif mode in (b'100644', b'100755') and kind == b'blob' and size.isdigit():
            count = int(size)
            inventory.file(count)
            files[name] = (mode, identity, count)
        else:
            raise ValueError('Private code tree contains unsupported entries')
    required = {parent.as_posix() for name in files for parent in Path(name).parents if str(parent) != '.'}
    if (directories != required or version['entry'] not in files
            or (version['selection'] == 'file' and set(files) != {version['entry']})):
        raise ValueError('Private code tree does not match the selected entry')
    return files, directories


def verify(root, *, step, call_id, code_version, limits=None, context=None) -> tuple[str, ...]:
    """Verify schema, exact call pin, complete regular tree and selected entry."""
    context = Deadline(65, upstream=context)
    version = _version(code_version)
    private = _private(root, step, call_id, create=False, context=context)
    files, _ = _tree(private, version, _limits(limits), context)
    return tuple(files)


def transfer(source_root, target_root, *, step, call_id, code_version, limits=None, context=None) -> None:
    """Stream this exact call's objects into an independent private Git."""
    context = Deadline(65, upstream=context)
    version, policy = _version(code_version), _limits(limits)
    source = _private(source_root, step, call_id, create=False, context=context)
    objects = {version['commit']}
    _tree(source, version, policy, context, objects=objects)
    objects.add(_oid(_git(source, ('rev-parse', version['commit'] + '^{tree}'), context=context)))
    # Explicit object IDs exclude unrelated refs and commit ancestry. Disable
    # deltas so the receiving repository never depends on a source-side base.
    names = ''.join(oid + '\n' for oid in sorted(objects)).encode('ascii')
    with tempfile.TemporaryFile(dir=source.holder) as packed:
        _git(source, ('pack-objects', '--stdout', '--window=0', '--threads=1',
            '--no-reuse-object', '--no-reuse-delta'), data=names, input_limit=len(names),
            output_limit=None, output_stream=packed, context=context)
        packed.seek(0)
        target = _private(target_root, step, call_id, create=True, context=context)
        _git(target, ('index-pack', '--stdin'), data=packed, input_limit=None,
            output_limit=128, context=context)
    _tree(source, version, policy, context)
    _pin(target, version['commit'], context)
    _tree(target, version, policy, context)


def _blob(private, record, context, *, output_stream=None):
    start = output_stream.tell() if output_stream is not None else 0
    content = _git(private, ('cat-file', 'blob', record[1]), output_limit=record[2],
                   output_stream=output_stream, context=context)
    size = output_stream.tell() - start if output_stream is not None else len(content)
    if size != record[2]:
        raise ValueError('Private code blob size changed')
    return content


def _validate_export(private, version, files, directories, policy, context):
    _, anchors = _directory(private.export)
    observed = _scan(private.export, version['entry'], 'directory', policy, context)
    if set(observed[0]) != set(files) or set(observed[1]) != {'.', *directories}:
        raise ValueError('Private export differs from the code version')
    actual = Inventory(policy, context=context)
    class Compare:
        def __init__(self, stream):
            self.stream = stream

        def write(self, chunk):
            if self.stream.read(len(chunk)) != chunk:
                raise ValueError('Private export differs from the code version')

    for name, record in files.items():
        expected_mode = 0o555 if record[0] == b'100755' else 0o444
        if stat.S_IMODE(observed[0][name][2]) != expected_mode:
            raise ValueError('Private export differs from the code version')
        with tempfile.TemporaryFile(dir=private.holder) as content:
            _blob(private, record, context, output_stream=content)
            content.seek(0)
            _read_file(private.export / name, observed[0][name], actual,
                       output_stream=Compare(content))
            if content.read(1):
                raise ValueError('Private export differs from the code version')
    if any(stat.S_IMODE(info[2]) != 0o555 for info in observed[1].values()):
        raise ValueError('Private export directory permissions changed')
    if _scan(private.export, version['entry'], 'directory', policy, context) != observed:
        raise ValueError('Private export changed during verification')
    _unchanged(anchors)
    _unchanged(private.anchors)


def _remove_export(private, context):
    """Remove only this exact tree using directory FDs, without following links."""
    _unchanged(private.anchors)
    parent = _open_directory(private.holder, expected=private.anchors[private.holder])
    try:
        path = private.holder
        for component in private.export.parent.relative_to(private.holder).parts:
            path = path / component
            child = _open_directory(component, dir_fd=parent, expected=private.anchors[path])
            os.close(parent)
            parent = child
        _unchanged(private.anchors)
    except BaseException:
        os.close(parent)
        raise
    stack = []
    try:
        root = _open_directory('tree', dir_fd=parent)
        try:
            os.fchmod(root, 0o700)
            stack.append((root, os.scandir(root), parent, 'tree'))
        except BaseException:
            os.close(root)
            raise
        while stack:
            context.remaining_seconds()
            fd, entries, parent_fd, name = stack[-1]
            item = next(entries, None)
            if item is None:
                info = os.fstat(fd)
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    raise ValueError('Private export directory changed')
                os.rmdir(name, dir_fd=parent_fd)
                entries.close()
                os.close(fd)
                stack.pop()
                continue
            info = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = _open_directory(item.name, dir_fd=fd, expected=(info.st_dev, info.st_ino))
                try:
                    os.fchmod(child, 0o700)
                    stack.append((child, os.scandir(child), fd, item.name))
                except BaseException:
                    os.close(child)
                    raise
            elif stat.S_ISREG(info.st_mode):
                os.unlink(item.name, dir_fd=fd)
            else:
                raise ValueError('Private export contains a non-regular entry')
        _unchanged(private.anchors)
    finally:
        for fd, entries, _, _ in stack:
            entries.close()
            os.close(fd)
        os.close(parent)


def materialize(root, *, step, call_id, code_version, limits=None, context=None) -> Path:
    """Export exact raw blobs as 0444/0555 files; reuse only an unchanged export."""
    context = Deadline(65, upstream=context)
    version, policy = _version(code_version), _limits(limits)
    private = _private(root, step, call_id, create=False, context=context)
    files, directories = _tree(private, version, policy, context)
    for directory in reversed(private.export.parent.relative_to(private.holder).parents):
        if str(directory) != '.':
            _mkdir(private.holder / directory, private.anchors)
    _mkdir(private.export.parent, private.anchors)
    try:
        private.export.mkdir(mode=0o700)
    except FileExistsError:
        _validate_export(private, version, files, directories, policy, context)
        return private.export
    export_anchors = dict(private.anchors)
    _extend(export_anchors, _lineage(private.export))
    try:
        for name in sorted(directories, key=lambda value: (len(Path(value).parts), value)):
            _mkdir(private.export / name, export_anchors)
        for name, record in files.items():
            _unchanged(export_anchors)
            fd = os.open(private.export / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                _blob(private, record, context, output_stream=stream)
                stream.flush()
                os.fchmod(stream.fileno(), 0o555 if record[0] == b'100755' else 0o444)
        for name in sorted(directories, key=lambda value: len(Path(value).parts), reverse=True):
            _unchanged(export_anchors)
            (private.export / name).chmod(0o555)
        _unchanged(export_anchors)
        private.export.chmod(0o555)
        _unchanged(export_anchors)
        _validate_export(private, version, files, directories, policy, context)
        return private.export
    except BaseException:
        _remove_export(private, context)
        raise


def cleanup_export(root, *, step, call_id, context=None) -> None:
    """Remove this call's export only; caller must establish lifecycle safety."""
    context = Deadline(65, upstream=context)
    _directory(root)
    try:
        private = _private(root, step, call_id, create=False, context=context, repository=False)
    except FileNotFoundError:
        return
    for directory in (private.holder / 'exports', private.export.parent.parent, private.export.parent):
        try:
            _extend(private.anchors, _lineage(directory))
        except FileNotFoundError:
            return
    try:
        info = private.export.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Private export must be a real directory')
    _remove_export(private, context)
