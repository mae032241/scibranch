"""Read-only fork preview and durable, paused child creation.

Source originals stay in a private immutable namespace. Creation never runs a
scientific participant or imports a prior execution grant into the new ledger.
"""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from scidiscovery import archive_io as files
from . import contracts as c, controller, fork_runtime, fork_sources, originals, qualification, research_config


class ForkError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _digest(value):
    return hashlib.sha256(files.canonical(value)).hexdigest()


def _source_call(function, *args, **options):
    try:
        return function(*args, **options)
    except ForkError:
        raise
    except (OSError, ValueError, files.ArchiveError, KeyError, TypeError, IndexError) as error:
        message = str(error)
        if isinstance(error, files.SharingUnsupported):
            code = 'fork_storage_unsupported'
        elif message.startswith('source_guard_unavailable:'):
            code = 'fork_source_guard_unavailable'
        elif isinstance(error, BlockingIOError):
            code = 'fork_source_busy'
        elif isinstance(error, FileNotFoundError):
            code = 'fork_input_missing'
        elif 'protocol' in message.lower() or 'unsupported' in message.lower():
            code = 'fork_source_unsupported'
        elif any(word in message.lower() for word in ('unknown', 'unresolved')):
            code = 'fork_external_effect_unknown'
        elif 'changed during' in message.lower() or 'stale' in message.lower():
            code = 'fork_source_changed'
        elif any(word in message.lower() for word in ('symbolic', 'canonical', 'escaped', 'path')):
            code = 'fork_path_unsafe'
        elif any(word in message.lower() for word in ('missing', 'lacks', 'absent')):
            code = 'fork_input_missing'
        elif any(word in message.lower() for word in ('ambiguous', 'selection requires', 'step or job')):
            code = 'fork_selector_ambiguous'
        else:
            code = 'fork_origin_invalid'
        raise ForkError(code, message) from error


@contextmanager
def _source_guard(parent):
    # Enter/exit use the same stable diagnostics as source readers; exceptions
    # from the transaction itself retain their own type and outcome semantics.
    guard = fork_sources.source_guard(parent)
    _source_call(guard.__enter__)
    try:
        yield
    finally:
        guard.__exit__(None, None, None)


def _target(parent, child):
    parent = files.check_path(Path(parent).expanduser().absolute())
    child = files.check_path(Path(child).expanduser().absolute())
    if parent.resolve() != parent or child.resolve() != child or child == parent or parent in child.parents or child in parent.parents:
        raise ForkError('fork_path_unsafe', 'Source and target must be separate canonical instance roots')
    if any(path.name == '.scid-fork-operations' for path in (child, *child.parents)):
        raise ForkError('fork_path_unsafe', 'Fork targets cannot name private operation storage')
    if not child.parent.is_dir():
        raise ForkError('fork_path_unsafe', 'The explicit target parent directory must already exist')
    return parent, child


