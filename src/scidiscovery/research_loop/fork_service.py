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
import shutil
import stat
import tempfile
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
    _source_call(fork_sources.validate_manifest, child)
    return receipt


def _reserve_operation(child, operation_id, request_hash):
    # Operation IDs are scoped to the explicit destination parent directory;
    # a retry may not silently choose a second sibling destination.
    directory = child.parent / '.scid-fork-operations'
    files.check_path(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (_digest(operation_id) + '.json')
    value = dict(operation_id=operation_id, request_sha256=request_hash, child_root=str(child))
    descriptor = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if path.exists():
            if c.read(c.regular(path)) != value:
                raise ForkError('fork_operation_conflict', 'Operation ID already names another request in this target parent')
        else:
            files.durable_json(path, value)
    finally:
        os.close(descriptor)


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


def _prepare(plan, request, transaction):
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
    for field in ('search_policy', 'role_guidance'):
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
        _source_facts_factory=source_facts)
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
    with _target_lock(child):
        existing = _existing(child, operation_id, request_hash)
        if existing is not None:
            return existing
        with _source_guard(parent):
            return _create_guarded(child, request, request_hash, expected_preview_id, operation_id)


def _create_guarded(child, request, request_hash, expected_preview_id, operation_id):
    inspection_request = {key: value for key, value in request.items() if key != 'expected_preview_id'}
    plan, checked = _inspect(inspection_request)
    if checked['preview_id'] != expected_preview_id:
        raise ForkError('fork_preview_stale', 'Source or request changed; preview this exact request again')
    if checked['blockers']:
        code = ('fork_storage_unsupported' if any(row.get('code') == 'fork_storage_unsupported' for row in checked['blockers']) else
            'fork_external_effect_unknown' if any('unknown' in row.get('code', '') for row in checked['blockers']) else 'fork_input_missing')
        raise ForkError(code, 'Source inputs or external effects block creation: ' + str(checked['blockers']))
    _reserve_operation(child, operation_id, request_hash)
    transaction = Path(tempfile.mkdtemp(prefix='.scid-fork-', dir=child.parent))
    try:
        stage, settings = _prepare(plan, request, transaction)
        _source_call(fork_sources.validate_manifest, stage, full=True)
        if _inspect(inspection_request)[1]['preview_id'] != expected_preview_id:
            raise ForkError('fork_source_changed', 'Source changed while retaining shared originals; repeat preview')
        receipt = dict(status='fork_created', version=1, operation_id=operation_id,
            request_sha256=request_hash, preview_id=expected_preview_id,
            child_identity=settings['goal_id'], state='paused', child_root=str(child),
            manifest_ref='host-runtime/fork/manifest.json')
        c.write(stage / 'host-runtime/fork/request.json', dict(request, operation_id=operation_id))
        c.write(stage / 'host-runtime/fork/receipt.json', receipt)
        _sync_tree(stage)
        from .instance_archive import _publish
        _publish(stage, child)
        return receipt
    finally:
        # A published receipt remains authoritative even when disposable
        # transaction cleanup fails after the atomic publication.
        shutil.rmtree(transaction, ignore_errors=True)
