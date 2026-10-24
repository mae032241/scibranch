"""Exact host snapshot recovery; never an adapter or execution admission."""
from contextlib import ExitStack
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat

from scidiscovery import archive_io as files
from . import contracts as c, execution, experiment_client, instance_owner as owner
from . import experiments as e, storage_policy as storage

TOKEN = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
RECEIPTS = ('.discard.intent.json', '.discard.outcome.json', '.published.json')


class PreparationError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _fail(message, code='preparation_identity_conflict'):
    raise PreparationError(code, message)


def _check(context):
    if context is not None:
        context.remaining_seconds()


def _identity(path):
    from . import instance_delete
    return instance_delete._identity(path)


def _json(path, context=None):
    from . import atomic_files
    path = files.check_path(path)
    chunks = []; size = 0
    with atomic_files._regular_stream(path) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > 32 * 1024 * 1024:
            _fail('Host preparation control original exceeds its parse bound')
        while True:
            _check(context)
            block = stream.read(min(1024 * 1024, before.st_size - size + 1))
            if not block:break
            chunks.append(block); size += len(block)
            if size > before.st_size:
                _fail('Host preparation control original grew during reading')
        after = os.fstat(stream.fileno())
        if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
                or size != before.st_size):
            _fail('Host preparation control original changed during reading')
    _check(context)
    value = json.loads(b''.join(chunks))
    if not isinstance(value, dict):
        _fail('Host preparation control original must be an object')
    return value


def _verify_ref(root, ref, context=None):
    if not isinstance(ref, dict) or set(ref) != {'path', 'size_bytes'}:
        _fail('Host preparation original reference changed')
    path = Path(ref['path'])
    path = path if path.is_absolute() else root / path
    actual = e.proof(path, context=context)
    if any(actual[key] != ref[key] for key in ('size_bytes',)):
        _fail('Host preparation original bytes changed')
    return path


def _acceptance_base(accepted):
    return {key: value for key, value in accepted.items()
        if key not in {'host_preparation', 'mechanism_binding'}}


def _inventory(items):
    if not isinstance(items, list):
        _fail('Exact host snapshot inventory required')
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            _fail('Host snapshot input inventory changed')
        name = item.get('name')
        if not isinstance(name, str):
            _fail('Host snapshot input path changed')
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                or not relative.parts or name == '.' or name in seen):
            _fail('Host snapshot input path is not canonical')
        if (not isinstance(item.get('path',), str) or not Path(item['path']).is_absolute()
                or type(item.get('size_bytes',)) is not int or item['size_bytes'] < 0):
            _fail('Host snapshot input original changed')
        seen.add(name)
    return items


def _configuration(root, config, context=None):
    scope = config['scope']; attempt = config.get('native_attempt')
    if not isinstance(attempt, dict) or type(attempt.get('number')) is not int:
        _fail('Host preparation requires its exact native attempt')
    job = root / 'jobs' / f'{scope["step"]:03d}.json'
    native = execution.record(root, scope['step'], attempt['number'])
    original = native / 'experiment-config.json'
    projection = root / 'host-runtime/experiment-control' / f'{scope["step"]:03d}' / 'interfaces' / f'{attempt["number"]:03d}.json'
    if (_json(original, context) != config or _json(projection, context) != config
            or c.record_ref(job) != scope['job_ref']
            or _json(native / 'native-intent.json', context).get('task_ref') != scope['job_ref']):
        _fail('Host preparation lost its original configuration or task')
    return dict(job=e.proof(job, context=context), configuration=e.proof(original, context=context),
        projection=e.proof(projection, context=context), native_intent=e.proof(native / 'native-intent.json', context=context))


def _mkdir(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current); current = current.parent
    files.check_path(current)
    for item in reversed(missing):
        item.mkdir(mode=0o700)
        files.fsync_directory(item.parent)


