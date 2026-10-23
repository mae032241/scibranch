"""Stream framework originals into an inert read-only package, never a live root."""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile

from scidiscovery import archive_io as files
from .instance_owner import effect as owner_effect

LEGACY_FORMAT = 'scidiscovery.instance-readonly-archive.v1'
SHARED_FORMAT = 'scidiscovery.instance-readonly-archive.v2'
FORMAT = 'scidiscovery.instance-readonly-archive.v3'
ROOT_FILES = {'settings.json', 'settings-identity.json', 'host-started.json', 'pause.request', 'instance-owner.json'}
ROOT_DIRS = {'jobs', 'project', 'control-ledger', 'correction-ledger', 'native-evidence',
             'host-runtime', 'host-history', 'host-delivery', 'source-provenance',
             'qualification', 'execution-authority', 'host-control', 'host-recovery'}
AUTHORITY_EXCLUDED = {'host-key-source.json', 'ui-endpoint.json', 'ui.lock', 'writer.lock'}
CALL_LOCKS = ('node.lock', 'accept.lock')
LIMITATIONS = ['Framework-owned originals only; undeclared external inputs/stores are not covered.',
    'Native homes, session caches and credentials are excluded; native continuation is not established.',
    'Historical decisions and resource events are bytes, not renewed permissions, budgets or signature verification.',
    'No writable restoration or protection against arbitrary same-uid shell writers.']


def _read(path):
    files.check_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise files.ArchiveError('Archive metadata must be a regular file')
        return json.load(stream)


def _path(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
            or PurePosixPath(value).as_posix() != value or value == '.'):
        raise files.ArchiveError('Archive logical path is not canonical')
    return PurePosixPath(value)


def _excluded(root, parts):
    if parts == ('.scid-ui',):
        return 'private_ui_cache_configuration_and_diagnostics'
    if (len(parts) == 5 and parts[:2] == ('project', 'nodes')
            and parts[2].isascii() and parts[2].isdecimal() and int(parts[2]) > 0
            and parts[2] == f'{int(parts[2]):03d}' and parts[3:] == ('.agents', 'skills')):
        from . import skill_supply
        skill_supply.archive_projection(root, int(parts[2]))
        return 'verified_derived_skill_projection'
    if parts[:2] == ('host-history', 'fork-cache'):
        return 'immutable_fork_snapshot_cache'
    if parts[0] == 'native-homes':
        return 'native_environment_sessions_caches_and_credentials'
    if len(parts) == 1 and parts[0] in {'host-run.lock', 'control-ledger.lock', 'configuration.lock'}:
        return 'controller_or_budget_lock'
    if parts == ('host-runtime', 'storage.lock'):
        return 'managed_storage_lease'
    if parts == ('host-runtime', 'shared-input-views'):
        return 'derived_host_shared_input_directory_pins'
    if len(parts) == 2 and parts[0] == 'execution-authority' and parts[1] in AUTHORITY_EXCLUDED:
        return 'private_key_locator_or_transient_ui_or_authority_lock'
    if (parts[0] == 'native-evidence' and parts[-1] == 'runner.lock'
            and (len(parts) == 3 and parts[1].isdecimal() or len(parts) == 5
                 and parts[1].isdecimal() and parts[2] == 'continuations' and parts[3].isdecimal())):
        return 'native_runner_lease'
    if (len(parts) == 4 and parts[:2] == ('host-runtime', 'experiment-control')
            and re.fullmatch('[0-9a-f]{64}', parts[2]) and parts[3] == 'accept.lock'):
        return 'managed_admission_lease'
    legacy_records = (len(parts) == 6 and parts[0:2] == ('project', 'nodes')
        and parts[2].isdecimal() and parts[3:5] == ('work', 'lineage'))
    host_records = (len(parts) == 5 and parts[:2] == ('host-runtime', 'experiment-control')
        and re.fullmatch('[0-9a-f]{64}', parts[2]) and parts[3] == 'records')
    if legacy_records or host_records:
        name = parts[-1]
        if name.endswith('.lock'):
            return 'managed_records_lease'
    return None


