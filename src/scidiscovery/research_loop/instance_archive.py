"""Shared lifecycle owner and original-file helpers for recovery, fork and deletion."""
from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from scidiscovery import archive_io as files

ROOT_FILES = {'settings.json', 'settings-identity.json', 'host-started.json', 'pause.request', 'instance-owner.json'}

ROOT_DIRS = {'jobs', 'project', 'control-ledger', 'correction-ledger', 'native-evidence',
             'host-runtime', 'host-history', 'host-delivery', 'source-provenance',
             'qualification', 'execution-authority', 'host-control', 'host-recovery'}

AUTHORITY_EXCLUDED = {'host-key-source.json', 'ui-endpoint.json', 'ui.lock', 'writer.lock'}

CALL_LOCKS = ('node.lock', 'accept.lock')


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
    # All lifecycle acquisitions are nonblocking; no high-level owner reentry.
    from . import controller, instance_owner
    source_gate = instance_owner._gates.get().get(str(Path(root).absolute()))
    if disposing:
        from . import instance_owner
        if str(Path(root).absolute()) not in instance_owner._disposing.get():
            raise files.ArchiveError('Delete locks require the exact disposal owner')
        stack.enter_context(controller._local_writer(root))
    elif source_gate is not None and source_gate['exclusive'] and not source_gate['cold']:
        # The source owner holds its gate exclusively; reentering it as
        # an effect would be forbidden. Existing cold owners retain their guards.
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
        snapshot = next(records.glob('.prepared-*'), None)
        if snapshot is not None:
            step = int(records.parent.name if records.name == 'records' else records.parent.parent.name)
            token = snapshot.name.removeprefix('.prepared-')
            raise files.ArchiveError(
                'Original preparation has not completed acceptance: '
                f'{snapshot.relative_to(root).as_posix()}; --step {step} --snapshot-token {token}. '
                'See scid-research execution-preparation-maintain --help. '
                'Only for a proven built-in local unbound partial, use --unbound-local --action inspect; '
                'bound or unknown preparations require the exact original call.')
        from . import preparation_recovery
        if any(not row['resolved'] for row in preparation_recovery.retained_preparations(root, records)):
            raise files.ArchiveError('Retained original host preparation is unresolved')
        for call in sorted((records / 'calls').glob('*')):
            accepted = _read(call / 'accepted.json'); interface = accepted['interface']
            if (call / 'submission-retirement.json').exists():
                verified = e.verify_retirement(_read(call / 'submission-retirement.json'),
                    lambda item: Path(item['path']), interface=interface, verify_files=False)
            elif (call / 'execution-observation.json').exists():
                verified = _read(call / 'execution-observation.json')
                e.verify_observation(verified, lambda item: Path(item['path']), interface=interface, verify_files=False)
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