def bind_snapshot(config, directory, descriptor, accepted_base, *, context):
    root = experiment_client.root_for_configuration(config, allow_standalone=True)
    if root is None:
        return None
    records = Path(config['record_dir']); directory = files.check_path(directory)
    token = directory.name.removeprefix('.prepared-')
    call = descriptor.name
    if (directory.parent != records or directory.name != '.prepared-' + token
            or not TOKEN.fullmatch(token) or not e.ID.fullmatch(call) or call in {'.', '..'}):
        _fail('Exact host-created preparation directory required')
    original = _configuration(root, config, context)
    inventory = _inventory(accepted_base.get('snapshot_files', []))
    value = dict(version=1, kind='host_preparation_snapshot', snapshot_token=token, call_id=call,
        records=str(records), snapshot=directory.name, directory_identity=_identity(directory),
        instance=dict(root=str(root), root_identity=_identity(root), host=owner._host(),
            settings=e.proof(root / 'settings.json', context=context), owner=owner._binding(root)),
        scope=config['scope'], native_attempt=config['native_attempt'], interface=config['execution_interface'],
        prepared=descriptor.model_dump(mode='json'), input_inventory=inventory,
        acceptance_base=_acceptance_base(accepted_base), **original)
    authority = accepted_base.get('execution_authority')
    refs = {}
    if authority is not None:
        permit_path = _verify_ref(root, authority, context)
        permit = _json(permit_path, context)
        refs['authority'] = authority
        for key in ('allocation', 'request', 'decision'):
            _verify_ref(root, permit[key], context); refs[key] = permit[key]
        prepare = root / 'execution-authority/calls' / call / 'prepare-intent.json'
        refs['prepare_intent'] = e.proof(prepare, context=context)
        refs['reservation'] = accepted_base.get('execution_reservation')
        _verify_ref(root, refs['reservation'], context)
    value['authority_originals'] = refs
    files.fsync_directory(records)
    path = records / 'host-preparations' / call / (token + '.json')
    _mkdir(path.parent)
    e.write(path, value, root=root, policy=config.get('storage_policy'), context=context)
    return e.proof(path, context=context)


def _binding(root, records, path, context=None):
    value = _json(path, context)
    token = value.get('snapshot_token'); call = value.get('call_id')
    if (value.get('version') != 1 or value.get('kind') != 'host_preparation_snapshot'
            or not isinstance(token, str) or not TOKEN.fullmatch(token)
            or not isinstance(call, str) or not e.ID.fullmatch(call) or call in {'.', '..'}
            or path != records / 'host-preparations' / call / (token + '.json')
            or value.get('records') != str(records) or value.get('snapshot') != '.prepared-' + token
            or value.get('instance', {}).get('root') != str(root)):
        _fail('Host preparation binding escaped its exact namespace')
    config = _json(_verify_ref(root, value['configuration'], context), context)
    if (experiment_client.root_for_configuration(config) != root or config['record_dir'] != str(records)
            or config['scope'] != value.get('scope') or config.get('native_attempt') != value.get('native_attempt')
            or config['execution_interface'] != value.get('interface')
            or _configuration(root, config, context) != {key: value[key]
                for key in ('job', 'configuration', 'projection', 'native_intent')}):
        _fail('Host preparation binding lost its exact original configuration')
    _verify_ref(root, value['instance']['settings'], context)
    _inventory(value['input_inventory'])
    return value, config, e.proof(path, context=context)


def _live(root, value):
    instance = value['instance']
    if (instance['host'] != owner._host() or instance['root_identity'] != _identity(root)
            or instance['owner'] != owner._binding(root)):
        _fail('Snapshot maintenance requires the original live root and owner generation')
    owner.require_owner(root)