def _private_originals(root):
    """Exclude only credentials explicitly located by the framework host binding."""
    locator = root / 'execution-authority/host-key-source.json'
    if not locator.exists():
        return frozenset()
    value = _read(locator)
    # Historical archives can retain an opaque locator marker without a source.
    if 'source' not in value:
        return frozenset()
    source = value['source']
    if (not isinstance(source, dict) or not isinstance(source.get('path'), str)
            or not isinstance(source.get('sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', source['sha256'])
            or type(source.get('size_bytes')) is not int or source['size_bytes'] < 0):
        raise files.ArchiveError('Private credential locator is malformed')
    path = Path(source['path'])
    if not path.is_absolute() or path.resolve() != path:
        raise files.ArchiveError('Private credential locator is not canonical')
    binding = root / 'execution-authority/host-key.json'
    key = value.get('key')
    if (not isinstance(key, dict) or key.get('path') != 'execution-authority/host-key.json'
            or any(key.get(field) != files.hash_file(binding)[field] for field in ('sha256', 'size_bytes'))
            or _read(binding).get('key_sha256') != source['sha256']):
        raise files.ArchiveError('Private credential locator lost its host binding')
    return frozenset({path.relative_to(root).as_posix()}) if path.is_relative_to(root) else frozenset()


def _inventory(root, *, context=None, inventory=None):
    """Walk only explicit owning namespaces; unknown roots are gaps, not trees."""
    private = _private_originals(root)
    def walk(path, parts):
        from . import storage_policy as storage
        storage.check(context)
        if inventory is not None:
            inventory.entry()
        name = '/'.join(parts)
        _path(name)
        reason = 'declared_private_credential' if name in private else _excluded(root, parts)
        if reason:
            yield dict(path=name, kind='excluded', reason=reason)
            return
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise files.ArchiveError('Symbolic link in declared archive scope: ' + name)
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise files.ArchiveError('Special file in archive scope: ' + name)
        identity = dict(mode=stat.S_IMODE(info.st_mode),
            source_stat=[info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns])
        unknown = (parts[0] not in ROOT_FILES | ROOT_DIRS
            or len(parts) == 2 and parts[0] == 'project' and parts[1] not in {'global', 'nodes'}
            or len(parts) == 2 and parts[0] == 'host-control' and parts[1] not in {'configuration', 'guidance', 'guidance-revocations', 'operations', 'wait-continuations', 'skills'}
            or len(parts) == 2 and parts[0] == 'host-runtime' and parts[1] not in {'experiment-control', 'restore', 'fork'})
        if unknown:
            yield dict(path=name, kind='gap', reason='unrecognized_framework_ownership_not_traversed', **identity)
            return
        if len(parts) == 1 and (parts[0] in ROOT_FILES) != stat.S_ISREG(info.st_mode):
            raise files.ArchiveError('Framework archive root has the wrong file type: ' + name)
        if stat.S_ISREG(info.st_mode):
            if inventory is not None:
                inventory.file(info.st_size)
            yield dict(path=name, kind='file', **files.hash_file(path, context=context))
        else:
            yield dict(path=name, kind='directory', **identity)
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                yield from walk(child, (*parts, child.name))
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        yield from walk(child, (child.name,))


def _lock(stack, path):
    files.check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    stack.callback(os.close, descriptor)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise files.ArchiveError('Archive owner lock is not a regular file')
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise files.ArchiveError('Original owner is active; archive was not created') from error


def _managed_roots(root):
    for node in sorted((root / 'project/nodes').glob('*')):
        path = node / 'work/lineage'
        if path.exists():
            files.check_path(path)
            if not path.is_dir() or not node.name.isdecimal():
                raise files.ArchiveError('Unknown managed records namespace')
            yield path
    from . import contracts as c, experiment_client
    expected = {experiment_client.record_directory(root, c.read(c.regular(job))['step'])
        for job in (root / 'jobs').glob('*.json')}
    for path in sorted((root / 'host-runtime/experiment-control').glob('*/records')):
        files.check_path(path)
        if not path.is_dir() or path not in expected:
            raise files.ArchiveError('Unknown managed records namespace')
        yield path


def record_leases(records):
    """Current protocol plus explicit conservative compatibility for old stores.

    Existing record owners keep their locks; no installed plugin is consulted.
    Taking all direct lock files also fences older writers during migration.
    """
    records = files.check_path(records)
    paths = sorted(path for path in records.glob('*.lock') if path.name not in CALL_LOCKS)
    for path in paths:
        files.check_path(path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise files.ArchiveError('Managed record lease is not a regular file')
    return paths


def _owner_locks(stack, root, *, disposing=False, context=None):
    # Production nested order: records node→accept→record-writer,
    # then authority→ledger. Runner completion probes controller nonblocking.
    # All exporter acquisitions are nonblocking; no high-level owner reentry.
    from . import controller, instance_owner
    source_gate = instance_owner._gates.get().get(str(Path(root).absolute()))
    if disposing:
        from . import instance_owner
        if str(Path(root).absolute()) not in instance_owner._disposing.get():
            raise files.ArchiveError('Delete locks require the exact disposal owner')
        stack.enter_context(controller._local_writer(root))
    elif source_gate is not None and source_gate['exclusive'] and not source_gate['cold']:
        # Ordinary archive owns the source gate exclusively; reentering it as
        # an effect would be forbidden. Cold transfer retains its own protocol.
        instance_owner.require_owner(root)
        stack.enter_context(controller._local_writer(root))
    else:
        stack.enter_context(controller._writer(root))
    if (root / 'configuration.lock').exists():
        _lock(stack, root / 'configuration.lock')
    _lock(stack, root / 'execution-authority/ui.lock')
    for path in sorted((root / 'native-evidence').glob('*/**/native-intent.json')):
        _lock(stack, path.parent / 'runner.lock')
    for records in _managed_roots(root):
        for name in CALL_LOCKS:
            _lock(stack, records / name)
        for call in sorted((records / 'calls').glob('*')):
            if not call.is_dir() or not re.fullmatch('[A-Za-z0-9._-]{1,128}', call.name):
                raise files.ArchiveError('Unknown managed call identity')
        for path in record_leases(records):
            _lock(stack, path)
    for path in sorted((root / 'host-runtime/experiment-control').glob('*/accept.lock')):
        _lock(stack, path)
    _lock(stack, root / 'execution-authority/writer.lock')
    _lock(stack, root / 'control-ledger.lock')
    from . import storage_policy as storage
    stack.enter_context(storage.writer_lock(root, context=context))


def _dead(identity, binding):
    from . import runtime
    if (not isinstance(binding, str) or not re.fullmatch('[0-9a-f]{64}', binding)
            or not isinstance(identity, dict) or identity.get('task_sha256') != binding
            or any(type(identity.get(key)) is not int or identity[key] <= 0
                   for key in ('pid', 'start_ticks', 'pgid', 'sid')) or not identity.get('boot_id')):
        raise files.ArchiveError('Original process identity is incomplete')
    if runtime.process_matches(identity, binding):
        raise files.ArchiveError('An original process is still active')


def _quiescent(root):
    """Only retained original facts; no plugin factory, query, snapshot or grant."""
    from . import budget, contracts as c, experiments as e, experiment_client
    for name in ('runner-process.json', 'native-process.json', 'watchdog-process.json'):
        for path in (root / 'native-evidence').glob('*/**/' + name):
            if not path.with_name('native-intent.json').exists():
                raise files.ArchiveError('Native writer has no accepted intent original')
    for path in sorted((root / 'native-evidence').glob('*/**/native-intent.json')):
        record = path.parent; relative = record.relative_to(root / 'native-evidence')
        if (len(relative.parts) not in (1, 3) or not relative.parts[0].isdecimal()
                or len(relative.parts) == 3 and (relative.parts[1] != 'continuations' or not relative.parts[2].isdecimal())):
            raise files.ArchiveError('Unrecognized native attempt owner')
        job = root / 'jobs' / (relative.parts[0] + '.json')
        intent = _read(path)
        if intent.get('task_sha256') != c.digest(c.regular(job)):
            raise files.ArchiveError('Native intent lost its exact task')
        outcome_path = record / 'native-outcome.json'
        if not outcome_path.exists():
            raise files.ArchiveError('Native outcome is absent; writer state is unknown')
        outcome = _read(outcome_path)
        if (outcome.get('native_started') is not False
                and (type(outcome.get('returncode')) is not int or outcome.get('cleanup') != 'observed_group_cleaned')):
            raise files.ArchiveError('Native cleanup is unconfirmed')
        binding = c.digest(job) if len(relative.parts) == 1 else c.digest(path)
        for name in ('runner-process.json', 'native-process.json', 'watchdog-process.json'):
            process = record / name
            if process.exists():
                _dead(_read(process), binding)
        cleanup = record / 'runner-cleanup.json'
        if cleanup.exists() and _read(cleanup).get('status') != 'observed_group_cleaned':
            raise files.ArchiveError('Native watchdog retained unknown cleanup')
    for records in _managed_roots(root):
        if any(records.glob('.prepared-*')):
            raise files.ArchiveError('Original preparation has not completed acceptance')
        for call in sorted((records / 'calls').glob('*')):
            accepted = _read(call / 'accepted.json'); interface = accepted['interface']
            if (call / 'submission-retirement.json').exists():
                verified = e.verify_retirement(_read(call / 'submission-retirement.json'),
                    lambda item: Path(item['path']), interface=interface)
            elif (call / 'execution-observation.json').exists():
                verified = _read(call / 'execution-observation.json')
                e.verify_observation(verified, lambda item: Path(item['path']), interface=interface)
            else:
                raise files.ArchiveError('Managed execution has no verified terminal or permanent retirement original')
            if (verified.get('call_id') != call.name or verified.get('scope') != accepted.get('scope')
                    or verified.get('accepted') != e.proof(call / 'accepted.json')):
                raise files.ArchiveError('Terminal original belongs to another managed call')
            if interface.get('terminal_evidence') == 'local_process' and (call / 'process.json').exists():
                _dead(_read(call / 'process.json'), hashlib.sha256(e.encoded(accepted)).hexdigest())
            replay = call / 'engineering-check.intent.json'
            if replay.exists():
                result = _read(call / 'engineering-check.outcome.json')
                if result.get('intent_sha256') != c.digest(replay) or result.get('result', {}).get('cleanup_confirmed') is not True:
                    raise files.ArchiveError('Engineering replay has unconfirmed cleanup')
    for path in (root / 'execution-authority/calls').glob('*/prepare-intent.json'):
        allocation = _read(path.with_name('allocation.json'))
        step = allocation['binding']['scope']['step']
        if not (experiment_client.record_directory(root, step) / 'calls' / allocation['call_id'] / 'accepted.json').exists():
            raise files.ArchiveError('Pre-accept preparation cannot be proved inactive')
    state = budget.closeout_state(root)  # Pure retained-ledger fold, no lock or current clock.
    if (state['unresolved_operations'] or state['begin'] is not None
            and state['begin']['stop_reason'] == 'delivery_cleanup_unknown'
            or any(row['outcome'] is None or row['outcome']['result'].get('cleanup_confirmed') is not True
                   for row in state['actions'])):
        raise files.ArchiveError('Delivery owner is unresolved')
    pairs = [(path, path.with_name('outcome.json'))
             for path in (root / 'host-delivery/audit-recovery').glob('*/intent.json')]
    pairs += [(path, path.with_name(path.name.replace('.intent.json', '.outcome.json')))
              for path in (root / 'host-delivery/selection').rglob('*.intent.json')]
    pairs += [(path, path.with_name('outcome.json'))
              for path in (root / 'native-evidence').glob('*/candidate-audit/intent.json')]
    for intent, outcome in pairs:
        if not outcome.exists():
            raise files.ArchiveError('Accepted delivery/audit intent has no outcome')
        value = _read(outcome)
        if value.get('intent_sha256', c.digest(intent)) != c.digest(intent) or value.get('result', value).get('cleanup_confirmed') is not True:
            raise files.ArchiveError('Delivery/audit cleanup is unconfirmed')
    for path in (root / 'host-delivery/preparation').glob('*.json'):
        if _read(path).get('cleanup_confirmed') is not True:
            raise files.ArchiveError('Delivery source preparation cleanup is unknown')
    process_paths = list((root / 'host-delivery/action-processes').glob('*.json'))
    process_paths += list((root / 'host-delivery/audit-recovery').glob('*/process.json'))
    process_paths += list((root / 'native-evidence').glob('*/candidate-audit/process.json'))
    for records in _managed_roots(root):
        process_paths += list((records / 'calls').glob('*/engineering-check.process.json'))
    for path in process_paths:
        identity = _read(path)['identity']
        _dead(identity, identity.get('task_sha256'))


def _dependencies(settings):
    yield dict(owner='public_root', reference=settings['public_root'], coverage='external_tree_not_copied')
    yield dict(owner='native_launcher', reference=settings['native_launcher_argv'], coverage='runtime_not_copied')
    for key in ('history_observer', 'delivery_policy'):
        value = settings.get(key)
        if value is not None:
            yield dict(owner=key, reference=value.get('adapter'), coverage='external_adapter_dependencies_not_copied')
    configured = settings.get('execution_config')
    if configured is not None:
        yield dict(owner='registered_plugin_storage', reference=configured['executor'],
                   coverage='unsupported_without_frozen_storage_ownership')
    yield dict(owner='native_sessions', reference='native-homes', coverage='credentials_caches_and_unretained_sessions_excluded')
    yield dict(owner='approval_receipt_key', reference=None, coverage='key_and_private_locator_excluded_no_signature_authority')


def _configuration_dependencies(root, settings):
    from . import configuration_updates
    yield from _dependencies(settings)
    for revision in configuration_updates.revisions(root):
        for row in _dependencies(revision['settings']):
            yield dict(row, owner=f'configuration/{revision["revision"]}/{row["owner"]}')


def _stream(path, values, *, context=None):
    count = 0
    with path.open('xb') as stream:
        for count, value in enumerate(values, 1):
            if context is not None:
                context.remaining_seconds()
            stream.write(files.canonical(dict(index=count, **value)) + b'\n')
        stream.flush(); os.fsync(stream.fileno())
    return count


def _publish(stage, output):
    # Linux, like the original runtime. No replacement fallback if unsupported.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, 'renameat2', None)
    if function is None:
        raise files.ArchiveError('Atomic no-replacement directory publication is unavailable')
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(stage), -100, os.fsencode(output), 1):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise files.ArchiveError('Archive destination already exists')
        raise OSError(error, os.strerror(error))
    files.fsync_directory(output.parent)
    if stage.parent != output.parent:
        files.fsync_directory(stage.parent)


def archive_export(root, output, *, independent=False):
    from .instance_owner import source_lease
    with source_lease(root):
        return _export(root, output, independent=independent)


def _same_inventory_entry(expected, current):
    if (expected.get('kind') == current.get('kind') == 'file'
            and expected.get('mode') == current.get('mode') == 0o400):
        # Both inventories freshly hash the file. Link/unlink changes ctime on
        # shared readonly inodes; every other property and field stays binding.
        expected = dict(expected, source_stat=expected['source_stat'][:3])
        current = dict(current, source_stat=current['source_stat'][:3])
    return expected == current


def _immutable_bindings(root):
    """Exact files published by the fork and Skill lifecycle owners."""
    from . import contracts as c, originals, skill_supply
    bindings = {}
    manifest = originals._manifest(root)
    if manifest is not None:
        for row in manifest['entries']:
            bindings[root / originals.FORK_DIRECTORY / _path(row['storage_path'])] = row
        for snapshot in manifest.get('public_snapshots', []):
            bindings[root / originals.FORK_DIRECTORY / 'public' / _path(snapshot['path'])] = snapshot['original_ref']
    settings = c.settings(root)
    manifest = skill_supply.validate(root, settings)
    if manifest is not None:
        for tree in manifest['objects'].values():
            for row in tree['files']:
                bindings[root / skill_supply.DIRECTORY / 'objects' / tree['tree_sha256'] / _path(row['path'])] = row
        manifest_path = root / skill_supply.MANIFEST
        bindings[manifest_path] = dict(settings['skills']['manifest'], size_bytes=manifest_path.stat().st_size)
    return bindings


def _managed_original(root, source, entry, bindings):
    """Mode alone never proves ownership; CAS names and owner bindings do."""
    if stat.S_IMODE(source.stat().st_mode) != 0o400:
        return False
    proof = bindings.get(source)
    if proof is not None and all(proof.get(key) == entry[key] for key in ('sha256', 'size_bytes')):
        return True
    candidate = files.check_path(root / 'host-history/objects' / entry['sha256'])
    if not candidate.exists():
        return False
    left, right = source.stat(), candidate.stat()
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _snapshot_original(root, source, entry, *, context, policy, managed=False):
    """Freeze once in the inventory-excluded owner cache, preserving live mode."""
    from . import atomic_files, storage_policy as storage
    source = files.check_path(source)
    storage.Inventory(policy, context=context).file(entry['size_bytes'])
    with storage.writer_lock(root, context=context):
        before = files.hash_file(source, context=context)
        if any(before[key] != entry[key] for key in ('sha256', 'size_bytes')):
            raise files.ArchiveError('Snapshot source differs from its exact binding')
        if managed:
            if before['mode'] != 0o400:
                raise files.ArchiveError('Managed snapshot original is not frozen')
            return source
        for candidate in (root / 'host-history/objects' / entry['sha256'],
                          root / 'host-history/fork-cache' / entry['sha256']):
            candidate = files.check_path(candidate)
            if candidate.exists():
                actual = files.hash_file(candidate, context=context)
                if (actual['mode'] != 0o400 or any(actual[key] != entry[key] for key in ('sha256', 'size_bytes'))):
                    raise files.ArchiveError('Snapshot cache original changed')
                return candidate
        target = files.check_path(root / 'host-history/fork-cache' / entry['sha256'])
        with storage.mutation(root, entry['size_bytes'], policy=policy, context=context, target=target):
            try:
                atomic_files.copy_original(source, target, sha256=entry['sha256'],
                    size_bytes=entry['size_bytes'], context=context)
            except OSError as error:
                if error.errno in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM, errno.EMLINK}:
                    raise files.SharingUnsupported('Snapshot publication requires supported same-filesystem hardlinks') from error
                raise
        after = files.hash_file(source, context=context)
        if not _same_inventory_entry(dict(kind='file', **before), dict(kind='file', **after)):
            raise files.ArchiveError('Snapshot source changed during first freeze')
        return target