def _budget(value):
    if not isinstance(value, dict) or set(value) != {'max_tokens', 'max_seconds', 'max_steps'}:
        raise ForkError('fork_budget_incomplete', 'Declare finite new max_tokens, max_seconds and max_steps')
    if any(type(value[name]) is not int or value[name] <= 0 for name in ('max_tokens', 'max_steps')):
        raise ForkError('fork_budget_incomplete', 'New token and action limits must be positive integers')
    seconds = value['max_seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ForkError('fork_budget_incomplete', 'New active seconds must be positive and finite')
    return dict(value, max_seconds=float(seconds))


def _request(parent, step, child, budget, guidance_text):
    if type(step) is not int or step <= 0:
        raise ForkError('fork_selector_ambiguous', 'Select an exact positive logical step')
    if guidance_text is not None and (not isinstance(guidance_text, str) or not guidance_text.strip()
            or len(guidance_text.encode('utf-8')) > 256000):
        raise ForkError('fork_guidance_invalid', 'Guidance must contain at most 256000 bytes of original text')
    return dict(parent_root=str(parent), step=step, child_root=str(child), budget=_budget(budget), guidance_text=guidance_text)


def _inspect(request):
    plan = _source_call(fork_sources.inspect_source, Path(request['parent_root']), request['step'])
    # Physical locations are deliberately excluded: retained manifests bind
    # immutable identities and digests, not the host's temporary locators.
    binding = {key: value for key, value in plan.items() if key not in fork_sources.SOURCE_LOCATORS}
    preview_id = _digest(dict(request=request, source=binding))
    blockers = list(plan.get('blocked_reasons', []))
    blockers.extend(_source_call(fork_sources.storage_blockers, plan, Path(request['child_root']).parent))
    result = dict(status='fork_preview_blocked' if blockers else 'fork_preview_ready', preview_id=preview_id,
        source=plan['source'], cut=dict(
            history_steps=[row['step'] for row in plan.get('cut', {}).get('history', {}).get('nodes', [])],
            execution_recovery_count=len(plan.get('cut', {}).get('execution_recoveries', []))), budget=request['budget'],
        guidance=dict(text=request['guidance_text'], kind='advice',
            visibility='legal_successors' if plan['source'].get('intent_kind') == 'formal_review' else 'selected_and_legal_successors'),
        blockers=blockers, limitations=plan.get('limitations', []),
        entry_count=len(plan.get('entries', [])),
        size_bytes=sum(row.get('size_bytes', 0) for row in plan.get('entries', [])),
        inherited_costs=plan.get('inherited_costs', dict(status='unknown', reason='Exact cut costs are not reconstructed')))
    settings = plan.get('selected_settings', {})
    result['native'] = {key: settings[key] for key in ('native_backend', 'model', 'reasoning_effort', 'service_tier', 'role_profiles') if key in settings}
    result['native']['permissions'] = {key: settings.get('native_options', {})[key]
        for key in ('workspace', 'network', 'web', 'subagents') if key in settings.get('native_options', {})}
    return plan, result


def preview(parent_root, step, child_root, *, budget, guidance_text=None):
    """Inspect exact inputs and finite new limits without writing either root."""
    parent, child = _target(parent_root, child_root)
    request = _request(parent, step, child, budget, guidance_text)
    return _inspect(request)[1]


@contextmanager
def _target_lock(child):
    path = child.parent / ('.scid-fork-' + hashlib.sha256(os.fsencode(child.name)).hexdigest()[:24] + '.lock')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ForkError('fork_path_unsafe', 'Fork target lock must be a regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ForkError('fork_busy', 'Another creator owns this exact target; reconcile its receipt') from error
        yield
    finally:
        os.close(descriptor)


def _existing(child, operation_id, request_hash):
    if not child.exists() and not child.is_symlink():
        return None
    receipt_path = child / 'host-runtime/fork/receipt.json'
    if child.is_symlink() or not receipt_path.is_file():
        raise ForkError('fork_target_exists', 'Target exists without this operation receipt')
    receipt = c.read(c.regular(receipt_path))
    if receipt.get('operation_id') != operation_id:
        raise ForkError('fork_target_exists', 'Target belongs to another operation')
    if receipt.get('request_sha256') != request_hash:
        raise ForkError('fork_operation_conflict', 'Operation ID was already used for a different request')
    _source_call(fork_sources.validate_manifest, child, full=True)
    if _source_call(c.settings, child).get('goal_id') != receipt.get('child_identity'):
        raise ForkError('fork_transaction_changed', 'Child settings differ from the original receipt identity')
    return receipt


def _directory_identity(path):
    files.check_path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ForkError('fork_transaction_changed', 'Fork transaction directory was replaced')
    return dict(dev=info.st_dev, ino=info.st_ino)


@contextmanager
def _operation(child, operation_id, request_hash):
    # Keep this exact operation locked until its publication/cleanup is durable.
    from . import instance_owner as owner
    directory = files.check_path(child.parent / '.scid-fork-operations')
    directory.mkdir(mode=0o700, exist_ok=True)
    owner._private_stat(directory.lstat(), directory=True)
    files.fsync_directory(directory.parent)
    key = _digest(operation_id)
    path = directory / (key + '.json')
    transaction = directory / (key + '.stage')
    expected = dict(version=1, operation_id=operation_id, request_sha256=request_hash,
        child_root=str(child), parent_identity=_directory_identity(child.parent), transaction_path=str(transaction))
    lock = path.with_suffix('.lock')
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        owner._same_gate_path(lock, descriptor, directory=False)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ForkError('fork_busy', 'Another creator owns this operation; reconcile its receipt') from error
        if path.exists() or path.is_symlink():
            record = c.read(c.regular(files.check_path(path)))
            if (not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items())
                    or set(record) != set(expected) | {'phase', 'transaction_identity', 'stage_identity', 'settings_sha256', 'receipt'}
                    or record.get('phase') not in {'reserved', 'staging', 'prepared', 'published'}):
                raise ForkError('fork_operation_conflict', 'Operation ID already names another request or unsupported record')
            identities = [record['transaction_identity'], record['stage_identity']]
            if any(value is not None and (not isinstance(value, dict) or set(value) != {'dev', 'ino'}
                    or any(type(number) is not int or number < 0 for number in value.values())) for value in identities):
                raise ForkError('fork_operation_conflict', 'Fork operation lost its exact directory identities')
            prepared = record['phase'] in {'prepared', 'published'}
            if (prepared != (record['receipt'] is not None and record['stage_identity'] is not None)
                    or (record['phase'] != 'reserved' and record['transaction_identity'] is None)
                    or (record['phase'] == 'reserved' and any(value is not None for value in identities))
                    or (not prepared and (record['receipt'] is not None or record['settings_sha256'] is not None))
                    or (prepared and not isinstance(record['settings_sha256'], str))
                    or (prepared and not re.fullmatch('[0-9a-f]{64}', record['settings_sha256']))):
                raise ForkError('fork_operation_conflict', 'Fork operation has an incomplete publication record')
            if prepared and (not isinstance(record['receipt'], dict)
                    or any(record['receipt'].get(key) != value for key, value in
                        dict(operation_id=operation_id, request_sha256=request_hash, child_root=str(child),
                             status='fork_created', version=1, state='paused').items())
                    or not isinstance(record['receipt'].get('child_identity'), str)):
                raise ForkError('fork_operation_conflict', 'Fork operation lost its original receipt')
        else:
            record = dict(expected, phase='reserved', transaction_identity=None, stage_identity=None, settings_sha256=None, receipt=None)
        yield path, record
        owner._same_gate_path(lock, descriptor, directory=False)
    finally:
        os.close(descriptor)


def _clean_transaction(path, record):
    """Unlink only references in this locked operation's registered directory."""
    from . import instance_delete as deletion
    transaction = Path(record['transaction_path'])
    if transaction.exists() or transaction.is_symlink():
        if record['transaction_identity'] is None or _directory_identity(transaction) != record['transaction_identity']:
            raise ForkError('fork_transaction_changed', 'Fork transaction no longer has its registered directory identity')
        if any(item.name not in {'origins', 'child'} for item in transaction.iterdir()):
            raise ForkError('fork_transaction_changed', 'Fork transaction contains unrecognized ownership')
        # The child was never published in staging; a published transaction must
        # be only its disposable envelope, never another child at the old path.
        if record['phase'] == 'published' and (transaction / 'child').exists():
            raise ForkError('fork_transaction_changed', 'Published fork transaction regained a child directory')
        stage = transaction / 'child'
        if stage.exists():
            if ((record['stage_identity'] is not None and _directory_identity(stage) != record['stage_identity'])
                    or record['stage_identity'] is None and (stage / 'settings.json').exists()
                    and (stage / 'settings-identity.json').exists()
                    or any((stage / name).exists() for name in ('host-started.json', 'jobs', 'native-evidence', 'instance-owner.json'))):
                raise ForkError('fork_transaction_changed', 'Unpublished child has unregistered identity or activity')
        origins = transaction / 'origins'
        if any((origins / name).exists() for name in ('settings.json', 'settings-identity.json', 'instance-owner.json')):
            raise ForkError('fork_transaction_changed', 'Fork originals directory contains an independent instance')
        deletion._check_mounts(transaction)
        for item in transaction.iterdir():
            deletion._preflight_tree(item, _directory_identity(item))
        deletion._remove_tree(transaction, record['transaction_identity'])
    if record['phase'] in {'reserved', 'staging'}:
        record.update(phase='reserved', transaction_identity=None, stage_identity=None, settings_sha256=None)
        files.durable_json(path, record)


def _published_receipt(path, record):
    try:
        _clean_transaction(path, record)
    except (OSError, files.ArchiveError, ForkError):
        # Disposable cleanup cannot revoke a positively confirmed publication.
        # Its exact registered identity remains available to the next retry.
        pass
    return record['receipt']


def _publication_unknown(record):
    receipt = record['receipt']
    error = ForkError('fork_publication_unknown', 'Original fork publication is unknown; do not recreate it. '
        + 'Original receipt: ' + files.canonical(receipt).decode())
    error.receipt = receipt
    return error


def _finish_prepared(child, path, record):
    transaction = Path(record['transaction_path'])
    stage = transaction / 'child'
    existing = _existing(child, record['operation_id'], record['request_sha256'])
    if existing is not None:
        if (existing != record['receipt'] or _directory_identity(child) != record['stage_identity']
                or c.digest(child / 'settings.json') != record['settings_sha256']
                or stage.exists() or stage.is_symlink()):
            raise ForkError('fork_transaction_changed', 'Published child differs from its original staged identity')
        if _directory_identity(transaction) != record['transaction_identity']:
            raise ForkError('fork_transaction_changed', 'Published fork lost its exact transaction directory')
        # A prior rename may have returned before either directory barrier.
        files.fsync_directory(child.parent)
        files.fsync_directory(transaction)
    else:
        if not stage.exists() and not stage.is_symlink():
            raise _publication_unknown(record)
        if (_directory_identity(transaction) != record['transaction_identity']
                or _directory_identity(stage) != record['stage_identity']
                or _existing(stage, record['operation_id'], record['request_sha256']) != record['receipt']
                or c.digest(stage / 'settings.json') != record['settings_sha256']):
            raise ForkError('fork_transaction_changed', 'Prepared fork lost its exact original child')
        from .instance_archive import _publish
        _publish(stage, child)
    record['phase'] = 'published'
    files.durable_json(path, record)
    return _published_receipt(path, record)


def _sync_tree(root):
    # This is our bounded newly-created transaction tree, never source discovery.
    for current, directories, names in os.walk(root, followlinks=False):
        for name in names:
            path = Path(current) / name
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ForkError('fork_path_unsafe', 'Special file in fork transaction')
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in directories:
            if (Path(current) / name).is_symlink():
                raise ForkError('fork_path_unsafe', 'Symbolic link in fork transaction')
    for current, _, _ in os.walk(root, topdown=False):
        files.fsync_directory(Path(current))


def _prepare(plan, request, transaction, *, _on_created=None):
    """Rebuild declared configuration with new identity and no inherited grant."""
    source_store = transaction / 'origins'
    _source_call(fork_sources.copy_source, plan, source_store)
    stage = transaction / 'child'
    final = Path(request['child_root'])
    document = research_config.to_document(Path(request['parent_root']), plan['selected_settings'])
    document['research'].update(root=str(stage), public_root=str(source_store / 'public'))
    # Exact selected globals already live in immutable inherited storage. Their
    # original bindings are retained by the source resolver, not re-read from
    # the parent's current project tree.
    document['research'].pop('user_updates', None)
    document['budget'].update(request['budget'])
    document.get('executor', {}).pop('resource_policy', None)
    document['ui'] = dict(port=0, open_browser=False)
    options = research_config.normalize(research_config.from_document(document, base=transaction))
    selected_settings = plan['selected_settings']
    # A declaration parser resolves path-like launchers for editing. The frozen
    # argv is already an exact validated binding; preserve its lexical spelling
    # too (for example an explicitly selected interpreter symlink).
    options['launcher'] = copy.deepcopy(selected_settings['native_launcher_argv'])
    # Editing declarations omit disabled options and literal role guidance.
    # Restore those exact values after the ordinary defaults have been applied.
    for field in ('search_policy', 'role_guidance', 'storage_policy'):
        options[field] = copy.deepcopy(selected_settings.get(field))
    if selected_settings.get('delivery_policy') is not None:
        # Keep declared invocation spellings while rebuilding (and checking)
        # the frozen launcher/input proofs through the ordinary policy validator.
        options['delivery_policy'] = copy.deepcopy(document['delivery'])
        adapter = options['delivery_policy'].get('adapter')
        if adapter is not None:
            adapter['inputs'] = [dict(label=row['label'], path=row.get('invoked_path', row['path']))
                for row in selected_settings['delivery_policy']['adapter']['inputs']]
    for field in ('correction_policy', 'node_budget_policy', 'delivery_policy', 'resume_policy',
                  'portfolio_policy', 'worker_fragment_policy', 'role_budget_policy', 'selection_policy',
                  'role_profiles', 'history_observer'):
        if selected_settings.get(field) is None:
            options[field] = None
    selected_globals = plan.get('selected_globals', [])
    texts = []
    source_snapshot = originals._snapshot(plan) if selected_globals else None
    for row in selected_globals:
        reference = row['original_ref']
        owner = originals._snapshot_source(source_store, source_snapshot, reference['origin_identity'], request['step'])
        original = _source_call(originals.resolve_ref, owner, reference)
        texts.append(original.read_bytes().decode('utf-8'))
    if texts and texts[0] != plan['selected_settings']['objective']:
        raise ForkError('fork_origin_invalid', 'Selected objective differs from its exact original global')
    def source_facts(storage_root, global_materials):
        destination = storage_root / 'host-runtime/fork'
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_store.rename(destination)
        configured = _child_source_facts(storage_root, global_materials, plan)
        # Prepare will verify these exact retained originals when it freezes
        # declarations. Seed immutable aliases so public payloads stay shared.
        for index, declaration in enumerate(configured):
            origin = declaration['origin']
            source = Path(origin['path'])
            if stat.S_IMODE(source.stat().st_mode) == 0o400:
                retained = storage_root / 'source-provenance/originals' / f'{index:06d}'
                _source_call(files.link_verified, source, retained, dict(origin, mode=0o400))
        return configured
    settings = controller.prepare(**options, _existing_settings=selected_settings, _identity_root=final,
        _public_identity_root=final / 'host-runtime/fork/public', _user_update_texts=texts[1:],
        _source_facts_factory=source_facts, _on_created=_on_created)
    settings = c.settings(stage)
    changed = fork_runtime.changed_task_settings(selected_settings, settings)
    if changed:
        raise ForkError('fork_configuration_changed', 'Frozen scientific settings could not be preserved: ' + ', '.join(changed))
    c.write(stage / 'pause.request', dict(reason='fork_created_paused'))
    if request['guidance_text'] is not None:
        guidance_id = uuid.uuid4().hex
        step = request['step'] + (plan['source'].get('intent_kind') == 'formal_review')
        c.write(stage / 'host-control/guidance' / (guidance_id + '.json'), dict(guidance_id=guidance_id,
            revision=1, sequence=1, status='pending', text=request['guidance_text'], target='global',
            kind='advice', effective_step=step, scope='research'))
    return stage, settings


def _child_source_facts(stage, global_materials, plan):
    """Retain existing source restrictions on exact child originals at birth.

    Clear/restricted source facts travel with the same bytes. This neither imports
    an old verdict nor grants a new execution allowance.
    """
    source = plan.get('source_declarations', {})
    configured = []
    def retain(path, restriction):
        if restriction in {'clear', 'restricted'}:
            configured.append(dict(origin=qualification._identity(path), restriction=restriction))
    for selected, child_material in zip(plan.get('selected_globals', []), global_materials):
        material = selected['material']
        mappings = [row for row in source.get('globals', []) if row['material'] == material]
        if len(mappings) != 1:
            continue  # A missing original declaration remains unknown.
        declarations = [row for row in source.get('declarations', []) if row['origin'] == mappings[0]['origin']]
        if len(declarations) == 1:
            retain(stage / child_material['source'], declarations[0]['restriction'])
    for snapshot in plan.get('public_snapshots', []):
        reference = snapshot['original_ref']
        declarations = [row for row in source.get('declarations', [])
            if row.get('retained', {}).get('path') == reference['path']
            and row['retained'].get('sha256') == reference['sha256']]
        if len(declarations) == 1:
            retain(stage / 'host-runtime/fork/public' / snapshot['path'], declarations[0]['restriction'])
    return configured


def create(parent_root, step, child_root, *, budget, guidance_text=None, expected_preview_id, operation_id):
    """Publish one self-contained paused child; a durable receipt makes retries safe."""
    parent, child = _target(parent_root, child_root)
    request = _request(parent, step, child, budget, guidance_text)
    if not isinstance(operation_id, str) or not re.fullmatch('[A-Za-z0-9._-]{1,128}', operation_id):
        raise ForkError('fork_operation_conflict', 'Declare one bounded operation ID')
    request.update(expected_preview_id=expected_preview_id)
    request_hash = _digest(request)
    from .instance_owner import creation
    with creation(child), _operation(child, operation_id, request_hash) as (path, record), _target_lock(child):
        if record['phase'] == 'published':
            existing = _existing(child, operation_id, request_hash)
            if existing is not None and (existing != record['receipt']
                    or _directory_identity(child) != record['stage_identity']
                    or c.digest(child / 'settings.json') != record['settings_sha256']):
                raise ForkError('fork_transaction_changed', 'Current target differs from the original published child')
            return _published_receipt(path, record)
        if record['phase'] == 'prepared':
            return _finish_prepared(child, path, record)
        if _existing(child, operation_id, request_hash) is not None:
            raise ForkError('fork_operation_conflict', 'Target receipt has no prepared operation record')
        if record['phase'] == 'staging':
            _clean_transaction(path, record)
        with _source_guard(parent):
            return _create_guarded(child, request, expected_preview_id, path, record)


def _create_guarded(child, request, expected_preview_id, path, record):
    inspection_request = {key: value for key, value in request.items() if key != 'expected_preview_id'}
    plan, checked = _inspect(inspection_request)
    if checked['preview_id'] != expected_preview_id:
        raise ForkError('fork_preview_stale', 'Source or request changed; preview this exact request again')
    if checked['blockers']:
        code = ('fork_storage_unsupported' if any(row.get('code') == 'fork_storage_unsupported' for row in checked['blockers']) else
            'fork_external_effect_unknown' if any('unknown' in row.get('code', '') for row in checked['blockers']) else 'fork_input_missing')
        raise ForkError(code, 'Source inputs or external effects block creation: ' + str(checked['blockers']))
    transaction = Path(record['transaction_path'])
    if transaction.exists() or transaction.is_symlink():
        raise ForkError('fork_transaction_changed', 'Unregistered fork transaction needs explicit reconciliation')
    files.durable_json(path, record)
    transaction.mkdir(mode=0o700)
    files.fsync_directory(transaction.parent)
    record.update(phase='staging', transaction_identity=_directory_identity(transaction))
    files.durable_json(path, record)  # No original links exist before this commit.
    def register_stage(stage):
        if stage != transaction / 'child' or record['stage_identity'] is not None:
            raise ForkError('fork_transaction_changed', 'Fork preparation created an unexpected child directory')
        record['stage_identity'] = _directory_identity(stage)
        files.durable_json(path, record)
    try:
        stage, settings = _prepare(plan, request, transaction, _on_created=register_stage)
        if stage != transaction / 'child' or _directory_identity(stage) != record['stage_identity']:
            raise ForkError('fork_transaction_changed', 'Fork preparation lost its registered child directory')
        _source_call(fork_sources.validate_manifest, stage, full=True)
        if _inspect(inspection_request)[1]['preview_id'] != expected_preview_id:
            raise ForkError('fork_source_changed', 'Source changed while retaining shared originals; repeat preview')
        receipt = dict(status='fork_created', version=1, operation_id=record['operation_id'],
            request_sha256=record['request_sha256'], preview_id=expected_preview_id,
            child_identity=settings['goal_id'], state='paused', child_root=str(child),
            manifest_ref='host-runtime/fork/manifest.json')
        c.write(stage / 'host-runtime/fork/request.json', dict(request, operation_id=record['operation_id']))
        c.write(stage / 'host-runtime/fork/receipt.json', receipt)
        _sync_tree(stage)
        files.fsync_directory(transaction)
        prepared = dict(record, phase='prepared', receipt=receipt, stage_identity=_directory_identity(stage),
                        settings_sha256=c.digest(stage / 'settings.json'))
        files.durable_json(path, prepared)
        record.update(prepared)
    except BaseException:
        # A prepared record is authoritative even if its durable write returned
        # an error. Never erase publication evidence or retry with a new identity.
        persisted = c.read(c.regular(path))
        if persisted['phase'] == 'staging':
            _clean_transaction(path, persisted)
        raise
    return _finish_prepared(child, path, record)