def _tree(directory, value, *, complete, context=None):
    from . import instance_delete as deletion
    deletion._same(directory, value['directory_identity'])
    deletion._check_mounts(directory)
    expected = {'source.py': value['prepared']}
    expected.update({'inputs/' + item['name']: item for item in value['input_inventory']})
    allowed_dirs = {'.'}
    for name in expected:
        allowed_dirs.update(parent.as_posix() for parent in Path(name).parents)
    observed = set()
    def failed(error):
        raise error
    for current, directories, names in os.walk(directory, followlinks=False, onerror=failed):
        _check(context)
        current = Path(current)
        for name in directories + names:
            _check(context)
            path = current / name; info = path.lstat()
            relative = path.relative_to(directory).as_posix()
            if info.st_dev != value['directory_identity']['dev'] or stat.S_ISLNK(info.st_mode):
                _fail('Host preparation contains a link or filesystem boundary')
            if stat.S_ISDIR(info.st_mode):
                if relative not in allowed_dirs:
                    _fail('Host preparation contains an unknown directory')
            elif stat.S_ISREG(info.st_mode):
                if relative == 'accepted.json':
                    continue
                if relative in expected:
                    observed.add(relative)
                    if complete:
                        actual = e.proof(path, context=context)
                        if any(actual[key] != expected[relative][key] for key in ('size_bytes',)):
                            _fail('Accepted host snapshot original changed')
                elif complete or not re.fullmatch(r'\.scid-[A-Za-z0-9_-]+\.partial', name):
                    _fail('Host preparation contains an unknown file')
            else:
                _fail('Host preparation contains a special file')
    if complete and observed != set(expected):
        _fail('Accepted host snapshot originals are incomplete')
    deletion._same(directory, value['directory_identity'])


def _accepted(directory, value, config, ref, context=None, *, historical=False):
    accepted = _json(directory / 'accepted.json', context)
    destination = Path(value['records']) / 'calls' / value['call_id']
    prepared = value['prepared']
    if (accepted.get('host_preparation') != ref or _acceptance_base(accepted) != value['acceptance_base']
            or accepted.get('scope') != value['scope'] or accepted.get('call_id') != value['call_id']
            or accepted.get('native_attempt') != value['native_attempt'] or accepted.get('interface') != value['interface']
            or accepted.get('prepared') != prepared
            or accepted.get('source_script') != prepared['local_path']
            or accepted.get('script') != dict(path=str(destination / 'source.py'), size_bytes=prepared['size_bytes'])
            or accepted.get('snapshot_files', []) != value['input_inventory']):
        _fail('Durable accepted original differs from its host snapshot binding')
    from . import breadth
    breadth.check_binding(config.get('breadth'), accepted.get('mechanism_binding'), value['scope'])
    parent = accepted.get('parent')
    if not isinstance(parent, dict) or 'candidate_id' not in parent:
        _fail('Durable accepted original has no exact parent')
    if historical:
        originals = [('source.py', prepared), *(('inputs/' + item['name'], item) for item in value['input_inventory'])]
        for name, original in originals:
            actual = e.proof(directory / name, context=context)
            if any(actual[key] != original[key] for key in ('size_bytes',)):
                _fail('Historical accepted snapshot original changed')
    else:
        _tree(directory, value, complete=True, context=context)
    return accepted