def _reference_growth(root, source, *, policy, context):
    """Instance quota counts the inode once even across external namespaces."""
    from . import storage_policy as storage
    if not policy['enabled'] or policy['max_instance_bytes'] is None:
        return 0
    wanted = source.stat()
    pending = [root]
    while pending:
        storage.check(context)
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                storage.check(context)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (wanted.st_dev, wanted.st_ino):
                    return 0
    return wanted.st_size


def _export(root, output, *, independent=False, on_locked=None, context=None):
    from . import contracts as c, storage_policy as storage
    root = files.check_path(Path(root).expanduser().absolute())
    output = files.check_path(Path(output).expanduser().absolute())
    if root.resolve() != root or output.resolve() != output:
        raise files.ArchiveError('Archive source and destination must be canonical paths')
    if root == output or output.is_relative_to(root) or root.is_relative_to(output):
        raise files.ArchiveError('Archive destination must be separate from the original instance')
    if output.exists():
        raise files.ArchiveError('Archive destination already exists')
    if not output.parent.is_dir():
        raise files.ArchiveError('Explicit archive destination parent must exist')
    if not independent and root.stat().st_dev != output.parent.stat().st_dev:
        raise files.SharingUnsupported('Archive export requires a supported same-filesystem destination')
    settings = c.settings(root)
    context = context or storage.Deadline(65)
    policy = storage.for_root(root)
    with ExitStack() as locks:
        _owner_locks(locks, root, context=context)
        _quiescent(root)
        # Both namespaces must exist before the freeze callback and first walk:
        # adding their parent entries later would change the snapshot itself.
        files.check_path(root / 'host-history/fork-cache').mkdir(parents=True, exist_ok=True, mode=0o700)
        bindings = _immutable_bindings(root)
        if on_locked is not None:
            on_locked(settings)
        stage = Path(tempfile.mkdtemp(prefix='.' + output.name + '-archive-', dir=output.parent))
        try:
            (stage / 'blobs').mkdir()
            counts = dict(files=0, directories=0, gaps=0, excluded=0, bytes=0, unique_blobs=0, blob_bytes=0)
            blobs = set()
            def originals():
                for entry in _inventory(root, context=context, inventory=storage.Inventory(policy, context=context)):
                    if entry['kind'] == 'file':
                        source = root / entry['path']
                        managed = _managed_original(root, source, entry, bindings)
                        entry['storage'] = 'immutable' if managed else 'independent'
                        entry['blob'] = f'blobs/{entry["sha256"]}-{entry["size_bytes"]}'
                        if entry['blob'] not in blobs:
                            frozen = _snapshot_original(root, source, entry, context=context, policy=policy, managed=managed)
                            publish = files.copy_independent_verified if independent else files.link_verified
                            publish(frozen, stage / entry['blob'], dict(entry, mode=0o400), context=context)
                            blobs.add(entry['blob'])
                            counts['unique_blobs'] += 1; counts['blob_bytes'] += entry['size_bytes']
                        counts['files'] += 1; counts['bytes'] += entry['size_bytes']
                    else:
                        counts[{'directory': 'directories', 'gap': 'gaps', 'excluded': 'excluded'}[entry['kind']]] += 1
                    yield entry
            entries = _stream(stage / 'manifest.jsonl', originals(), context=context)
            dependencies = _stream(stage / 'dependencies.jsonl', _configuration_dependencies(root, settings), context=context)
            metadata = dict(format=FORMAT, blob_ownership='independent' if independent else 'shared', instance_id=settings['goal_id'], settings_sha256=c.digest(root / 'settings.json'),
                restorable=False, limitations=LIMITATIONS, entries=entries, dependencies=dependencies, **counts,
                manifest={key: value for key, value in files.hash_file(stage / 'manifest.jsonl', context=context).items() if key in {'sha256', 'size_bytes'}},
                dependency_manifest={key: value for key, value in files.hash_file(stage / 'dependencies.jsonl', context=context).items() if key in {'sha256', 'size_bytes'}})
            files.durable_json(stage / 'archive.json', metadata)
            with (stage / 'manifest.jsonl').open('rb') as stream:
                for index, current in enumerate(_inventory(root, context=context), 1):
                    line = stream.readline()
                    if not line:
                        raise files.ArchiveError('Original archive scope gained entries before publication')
                    expected = json.loads(line)
                    expected.pop('blob', None)
                    expected.pop('storage', None)
                    if not _same_inventory_entry(expected, dict(index=index, **current)):
                        raise files.ArchiveError('Original archive scope changed before publication')
                if stream.read(1):
                    raise files.ArchiveError('Original archive scope lost entries before publication')
            files.fsync_directory(stage / 'blobs'); files.fsync_directory(stage)
            _publish(stage, output)
            return dict(status='archive_exported', package=str(output), instance_id=settings['goal_id'],
                        blob_ownership=metadata['blob_ownership'],
                        restorable=False, **counts, external_dependencies=dependencies, limitations=LIMITATIONS)
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def archive_inspect(package, *, after=0, limit=20, section='manifest', _context=None):
    """Verify every blob/line without importing framework plugins or writing files."""
    package = files.check_path(Path(package).expanduser().absolute())
    from . import storage_policy as storage
    context = _context or storage.Deadline(65)
    if package.resolve() != package:
        raise files.ArchiveError('Read-only archive path must be canonical')
    if (type(after) is not int or after < 0 or type(limit) is not int or limit <= 0
            or section not in {'manifest', 'dependencies'}):
        raise files.ArchiveError('Exact positive archive page and continuation required')
    if {path.name for path in package.iterdir()} != {'archive.json', 'manifest.jsonl', 'dependencies.jsonl', 'blobs'}:
        raise files.ArchiveError('Read-only package layout changed')
    metadata = _read(package / 'archive.json')
    if not isinstance(metadata, dict):
        raise files.ArchiveError('Unsupported read-only archive format')
    legacy = metadata.get('format') == LEGACY_FORMAT
    counters = {'entries', 'dependencies', 'files', 'directories', 'gaps', 'excluded', 'bytes'}
    if not legacy:
        counters |= {'unique_blobs', 'blob_bytes'}
    fields = counters | {'format', 'instance_id', 'settings_sha256', 'restorable', 'limitations', 'manifest', 'dependency_manifest'}
    if metadata.get('format') == FORMAT:
        fields.add('blob_ownership')
    if (set(metadata) != fields or metadata.get('format') not in {FORMAT, SHARED_FORMAT, LEGACY_FORMAT}
            or metadata.get('format') == FORMAT and metadata.get('blob_ownership') not in ('shared', 'independent')
            or metadata.get('restorable') is not False
            or any(type(metadata[key]) is not int or metadata[key] < 0 for key in counters)
            or not isinstance(metadata['instance_id'], str) or not metadata['instance_id']
            or not isinstance(metadata['settings_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', metadata['settings_sha256'])):
        raise files.ArchiveError('Unsupported read-only archive format')
    counts = dict(files=0, directories=0, gaps=0, excluded=0, bytes=0)
    blobs = {}
    selected, last, total = [], None, 0
    settings_blob = identity_blob = None
    files.check_path(package / 'blobs')
    for name, proof in (('manifest', metadata['manifest']), ('dependencies', metadata['dependency_manifest'])):
        if (not isinstance(proof, dict) or set(proof) != {'sha256', 'size_bytes'}
                or not isinstance(proof['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', proof['sha256'])
                or type(proof['size_bytes']) is not int or proof['size_bytes'] < 0):
            raise files.ArchiveError('Malformed archive manifest identity')
        source = package / (name + '.jsonl')
        before = files.hash_file(source, context=context)
        if any(before[key] != proof[key] for key in ('sha256', 'size_bytes')):
            raise files.ArchiveError('Archive manifest changed')
        number = 0; previous = None
        with source.open('rb') as stream:
            for number, line in enumerate(stream, 1):
                storage.check(context)
                entry = json.loads(line)
                if not isinstance(entry, dict) or type(entry.get('index')) is not int or entry['index'] != number:
                    raise files.ArchiveError('Archive entry sequence changed')
                if name == 'manifest':
                    kind = entry.get('kind')
                    fields = {'index', 'path', 'kind'}
                    fields |= ({'blob', 'size_bytes', 'sha256', 'mode', 'source_stat'} if kind == 'file' else
                        {'mode', 'source_stat'} if kind == 'directory' else
                        {'reason', 'mode', 'source_stat'} if kind == 'gap' else {'reason'} if kind == 'excluded' else set())
                    if kind == 'file' and not legacy:
                        fields.add('storage')
                    if not isinstance(kind, str) or set(entry) != fields or kind not in {'file', 'directory', 'gap', 'excluded'}:
                        raise files.ArchiveError('Malformed archive entry')
                    logical = _path(entry['path'])
                    if previous is not None and logical.parts <= previous:
                        raise files.ArchiveError('Duplicate or unordered archive logical path')
                    previous = logical.parts
                    if kind == 'file':
                        if (not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                                or type(entry['size_bytes']) is not int or entry['size_bytes'] < 0
                                or type(entry['mode']) is not int or not 0 <= entry['mode'] <= 0o7777):
                            raise files.ArchiveError('Malformed archive file binding')
                        expected_blob = (f'blobs/{number:012d}' if legacy else
                            f'blobs/{entry["sha256"]}-{entry["size_bytes"]}')
                        if entry.get('blob') != expected_blob:
                            raise files.ArchiveError('Archive blob path changed')
                        if not legacy and (not isinstance(entry['storage'], str) or entry['storage'] not in {'immutable', 'independent'}
                                or entry['storage'] == 'immutable' and entry['mode'] != 0o400):
                            raise files.ArchiveError('Malformed archive storage semantics')
                        actual = blobs.get(entry['blob'])
                        if actual is None:
                            actual = files.hash_file(package / entry['blob'], context=context)
                            blobs[entry['blob']] = actual
                        if (any(actual[key] != entry[key] for key in ('sha256', 'size_bytes'))
                                or actual['mode'] != (entry['mode'] if legacy else 0o400)):
                            raise files.ArchiveError('Archive blob changed')
                        if entry['path'] == 'settings.json':
                            if entry['sha256'] != metadata['settings_sha256']:
                                raise files.ArchiveError('Archive instance settings identity changed')
                            settings_blob = package / entry['blob']
                        elif entry['path'] == 'settings-identity.json':
                            identity_blob = package / entry['blob']
                        counts['files'] += 1; counts['bytes'] += entry['size_bytes']
                    elif kind in {'directory', 'gap', 'excluded'}:
                        counts[{'directory': 'directories', 'gap': 'gaps', 'excluded': 'excluded'}[kind]] += 1
                    else:
                        raise files.ArchiveError('Unknown archive entry kind')
                elif set(entry) != {'index', 'owner', 'reference', 'coverage'}:
                    raise files.ArchiveError('Malformed archive dependency reference')
                if name == section and number > after and len(selected) < limit:
                    selected.append(entry); last = number
        if files.hash_file(source, context=context) != before:
            raise files.ArchiveError('Archive manifest changed during inspection')
        expected = metadata['entries'] if name == 'manifest' else metadata['dependencies']
        if number != expected:
            raise files.ArchiveError('Archive manifest count changed')
        if name == section:
            total = number
    if any(counts[key] != metadata[key] for key in counts):
        raise files.ArchiveError('Archive summary changed')
    if not legacy:
        counts.update(unique_blobs=len(blobs), blob_bytes=sum(row['size_bytes'] for row in blobs.values()))
        if any(counts[key] != metadata[key] for key in ('unique_blobs', 'blob_bytes')):
            raise files.ArchiveError('Archive unique content summary changed')
    if {path.name for path in (package / 'blobs').iterdir()} != {Path(name).name for name in blobs}:
        raise files.ArchiveError('Archive has unlisted blob entries')
    settings = _read(settings_blob) if settings_blob is not None else None
    identity = _read(identity_blob) if identity_blob is not None else None
    if (not isinstance(settings, dict) or not isinstance(identity, dict)
            or settings.get('goal_id') != metadata['instance_id']
            or identity.get('sha256') != metadata['settings_sha256']):
        raise files.ArchiveError('Read-only package lost its original instance identity')
    return dict(status='archive_inspected', instance_id=metadata['instance_id'], restorable=False,
        blob_ownership=metadata.get('blob_ownership', 'shared'),
        section=section, entries=selected, next_after=last if last is not None and last < total else None,
        total_entries=total, **counts, external_dependencies=metadata['dependencies'], limitations=LIMITATIONS)


@owner_effect
def archive_restore_material(root, package, *, step, index, independent=False):
    """Restore one missing ordinary sealed file, never control or call originals."""
    from . import contracts as c, instance_science as science, storage_policy as storage
    from .protocol import require_current
    root = files.check_path(Path(root).expanduser().absolute())
    package = files.check_path(Path(package).expanduser().absolute())
    if root.resolve() != root or package.resolve() != package:
        raise files.ArchiveError('Restore paths must be canonical originals')
    if root == package or root.is_relative_to(package) or package.is_relative_to(root):
        raise files.ArchiveError('Archive package must be separate from its original instance')
    settings = require_current(root)
    context = storage.Deadline(65)
    with ExitStack() as locks:
        _owner_locks(locks, root, context=context)
        _quiescent(root)
        _, seal, raw, raw_proof = science._sealed(root, step)
        with science.Original(raw, raw_proof):
            pass
        if type(index) is not int or not 1 <= index <= len(seal['materials']):
            raise files.ArchiveError('Exact sealed material index required')
        material = seal['materials'][index - 1]
        name = science._label(material, step)
        if sum(row['path'] == material['path'] for row in seal['materials']) != 1:
            raise files.ArchiveError('Sealed material name is ambiguous')
        source = Path(material['source'])
        if not source.is_absolute():
            raise files.ArchiveError('Sealed material source must be its absolute original path')
        files.check_path(source)
        work = c.workspace(root, step)
        nested = re.fullmatch(r'(?:experiments/[^/]+/)+(?:artifacts/.+|conclusion\.(?:json|md))', name)
        if (not source.is_absolute() or source.resolve() != source or not source.is_relative_to(root)
                or not (source == work / 'artifacts' / name or nested and source == work / name)):
            raise files.ArchiveError('Only ordinary sealed artifacts or nested experiment files can be restored; call and control originals are excluded')
        metadata_path = package / 'archive.json'
        metadata_before = files.hash_file(metadata_path)
        archive_inspect(package, limit=1, _context=context)
        metadata = _read(package / 'archive.json')
        if metadata['format'] != FORMAT:
            raise files.ArchiveError('Legacy archives support read-only inspection only')
        if files.hash_file(metadata_path) != metadata_before:
            raise files.ArchiveError('Archive metadata changed during inspection')
        ownership = 'independent' if independent or metadata['blob_ownership'] == 'independent' else 'shared'
        if metadata['instance_id'] != settings['goal_id'] or metadata['settings_sha256'] != c.digest(root / 'settings.json'):
            raise files.ArchiveError('Archive belongs to another original instance')
        manifest = package / 'manifest.jsonl'
        before = files.hash_file(manifest)
        if any(before[key] != metadata['manifest'][key] for key in ('sha256', 'size_bytes')):
            raise files.ArchiveError('Archive manifest changed after inspection')
        wanted = source.relative_to(root).as_posix()
        seal_path = c.host_record(work) / 'result-identity.json'
        sealed = seal_path.relative_to(root).as_posix()
        selected = producer = None
        with manifest.open('rb') as stream:
            for line in stream:
                row = json.loads(line)
                if row['path'] == wanted:
                    selected = row
                if row['path'] == sealed:
                    producer = row
        if files.hash_file(manifest) != before:
            raise files.ArchiveError('Archive manifest changed during material selection')
        if (producer is None or producer['kind'] != 'file'
                or producer['sha256'] != c.digest(seal_path) or producer['size_bytes'] != seal_path.stat().st_size):
            raise files.ArchiveError('Archive does not contain this exact current producer seal')
        if (selected is None or selected['kind'] != 'file'
                or any(selected[key] != material[key] for key in ('sha256', 'size_bytes'))):
            raise files.ArchiveError('Archive does not contain this exact sealed material')

        def existing():
            if not source.exists() and not source.is_symlink():
                return False
            actual = files.hash_file(source, context=context)
            if ownership == 'independent':
                for candidate in (package / 'blobs').iterdir():
                    context.remaining_seconds()
                    info = candidate.stat(follow_symlinks=False)
                    if actual['source_stat'][:2] == [info.st_dev, info.st_ino]:
                        raise files.ArchiveError('Existing material shares a package inode; independent restore never overwrites it')
            if any(actual[key] != material[key] for key in ('sha256', 'size_bytes')):
                raise files.ArchiveError('Existing original differs from the sealed material; never overwritten')
            return True

        result = dict(step=step, index=index, name=name, blob_ownership=ownership,
            permission='File bytes only; no scientific qualification, execution permission or automatic continuation')
        if existing():
            return dict(status='material_already_present', **result)
        blob = package / selected['blob']
        policy = storage.for_path(root, source)
        storage.Inventory(policy, context=context).file(selected['size_bytes'])
        growth = (selected['size_bytes'] if ownership == 'independent' else
            _reference_growth(root, blob, policy=policy, context=context))
        with storage.mutation(root, growth, policy=policy, context=context):
            try:
                publish = files.copy_independent_verified if ownership == 'independent' else files.link_verified
                publish(blob, source, dict(selected, mode=0o400), context=context)
            except files.ArchiveError:
                if existing():
                    return dict(status='material_already_present', **result)
                raise
        existing()
        return dict(status='material_restored', **result)


def checkpoint_enroll(root, anchor_directory):
    from . import instance_owner as owner
    root = files.check_path(Path(root).absolute())
    owner.repair_enrollment(root, anchor_directory)
    with ExitStack() as locks:
        _owner_locks(locks, root)
        _quiescent(root)
        value = owner.enroll(root, anchor_directory)
    return dict(status='checkpoint_enrolled', generation=value['generation'], root=str(root))


def _json_identity(path):
    """Decode and identify the same stable descriptor, not a second pathname read."""
    files.check_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise files.ArchiveError('Checkpoint metadata must be a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read()
        after = os.fstat(fd)
        if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise files.ArchiveError('Checkpoint metadata changed while reading')
        return json.loads(raw), dict(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), mode=stat.S_IMODE(before.st_mode))
    finally:
        os.close(fd)


def _checkpoint(package, *, with_proof=False, context=None):
    package = files.check_path(Path(package).absolute())
    value, proof = _json_identity(package / 'checkpoint.json')
    if (value.get('version') != 2 or value.get('kind') != 'cold_checkpoint'
            or value.get('blob_ownership') not in ('shared', 'independent')):
        raise files.ArchiveError('Only an explicitly enrolled cold checkpoint can restore an instance')
    metadata, actual = _json_identity(package / 'payload/archive.json')
    if (value['payload_ref']['path'] != 'payload/archive.json'
            or any(value['payload_ref'][key] != actual[key] for key in ('sha256','size_bytes'))):
        raise files.ArchiveError('Cold checkpoint payload identity changed')
    archive_inspect(package / 'payload', limit=1, _context=context)
    if metadata['format'] != FORMAT:
        raise files.ArchiveError('Legacy archives support read-only inspection only')
    if metadata['blob_ownership'] != value['blob_ownership']:
        raise files.ArchiveError('Cold checkpoint payload ownership changed')
    after = files.hash_file(package / 'payload/archive.json')
    if any(after[key] != actual[key] for key in ('sha256','size_bytes','mode')):
        raise files.ArchiveError('Checkpoint payload metadata changed during inspection')
    if (metadata['gaps'] or value['instance_id'] != metadata['instance_id']
            or value['settings_ref']['sha256'] != metadata['settings_sha256']):
        raise files.ArchiveError('Cold checkpoint lost its complete exact instance payload')
    return (value,proof,metadata) if with_proof else value


def _checkpoint_entries(package, metadata, *, context=None):
    """Bind the actual copy/activation iteration to the verified manifest bytes."""
    path = package / 'payload/manifest.jsonl'; files.check_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise files.ArchiveError('Checkpoint manifest is not regular')
        digest = hashlib.sha256(); size = 0
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for line in stream:
                if context is not None:
                    context.remaining_seconds()
                digest.update(line); size += len(line)
                entry = json.loads(line); _path(entry['path'])
                if entry['kind'] == 'file' and entry['blob'] != f'blobs/{entry["sha256"]}-{entry["size_bytes"]}':
                    raise files.ArchiveError('Checkpoint copy blob identity changed')
                yield entry
        after = os.fstat(fd)
        if (digest.hexdigest() != metadata['manifest']['sha256'] or size != metadata['manifest']['size_bytes']
                or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise files.ArchiveError('Checkpoint manifest changed during consumption')
    finally:
        os.close(fd)


def checkpoint_export(root, output, *, independent=False):
    from . import budget, instance_owner as owner, qualification as q, storage_policy as storage
    context = storage.Deadline(65)
    ownership = 'independent' if independent else 'shared'
    root, output = Path(root).absolute(), files.check_path(Path(output).absolute())
    if output.resolve() != output or not output.parent.is_dir() or output.is_relative_to(root) or root.is_relative_to(output):
        raise files.ArchiveError('Explicit separate checkpoint destination required')
    with owner.exclusive(root) as (directory, anchor, binding):
        if anchor['state'] == 'frozen':
            if anchor.get('output') != str(output) or anchor.get('export_blob_ownership') != ownership:
                raise files.ArchiveError('Resume the original frozen checkpoint transaction')
            if output.exists():
                value, checkpoint_proof, _ = _checkpoint(output, with_proof=True, context=context)
                if (value['transaction'] != anchor['transaction'] or value['blob_ownership'] != ownership
                        or anchor.get('checkpoint_sha256') != checkpoint_proof['sha256']):
                    raise files.ArchiveError('Published checkpoint belongs to another transaction')
                anchor['checkpoint_sha256'] = checkpoint_proof['sha256']
                files.durable_json(directory / 'owner.json', anchor)
                return dict(status='checkpoint_exported', package=str(output), generation=binding['generation'], frozen=True, blob_ownership=ownership)
        elif anchor['state'] != 'active' or anchor['generation'] != binding['generation']:
            raise files.ArchiveError('Original owner is unavailable for checkpoint')
        else:
            owner._active(root, binding)
            if output.exists():
                raise files.ArchiveError('Checkpoint destination already exists')
        transaction = anchor.get('transaction') if anchor['state'] == 'frozen' else os.urandom(16).hex()
        stage = Path(tempfile.mkdtemp(prefix='.' + output.name + '-checkpoint-', dir=output.parent))
        captured = {}
        try:
            def freeze(settings):
                if any(row['kind'] == 'gap' for row in _inventory(root, context=context)):
                    raise files.ArchiveError('Cold checkpoint has unsupported framework storage ownership')
                captured.update(budget.paused_checkpoint(root))
                anchor.pop('restore_blob_ownership', None)
                anchor.update(state='frozen', transaction=transaction, output=str(output), export_blob_ownership=ownership)
                files.durable_json(directory / 'owner.json', anchor)
            _export(root, stage / 'payload', independent=independent, on_locked=freeze, context=context)
            payload = files.hash_file(stage / 'payload/archive.json')
            value = dict(version=2, kind='cold_checkpoint', transaction=transaction, blob_ownership=ownership,
                instance_id=binding['instance_id'], settings_ref=q._ref(root, root / 'settings.json'),
                original_root=str(root), host=binding['host'], anchor_id=binding['anchor_id'],
                source_generation=binding['generation'], binding=binding,
                payload_ref=dict(path='payload/archive.json', **{key:payload[key] for key in ('sha256','size_bytes')}),
                budget=captured)
            files.durable_json(stage / 'checkpoint.json', value)
            _checkpoint(stage, context=context)
            anchor['checkpoint_sha256'] = files.hash_file(stage / 'checkpoint.json')['sha256']
            files.durable_json(directory / 'owner.json', anchor)
            _publish(stage, output)
            return dict(status='checkpoint_exported', package=str(output), generation=binding['generation'], frozen=True, blob_ownership=ownership)
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def _restore_dependencies(settings):
    """Current implementation/config dependencies; historical sessions are optional."""
    argv = settings['native_launcher_argv']
    if not argv or shutil.which(argv[0]) is None:
        raise files.ArchiveError('Current native launcher is unavailable; no automatic installation')


def _validate_restored_payload(root, package, checkpoint, restored, metadata, *, ownership, context=None):
    """A pending transfer cannot repair overwritten control originals by retry."""
    expected = set()
    independent = set(); immutable = {}; immutable_inodes = {}
    package_inodes = set()
    for path in (package / 'payload/blobs').iterdir():
        if context is not None:
            context.remaining_seconds()
        info = files.check_path(path).stat(follow_symlinks=False)
        package_inodes.add((info.st_dev, info.st_ino))
    for entry in _checkpoint_entries(package, metadata, context=context):
        name = entry['path']; expected.add(name)
        if entry['kind'] == 'file':
            actual = files.hash_file(root / name, context=context)
            blob = (package / 'payload' / entry['blob']).stat()
            identity = tuple(actual['source_stat'][:2])
            if entry['storage'] == 'immutable':
                if ownership == 'shared' and identity != (blob.st_dev, blob.st_ino):
                    raise files.ArchiveError('Pending restore immutable alias is not shared: ' + name)
                if (ownership == 'independent' and identity in package_inodes
                        or identity in independent
                        or entry['blob'] in immutable and immutable[entry['blob']] != identity
                        or identity in immutable_inodes and immutable_inodes[identity] != entry['blob']):
                    raise files.ArchiveError('Pending restore immutable alias ownership changed: ' + name)
                immutable[entry['blob']] = identity
                immutable_inodes[identity] = entry['blob']
            else:
                if identity in package_inodes or identity in independent or identity in immutable_inodes:
                    raise files.ArchiveError('Pending restore mutable original is not independent: ' + name)
                independent.add(identity)
            if name == 'instance-owner.json':
                if (_read(root / name) not in (checkpoint['binding'], restored)
                        or stat.S_IMODE((root/name).stat().st_mode) != entry['mode']):
                    raise files.ArchiveError('Pending restore owner binding changed')
                continue
            if name == 'pause.request' and _read(root / name) == dict(reason='cold_checkpoint_restored'):
                continue
            if any(actual[key] != entry[key] for key in ('sha256','size_bytes','mode')):
                raise files.ArchiveError('Pending restore original changed: ' + name)
        elif entry['kind'] == 'directory':
            if not (root/name).is_dir() or stat.S_IMODE((root/name).stat().st_mode) != entry['mode']:
                raise files.ArchiveError('Pending restore directory is unavailable or changed: ' + name)
    prefix = 'host-runtime/restore/' + checkpoint['transaction']
    extra = {'pause.request','host-runtime','host-runtime/restore',prefix,
        prefix+'/checkpoint.json',prefix+'/transfer.json'}
    for entry in _inventory(root, context=context):
        name = entry['path']
        if name == 'pause.request' and name not in expected and _read(root/name) != dict(reason='cold_checkpoint_restored'):
            raise files.ArchiveError('Pending restore gained an unrecognized pause original')
        if name in expected or name in extra or entry['kind'] == 'excluded':
            continue
        if entry['kind'] == 'file' and name.startswith('control-ledger/'):
            event = _read(root / name); marker = event.get('reanchor', {})
            if (event.get('kind') == 'clock' and event.get('phase') == 'paused'
                    and marker.get('checkpoint',{}).get('path') == prefix+'/checkpoint.json'
                    and marker.get('transfer',{}).get('path') == prefix+'/transfer.json'):
                continue  # The budget owner verifies the full same-transfer event.
        raise files.ArchiveError('Pending restore gained an unrecognized original: ' + name)


def checkpoint_restore(root, package, *, independent=False):
    from . import budget, contracts as c, instance_owner as owner, qualification as q, storage_policy as storage
    root, package = files.check_path(Path(root).absolute()), files.check_path(Path(package).absolute())
    context = storage.Deadline(65)
    checkpoint, checkpoint_proof, metadata = _checkpoint(package, with_proof=True, context=context); binding = checkpoint['binding']
    ownership = 'independent' if independent or checkpoint['blob_ownership'] == 'independent' else 'shared'
    if (str(root) != checkpoint['original_root'] or root.resolve() != root or owner._host() != checkpoint['host']
            or root == package or root.is_relative_to(package) or package.is_relative_to(root)):
        raise files.ArchiveError('Cold restore supports only the same host and exact original absolute root')
    digest = checkpoint_proof['sha256']
    if ownership == 'shared' and package.stat().st_dev != root.parent.stat().st_dev:
        raise files.SharingUnsupported('Checkpoint restore requires supported same-filesystem hardlinks')
    with owner.exclusive(root, binding=binding) as (directory, anchor, _):
        if (anchor.get('transaction') != checkpoint['transaction'] or anchor.get('checkpoint_sha256') != digest
                or anchor.get('export_blob_ownership') != checkpoint['blob_ownership']
                or anchor['generation'] not in {binding['generation'], binding['generation']+1}):
            raise files.ArchiveError('Cold checkpoint is stale or unregistered at its original anchor')
        if anchor['state'] in {'restore_pending', 'active'} and anchor.get('restore_blob_ownership') != ownership:
            raise files.ArchiveError('Resume the original frozen restore ownership mode')
        if anchor['state'] == 'active':
            current = dict(binding, generation=binding['generation']+1)
            owner._active(root, current)
            if owner._binding(root) != current:
                raise files.ArchiveError('Completed restore root lost its current generation')
            return dict(status='checkpoint_restore_already_completed', root=str(root),
                generation=current['generation'], paused=(root/'pause.request').exists(), blob_ownership=ownership)
        with owner.creation(root):
            if anchor['state'] == 'frozen':
                if root.exists() or root.is_symlink():
                    raise files.ArchiveError('Move the original root aside explicitly; cold restore never deletes or overwrites it')
                anchor.update(state='restore_pending', generation=binding['generation']+1, root_identity=None,
                    restore_blob_ownership=ownership)
                files.durable_json(directory / 'owner.json', anchor)
            elif anchor['state'] not in {'restore_pending','active'}:
                raise files.ArchiveError('Original anchor has no matching cold restore transaction')
            if not root.exists():
                if anchor['state'] != 'restore_pending' or not root.parent.is_dir():
                    raise files.ArchiveError('Original restore destination parent is unavailable')
                stage = Path(tempfile.mkdtemp(prefix='.' + root.name + '-restore-', dir=root.parent))
                try:
                    directories = []; immutable = {}
                    for entry in _checkpoint_entries(package, metadata, context=context):
                        target = stage / _path(entry['path'])
                        if entry['kind'] == 'file':
                            blob = package / 'payload' / entry['blob']
                            if entry['storage'] == 'immutable':
                                if ownership == 'shared':
                                    files.link_verified(blob, target, entry, context=context)
                                elif entry['blob'] in immutable:
                                    files.link_verified(immutable[entry['blob']], target, entry, context=context)
                                else:
                                    files.copy_independent_verified(blob, target, entry, context=context)
                                    immutable[entry['blob']] = target
                            else:
                                files.copy_independent_verified(blob, target, entry, context=context)
                        elif entry['kind'] == 'directory':
                            target.mkdir(parents=True, exist_ok=True); directories.append((target,entry['mode']))
                        elif entry['kind'] == 'gap':
                            raise files.ArchiveError('Unsupported checkpoint storage gap')
                    for target, mode in reversed(directories):
                        target.chmod(mode)
                    policy = storage.configuration(_read(stage / 'settings.json').get('storage_policy'))
                    with storage.mutation(stage, 0, policy=policy, context=context):
                        pass
                    # Rename preserves this inode. Commit it before publication so
                    # a crash after rename cannot strand an otherwise exact root.
                    anchor['root_identity'] = owner._identity(stage)
                    files.durable_json(directory / 'owner.json', anchor)
                    _publish(stage, root)
                finally:
                    if stage.exists():
                        from . import instance_delete
                        instance_delete._remove_tree(stage, owner._identity(stage))
            elif anchor.get('root_identity') != owner._identity(root):
                raise files.ArchiveError('Existing restore root has an unrecognized owner')
            restored = dict(binding, generation=anchor['generation'])
            _validate_restored_payload(root, package, checkpoint, restored, metadata, ownership=ownership, context=context)
            files.durable_json(root / owner.BINDING, restored)
            receipt = root / 'host-runtime/restore' / checkpoint['transaction']
            receipt.mkdir(parents=True, exist_ok=True)
            source = package / 'checkpoint.json'
            files.copy_verified(source, receipt / 'checkpoint.json', dict(kind='file', **checkpoint_proof), context=context)
            checkpoint_ref = q._ref(root, receipt / 'checkpoint.json')
            transfer = dict(version=1, kind='owner_restore', transaction=checkpoint['transaction'],
                checkpoint_ref=checkpoint_ref, instance_id=binding['instance_id'], original_root=str(root),
                anchor_id=binding['anchor_id'], host=binding['host'], from_generation=binding['generation'],
                to_generation=anchor['generation'], root_identity=anchor['root_identity'], restore_blob_ownership=ownership)
            path = receipt / 'transfer.json'
            if path.exists() and _read(path) != transfer:
                raise files.ArchiveError('Original restore transfer receipt changed')
            if not path.exists():files.durable_json(path, transfer)
            owner.verify_transition(root, checkpoint_ref, q._ref(root, path))
            budget.reanchor_paused_checkpoint(root, checkpoint_ref=checkpoint_ref, transfer_ref=q._ref(root, path))
            from . import configuration_updates
            configuration = configuration_updates.status(root)
            _restore_dependencies(configuration['active_settings'])
            if configuration['pending']:
                _restore_dependencies(configuration['settings'])
            from . import skill_supply
            skill_supply.rebuild(root)
            if anchor['state'] != 'active':
                files.durable_json(root / 'pause.request', dict(reason='cold_checkpoint_restored'))
                # Admission is based on the final physical inodes, including
                # independent control files, receipts and rebuilt projections.
                with storage.writer_lock(root, context=context), storage.mutation(root, 0, context=context):
                    pass
                anchor['state'] = 'active'
                files.durable_json(directory / 'owner.json', anchor)
            return dict(status='checkpoint_restored', root=str(root), generation=anchor['generation'], paused=True, blob_ownership=ownership,
                limitations=['Original native sessions and host credentials are not restored; their optional capabilities require the original dependencies.',
                    'No automatic scientific advance, replay, new execution permission or resource grant.'])
