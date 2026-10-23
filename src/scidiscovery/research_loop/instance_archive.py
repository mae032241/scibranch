"""Stream framework originals into an inert read-only package, never a live root."""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile

from scidiscovery import archive_io as files
from .instance_owner import effect as owner_effect

FORMAT = 'scidiscovery.instance-readonly-archive.v4'
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
            and parts[2].isdecimal() and parts[3] == 'accept.lock'):
        return 'managed_admission_lease'
    legacy_records = (len(parts) == 6 and parts[0:2] == ('project', 'nodes')
        and parts[2].isdecimal() and parts[3:5] == ('work', 'lineage'))
    host_records = (len(parts) == 5 and parts[:2] == ('host-runtime', 'experiment-control')
        and parts[2].isdecimal() and parts[3] == 'records')
    if legacy_records or host_records:
        name = parts[-1]
        if name.endswith('.lock'):
            return 'managed_records_lease'
    return None


def _private_originals(root):
    """Exclude credentials from the exact current host source declaration."""
    locator = root / 'execution-authority/host-key-source.json'
    if not locator.exists():
        return frozenset()
    value = _read(locator)
    source = value.get('source')
    if source is None:
        return frozenset()
    if not isinstance(source, dict) or not isinstance(source.get('path'), str):
        raise files.ArchiveError('Private credential locator is malformed')
    path = Path(source['path'])
    if not path.is_absolute() or path.resolve() != path:
        raise files.ArchiveError('Private credential locator is not canonical')
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
            yield dict(path=name, kind='file', **files.stat_file(path, context=context))
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
    if (not isinstance(binding, dict) or set(binding) != {'path'} or not Path(binding['path']).is_absolute()
            or not isinstance(identity, dict) or identity.get('task_ref') != binding
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
        if intent.get('task_ref') != c.record_ref(c.regular(job)):
            raise files.ArchiveError('Native intent lost its exact task')
        outcome_path = record / 'native-outcome.json'
        if not outcome_path.exists():
            raise files.ArchiveError('Native outcome is absent; writer state is unknown')
        outcome = _read(outcome_path)
        if (outcome.get('native_started') is not False
                and (type(outcome.get('returncode')) is not int or outcome.get('cleanup') != 'observed_group_cleaned')):
            raise files.ArchiveError('Native cleanup is unconfirmed')
        binding = c.record_ref(job) if len(relative.parts) == 1 else c.record_ref(path)
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
        from . import preparation_recovery
        if any(not row['resolved'] for row in preparation_recovery.retained_preparations(root, records)):
            raise files.ArchiveError('Retained original host preparation is unresolved')
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
                _dead(_read(call / 'process.json'), c.record_ref(call / 'accepted.json'))
            replay = call / 'engineering-check.intent.json'
            if replay.exists():
                result = _read(call / 'engineering-check.outcome.json')
                if result.get('intent_ref') != c.record_ref(replay) or result.get('result', {}).get('cleanup_confirmed') is not True:
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
        if value.get('intent_ref', c.record_ref(intent)) != c.record_ref(intent) or value.get('result', value).get('cleanup_confirmed') is not True:
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
        _dead(identity, identity.get('task_ref'))


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
        # Link/unlink changes ctime on
        # shared readonly inodes; every other property and field stays binding.
        expected = dict(expected, source_stat=expected['source_stat'][:3])
        current = dict(current, source_stat=current['source_stat'][:3])
    return expected == current


def _immutable_bindings(root):
    """Only lifecycle-owned paths identify frozen originals."""
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
        for item, tree in manifest['objects'].items():
            for row in tree['files']:
                bindings[root / skill_supply.DIRECTORY / 'objects' / item / _path(row['path'])] = row
        path = root / skill_supply.MANIFEST
        bindings[path] = dict(size_bytes=path.stat().st_size)
    return bindings

def _managed_inodes(root, bindings, *, context=None):
    """Resolve lifecycle-owned originals once; their readonly aliases share ownership."""
    inodes = set()
    for source, expected in bindings.items():
        actual = files.stat_file(source, context=context)
        if actual['mode'] != 0o400 or actual['size_bytes'] != expected['size_bytes']:
            raise files.ArchiveError('Managed original differs from its owner binding')
        inodes.add(tuple(actual['source_stat'][:2]))
    objects = files.check_path(root / 'host-history/objects')
    if objects.exists():
        with os.scandir(objects) as entries:
            for entry in entries:
                actual = files.stat_file(Path(entry.path), context=context)
                if actual['mode'] != 0o400:
                    raise files.ArchiveError('Managed object is not frozen')
                inodes.add(tuple(actual['source_stat'][:2]))
    return inodes


def _managed_original(root, source, entry, inodes):
    actual = files.stat_file(source)
    return (actual['mode'] == 0o400 and actual['size_bytes'] == entry['size_bytes']
        and tuple(actual['source_stat'][:2]) in inodes)


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
    if root.resolve() != root or output.resolve() != output or root == output or output.is_relative_to(root) or root.is_relative_to(output):
        raise files.ArchiveError('Archive destination must be a separate canonical path')
    if output.exists() or not output.parent.is_dir():
        raise files.ArchiveError('Archive requires an absent destination and existing explicit parent')
    if not independent and root.stat().st_dev != output.parent.stat().st_dev:
        raise files.SharingUnsupported('Archive export requires a supported same-filesystem destination')
    settings = c.settings(root)
    context = context or storage.Deadline(65)
    policy = storage.for_root(root)
    with ExitStack() as locks:
        _owner_locks(locks, root, context=context)
        _quiescent(root)
        managed_inodes = _managed_inodes(root, _immutable_bindings(root), context=context)
        if on_locked is not None:
            on_locked(settings)
        stage = Path(tempfile.mkdtemp(prefix='.' + output.name + '-archive-', dir=output.parent))
        try:
            (stage / 'blobs').mkdir()
            counts = dict(files=0, directories=0, gaps=0, excluded=0, bytes=0, unique_blobs=0, blob_bytes=0)
            blobs, generations = {}, {}
            def originals():
                for current in _inventory(root, context=context, inventory=storage.Inventory(policy, context=context)):
                    entry = dict(current)
                    generations[entry['path']] = current
                    entry.pop('source_stat', None)
                    if entry['kind'] == 'file':
                        source = root / entry['path']
                        managed = _managed_original(root, source, current, managed_inodes)
                        entry['storage'] = 'immutable' if managed else 'independent'
                        identity = tuple(current['source_stat'][:2])
                        blob = blobs.get(identity)
                        if blob is None:
                            blob = f'blobs/{len(blobs) + 1:012d}'
                            frozen_entry = dict(entry, mode=0o400)
                            if managed and not independent:
                                files.link_original(source, stage / blob, frozen_entry, context=context)
                            else:
                                files.copy_original(source, stage / blob, frozen_entry, context=context)
                            blobs[identity] = blob
                            counts['unique_blobs'] += 1; counts['blob_bytes'] += entry['size_bytes']
                        entry['blob'] = blob
                        counts['files'] += 1; counts['bytes'] += entry['size_bytes']
                    else:
                        counts[{'directory': 'directories', 'gap': 'gaps', 'excluded': 'excluded'}[entry['kind']]] += 1
                    yield entry
            entries = _stream(stage / 'manifest.jsonl', originals(), context=context)
            dependencies = _stream(stage / 'dependencies.jsonl', _configuration_dependencies(root, settings), context=context)
            metadata = dict(format=FORMAT, blob_ownership='independent' if independent else 'shared',
                instance_id=settings['goal_id'], settings_ref=c.record_ref(root / 'settings.json'),
                restorable=False, limitations=LIMITATIONS, entries=entries, dependencies=dependencies, **counts,
                manifest=dict(path='manifest.jsonl', size_bytes=(stage / 'manifest.jsonl').stat().st_size),
                dependency_manifest=dict(path='dependencies.jsonl', size_bytes=(stage / 'dependencies.jsonl').stat().st_size))
            files.durable_json(stage / 'archive.json', metadata)
            checked = set()
            for current in _inventory(root, context=context):
                expected = generations.get(current['path'])
                if expected is None or not _same_inventory_entry(expected, current):
                    raise files.ArchiveError('Original archive scope changed before publication')
                checked.add(current['path'])
            if checked != set(generations):
                raise files.ArchiveError('Original archive scope lost entries before publication')
            files.fsync_directory(stage / 'blobs'); files.fsync_directory(stage)
            _publish(stage, output)
            return dict(status='archive_exported', package=str(output), instance_id=settings['goal_id'],
                blob_ownership=metadata['blob_ownership'], restorable=False, **counts,
                external_dependencies=dependencies, limitations=LIMITATIONS)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

def archive_inspect(package, *, after=0, limit=20, section='manifest', _context=None):
    """Inspect package structure and original references without reading payloads."""
    package = files.check_path(Path(package).expanduser().absolute())
    from . import storage_policy as storage
    context = _context or storage.Deadline(65)
    if package.resolve() != package or type(after) is not int or after < 0 or type(limit) is not int or limit <= 0 or section not in {'manifest', 'dependencies'}:
        raise files.ArchiveError('Exact canonical archive and positive page required')
    if {path.name for path in package.iterdir()} != {'archive.json', 'manifest.jsonl', 'dependencies.jsonl', 'blobs'}:
        raise files.ArchiveError('Read-only package layout changed')
    metadata = _read(package / 'archive.json')
    counters = {'entries', 'dependencies', 'files', 'directories', 'gaps', 'excluded', 'bytes', 'unique_blobs', 'blob_bytes'}
    if (not isinstance(metadata, dict) or metadata.get('format') != FORMAT
            or metadata.get('blob_ownership') not in {'shared', 'independent'} or metadata.get('restorable') is not False
            or any(type(metadata.get(k)) is not int or metadata[k] < 0 for k in counters)
            or not isinstance(metadata.get('settings_ref'), dict) or not metadata.get('instance_id')):
        raise files.ArchiveError('Unsupported read-only archive format')
    counts = dict(files=0, directories=0, gaps=0, excluded=0, bytes=0)
    blobs = {}; selected = []; last = None; total = 0; settings_blob = None
    for name, proof in (('manifest', metadata['manifest']), ('dependencies', metadata['dependency_manifest'])):
        if not isinstance(proof, dict) or proof.get('path') != name + '.jsonl' or type(proof.get('size_bytes')) is not int:
            raise files.ArchiveError('Malformed archive manifest reference')
        source = package / proof['path']; before = files.stat_file(source, context=context)
        if before['size_bytes'] != proof['size_bytes']:
            raise files.ArchiveError('Archive manifest size changed')
        number = 0; previous = None
        with source.open('rb') as stream:
            for number, line in enumerate(stream, 1):
                storage.check(context); entry = json.loads(line)
                if not isinstance(entry, dict) or entry.get('index') != number:
                    raise files.ArchiveError('Archive entry sequence changed')
                if name == 'manifest':
                    kind = entry.get('kind'); logical = _path(entry['path'])
                    if previous is not None and logical.parts <= previous:
                        raise files.ArchiveError('Duplicate or unordered archive logical path')
                    previous = logical.parts
                    if kind == 'file':
                        if (set(entry) != {'index','path','kind','blob','size_bytes','mode','storage'}
                                or type(entry['size_bytes']) is not int or entry['size_bytes'] < 0
                                or type(entry['mode']) is not int or not 0 <= entry['mode'] <= 0o7777
                                or entry['storage'] not in {'immutable','independent'}
                                or entry['storage'] == 'immutable' and entry['mode'] != 0o400
                                or re.fullmatch(r'blobs/[0-9]{12}', entry['blob']) is None):
                            raise files.ArchiveError('Malformed archive file binding')
                        actual = blobs.get(entry['blob'])
                        if actual is None:
                            actual = files.stat_file(package / entry['blob'], context=context); blobs[entry['blob']] = actual
                        if actual['size_bytes'] != entry['size_bytes'] or actual['mode'] != 0o400:
                            raise files.ArchiveError('Archive blob metadata changed')
                        if entry['path'] == 'settings.json': settings_blob = package / entry['blob']
                        counts['files'] += 1; counts['bytes'] += entry['size_bytes']
                    elif kind in {'directory','gap','excluded'}:
                        expected = {'index','path','kind'} | ({'mode'} if kind == 'directory' else {'mode','reason'} if kind == 'gap' else {'reason'})
                        if set(entry) != expected: raise files.ArchiveError('Malformed archive entry')
                        counts[{'directory':'directories','gap':'gaps','excluded':'excluded'}[kind]] += 1
                    else: raise files.ArchiveError('Unknown archive entry kind')
                elif set(entry) != {'index','owner','reference','coverage'}:
                    raise files.ArchiveError('Malformed archive dependency')
                if name == section and number > after and len(selected) < limit:
                    selected.append(entry); last = number
        if files.stat_file(source, context=context) != before:
            raise files.ArchiveError('Archive manifest changed during inspection')
        if number != metadata['entries' if name == 'manifest' else 'dependencies']:
            raise files.ArchiveError('Archive manifest count changed')
        if name == section: total = number
    counts.update(unique_blobs=len(blobs), blob_bytes=sum(row['size_bytes'] for row in blobs.values()))
    if any(counts[k] != metadata[k] for k in counts): raise files.ArchiveError('Archive summary changed')
    if {path.name for path in (package / 'blobs').iterdir()} != {Path(name).name for name in blobs}:
        raise files.ArchiveError('Archive has unlisted blob entries')
    if settings_blob is None or _read(settings_blob).get('goal_id') != metadata['instance_id']:
        raise files.ArchiveError('Archive lost its instance identity')
    return dict(status='archive_inspected', instance_id=metadata['instance_id'], restorable=False,
        blob_ownership=metadata['blob_ownership'], section=section, entries=selected,
        next_after=last if last is not None and last < total else None, total_entries=total,
        **counts, external_dependencies=metadata['dependencies'], limitations=LIMITATIONS)

def _restore_material_original(root, source, blob, entry, request, *, context, policy=None):
    """Resume only this exact no-replacement file publication, never same-size adoption."""
    from . import instance_owner as owner, storage_policy as storage
    directory = files.check_path(root / 'host-runtime/restore')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = directory / f'material-{request["step"]:03d}-{request["index"]:06d}.json'
    value = _read(receipt) if receipt.exists() else None
    if value is not None and (value.get('version') != 1 or value.get('request') != request
            or value.get('phase') not in {'allocated', 'prepared', 'published'}):
        raise files.ArchiveError('Material restore belongs to another exact operation')
    if source.exists() or source.is_symlink():
        actual = files.stat_file(source, context=context)
        if value is None and request['ownership'] == 'shared' and actual['source_stat'][:2] == files.stat_file(blob)['source_stat'][:2]:
            return False
        if (value is None or value['phase'] == 'allocated'
                or actual['source_stat'][:2] != value.get('target_identity')
                or actual['size_bytes'] != entry['size_bytes'] or actual['mode'] != 0o400):
            raise files.ArchiveError('Existing material is not this restore publication; never overwritten')
        value['phase'] = 'published'; files.durable_json(receipt, value)
        stage = Path(value['stage'])
        if stage.exists():
            if owner._identity(stage) != value['stage_identity']:
                raise files.ArchiveError('Material restore stage was replaced')
            original = stage / 'original'
            if original.exists():
                if files.stat_file(original)['source_stat'][:2] != value['target_identity']:
                    raise files.ArchiveError('Material restore stage original changed')
                original.unlink(); files.fsync_directory(stage)
            stage.rmdir(); files.fsync_directory(directory)
        return False
    if value is None:
        stage = Path(tempfile.mkdtemp(prefix='.material-', dir=directory))
        value = dict(version=1, request=request, phase='allocated', stage=str(stage),
            stage_identity=owner._identity(stage), target_identity=None)
        files.durable_json(receipt, value)
    elif value['phase'] == 'published':
        raise files.ArchiveError('Published material is now missing; its original publication cannot be replayed')
    stage = files.check_path(Path(value['stage']))
    if stage.parent != directory or owner._identity(stage) != value['stage_identity']:
        raise files.ArchiveError('Material restore lost its exact private stage')
    original = stage / 'original'
    if any(path.name != 'original' for path in stage.iterdir()):
        raise files.ArchiveError('Material restore stage gained unrecognized entries')
    if value['phase'] == 'allocated':
        # No target was published in this phase. Only our bound private stage
        # may contain an incomplete first copy; it is not an accepted original.
        if original.exists() or original.is_symlink():
            files.stat_file(original, context=context); original.unlink()
        growth = entry['size_bytes'] if request['ownership'] == 'independent' else _reference_growth(root, blob, policy=storage.configuration(policy), context=context)
        with storage.mutation(root, growth, policy=policy, context=context):
            if request['ownership'] == 'independent':
                files.copy_independent(blob, original, dict(entry, mode=0o400), context=context)
            else:
                files.link_original(blob, original, dict(entry, mode=0o400), context=context)
        value.update(phase='prepared', target_identity=files.stat_file(original)['source_stat'][:2])
        files.durable_json(receipt, value)
    if files.stat_file(original)['source_stat'][:2] != value['target_identity']:
        raise files.ArchiveError('Material restore prepared original was replaced')
    files.link_original(original, source, dict(entry, mode=0o400), context=context)
    value['phase'] = 'published'; files.durable_json(receipt, value)
    original.unlink(); stage.rmdir(); files.fsync_directory(directory)
    return True


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
        declared = Path(material.get('original_source', material['source']))
        nested = re.fullmatch(r'(?:experiments/[^/]+/)+(?:artifacts/.+|conclusion\.(?:json|md))', name)
        ordinary = declared == work / 'artifacts' / name or nested and declared == work / name
        owned = source == declared or source.parent == root / 'host-history/objects'
        if (not source.is_absolute() or source.resolve() != source or not source.is_relative_to(root)
                or not ordinary or not owned):
            raise files.ArchiveError('Only ordinary sealed artifacts or nested experiment files can be restored; call and control originals are excluded')
        metadata_path = package / 'archive.json'
        metadata_before = files.stat_file(metadata_path)
        archive_inspect(package, limit=1, _context=context)
        metadata = _read(package / 'archive.json')
        if metadata['format'] != FORMAT:
            raise files.ArchiveError('Legacy archives support read-only inspection only')
        if files.stat_file(metadata_path) != metadata_before:
            raise files.ArchiveError('Archive metadata changed during inspection')
        ownership = 'independent' if independent or metadata['blob_ownership'] == 'independent' else 'shared'
        if metadata['instance_id'] != settings['goal_id'] or metadata['settings_ref'] != c.record_ref(root / 'settings.json'):
            raise files.ArchiveError('Archive belongs to another original instance')
        manifest = package / 'manifest.jsonl'
        before = files.stat_file(manifest)
        if any(before[key] != metadata['manifest'][key] for key in ('size_bytes',)):
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
        if files.stat_file(manifest) != before:
            raise files.ArchiveError('Archive manifest changed during material selection')
        if (producer is None or producer['kind'] != 'file'
                or producer['size_bytes'] != seal_path.stat().st_size or _read(package / producer['blob']) != _read(seal_path)):
            raise files.ArchiveError('Archive does not contain this exact current producer seal')
        if (selected is None or selected['kind'] != 'file'
                or any(selected[key] != material[key] for key in ('size_bytes',))):
            raise files.ArchiveError('Archive does not contain this exact sealed material')

        result = dict(step=step, index=index, name=name, blob_ownership=ownership,
            permission='File bytes only; no scientific qualification, execution permission or automatic continuation')
        blob = package / selected['blob']
        request = dict(root=str(root), step=step, index=index, package=str(package), blob=selected['blob'],
            source=str(source), producer=c.record_ref(seal_path), ownership=ownership)
        policy = storage.for_path(root, source)
        storage.Inventory(policy, context=context).file(selected['size_bytes'])
        with storage.writer_lock(root, context=context):
            created = _restore_material_original(root, source, blob, selected, request, context=context, policy=policy)
        return dict(status='material_restored' if created else 'material_already_present', **result)


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
        return json.loads(raw), dict(path=str(Path(path).absolute()), size_bytes=len(raw), mode=stat.S_IMODE(before.st_mode),
            source_stat=[before.st_dev,before.st_ino,before.st_mtime_ns,before.st_ctime_ns])
    finally:
        os.close(fd)


def _checkpoint(package, *, with_proof=False, context=None):
    package = files.check_path(Path(package).absolute())
    value, proof = _json_identity(package / 'checkpoint.json')
    if (value.get('version') != 3 or value.get('kind') != 'cold_checkpoint'
            or value.get('blob_ownership') not in ('shared', 'independent')):
        raise files.ArchiveError('Only an explicitly enrolled cold checkpoint can restore an instance')
    metadata, actual = _json_identity(package / 'payload/archive.json')
    if (value['payload_ref']['path'] != 'payload/archive.json'
            or any(value['payload_ref'][key] != actual[key] for key in ('size_bytes',))):
        raise files.ArchiveError('Cold checkpoint payload identity changed')
    archive_inspect(package / 'payload', limit=1, _context=context)
    if metadata['format'] != FORMAT:
        raise files.ArchiveError('Legacy archives support read-only inspection only')
    if metadata['blob_ownership'] != value['blob_ownership']:
        raise files.ArchiveError('Cold checkpoint payload ownership changed')
    after = files.stat_file(package / 'payload/archive.json')
    if any(after[key] != actual[key] for key in ('size_bytes','mode','source_stat')):
        raise files.ArchiveError('Checkpoint payload metadata changed during inspection')
    if (metadata['gaps'] or value['instance_id'] != metadata['instance_id']
            or value['settings_ref'] != metadata['settings_ref']):
        raise files.ArchiveError('Cold checkpoint lost its complete exact instance payload')
    return (value,proof,metadata) if with_proof else value


def _checkpoint_entries(package, metadata, *, context=None):
    """Consume one stable manifest descriptor; no scientific payload is hashed."""
    path = package / 'payload/manifest.jsonl'; files.check_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != metadata['manifest']['size_bytes']:
            raise files.ArchiveError('Checkpoint manifest metadata changed')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for line in stream:
                if context is not None: context.remaining_seconds()
                entry = json.loads(line); _path(entry['path'])
                if entry['kind'] == 'file' and re.fullmatch(r'blobs/[0-9]{12}', entry['blob']) is None:
                    raise files.ArchiveError('Checkpoint blob locator changed')
                yield entry
        after = os.fstat(fd)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
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
                        or anchor.get('checkpoint_ref') != dict(path=str(output / 'checkpoint.json'))):
                    raise files.ArchiveError('Published checkpoint belongs to another transaction')
                anchor['checkpoint_ref'] = dict(path=str(output / 'checkpoint.json'))
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
            payload = files.stat_file(stage / 'payload/archive.json')
            value = dict(version=3, kind='cold_checkpoint', transaction=transaction, blob_ownership=ownership,
                instance_id=binding['instance_id'], settings_ref=dict(path=str(root / 'settings.json')),
                original_root=str(root), host=binding['host'], anchor_id=binding['anchor_id'],
                source_generation=binding['generation'], binding=binding,
                payload_ref=dict(path='payload/archive.json', **{key:payload[key] for key in ('size_bytes',)}),
                budget=captured)
            files.durable_json(stage / 'checkpoint.json', value)
            _checkpoint(stage, context=context)
            anchor['checkpoint_ref'] = dict(path=str(output / 'checkpoint.json'))
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
            actual = files.stat_file(root / name, context=context)
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
            if any(actual[key] != entry[key] for key in ('size_bytes','mode')):
                raise files.ArchiveError('Pending restore original changed: ' + name)
        elif entry['kind'] == 'directory':
            if not (root/name).is_dir() or stat.S_IMODE((root/name).stat().st_mode) != entry['mode']:
                raise files.ArchiveError('Pending restore directory is unavailable or changed: ' + name)
    prefix = 'host-runtime/restore/' + checkpoint['transaction']
    extra = {'pause.request','host-runtime','host-runtime/restore',prefix,
        prefix+'/checkpoint.json',prefix+'/transfer.json',prefix+'/archive.json',prefix+'/manifest.jsonl'}
    for name, proof in (('archive.json', checkpoint['payload_ref']), ('manifest.jsonl', metadata['manifest'])):
        retained = root / prefix / name
        if retained.exists():
            actual = files.stat_file(retained, context=context)
            if any(actual[key] != proof[key] for key in ('size_bytes',)):
                raise files.ArchiveError('Pending restore retained manifest changed')
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
    checkpoint_ref = dict(path=str(package / 'checkpoint.json'))
    if ownership == 'shared' and package.stat().st_dev != root.parent.stat().st_dev:
        raise files.SharingUnsupported('Checkpoint restore requires supported same-filesystem hardlinks')
    with owner.exclusive(root, binding=binding) as (directory, anchor, _):
        if (anchor.get('transaction') != checkpoint['transaction'] or anchor.get('checkpoint_ref') != checkpoint_ref
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
                                    files.link_original(blob, target, entry, context=context)
                                elif entry['blob'] in immutable:
                                    files.link_original(immutable[entry['blob']], target, entry, context=context)
                                else:
                                    files.copy_independent(blob, target, entry, context=context)
                                    immutable[entry['blob']] = target
                            else:
                                files.copy_independent(blob, target, entry, context=context)
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
            if not (receipt / 'checkpoint.json').exists():
                files.copy_original(source, receipt / 'checkpoint.json', dict(kind='file', **checkpoint_proof), context=context)
            elif _read(receipt / 'checkpoint.json') != checkpoint:
                raise files.ArchiveError('Retained checkpoint control record changed')
            checkpoint_ref = q._ref(root, receipt / 'checkpoint.json')
            # Retain the two original control manifests so historical snapshot
            # bindings can verify their cold inheritance without the package.
            # Payload bytes remain solely in their original restored locations.
            for name in ('archive.json', 'manifest.jsonl'):
                source = package / 'payload' / name
                original = files.stat_file(source, context=context)
                expected = checkpoint['payload_ref'] if name == 'archive.json' else metadata['manifest']
                if any(original[key] != expected[key] for key in ('size_bytes',)):
                    raise files.ArchiveError('Cold restore control manifest source changed')
                if not (receipt / name).exists():
                    files.copy_original(source, receipt / name, dict(kind='file', **original), context=context)
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