def _historical_owner(root, value, originals, context=None):
    """A retained transition proves history, never permission to mutate its old inode."""
    instance = value['instance']; current = owner._binding(root)
    if instance['host'] != owner._host():
        _fail('Host snapshot history belongs to another host')
    if instance['root_identity'] == _identity(root):
        if instance['owner'] != current:
            if instance['owner'] is not None or current is None or current['generation'] != 1:
                _fail('Host snapshot history has no original owner transition')
            owner.require_owner(root)
        return False
    if current is None:
        _fail('Host snapshot history has no cold restore owner')
    owner.require_owner(root)
    generation = (instance['owner'] or {}).get('generation', 1)
    transitions = {}
    for path in sorted((root / 'host-runtime/restore').glob('*/transfer.json')):
        _check(context)
        transfer = _json(path, context)
        checkpoint_path = path.with_name('checkpoint.json')
        checkpoint_ref = e.proof(checkpoint_path, context=context)
        # The retained protocol uses root-relative transition references.
        checkpoint_ref['path'] = checkpoint_path.relative_to(root).as_posix()
        transfer_ref = e.proof(path, context=context); transfer_ref['path'] = path.relative_to(root).as_posix()
        transition = owner.read_transition(root, checkpoint_ref, transfer_ref)
        checkpoint = transition['checkpoint']
        if (checkpoint['anchor_id'] != current['anchor_id'] or checkpoint['host'] != instance['host']
                or checkpoint['settings_ref'] != dict(path=instance['settings']['path']) or instance['settings']['path'] != str(root / 'settings.json')):
            _fail('Retained snapshot cold transition lost its original instance')
        transitions[transfer['from_generation']] = (path.parent, transition)
    while generation < current['generation']:
        _check(context)
        if generation not in transitions:
            _fail('Host snapshot history has no complete cold transition chain')
        receipt, transition = transitions[generation]
        checkpoint, transfer = transition['checkpoint'], transition['transfer']
        metadata_path = receipt / 'archive.json'
        metadata = _json(metadata_path, context)
        if any(e.proof(metadata_path, context=context)[key] != checkpoint['payload_ref'][key]
                for key in ('size_bytes',)):
            _fail('Retained checkpoint payload identity changed')
        manifest = receipt / 'manifest.jsonl'
        actual = e.proof(manifest, context=context)
        if any(actual[key] != metadata['manifest'][key] for key in ('size_bytes',)):
            _fail('Retained checkpoint manifest identity changed')
        found = set()
        with manifest.open('rb') as stream:
            for line in stream:
                _check(context); row = json.loads(line)
                name = row['path']
                if name in originals:
                    if row.get('kind') != 'file' or any(row[key] != originals[name][key] for key in ('size_bytes',)):
                        _fail('Snapshot original was not inherited by its exact checkpoint')
                    found.add(name)
        if found != set(originals):
            _fail('Snapshot checkpoint manifest omits exact originals')
        generation = transfer['to_generation']
        if generation == current['generation'] and transfer['root_identity'] != _identity(root):
            _fail('Snapshot history lost its current restored root')
    return True


def retained_preparations(root, records, *, context=None):
    root = files.check_path(Path(root).absolute()); records = files.check_path(Path(records).absolute())
    directory = files.check_path(records / 'host-preparations')
    if not directory.exists():
        return []
    rows = []
    for calls in sorted(directory.iterdir()):
        _check(context); files.check_path(calls)
        if not calls.is_dir() or not e.ID.fullmatch(calls.name) or calls.name in {'.', '..'}:
            _fail('Unknown host preparation call namespace')
        bindings = {}; receipts = []
        for path in sorted(calls.iterdir()):
            _check(context)
            suffix = next((suffix for suffix in RECEIPTS if path.name.endswith(suffix)), None)
            if suffix is not None:
                receipts.append((path, path.name[:-len(suffix)], suffix)); continue
            token = path.name.removesuffix('.json')
            if path.name != token + '.json' or not TOKEN.fullmatch(token):
                _fail('Unknown host preparation original')
            value, config, ref = _binding(root, records, path, context)
            bindings[token] = ref
            destination = files.check_path(records / 'calls' / value['call_id'])
            snapshot = files.check_path(records / value['snapshot'])
            resolved = False
            if destination.exists():
                accepted = _json(destination / 'accepted.json', context)
                if accepted.get('host_preparation') == ref:
                    # Historical verification uses snapshot bytes but the original inode
                    # fence applies only when no legitimate restore changed them.
                    historical = dict(value, directory_identity=_identity(destination))
                    _accepted(destination, historical, config, ref, context, historical=True)
                    originals = {path.relative_to(root).as_posix(): ref,
                        (destination / 'accepted.json').relative_to(root).as_posix(): e.proof(destination / 'accepted.json', context=context)}
                    originals.update({(destination / name).relative_to(root).as_posix(): e.proof(destination / name, context=context)
                        for name in ['source.py', *('inputs/' + item['name'] for item in value['input_inventory'])]})
                    restored = _historical_owner(root, value, originals, context)
                    if not restored and _identity(destination) != value['directory_identity']:
                        _fail('Original published snapshot directory identity changed')
                    if snapshot.exists():
                        _fail('Host snapshot exists beside its canonical publication')
                    resolved = True
            rows.append(dict(call_id=value['call_id'], snapshot_token=value['snapshot_token'], binding=ref,
                resolved=resolved, status='published' if resolved else 'unknown', snapshot=str(snapshot)))
        for path, token, suffix in receipts:
            receipt = _json(path, context)
            kinds = {'.discard.intent.json':'discard_partial_snapshot',
                '.discard.outcome.json':'partial_snapshot_discarded', '.published.json':'accepted_snapshot_published'}
            if (token not in bindings or receipt.get('binding') != bindings[token]
                    or receipt.get('version') != 1 or receipt.get('kind') != kinds[suffix]):
                _fail('Host preparation operation receipt lost its exact binding')
            if suffix == '.discard.outcome.json':
                intent_path = calls / (token + '.discard.intent.json')
                if receipt.get('intent') != e.proof(intent_path, context=context):
                    _fail('Host preparation discard outcome lost its original intent')
    return rows


def _authority(root, value, config, accepted, context, *, directory=None):
    refs = value['authority_originals']
    if not refs:
        if accepted.get('execution_authority') is not None:
            _fail('Accepted host snapshot lost its original authority')
        return
    from . import execution_authority as authority, budget
    for ref in refs.values():
        _verify_ref(root, ref, context)
    permit = authority._permit(root, refs['authority'], config, accepted['execution_target'],
        value['call_id'], None, authority.host_secret(root))
    if any(permit[key] != refs[key] for key in ('allocation', 'request', 'decision')):
        _fail('Original accepted authority references changed')
    allocation = _json(_verify_ref(root, refs['allocation'], context), context)
    request = _json(_verify_ref(root, refs['request'], context), context)
    decision = _json(_verify_ref(root, refs['decision'], context), context)
    prepare = _json(_verify_ref(root, refs['prepare_intent'], context), context)
    reservation = budget.execution_reservation(root, refs['reservation'])
    expected = dict(call_id=value['call_id'], scope=value['scope'], native_attempt=value['native_attempt'],
        interface=value['interface'], authority=refs['authority'],request=refs['request'],decision=refs['decision'])
    binding = allocation['binding']
    from scidiscovery import execution_security as security
    decision_body = {key: item for key, item in decision.items() if key != 'receipt_hmac'}
    native_intent = e.proof(execution.record(root, value['scope']['step'], value['native_attempt']['number']) / 'native-intent.json', context=context)
    native_intent['path'] = Path(native_intent['path']).relative_to(root).as_posix()
    if (accepted.get('execution_authority') != refs['authority'] or accepted.get('execution_reservation') != refs['reservation']
            or allocation.get('call_id') != value['call_id']
            or any(binding.get(key) != expected[key] for key in ('scope', 'native_attempt', 'interface'))
            or allocation.get('native_intent') != request.get('native_intent')
            or request.get('native_intent') != native_intent
            or request.get('call_id') != value['call_id'] or c.record_ref(root / request.get('job', {})['path']) != value['scope']['job_ref']
            or decision.get('request') != refs['request'] or permit.get('decision') != refs['decision']
            or decision.get('nonce_hash') != request.get('nonce_hash')
            or not security.decision_authorizes(request['kind'],decision['selected_option'],request['options'])
            or not hmac.compare_digest(decision.get('receipt_hmac',''),security.receipt_hmac(authority.host_secret(root),security.canonical(decision_body)))
            or any(binding.get(key) != request.get(key) for key in ('subject','review'))
            or binding.get('payload_alias') != request.get('payload_alias',request.get('payload'))
            or permit.get('payload_alias') != binding.get('payload_alias')
            or permit.get('target') != request.get('target')
            or prepare.get('call_id') != value['call_id'] or prepare.get('authority') != refs['authority']
            or prepare.get('reservation') != refs['reservation'] or reservation['binding'] != expected
            or reservation.get('released') or permit['allocation'] != refs['allocation']):
        _fail('Original allocation, preparation or reservation binding changed')
    # A pre-submit reservation may legitimately remain unbound. Recovery never
    # writes bind/settle/release events to make publication appear complete.
    if reservation['bound'] is not None:
        canonical = Path(value['records']) / 'calls' / value['call_id'] / 'accepted.json'
        directory = directory or Path(value['records']) / value['snapshot']
        accepted_ref = e.proof(directory / 'accepted.json', context=context)
        accepted_ref['path'] = canonical.relative_to(root).as_posix()
        if reservation['bound'] != dict(accepted=accepted_ref, prepared=value['prepared']):
            _fail('Original reservation already binds another acceptance')


def _result(value, ref, status):
    return dict(status=status, call_id=value['call_id'], snapshot_token=value['snapshot_token'],
        binding=ref, external_preparation_state='unknown')


def _receipt(path, value, context):
    from . import atomic_files
    _check(context)
    atomic_files.publish_bytes(path, e.encoded(value), existing_ok=True)


def discard_owned_snapshot(config, binding_ref, *, reason, context):
    root = experiment_client.root_for_configuration(config)
    records = Path(config['record_dir'])
    path = _verify_ref(root, binding_ref, context)
    value, original, ref = _binding(root, records, path, context)
    if config != original or binding_ref != ref:
        _fail('Snapshot cleanup escaped its original writer')
    _live(root, value)
    snapshot = files.check_path(records / value['snapshot'])
    intent_path = path.with_name(value['snapshot_token'] + '.discard.intent.json')
    outcome = path.with_name(value['snapshot_token'] + '.discard.outcome.json')
    if files.check_path(records / 'calls' / value['call_id']).exists():
        _fail('Partial snapshot cleanup cannot remove a canonical accepted call')
    intent = dict(version=1, kind='discard_partial_snapshot', binding=ref,
        directory_identity=value['directory_identity'], reason=reason)
    if intent_path.exists():
        prior = _json(intent_path, context)
        if any(prior.get(key) != intent[key] for key in ('version', 'kind', 'binding', 'directory_identity')):
            _fail('Snapshot discard retry changed its original operation')
        intent = prior
    if snapshot.exists():
        if (snapshot / 'accepted.json').exists():
            _fail('Durable accepted snapshot requires publication, not partial discard', 'preparation_publish_required')
        _tree(snapshot, value, complete=False, context=context)
        from . import instance_delete as deletion
        deletion._preflight_tree(snapshot, value['directory_identity'], context=context)
        _receipt(intent_path, intent, context)
        deletion._remove_tree(snapshot, value['directory_identity'], context=context)
    elif not intent_path.exists():
        _fail('Missing partial snapshot has no original discard intent')
    _receipt(outcome, dict(version=1, kind='partial_snapshot_discarded', binding=ref,
        intent=e.proof(intent_path, context=context)), context)
    return _result(value, ref, 'partial_discarded')


def _local_configurations(root, step, records, context):
    """Retained originals, never the current client or an installed provider."""
    attempts = []
    for attempt in execution.accepted_intents(root, step):
        _check(context)
        attempts.append(attempt)
    if not attempts:
        _fail('Unbound local cleanup requires retained native attempt originals')
    interfaces = files.check_path(root / 'host-runtime/experiment-control' / f'{step:03d}' / 'interfaces')
    expected = {f'{number:03d}.json' for number in range(len(attempts))}
    observed = set()
    for path in interfaces.iterdir():
        _check(context)
        files.check_path(path)
        if not path.is_file():
            _fail('Unbound local cleanup has an ambiguous interface namespace')
        observed.add(path.name)
    if observed != expected:
        _fail('Unbound local cleanup requires every exact retained interface')
    for number, (native, _) in enumerate(attempts):
        _check(context)
        config = _json(native / 'experiment-config.json', context)
        interface = config.get('execution_interface')
        execution_key = c.record_ref(root / 'jobs' / f'{step:03d}.json') if number == 0 else c.record_ref(native / 'native-intent.json')
        if (experiment_client.root_for_configuration(config) != root or config['record_dir'] != str(records)
                or config.get('scope', {}).get('step') != step
                or config.get('native_attempt') != dict(number=number, execution_key=execution_key)
                or not isinstance(interface, dict)
                or interface.get('version') != e.VERSION
                or interface.get('executor') != 'local_execution:command'
                or interface.get('plugin_version') != '1'
                or interface.get('observation_version') != e.OBSERVATION_VERSION
                or interface.get('projection_contract') != 'scidiscovery.local-projections.v1'
                or interface.get('terminal_evidence') != 'local_process'
                or interface.get('result_protocol') != e.LOCAL_RESULT_PROTOCOL
                or interface.get('config_record') != {key: value for key, value in config.items() if key != 'execution_interface'}
                or config.get('host_execution_authority') is not None):
            _fail('Unbound local cleanup requires an exclusively built-in local preparation namespace')
        _configuration(root, config, context)


def _unbound_local(root, step, records, snapshot_token, *, action, context):
    from . import controller, instance_archive as archive, instance_delete as deletion
    # The root source gate fences configuration and effects; the exact record
    # writer lease also fences preparation before its temporary directory exists.
    # Do not acquire the whole-instance archive owner inventory for one target.
    with owner.source_lease(root), ExitStack() as locks:
        locks.enter_context(controller._local_writer(root))
        if (root / 'configuration.lock').exists():
            archive._lock(locks, root / 'configuration.lock')
        locks.enter_context(c.record_writer(records, blocking=False, exclusive=True))
        owner.require_owner(root)
        _check(context)
        _local_configurations(root, step, records, context)
        bindings = files.check_path(records / 'host-preparations')
        if bindings.exists():
            for calls in bindings.iterdir():
                _check(context)
                files.check_path(calls)
                if not calls.is_dir() or not e.ID.fullmatch(calls.name) or calls.name in {'.', '..'}:
                    _fail('Unbound local cleanup has an ambiguous host preparation namespace')
                if files.check_path(calls / (snapshot_token + '.json')).exists():
                    _fail('A bound host preparation requires its exact original call')
        snapshot = files.check_path(records / ('.prepared-' + snapshot_token))
        if not snapshot.exists():
            _fail('The explicitly selected unbound local partial is absent')
        identity = _identity(snapshot)
        accepted = files.check_path(snapshot / 'accepted.json')
        if accepted.exists():
            _fail('Durable accepted snapshot cannot use unbound partial cleanup', 'preparation_publish_required')
        deletion._preflight_tree(snapshot, identity, context=context)
        if action == 'discard-partial':
            deletion._remove_tree(snapshot, identity, context=context)
        return dict(status='partial_discarded' if action == 'discard-partial' else 'partial',
            call_id=None, snapshot_token=snapshot_token, binding=None)


def _maintain(root, step, call_id, snapshot_token, *, action, max_seconds, authorization_note, unbound_local=False):
    exact_call = isinstance(call_id, str) and e.ID.fullmatch(call_id) and call_id not in {'.', '..'}
    if (type(step) is not int or step <= 0 or type(unbound_local) is not bool
            or not (call_id is None if unbound_local else exact_call)
            or unbound_local and action == 'publish-accepted'
            or not isinstance(snapshot_token, str) or not TOKEN.fullmatch(snapshot_token)
            or action not in {'inspect', 'discard-partial', 'publish-accepted'}
            or type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0
            or not isinstance(authorization_note, str) or not authorization_note.strip()):
        _fail('Exact snapshot identity, finite maintenance window and audit note required', 'preparation_invalid_request')
    if not Path(root).is_absolute():
        _fail('An explicit absolute instance root is required', 'preparation_invalid_request')
    context = storage.Deadline(max_seconds)
    root = files.check_path(Path(root).absolute())
    from . import protocol, instance_archive as archive
    protocol.require_current(root)
    records = experiment_client.record_directory(root, step)
    if unbound_local:
        return _unbound_local(root, step, records, snapshot_token, action=action, context=context)
    path = records / 'host-preparations' / call_id / (snapshot_token + '.json')
    with owner.source_lease(root), ExitStack() as locks:
        archive._owner_locks(locks, root, context=context)
        value, config, ref = _binding(root, records, path, context)
        if value['call_id'] != call_id or value['scope']['step'] != step:
            _fail('Snapshot maintenance escaped the exact requested job')
        _live(root, value)
        snapshot = files.check_path(records / value['snapshot'])
        destination = files.check_path(records / 'calls' / call_id)
        if destination.exists():
            if snapshot.exists() or _identity(destination) != value['directory_identity']:
                _fail('Canonical call differs from the original snapshot publication')
            accepted = _accepted(destination, value, config, ref, context, historical=True)
            _authority(root, value, config, accepted, context, directory=destination)
            if action == 'discard-partial':
                _fail('Published acceptance cannot be discarded', 'preparation_publish_required')
            if action == 'publish-accepted':
                _receipt(path.with_name(snapshot_token + '.published.json'),
                    dict(version=1, kind='accepted_snapshot_published', binding=ref,
                        accepted=e.proof(destination / 'accepted.json', context=context)), context)
            return _result(value, ref, 'already_published')
        if action == 'discard-partial':
            return discard_owned_snapshot(config, ref, reason=authorization_note, context=context)
        if not snapshot.exists():
            if action == 'inspect' and path.with_name(snapshot_token + '.discard.outcome.json').exists():
                retained_preparations(root, records, context=context)
                return _result(value, ref, 'partial_discarded')
            _fail('Original host snapshot is absent without a completed exact discard')
        if not (snapshot / 'accepted.json').exists():
            _tree(snapshot, value, complete=False, context=context)
            if action == 'publish-accepted':
                _fail('No durable original accepted snapshot exists', 'preparation_incomplete')
            return _result(value, ref, 'partial')
        accepted = _accepted(snapshot, value, config, ref, context)
        _authority(root, value, config, accepted, context)
        if action == 'inspect':
            return _result(value, ref, 'accepted_unpublished')
        _mkdir(destination.parent)
        archive._publish(snapshot, destination)
        _receipt(path.with_name(snapshot_token + '.published.json'),
            dict(version=1, kind='accepted_snapshot_published', binding=ref,
                accepted=e.proof(destination / 'accepted.json', context=context)), context)
        return _result(value, ref, 'accepted_published')


def maintain(root, step, call_id, snapshot_token, *, action, max_seconds, authorization_note, unbound_local=False):
    try:
        return _maintain(root, step, call_id, snapshot_token, action=action,
            max_seconds=max_seconds, authorization_note=authorization_note, unbound_local=unbound_local)
    except (BlockingIOError, files.ArchiveError, RuntimeError) as error:
        cause = error
        while cause is not None:
            if isinstance(cause, BlockingIOError):
                return dict(status='busy', call_id=call_id, snapshot_token=snapshot_token,
                    binding=None, **({} if unbound_local else dict(external_preparation_state='unknown')))
            cause = cause.__cause__
        raise
