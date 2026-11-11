"""Pi Backend: exact RPC/session/namespace adapters, not a research workflow.

H/W/A/E and owner schemas share the existing control owners. Transport alone
never grants scientific qualification, complete authorship or unknown-call replay.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from types import MappingProxyType

from .backends import Backend, JSON_RESPONSE_INSTRUCTION
from .pi_sessions import (CONTEXT_EVENT, SessionUsage, UsageEvents, context_boundary,
    exact_path, message_identity, session_path, usage_tokens, validate_header, validated_context)

THINKING_LEVELS = frozenset({'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'})
# Offline suppresses automatic catalog/package network activity, NOT package
# resolution itself. The launch owner separately refuses configured package refs
# and custom resolution before any native process; tools/provider keep network.
RESOURCE_FLAGS = ('--offline', '--no-approve', '--no-context-files', '--no-skills', '--no-extensions',
    '--no-mcp', '--no-prompt-templates', '--no-themes')
_INVALID_PROVIDER_FAILURE = object()
DEFAULT_RESUME_POLICY = dict(max_attempts=2, protocol='pi_rpc_v1', usage_scope='turn',
    output_repair=True, output_only=True, execution_wake=True)
PARTICIPANT_SCOPE = 'pi_controlled_session_v1'
PARTICIPANT_PROOF = 'pi_controlled_native_v1'
CATALOG_TIMEOUT_SECONDS = 5
CATALOG_MAX_BYTES = 1_000_000
CATALOG_THINKING_ORDER = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


def _catalog_output(argv, *, base, env):
    """Read bounded CLI metadata; never send a prompt or invoke a model."""
    process = subprocess.Popen(argv, cwd=base, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    chunks = []
    size = 0
    deadline = time.monotonic() + CATALOG_TIMEOUT_SECONDS
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Pi 模型目录读取超时。')
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), min(65536, CATALOG_MAX_BYTES + 1 - size))
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                size += len(data)
                if size > CATALOG_MAX_BYTES:
                    raise ValueError('Pi 模型目录超过可读取的元数据上限。')
                chunks.append(data)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Pi 模型目录读取超时。')
        code = process.wait(timeout=remaining)
        if code != 0:
            raise ValueError('Pi 模型目录命令退出码 ' + str(code))
        return b''.join(chunks).decode('utf-8')
    finally:
        selector.close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()


def resource_mode(value='closed'):
    """Pure explicit selector; unknown modes never fall back to closed."""
    if not isinstance(value, str) or value not in {'closed', 'configured'}:
        raise ValueError('Pi resource_mode must be closed or configured')
    return value


def resource_flags(mode='closed'):
    mode = resource_mode(mode)
    return tuple(flag for flag in RESOURCE_FLAGS
        if mode == 'closed' or flag != '--no-extensions')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _outcome_facts(outcome):
    # Output parsing/repair adds raw_ref/output_status later. It must not erase
    # the author of a malformed final or alter these native-owner facts.
    return {key: outcome.get(key) for key in ('native_backend', 'native_started', 'returncode',
        'stop_reason', 'cleanup', 'thread_id', 'terminal_observed', 'native_failure',
        'native_activity_observed', 'native_thread_event', 'native_schema_transport',
        'native_permissions', 'native_stream', 'terminal_evidence', 'shared_inputs', 'metering')}


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _controlled_transport(value, launch, session, prompt_hash):
    """Pure bridge receipt validation; no executable/session/source probes."""
    from .pi_transport import CONTROLLED_EXIT, _arguments, observation_proof, runtime_resource_mode, resource_proof
    from .pi_observation import validate_start, START, command_proof
    observer = observation_proof(launch['observer'])
    mode = runtime_resource_mode(launch['resource_policy'])
    kind = 'pi_cli_closed_resources_v1' if mode == 'closed' else 'pi_cli_configured_extensions_v1'
    source = launch['observer_source']
    if (set(launch) != {'kind', 'launcher', 'resource_policy', 'resources', 'observer', 'observer_source', 'source_readonly'}
            or launch.get('kind') != kind or launch.get('source_readonly') is not True
            or not isinstance(source, dict) or set(source) != {'version', 'path', 'sha256', 'size_bytes'}
            or type(source.get('version')) is not int or source['version'] != 1
            or source.get('path') != observer['extension'] or not _hash(source.get('sha256')) or type(source.get('size_bytes')) is not int
            or source['size_bytes'] <= 0
            or not isinstance(value, dict) or set(value) - {'turn_inputs'} != {'type', 'version', 'argv', 'session_id',
                'path', 'workspace', 'profile', 'resource_policy', 'resources', 'observer_start', 'observer_command',
                'prompt_sha256', 'disposition', 'settled', 'idle', 'stdin_closed', 'stdout_eof', 'drained',
                'pending_commands', 'returncode'} or value.get('type') != CONTROLLED_EXIT
            or type(value.get('version')) is not int or value['version'] != 1
            or value.get('session_id') != session['thread_id'] or value.get('path') != session['path']
            or value.get('workspace') != session['workspace'] or value.get('profile') != session['profile']
            or value.get('resource_policy') != launch['resource_policy']
            or value.get('resources') != launch['resources']
            or value.get('observer_command') != command_proof(observer['extension'])
            or observer['path'] != session['path'] or observer['workspace'] != session['workspace']
            or not _hash(prompt_hash) or value.get('prompt_sha256') != prompt_hash
            or value.get('disposition') != 'started'
            or value.get('settled') != dict(type='agent_settled', aborted=False)
            or any(value.get(key) is not True for key in ('idle', 'stdin_closed', 'stdout_eof', 'drained'))
            or type(value.get('pending_commands')) is not int or value['pending_commands'] != 0
            or type(value.get('returncode')) is not int or value['returncode'] != 0):
        raise ValueError('pi_authorship_transport_incomplete')
    if runtime_resource_mode(value['resource_policy']) != mode:
        raise ValueError('pi_authorship_resource_profile_changed')
    inputs = value.get('turn_inputs', [])
    seen = set()
    if not isinstance(inputs, list): raise ValueError('pi_authorship_turn_inputs_changed')
    for receipt in inputs:
        if (not isinstance(receipt, dict) or set(receipt) != {'type', 'input_id', 'channel_id',
                'thread', 'turn', 'status', 'payload_sha256'}
                or receipt.get('type') != 'turn.input' or receipt.get('status') != 'accepted'
                or receipt.get('thread') != session['thread_id'] or not _hash(receipt.get('payload_sha256'))
                or not isinstance(receipt.get('turn'), str) or not receipt['turn']
                or any(not isinstance(receipt.get(key), str)
                    or re.fullmatch('[0-9a-f]{32}', receipt[key]) is None for key in ('input_id', 'channel_id'))
                or receipt['input_id'] in seen):
            raise ValueError('pi_authorship_turn_inputs_changed')
        seen.add(receipt['input_id'])
    start = value.get('observer_start')
    if not isinstance(start, dict) or validate_start(dict(start, type=START), observer,
            session=session['thread_id']) != start:
        raise ValueError('pi_authorship_start_changed')
    argv = value.get('argv')
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise ValueError('pi_authorship_launch_changed')
    child, provider, model, thinking, path, skills = _arguments(['--', *argv], observation=observer, resource_mode=mode)
    if (child[0] != launch['launcher'] or path != session['path']
            or dict(model=provider + '/' + model, reasoning_effort=thinking, service_tier=None) != session['profile']
            or skills != tuple(row['path'] for row in resource_proof(launch['resources']))):
        raise ValueError('pi_authorship_launch_changed')


def _participant(outcome, intent, workspace):
    """Malformed/unsupported retained facts refuse without IO or private errors."""
    try:
        return _participant_from_facts(outcome, intent, workspace)
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError('pi_controlled_native_proof_invalid') from error


def _participant_from_facts(outcome, intent, workspace):
    """Validate retained Pi owner facts only, including historical prefixes."""
    from .pi_sessions import validate_resume_binding, validate_session_event
    from .pi_observation import SOURCE
    binding = outcome.get('native_identity') or {}
    proof = binding.get('controlled')
    session = binding.get('session')
    if (binding.get('backend') != 'pi' or binding.get('participant_scope') != PARTICIPANT_SCOPE
            or not isinstance(proof, dict) or set(proof) != {'version', 'kind', 'capability', 'session',
                'launch', 'transport', 'persisted', 'prefix_verified', 'intent_sha256', 'outcome_sha256', 'process'}
            or type(proof.get('version')) is not int or proof['version'] != 1
            or proof.get('kind') != PARTICIPANT_PROOF or proof.get('capability') != PARTICIPANT_SCOPE
            or not isinstance(proof.get('launch'), dict)
            or proof['launch'].get('kind') != 'pi_cli_closed_resources_v1'
            or not isinstance(session, dict) or proof.get('session') != session
            or binding.get('path') != session.get('path') or binding.get('home') != session.get('home')
            or binding.get('session_id') != outcome.get('thread_id')
            or session.get('thread_id') != outcome.get('thread_id')
            or session.get('workspace') != str(workspace) or session.get('profile') != intent.get('native_profile')
            or not _hash(session.get('sha256'))
            or type(session.get('size_bytes')) is not int or session['size_bytes'] <= 0
            or not isinstance(session.get('identity'), list) or len(session['identity']) != 2
            or any(type(value) is not int or value < 0 for value in session['identity'])
            or proof.get('intent_sha256') != _digest(intent)
            or proof.get('outcome_sha256') != _digest(_outcome_facts(outcome))
            or proof.get('prefix_verified') is not True or outcome.get('native_backend') != 'pi'
            or outcome.get('native_started') is not True or type(outcome.get('returncode')) is not int
            or outcome['returncode'] != 0 or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('terminal_observed') is not True or outcome.get('native_failure') is not None
            or outcome.get('stop_reason') is not None or outcome.get('native_activity_observed') is not True
            or (outcome.get('shared_inputs') or {}).get('enforcement') != 'kernel_readonly_mounts'):
        raise ValueError('pi_authorship_owner_binding_changed')
    validate_resume_binding(session, workspace, session['home'])  # lexical/original only; NOT live EOF
    validate_header(session.get('header'), session['thread_id'], workspace)
    event = validate_session_event(outcome.get('native_thread_event'), session['thread_id'])
    if any(event[key] != session[key] for key in ('path', 'workspace', 'profile')):
        raise ValueError('pi_authorship_session_changed')
    persisted = proof['persisted']; user = persisted['user']; delivery = persisted['delivery']
    _controlled_transport(proof['transport'], proof['launch'], session, user['prompt_sha256'])
    if (any(row['turn'] != user['id'] for row in proof['transport'].get('turn_inputs', []))
            or len({row['channel_id'] for row in proof['transport'].get('turn_inputs', [])}) > 1):
        raise ValueError('pi_authorship_turn_input_call_changed')
    def entry_id(value):
        return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value) is not None
    if (set(persisted) - {'supplemental_users'} != {'user', 'leaf', 'baseline_leaf', 'baseline_count', 'baseline_offset',
                'entry_count', 'branch_sha256', 'responses', 'delivery'}
            or set(user) != {'id', 'prompt_sha256'}
            or set(delivery) != {'source', 'nonce', 'attempt', 'sequence', 'acknowledged', 'pending', 'fault', 'terminal'}
            or not entry_id(user.get('id')) or not entry_id(persisted.get('leaf'))
            or persisted.get('baseline_leaf') is not None and not entry_id(persisted['baseline_leaf'])
            or not _hash(persisted.get('branch_sha256'))
            or any(type(persisted.get(key)) is not int or persisted[key] < 0
                for key in ('baseline_count', 'baseline_offset', 'entry_count'))
            or not persisted['baseline_count'] < persisted['entry_count'] <= 65536
            or not persisted['baseline_offset'] < session['size_bytes']
            or delivery.get('source') != SOURCE or delivery.get('nonce') != proof['launch']['observer']['nonce']
            or type(delivery.get('attempt')) is not int or delivery['attempt'] != intent.get('attempt', 0)
            or type(delivery.get('sequence')) is not int or delivery['sequence'] <= 0
            or type(delivery.get('acknowledged')) is not int or delivery['acknowledged'] != delivery['sequence']
            or delivery.get('pending') is not False or delivery.get('fault') is not None
            or delivery.get('terminal') is not True):
        raise ValueError('pi_authorship_persistence_changed')
    prior = intent.get('session') if intent.get('context_mode') != 'new_context' else None
    if prior is not None:
        validate_resume_binding(prior, workspace, session['home'])
        validate_header(prior.get('header'), prior['thread_id'], workspace)
        if (not _hash(prior.get('sha256'))
                or type(prior.get('size_bytes')) is not int or prior['size_bytes'] <= 0
                or not isinstance(prior.get('identity'), list) or len(prior['identity']) != 2
                or any(type(value) is not int or value < 0 for value in prior['identity'])):
            raise ValueError('pi_authorship_resume_changed')
        if (prior['thread_id'] != session['thread_id'] or prior['profile'] != session['profile']
                or prior['path'] != session['path'] or persisted['baseline_offset'] != prior['size_bytes']):
            raise ValueError('pi_authorship_resume_changed')
    elif persisted['baseline_offset'] != 0:
        raise ValueError('pi_authorship_import_unsupported')
    rows = persisted.get('responses')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 65536:
        raise ValueError('pi_authorship_responses_missing')
    ids = set()
    supplemental = persisted.get('supplemental_users', [])
    if not isinstance(supplemental, list): raise ValueError('pi_authorship_supplemental_users_changed')
    input_ids = set()
    for row in supplemental:
        receipt = row.get('receipt') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or set(row) != {'id', 'receipt'} or not entry_id(row.get('id'))
                or row['id'] in ids | {user['id']} or not isinstance(receipt, dict)
                or receipt not in proof['transport'].get('turn_inputs', [])
                or receipt.get('turn') != user['id'] or receipt['input_id'] in input_ids):
            raise ValueError('pi_authorship_supplemental_users_changed')
        ids.add(row['id']); input_ids.add(receipt['input_id'])
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id', 'status', 'profile', 'final'}
                or not entry_id(row.get('id')) or row['id'] in ids | {user['id']}
                or row.get('status') not in {'completed', 'failed', 'cancelled'}
                or row.get('profile') != session['profile']):
            raise ValueError('pi_authorship_response_changed')
        response_final = row['final']
        if response_final is not None and (row['status'] != 'completed'
                or not isinstance(response_final, dict) or set(response_final) != {'sha256', 'size_bytes'}
                or not _hash(response_final.get('sha256')) or type(response_final.get('size_bytes')) is not int
                or response_final['size_bytes'] < 0):
            raise ValueError('pi_authorship_response_final_changed')
        ids.add(row['id'])
    terminal = outcome.get('terminal_evidence') or {}
    final = rows[-1].get('final')
    if (rows[-1]['status'] != 'completed' or not isinstance(final, dict)
            or set(final) != {'sha256', 'size_bytes'} or not _hash(final.get('sha256'))
            or type(final.get('size_bytes')) is not int or final['size_bytes'] < 0
            or final != terminal.get('final') or terminal.get('thread') != session['thread_id']
            or terminal.get('ambiguous') is not False):
        raise ValueError('pi_authorship_final_changed')
    metering = outcome.get('metering') or {}
    if (metering.get('session_binding') != session or metering.get('turn_id') != user['id']
            or metering.get('coverage') != 'terminal'
            or metering.get('metadata_delivery') != {key: delivery[key] for key in ('acknowledged', 'pending', 'fault')}):
        raise ValueError('pi_authorship_terminal_changed')
    return ('pi_native_session', session['thread_id'])


def validate_profile(profile):
    """Admit a literal declaration, not a catalog lookup or native selector.

    Pi tries full IDs before interpreting suffixes or patterns: colons (even
    :high), brackets and nested paths can be literal model IDs. Character
    presence cannot establish substitution. The transport must still set the
    exact provider/model ID and verify native model/thinking before any prompt;
    fuzzy, glob or thinking-suffix fallback to a different ID never qualifies.
    """
    if not isinstance(profile, dict): raise ValueError('Pi profile must be an object')
    model = profile.get('model')
    if (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*/[^\s\x00]+', model)
            or model.endswith('/') or model.split('/', 1)[1].startswith('/')):
        raise ValueError('Pi requires an exact provider/model-id without NUL or whitespace')
    if profile.get('service_tier') is not None: raise ValueError('Pi does not support service_tier')
    if not isinstance(profile.get('reasoning_effort'), str) or profile['reasoning_effort'] not in THINKING_LEVELS:
        raise ValueError('Pi requires an explicit supported thinking level')
    return profile


def validate_launcher(launcher):
    if (not isinstance(launcher, (tuple, list)) or len(launcher) != 1
            or not isinstance(launcher[0], str) or not launcher[0]
            or '\0' in launcher[0] or launcher[0].startswith('-')):
        raise ValueError('Pi requires a single executable launcher without arguments')


def validate_configuration(options):
    if isinstance(options, dict) and 'pi_extensions' in options:
        raise ValueError('Pi requires explicit supported permissions: user extensions are unsupported because native hooks can add web tools, subagents and model calls; exact paths do not attest web=disabled or subagents=false')
    if not isinstance(options, dict):
        raise ValueError('Pi requires explicit supported permissions')
    mode = resource_mode(options.get('resource_mode', 'closed'))
    expected = dict(network=True, web='live' if mode == 'configured' else 'disabled', subagents=False)
    if (set(options) - {'resource_mode'} != {*expected, 'workspace'}
            or not isinstance(options.get('workspace'), str) or options['workspace'] not in {'read-only', 'workspace-write'}
            or any(type(options[k]) is not type(v) or options[k] != v for k, v in expected.items())):
        raise ValueError('Pi requires explicit workspace=read-only or workspace-write, network=true, subagents=false; closed requires web=disabled, configured requires web=live')
    return dict(options)


def validate_preparation_contract(settings):
    """Pure permissions/launcher admission for raw or prepared declarations.

    Public normalization supplies ``launcher``; controller/prepared settings
    supply ``native_launcher_argv``. Omission uses the provider's static argv,
    never executable discovery, native home, project or executor observations.
    """
    launcher = settings.get('native_launcher_argv', settings.get('launcher'))
    if launcher is None:
        launcher = Pi.default_launcher()
    if ('native_launcher_argv' in settings and 'launcher' in settings
            and settings['native_launcher_argv'] != settings['launcher']):
        raise ValueError('Pi launcher declarations conflict')
    validate_launcher(launcher)
    options = validate_configuration(settings.get('native_options'))
    mode = settings.get('experiment_mode')
    if settings.get('execution_config') is not None:
        if mode == 'external':
            raise ValueError('Pi execution config conflicts with external launcher-owned execution')
        if mode is None:
            mode = 'local'
    if mode is None:
        mode = Pi.default_experiment_mode()
    if mode == 'local' or settings.get('project') is not None:
        if (options['workspace'] != 'workspace-write'
                or (settings.get('native_capabilities') or {}).get('native_launcher_contract') is not None):
            requirement = 'Pi Local requires' if mode == 'local' else 'Pi projects require'
            raise ValueError(requirement + ' an explicit workspace-write policy without a custom launcher contract')


def validate_settings(settings, *, require_profile=False, prepared=False):
    """Pure declarations, including unused roles/named Workers and policies.

    Preparation consumes the owner's normalized role rows, never reloads config
    or repeats its generic normalization. Raw public declarations are normalized
    only at their earlier declaration-admission boundary.
    """
    from . import profiles
    if require_profile or 'model' in settings:
        validate_profile({k: settings.get(k) for k in profiles.FIELDS})
        rows = ((settings.get('role_profiles') or {}) if prepared else
            profiles.normalize(settings.get('role_profiles') or {}, settings,
                settings.get('worker_profiles'), (settings.get('skills') or {}).get('sets', {})))
        for row in rows.values():
            validate_profile(row)
            name = row.get('skill_set')
            if name is not None and (not isinstance(name, str)
                    or name not in (settings.get('skills') or {}).get('sets', {})):
                raise ValueError('Pi role references an unknown Skill set')
        for worker in (settings.get('worker_profiles') or {}).values():
            validate_profile({**{k: settings.get(k) for k in profiles.FIELDS}, **worker})
            name = worker.get('skill_set')
            if name is not None and (not isinstance(name, str)
                    or name not in (settings.get('skills') or {}).get('sets', {})):
                raise ValueError('Pi named Worker references an unknown Skill set')
    if settings.get('experiment_mode') not in (None, 'external', 'local'):
        raise ValueError('Pi requires an explicit supported experiment mode')
    resume = settings.get('resume_policy')
    if resume is not None:validate_resume_policy(resume)
    fragment = settings.get('worker_fragment_policy')
    if fragment is not None:
        from . import checkpoint
        checkpoint.policy(fragment)

    # Scientific policies belong to their common control owners. Declaration
    # admission is not a review verdict, complete authorship or execution grant.
    from . import skill_supply
    declared = settings.get('skills')
    if declared is not None:
        if isinstance(declared, dict) and 'version' in declared: skill_supply._current_binding(declared)
        else: skill_supply.configuration(declared)


def validate_resume_policy(value):
    """Provider protocol only; continuation owns finite attempts and permissions."""
    if (not isinstance(value, dict) or value.get('protocol') != 'pi_rpc_v1'
            or value.get('usage_scope') != 'turn' or type(value.get('max_attempts')) is not int
            or not 1 <= value['max_attempts'] <= 2
            or set(value) - {'protocol', 'usage_scope', 'max_attempts', 'output_repair', 'output_only',
                'execution_wake', 'automatic_recovery', 'launcher_files', 'native_home'}
            or any(k in value and type(value[k]) is not bool for k in ('output_repair', 'output_only', 'execution_wake'))
            or value.get('output_only') and not value.get('output_repair')):
        raise ValueError('Pi resume requires finite attempts, protocol=pi_rpc_v1 and usage_scope=turn')
    if 'native_home' in value:
        raise ValueError('Pi private session storage cannot redirect inherited authentication')
    if 'automatic_recovery' in value:
        from . import native_recovery
        native_recovery.policy(value['automatic_recovery'])


def _provider_failure_body(text):
    """Original Pi diagnostic allowlist; prose never supplies retry authority."""
    from . import native_recovery as recovery
    from .pi_sessions import decode
    if not isinstance(text, str): return None
    prefix = re.match(r'([45][0-9]{2})(?:: | )(?=\{)', text)
    if not prefix and not text.lstrip().startswith('{'): return None
    if len(text) > recovery._JSON_ERROR_MESSAGE_LIMIT: return _INVALID_PROVIDER_FAILURE
    status = int(prefix[1]) if prefix else None
    try: body = decode(text[prefix.end():] if prefix else text)
    except (ValueError, UnicodeError, RecursionError): return _INVALID_PROVIDER_FAILURE
    if not isinstance(body, dict): return None
    envelope = body.get('type') == 'error' and isinstance(body.get('error'), dict)
    if not prefix and not envelope:
        return _INVALID_PROVIDER_FAILURE if body.get('type') == 'error' else None
    if not envelope and any(isinstance(body.get(k), dict) for k in ('error', 'response')):
        return _INVALID_PROVIDER_FAILURE
    statuses = {status} if status is not None else set()
    layer_count = 0
    known = recovery._PERMANENT | recovery._TEMPORARY | recovery._INCOMPLETE
    def fields(layer):
        result = {}
        for key in ('code', 'type', 'error_type'):
            value = layer.get(key)
            if not isinstance(value, str): continue
            value = value.lower()
            if value in known: result[key] = value
            elif value.endswith('spend_limit_exceeded'): result[key] = 'usage_limit_exceeded'
        for key in ('status', 'status_code', 'http_status_code'):
            if key not in layer: continue
            value = layer[key]
            if type(value) is not int or not 100 <= value <= 599: raise ValueError('invalid_provider_status')
            statuses.add(value); result[key] = value
        return result
    def project(envelope, depth):
        nonlocal layer_count
        layers = recovery._error_layers(envelope); layer_count += len(layers)
        if layer_count > recovery._JSON_ERROR_MAX_LAYERS: raise ValueError('provider_error_layer_limit')
        safe = fields(envelope)
        for key in ('error', 'response'):
            child = envelope.get(key)
            if isinstance(child, dict):
                safe[key] = fields(child)
                if isinstance(child.get('error'), dict): safe[key]['error'] = fields(child['error'])
        safe['type'] = 'error'
        for layer, target in zip(layers, recovery._error_layers(safe)):
            message = layer.get('message')
            if not isinstance(message, str) or not message.lstrip().startswith('{'): continue
            if len(message) > recovery._JSON_ERROR_MESSAGE_LIMIT or depth >= recovery._JSON_ERROR_MAX_DEPTH:
                raise ValueError('provider_error_message_limit')
            nested = decode(message)
            if isinstance(nested, dict) and nested.get('type') == 'error':
                if not isinstance(nested.get('error'), dict): raise ValueError('invalid_provider_error_envelope')
                target['message'] = json.dumps(project(nested, depth + 1), ensure_ascii=True, separators=(',', ':'))
        return safe
    try: safe = project(body if envelope else dict(type='error', error=body), 1)
    except (ValueError, UnicodeError, RecursionError): return _INVALID_PROVIDER_FAILURE
    if len(statuses) > 1: return _INVALID_PROVIDER_FAILURE
    if status is not None: safe['http_status_code'] = status
    return safe, statuses, layer_count


def failure_projection(source, *, reason=None, event_type=None):
    from . import native_recovery as recovery
    kind = event_type or ('turn.incomplete' if reason == 'length' else 'turn.interrupted' if reason == 'aborted' else 'error')
    event = dict(type=kind)
    keys = ('code', 'error_type', 'status', 'status_code', 'http_status_code',
        'retry_after', 'retry_after_seconds', 'accepted', 'request_started')
    for key in keys:
        if key in source: event[key] = source[key]
    for key in ('error', 'response'):
        if isinstance(source.get(key), dict):
            child = source[key]
            event[key] = {k: child[k] for k in (*keys, 'type') if k in child}
            if isinstance(child.get('error'), dict):
                event[key]['error'] = {k: child['error'][k] for k in (*keys, 'type') if k in child['error']}
    layers = recovery._error_layers(event)
    if any(layer.get('accepted') is True or layer.get('request_started') is True for layer in layers):
        for layer in layers: layer.pop('accepted', None); layer.pop('request_started', None)
    diagnostics = [_provider_failure_body(source.get(k)) for k in ('errorMessage', 'finalError', 'message')]
    bodies = [b for b in diagnostics if b is not None and b is not _INVALID_PROVIDER_FAILURE]
    statuses = {v for layer in layers for k in ('status', 'status_code', 'http_status_code') if type(v := layer.get(k)) is int}
    statuses.update(s for _, observed, _ in bodies for s in observed)
    rejected = (any(b is _INVALID_PROVIDER_FAILURE for b in diagnostics) or len(statuses) > 1
        or sum(count for _, _, count in bodies) > recovery._JSON_ERROR_MAX_LAYERS)
    if not rejected and kind in {'error', 'turn.failed'}:
        for slot, (body, _, _) in zip((None, 'error', 'response'), bodies):
            target = event if slot is None else event.setdefault(slot, {})
            target['message'] = json.dumps(body, ensure_ascii=True, separators=(',', ':'))
    failure = recovery.classify(event)
    # Exact native SDK diagnostics only: an assistant/tool's ordinary text is
    # never inspected, and a connection failure proves no request rejection.
    if (not rejected and kind in {'error', 'turn.failed'}
            and any(source.get(key) == 'fetch failed' for key in ('errorMessage', 'finalError'))):
        connection = recovery.classify(dict(type=kind, code='connection_error'))
        failure = dict(failure, category=recovery.combine([failure, connection])['category'],
            codes=sorted(set(failure['codes']) | {'connection_error'}),
            message_patterns=sorted(set(failure['message_patterns']) | {'pi_native_fetch_failed_v1'}))
    if rejected and kind in {'error', 'turn.failed'} and failure['category'] != 'permanent': failure['category'] = 'unknown'
    return failure


class PiEvents:
    """Pure native event/recovery engine, separate from the filesystem meter."""
    def __init__(self):
        self.usage = UsageEvents()
        self.binding = self.profile = self.session = self.final = None
        self.seen_messages = set(); self.seen_tools = set(); self.seen_entries = set()
        self.tools = set(); self.open_message = self.compacting = self.summarizing = False
        self.settled = self.accounting_only = self.failed = self.retrying = self.retry_final = False
        self.failures = []; self.recovering = set(); self.recovered = set()
        self.unidentified_compaction = False
        self.identified_compaction_tokens = dict.fromkeys(self.usage.live['compaction'], 0)
        self.turn_input_payloads = set()

    def failure(self, source=None, *, kind='turn.failed', fatal=True, activity=True):
        failure = failure_projection(source or {}, event_type=kind)
        self.failed = self.failed or fatal or failure['category'] not in {'temporary', 'incomplete'}
        self.final = None; self.failures.append(failure)
        return dict(failed=True, activity=activity, failure=failure, terminal_failure=False)

    def combine(self, failures, *, completed_before=None):
        from . import native_recovery
        retained = [f for i, f in enumerate(failures)
            if completed_before is None or i >= completed_before or i not in self.recovered]
        blocking = [f for f in retained if f['category'] in {'permanent', 'unknown'}]
        return native_recovery.combine(blocking or retained)

    def authorize_recovery(self, *, length=False):
        for i, f in enumerate(self.failures):
            if f['category'] == ('incomplete' if length else 'temporary') and f['event_type'] == ('turn.incomplete' if length else 'error'):
                self.recovering.add(i)

    def message(self, message, *, accounting_only=False):
        if not isinstance(message, dict): return self.failure()
        role = message.get('role')
        if role == 'toolResult':
            if 'usage' in message:
                try: identity = message_identity(message)
                except ValueError:
                    self.usage.floor(message['usage'], category='toolResult')
                    self.usage.unknown('pi_tool_usage_identity_unavailable'); return self.failure()
                if identity in self.seen_tools:
                    self.usage.floor(message['usage'], category='toolResult'); return self.failure()
                self.seen_tools.add(identity)
                self.usage.add(message['usage'], category='toolResult', identity=identity)
            if not accounting_only: self.final = None
            return dict(activity=True)
        if role != 'assistant':
            return dict(activity=True) if role in {'user', 'system', 'custom', 'bashExecution', 'compactionSummary', 'branchSummary'} else self.failure()
        try: identity = message_identity(message)
        except ValueError:
            self.usage.floor(message.get('usage')); self.usage.unknown('pi_message_identity_unavailable'); return self.failure()
        if identity in self.seen_messages:
            if accounting_only: self.usage.floor(message.get('usage'))
            return self.failure()
        self.seen_messages.add(identity); self.usage.add(message.get('usage'), identity=identity)
        if accounting_only: return dict(activity=True)
        if self.profile is None: return self.failure()
        provider, model = self.profile['model'].split('/', 1)
        if (message.get('provider') != provider or message.get('model') != model
                or message.get('thinkingLevel', self.profile['reasoning_effort']) != self.profile['reasoning_effort']): return self.failure()
        reason, content = message.get('stopReason'), message.get('content')
        self.final = None
        if reason in {'length', 'aborted', 'error', 'failed'}:
            return self.failure(message, kind={'length': 'turn.incomplete', 'aborted': 'turn.interrupted', 'error': 'error', 'failed': 'turn.failed'}[reason], fatal=False)
        if reason not in {'stop', 'toolUse'} or not isinstance(content, list): return self.failure()
        if any(not isinstance(b, dict) or b.get('type') not in {'text', 'thinking', 'toolCall'}
                or b.get('type') == 'text' and not isinstance(b.get('text'), str) for b in content): return self.failure()
        if reason == 'stop':
            if any(b['type'] == 'toolCall' for b in content): return self.failure()
            self.final = ''.join(b['text'] for b in content if b['type'] == 'text')
            if self.retrying: self.retry_final = True
            else: self.recovered.update(self.recovering); self.recovering.clear()
        return dict(activity=True)

    def entry_usage(self, entry, *, accounting_only=False):
        category = entry['type']; usage = entry.get('usage')
        if category == 'compaction':
            converted = usage_tokens(usage)
            if converted:
                for k, v in converted.items(): self.identified_compaction_tokens[k] += v
            # Entry mirrors and compaction_end are overlapping views. Distinct
            # entry IDs prove an aggregate floor, never another additive debit.
            self.usage.floor(usage, category=category)
            for k, v in self.identified_compaction_tokens.items():
                self.usage.floors[category][k] = max(self.usage.floors[category][k], v)
            return
        self.usage.add(usage, category=category)

    def accounting_event(self, value):
        kind = value['type']
        if kind == 'message_end': self.message(value.get('message'), accounting_only=True)
        elif kind == 'message_update' and 'usage' in value: self.usage.floor(value['usage'])
        elif kind == 'compaction_end':
            result = value.get('result')
            if isinstance(result, dict): self.usage.floor(result.get('usage'), category='compaction')
            else: self.usage.unknown('pi_compaction_usage_unknown')
        elif kind == 'entry_appended':
            entry = value.get('entry')
            if isinstance(entry, dict):
                category = entry.get('type')
                if category == 'message': self.message(entry.get('message'), accounting_only=True)
                elif category in {'usage', 'compaction', 'branch_summary'}:
                    identity = entry.get('id')
                    if not isinstance(identity, str) or not identity or identity in self.seen_entries:
                        self.usage.floor(entry.get('usage'), category=category)
                    else: self.seen_entries.add(identity); self.entry_usage(entry, accounting_only=True)
                elif 'usage' in entry: self.usage.unknown('pi_unknown_entry_usage')
        return self.failure(activity=kind != 'agent_settled')

    def event(self, value):
        kind = value.get('type')
        if not isinstance(kind, str): return self.failure()
        if kind == CONTEXT_EVENT:
            self.usage.context = (validated_context(value.get('context')) if set(value) == {'type', 'context'}
                and self.session is not None and not self.accounting_only and not self.settled else dict(status='unavailable'))
            return {}
        if context_boundary(value)[0] or kind == 'pi_transport_fault': self.usage.context = dict(status='unavailable')
        if kind == 'pi_transport_fault':
            from .pi_transport import FAULT_CODES
            code = value.get('code')
            reason = 'pi_transport_fault'
            if isinstance(code, str) and code in FAULT_CODES: reason += ':' + code
            self.accounting_only = True; self.usage.unknown(reason); return self.failure()
        if kind == 'queue_update':
            steering = value.get('steering')
            return (dict(activity=True) if isinstance(steering, list) and value.get('followUp') == []
                and all(isinstance(text, str) and text in self.turn_input_payloads for text in steering)
                and len(steering) == len(set(steering))
                else self.failure())
        if self.accounting_only or self.settled:
            self.accounting_only = True; self.usage.unknown('pi_activity_after_settlement'); return self.accounting_event(value)
        if kind == 'pi.session_bound':
            from .pi_sessions import validate_session_event
            try:
                validate_session_event(value, self.session)
                if (self.binding is None or value['path'] != self.binding['path']
                        or value['workspace'] != self.binding['workspace'] or value['profile'] != self.profile
                        or getattr(self, 'thread_event', None) is not None):raise ValueError('binding changed')
            except (ValueError, TypeError):return self.failure()
            self.thread_event = dict(value)
            return dict(thread_event=dict(value))
        if kind == 'session':
            try:
                if self.session is not None or self.binding is None: raise ValueError('binding missing')
                validate_header(json.dumps(value), value.get('id'), self.binding['workspace'])
            except (ValueError, TypeError): return self.failure()
            self.session = value['id']; return dict(session=self.session)
        if kind == 'message_start':
            if self.open_message: return self.failure()
            self.open_message = True
            if isinstance(value.get('message'), dict) and value['message'].get('role') == 'assistant': self.usage.begin()
            self.final = None; return dict(activity=True)
        if kind == 'message_end': self.open_message = False; return self.message(value.get('message'))
        if kind == 'message_update':
            if 'usage' in value: self.usage.partial(value['usage'])
            update = value.get('assistantMessageEvent')
            if isinstance(update, dict) and update.get('type') == 'error':
                return self.failure(update.get('error') if isinstance(update.get('error'), dict) else {},
                    kind='turn.interrupted' if update.get('reason') == 'aborted' else 'error', fatal=False)
            self.final = None; return dict(activity=True)
        if kind in {'tool_execution_start', 'tool_execution_end'}:
            call = value.get('toolCallId')
            if not isinstance(call, str) or not call: return self.failure()
            if kind == 'tool_execution_start':
                if call in self.tools: return self.failure()
                self.tools.add(call)
            else:
                if call not in self.tools and value.get('isError') is not True: return self.failure()
                self.tools.discard(call)
            self.final = None; return dict(activity=True, tool_action=True)
        if kind == 'compaction_start':
            if self.compacting: return self.failure()
            self.compacting = True; return dict(activity=True)
        if kind == 'compaction_end':
            if not self.compacting: return self.failure()
            self.compacting = False; result = value.get('result')
            if value.get('aborted') is not False or not isinstance(result, dict) or value.get('errorMessage'):
                self.usage.unknown('pi_compaction_usage_unknown'); return self.failure(value, kind='error', fatal=False)
            self.usage.add(result.get('usage'), category='compaction'); self.unidentified_compaction = True
            self.summarizing = False
            if value.get('willRetry') is True: self.authorize_recovery(length=True); self.final = None
            return dict(activity=True)
        if kind == 'agent_settled':
            self.settled = True
            if value.get('aborted') is True: return self.failure(value, kind='turn.interrupted')
            if self.summarizing: self.usage.unknown('pi_summary_usage_unknown'); return self.failure()
            if (value.get('aborted') is False and self.session is not None and self.final is None
                    and self.failures and not self.open_message and not self.tools and not self.compacting): return dict(failed=True)
            if (value.get('aborted') is not False or self.failed or self.session is None or self.final is None
                    or self.open_message or self.tools or self.compacting): return self.failure()
            return dict(completed=True, result_text=self.final)
        if kind in {'error', 'auto_retry_end'}:
            if kind == 'auto_retry_end' and value.get('success') is True:
                if self.retrying and self.retry_final: self.recovered.update(self.recovering); self.recovering.clear()
                self.retrying = False; return dict(activity=True)
            return self.failure(value, kind='error', fatal=False, activity=kind != 'error' or self.session is not None)
        if kind in {'turn_failed', 'turn_incomplete', 'turn_interrupted', 'turn.failed', 'turn.incomplete', 'turn.interrupted'}:
            return self.failure(value, kind=kind.replace('turn_', 'turn.'), fatal=False)
        if kind == 'extension_error': return self.failure()
        if kind == 'entry_appended':
            entry = value.get('entry')
            if not isinstance(entry, dict): return self.failure()
            category = entry.get('type')
            if category in {'usage', 'compaction', 'branch_summary'}:
                identity = entry.get('id')
                if not isinstance(identity, str) or not identity or identity in self.seen_entries: return self.failure()
                self.seen_entries.add(identity); self.entry_usage(entry)
                if category == 'branch_summary': self.summarizing = False
            elif 'usage' in entry:
                self.usage.unknown('pi_unknown_entry_usage'); return self.failure()
            return dict(activity=True)
        if kind in {'session_info_changed', 'thinking_level_changed'}: return dict(activity=True)
        if kind in {'summarization_retry_scheduled', 'summarization_retry_attempt_start', 'summarization_retry_finished'}:
            self.summarizing = True; return dict(activity=True)
        if kind == 'auto_retry_start':
            self.authorize_recovery(); self.retrying = True; self.retry_final = False; self.final = None
            return dict(activity=True)
        if kind in {'agent_start', 'turn_start', 'tool_execution_update'}: self.final = None; return dict(activity=True)
        if kind in {'agent_end', 'turn_end'}:
            if kind == 'agent_end' and value.get('willRetry') is True: self.final = None
            return dict(activity=True)
        return self.failure()


class PiEventSelector:
    """Strict LF/JSON framing with no native record byte quota."""
    def __init__(self):
        from .pi_sessions import LFRecords
        self.framing = LFRecords()
        self.pending = self.framing.pending

    def feed(self, data, *, final=False):
        from .pi_sessions import decode
        events = []
        for line in self.framing.feed(data):
            try:
                value = decode(line)
                if not isinstance(value, dict): raise ValueError('object required')
            except (ValueError, UnicodeError, RecursionError): value = dict(type='pi_transport_fault')
            events.append(value)
        if final and self.pending:
            events.append(dict(type='pi_transport_fault')); self.pending.clear()
        return events


class Pi(Backend):
    name = 'pi'
    standard = supports_native_schema_flag = False

    @property
    def complete_authorship(self):
        try: validate_configuration(self.settings.get('native_options'))
        except ValueError: return False
        if self.settings['native_options'].get('resource_mode', 'closed') != 'closed':
            return False
        retained = self.settings.get('native_capabilities')
        return (retained.get('complete_native_authorship') is True if isinstance(retained, dict)
            else 'native_capabilities' not in self.settings and 'goal_id' not in self.settings)
    supports_resume = True
    instruction_transport = schema_transport = active_schema_transport = 'prompt'
    terminal_source = 'pi.agent_settled'

    def __init__(self, settings, *, schema_transport=None):
        validate_launcher(settings['native_launcher_argv'])
        if 'native_options' in settings: validate_configuration(settings['native_options'])
        if schema_transport not in (None, 'prompt'): raise ValueError('Pi owns prompt response schema transport')
        validate_settings(settings)
        self.settings = settings; self.launcher = list(settings['native_launcher_argv'])
        self.engine = PiEvents(); self._binding = None; self._profile = None; self._meter = None
        self._args = None; self._session = self._resume_original = None
        self._observation_proof = None
        self._observer_staged = False
        self._observer_source = self._launch_evidence = self._controlled_exit = None
        self._job = None
        self._notice_channel = None
        self._notice_writable = False
        self._turn_input = None

    def begin_turn_input(self, root, job, work, attempt, *, task_ref, base):
        from . import turn_input
        self._turn_input = turn_input.begin(root, job, work, attempt, task_ref=task_ref, base=base)
        return self._turn_input

    def poll_turn_input(self, context, *, thread=None, turn=None, event=None):
        super().poll_turn_input(context, thread=thread, turn=turn, event=event)
        if event is not None and event.get('status') == 'accepted':
            from .turn_input import request_for_receipt
            request = request_for_receipt(context, event)
            if request is not None and self._meter is not None and self._meter.observation is not None:
                self._meter.observation.turn_inputs[request['input_id']] = dict(event)

    @staticmethod
    def default_launcher(): return ['pi']
    @classmethod
    def model_catalog_context(cls, **declaration):
        context = super().model_catalog_context(**declaration)
        validate_launcher(context['launcher'])
        native = context['native']
        if native is not None:
            native = cls.declaration_options(native, base=context['base'])
        context['resource_mode'] = resource_mode((native or {}).get('resource_mode', 'closed'))
        launcher = context['launcher'][0]
        if '/' in launcher or launcher.startswith('~'):
            # Preserve the invoked entry point, as the native launcher owner
            # does; resolving a symlink can change wrapper/runtime behavior.
            launcher = os.path.abspath(Path(context['base']) / Path(launcher).expanduser())
        else:
            launcher = shutil.which(launcher)
            if launcher is None:
                raise ValueError('未找到所选 Pi 启动器；请填写精确 provider/model-id。')
            launcher = str(Path(launcher).absolute())
        context['launcher'] = [launcher]
        context['home'] = str(Path.home())
        context['agent_directory'] = str((Path(context['base']) / cls.default_home().expanduser()).resolve())
        return context
    @classmethod
    def model_catalog(cls, context):
        from .model_catalog import unavailable
        env = dict(os.environ, HOME=context['home'], PI_OFFLINE='1', PI_CODING_AGENT_DIR=context['agent_directory'])
        try:
            from .pi_transport import load_resource_settings
            # Reuse the declared runtime's package admission. Configured cached
            # packages remain native-owned; offline suppresses install/refresh,
            # and the metadata-only command keeps extension loading disabled.
            load_resource_settings(context['agent_directory'], context['base'], mode=context['resource_mode'])
            output = _catalog_output([*context['launcher'], *resource_flags('closed'), '--list-models'],
                base=context['base'], env=env)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return unavailable('读取 Pi 模型列表失败：' + str(error))
        lines = [line.split() for line in output.splitlines() if line.strip()]
        if not lines or lines[0][:2] != ['provider', 'model']:
            return unavailable('无法识别所选 Pi 启动器的模型列表格式。')
        if len(lines) > 4097:
            return unavailable('Pi 模型目录条目超过上限。')
        header = lines[0]
        thinking = header.index('thinking') if 'thinking' in header else None
        models = []
        for row in lines[1:]:
            if len(row) < 2:
                continue
            supports = thinking is not None and len(row) > thinking and row[thinking] == 'yes'
            identity = row[0] + '/' + row[1]
            models.append(dict(id=identity, label=identity,
                efforts=list(CATALOG_THINKING_ORDER) if supports else ['off'], default_effort=None, tiers=[]))
        return dict(models=models, source='所选 Pi 启动器 --list-models',
            fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            error=None if models else 'Pi 没有列出可用模型。')
    @staticmethod
    def profile_defaults(): return dict(model=None, reasoning_effort=None, service_tier=None)
    @staticmethod
    def declaration_options(options, *, base):
        if not isinstance(options, dict) or set(options) - {'resource_mode'}:
            raise ValueError('Pi agent.native supports only explicit resource_mode=closed or configured')
        if 'resource_mode' in options: resource_mode(options['resource_mode'])
        return dict(options)
    @staticmethod
    def validate_configuration(options): return validate_configuration(options)
    @classmethod
    def validate_declaration(cls, settings):
        # Do not load project Git facts, Skills or executor plugins merely to
        # discover that this provider cannot honor their explicit declaration.
        validate_preparation_contract(settings)
        validate_settings(settings, require_profile=True)
    def validate_preparation(self, settings):
        validate_preparation_contract(settings)
        validate_settings(settings, require_profile=True, prepared=True)
    @staticmethod
    def configuration_fields():
        # The shared form consumes these provider descriptions; it must still
        # submit explicit permission declarations, never pre-grant defaults.
        return (
            dict(path='agent.native.resource_mode', label='Pi 原生资源', choices=('closed', 'configured'),
                description='缺省 closed；configured 允许原设置中的已安装原生包/扩展，须 web=live、subagents=false；扩展行为由用户信任，不代表完整调用计量。'),
            dict(path='agent.model', label='Pi 精确模型', required=True, placeholder='provider/model-id',
                description='必须填写完整精确 provider/model-id（可含字面冒号或方括号）；thinking 单独声明，不接受模糊、通配或后缀解析替换模型。'),
            dict(path='agent.reasoning_effort', label='Pi thinking', required=True,
                choices=('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
                description='必须明确选择模型支持的 thinking level；不沿用其他后端默认值。'),
            dict(path='agent.service_tier', label='Pi 服务档位',
                description='Pi 不支持 service_tier；保持未声明。'),
            dict(path='permissions.workspace', label='Pi 文件系统权限', required=True,
                choices=('workspace-write', 'read-only'), placeholder='workspace-write',
                description='默认采用 Local 执行，必须明确选择 workspace-write。read-only 仅支持显式 external，不能扩大 Local 写权限。'),
            dict(path='permissions.network', label='Pi 网络权限', kind='boolean', required=True,
                description='必须明确允许联网（true）；Pi 不支持禁网投影。'),
            dict(path='permissions.web', label='Pi 网页检索', choices=('disabled', 'live'), required=True,
                description='closed 要求 disabled；configured 要求 live，允许可信原生扩展联网；不限制 bash 或 provider 的网络访问。'),
            dict(path='permissions.subagents', label='Pi 子助手', kind='boolean', required=True,
                description='两种 profile 均须 false：不请求 Backend 原生委派。configured 的可信扩展仍可能另开调用/进程，false 不证明其行为受禁。'),
        )
    @staticmethod
    def default_home():
        from .pi_namespace import agent_directory
        return agent_directory()
    @staticmethod
    def resume_policy_defaults(): return dict(DEFAULT_RESUME_POLICY)
    def default_resume_policy(self):
        import shutil
        from . import native_recovery
        invoked = shutil.which(self.launcher[0])
        if invoked is None:
            raise ValueError('The selected Pi native launcher is unavailable')
        # Preserve the invoked entry point. The existing continuation owner
        # freezes its exact executable originals and validates finite grammar.
        # Private session storage never redirects inherited Pi authentication.
        return dict(launcher=[str(Path(invoked).absolute())], policy=dict(self.resume_policy_defaults(),
            automatic_recovery=native_recovery.policy({})))
    def validate_resume_policy(self, value): return validate_resume_policy(value)
    @staticmethod
    def default_experiment_mode(): return 'local'
    def public_options(self, options): return dict(validate_configuration(options))
    def permissions(self, settings):
        options = validate_configuration(settings.get('native_options'))
        from .pi_transport import resource_policy
        configured = options.get('resource_mode', 'closed') == 'configured'
        declared = {key: options[key] for key in ('workspace', 'network', 'web', 'subagents')}
        result = dict(requested=declared, effective=dict(declared),
            resource_policy=resource_policy(options.get('resource_mode', 'closed')),
            enforcement=dict(workspace=('host_readonly_mounts' if options['workspace'] == 'read-only'
                else 'host_workspace_mounts_with_readonly_originals'), network='unrestricted',
                web='no_extensions', subagents='no_default_subagent_extension'),
            limitations=['Linux readonly mounts are required; no unsandboxed fallback.',
                'The framework-owned workspace root is readonly, including absent publications; workspace-write grants work/, artifacts/ and experiments/ plus the current W code draft and declared project views, with exact original overlays taking precedence.',
                'Published work/ controls are readonly; bind mounts alone cannot reserve absent names inside that writable scientific slot.',
                'Network is unrestricted, including bash; web=disabled disables dedicated plugins, not network. --offline (PI_OFFLINE=1 in Pi) disables automatic resource/catalog network activity, not explicit provider API requests or tool network access.',
                'NativeRuntimeProfile limitation: the current exact inherited agent settings.json and workspace .pi/settings.json must have absent or empty packages and no custom npmCommand. Pi resolves packages before --no-* filtering; offline alone can still invoke a configured npm command for legacy global lookup. Both scopes are checked before native startup, even with no selected Skills or --no-approve; no config copy or user-config edit is performed. Concurrent same-user configuration changes remain trusted.',
                'User extensions remain unsupported: arbitrary hooks cannot attest web=disabled or subagents=false. Only the exact readonly framework observer is admitted through native command-source provenance and private startup framing; it adds no tool, provider, prompt or continuation.',
                'Inherited HOME/PI_CODING_AGENT_DIR auth/runtime stays unchanged and writable, not the entire HOME; private sessions are separate.',
                'Same-user processes remain trusted; not a confidentiality or hostile-worker boundary.',
                'Automatic project context and resource loading are disabled; only exact selected installation SKILL.md files load, verified through native get_commands before the single prompt. NativeRuntimeProfile also requires absent SYSTEM.md and APPEND_SYSTEM.md at the exact inherited agent directory and workspace .pi directory: --no-context-files does not suppress them and get_commands does not expose them. These four candidates are checked without reading contents before any native startup; no replacement system prompt removes Pi default tool instructions. The SDK may still discover local metadata before filtering; this is not a claim that its discovery code never runs.',
                'Local uses the existing accepted execution bridge with an isolated empty scientific HOME; it inherits no Pi authentication or private session directory.',
                'Projects expose only declared editable working copies and scratch/output slots over a readonly base; selected live Skill bundles and all their workspace inode aliases remain readonly, preserving executable modes.',
                'Skill selection freezes bundle directory identities, not manual/script bytes or native names. The native SDK parses live SKILL.md YAML; get_commands must expose one valid named/described Skill per exact selected manual with original local/temporary/top-level provenance and its own baseDir. Missing, colliding or unselected sources fail closed before the prompt. Selected resource symlinks are unsupported, including managed Local with instruction loading excluded.',
                'Formal response transport supplies no scientific qualification or complete authorship.',
                'Native context occupancy is an SDK estimate qualified at persisted native boundaries, not cumulative billing. Exhaustive native calls remain unsupported: auxiliary summarization, retries and warming are not fully receipted; response receipts are only a lower bound.'])
        if configured:
            result['enforcement'].update(web='trusted_native_extensions', subagents='extension_dependent')
            result['effective']['subagents'] = None
            result['limitations'] = [
                'Configured mode loads original cached native packages/extensions with --offline and the SDK default resolver; custom npmCommand remains unsupported. No settings/auth copies or automatic package installation are supplied by the framework.',
                'web=live permits trusted native extensions. subagents=false requests no Backend native delegation; extensions may create independent calls/processes, so their delegation behavior is unknown and Backend native subagents remain unsupported.',
                'Readonly originals and Linux namespace mounts remain required. Native authentication/runtime stays in the original writable agent directory; scientific Local children retain an isolated empty HOME without native authentication.',
                'Automatic Skills, prompt templates, themes, MCP and context files remain disabled; selected framework Skills and the readonly observer retain exact source validation. The four inherited SYSTEM.md/APPEND_SYSTEM.md candidates must remain absent.',
                'Observations cover the primary session only. Extension model calls are not completely metered; token receipts are a lower bound, exhaustive native calls and complete authorship are unsupported.',
                'Native extensions and same-user processes remain trusted; this is not a hostile-worker or confidentiality boundary.']
            result['observation_scope'] = 'primary_session'
        return result
    def launcher_contract(self, launcher, *, retained):
        validate_launcher(launcher)
        if retained is not ... and retained is not None: raise ValueError('Pi frozen launcher contract changed')
        return None
    def namespace_launcher(self, settings):
        capabilities = settings.get('native_capabilities') or {}
        if 'native_launcher_contract' not in capabilities: raise ValueError('Frozen native namespace launcher contract is missing')
        return self.launcher_contract(settings['native_launcher_argv'], retained=capabilities['native_launcher_contract'])
    def skill_loading_contract(self, launcher, *, retained=None):
        validate_launcher(launcher)
        value = dict(kind='pi_explicit_skills', version=1)
        if retained is not None and value != retained:
            raise ValueError('Frozen native Skill launcher identity changed; create a new instance')
        return value
    def project_policy(self, settings):
        from . import pi_namespace
        return pi_namespace.project_policy(settings)
    def project_skills(self, root, settings, job):
        validate_settings(settings, require_profile=True, prepared=True)
        if job.get('role') not in {'H', 'W', 'A', 'E'}:
            raise ValueError('Pi requires a valid scientific role')
        from . import skill_supply
        # Frozen selection is data. The actual mount producer observes live
        # installation assets, not this pre-admission consumer.
        skill_supply.for_job(settings, job)
        if job.get('project')!=settings.get('project'):
            raise ValueError('Pi project differs from its frozen job')
        if job.get('project') is not None:self.project_policy(settings)
    def validate_profile(self, profile): return validate_profile(profile)
    def bind_attempt(self, home, work, session=None):
        from .shared_input_views import _exact
        path = session_path(home, work, session)
        # Owner boundary: reject substituted parents before creating storage.
        home, work = _exact(exact_path(home)), _exact(exact_path(work))
        sessions = _exact(home / 'sessions')
        if session is None:
            if path.exists() or path.is_symlink():raise ValueError('Pi requires a fresh exact private session')
        elif self._resume_original is None or self._resume_original['session'] != session:
            # Native's owner calls validate_resume_session before bind_attempt.
            # Consume the owner's current native cursor without reacquisition.
            raise ValueError('Pi resume requires the owner-validated original')
        self._session = dict(session) if session is not None else None
        sessions.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._binding = dict(home=str(home), workspace=str(work), path=str(path))
        import secrets
        nonce = secrets.token_hex(32)
        self._observation_proof = dict(nonce=nonce, path=str(path), workspace=str(work),
            metadata=str(sessions / ('.pi-observation-' + nonce + '.json')),
            extension=str(sessions / ('.scid-pi-observation-' + nonce + '.ts')), required=False)
        self.engine.binding = MappingProxyType(dict(self._binding))
    def launch(self, profile, work, pending, schema, instruction):
        validate_configuration(self.settings.get('native_options')); validate_profile(profile)
        if self._binding is None or self._binding['workspace'] != str(work): raise ValueError('Pi requires owner-bound home/work before launch')
        self._profile = MappingProxyType({k: profile.get(k) for k in ('model', 'reasoning_effort', 'service_tier')})
        if self._session is not None and self._session['profile'] != self._profile:
            raise ValueError('Pi resume original model/thinking binding changed')
        self.engine.profile = self._profile
        provider, model = profile['model'].split('/', 1)
        self._args = [sys.executable, '-m', 'scidiscovery.research_loop.pi_transport', '--', *self.launcher,
            '--mode', 'rpc', *resource_flags(self.settings['native_options'].get('resource_mode', 'closed')),
            '--tools', 'read,bash,edit,write,grep,find,ls',
            '--session', self._binding['path'], '--session-dir', str(Path(self._binding['path']).parent),
            '--provider', provider, '--model', model, '--thinking', profile['reasoning_effort']]
        return list(self._args)
    def resume(self, args, profile, work, pending, schema, session):
        if (self._session != session or list(args) != self._args
                or dict(self._profile or {}) != {k: profile.get(k) for k in ('model', 'reasoning_effort', 'service_tier')}
                or self._binding['workspace'] != str(work)):
            raise ValueError('Pi resume differs from its exact owner-bound attempt')
        self._args = [*args[:3], '--resume-proof', json.dumps(session, ensure_ascii=True, separators=(',', ':')), *args[3:]]
        return list(self._args), None
    def request(self, prompt, *, profile, work, schema, formal_review=False, response_schema=None):
        if (dict(self._profile or {}) != {k: profile.get(k) for k in ('model', 'reasoning_effort', 'service_tier')}
                or self._binding is None or self._binding['workspace'] != str(work)):
            raise ValueError('Pi original request profile or workspace changed')
        if not isinstance(response_schema, dict): raise ValueError('Pi requires the owner-provided current response schema')
        request = (prompt + '\n' + JSON_RESPONSE_INSTRUCTION + '\nCurrent required final response JSON Schema:\n'
            + json.dumps(response_schema, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        if self._meter is not None and self._meter.observation is not None:
            import hashlib
            self._meter.observation.prompt_hash = hashlib.sha256(request).hexdigest()
        return request
    def transport_args(self, args): return list(args)
    def for_job(self, job):
        self._job = job
        return self
    def apply_home(self, env, home, *, isolated):
        # Framework isolation names session storage, never a new auth/config HOME.
        pass
    def actual_home(self, env, work, home, *, isolated): return home
    def meter(self, home, work, session=None, *, context_state=None):
        if self._binding is None or (str(home), str(work)) != (self._binding['home'], self._binding['workspace']):
            raise ValueError('Pi meter differs from owner-bound home/work')
        if self._meter is not None: raise ValueError('Pi invocation already has its meter')
        if self._session != session:raise ValueError('Pi meter changed its exact resume original')
        self._meter = SessionUsage(home, work, session, context_state=context_state,
            resume_original=self._resume_original)
        self._meter.profile = self._profile
        self._meter.use_events(self.engine.usage)
        from .pi_observation import Observation
        self._meter.observation = Observation(self._meter, self._observation_proof, retained=context_state)
        return self._meter
    def event_selector(self, *, source): return PiEventSelector()
    def event(self, value):
        from .pi_observation import BOUND
        if value.get('type') == 'turn.input':
            # This frame is emitted only by the bridge; native stdout frames
            # with this type are rejected before forwarding. Retained proof
            # additionally requires the host owner's exact queued original.
            if value.get('status') == 'submitting' and isinstance(value.get('payload'), str):
                self.engine.turn_input_payloads.add(value['payload'])
            return dict(turn_input=value)
        from .pi_transport import CONTROLLED_EXIT
        if value.get('type') == CONTROLLED_EXIT:
            try:
                failed_settlement = (self.engine.final is None and bool(self.engine.failures)
                    and not self.engine.accounting_only and not self.engine.open_message
                    and not self.engine.tools and not self.engine.compacting and not self.engine.summarizing)
                if (self._controlled_exit is not None or not self.engine.settled
                        or ((self.engine.failed or self.engine.final is None) and not failed_settlement)
                        or self._launch_evidence is None or self._meter is None):
                    raise ValueError('pi_authorship_exit_unexpected')
                session = dict(thread_id=self.engine.session, profile=dict(self._profile),
                    path=self._binding['path'], workspace=self._binding['workspace'])
                _controlled_transport(value, self._launch_evidence, session, self._meter.observation.prompt_hash)
                # A verified failed settlement retains lifecycle evidence only.
                # It supplies neither a completed event nor successful authorship.
                self._controlled_exit = copy.deepcopy(value)
                return {}
            except (ValueError, TypeError, KeyError, AttributeError):
                if self._meter is not None: self._meter.observation.fail('pi_authorship_exit_invalid')
                return self.engine.failure()
        if value.get('type') == BOUND:
            return dict(observation_binding=value)
        if self._meter is not None and self._meter.observation is not None:
            if value.get('type') == 'pi_transport_fault':
                self._meter.observation.fail('native_metadata_source_transport_fault')
            elif context_boundary(value)[0]:
                self._meter.observation.invalidate()
        return self.engine.event(value)
    def observe(self, meter, event):
        if 'session' in event: meter.bind_thread(event['session'])
        if 'observation_binding' in event:
            meter.observation.bind(event['observation_binding'])
            if self._notice_channel is not None and self._notice_writable:
                self._notice_channel.clear()
                self._notice_channel.update(status='available', mechanism='pi_tool_result')
    def snapshot_observation(self, meter): return meter.snapshot()
    def poll_observation(self, meter): return meter.poll()
    def observation_fault(self, meter):
        observer = meter.observation
        return (meter.fault or meter.events.fault or meter.reason
            or (observer.fault if observer is not None and observer.required else None))
    def observation_capability(self, fact):
        from .pi_observation import capability
        return capability(fact)
    def bind_metadata_callback(self, meter, callback):
        meter.metadata_callback = callback
    def configure_observation(self, meter, work, attempt, thread, channel, *, required):
        from .native_usage import MetadataUnavailable
        meter.capabilities = {fact: self.observation_capability(fact) for fact in ('tokens', 'context', 'compactions', 'native_calls', 'terminal')}
        if not self._observer_staged:
            if required: raise MetadataUnavailable('Pi required controlled observer is unavailable')
            return
        if self._observation_proof['required'] != required:
            raise MetadataUnavailable('Pi observer requirement changed after launch projection')
        meter.observation.configure(attempt, required=required)
    def finish_observation(self, meter, *, required):
        meter.terminal()  # Always reconcile billing, independently of metadata.
        if meter.observation is not None: meter.observation.finish()
        return meter.snapshot()
    def terminal_usage(self, event, meter):
        observation = meter.snapshot()
        return (observation['tokens'] if event.get('post_exit') and event.get('completed')
            and meter.finished and observation['coverage'] == 'terminal' else None)
    def retain_terminal(self, meter, observation, terminal): pass
    def combine_failures(self, failures, *, completed_before=None): return self.engine.combine(failures, completed_before=completed_before)
    def identity(self, meter, session):
        binding = meter.session_binding()
        return dict(backend='pi', home=str(meter.home), path=binding['path'] if binding else None,
            session=binding, session_id=session, participant_scope='unsupported',
            limitation='Pi does not attest complete native authorship.')
    def initial_notice(self, namespace_launcher):
        return dict(status='unavailable', reason='not_preflighted' if namespace_launcher is None else 'unsupported_namespace')
    def stage_notice(self, work):
        from .pi_observation import stage, extension_source
        if self._observation_proof is None or str(work) != self._binding['workspace']:
            raise ValueError('Pi observer requires the owner-bound attempt')
        helper = stage(work, self._observation_proof)
        original = extension_source(work, self._observation_proof).encode('utf-8')
        self._observer_source = dict(version=1, path=self._observation_proof['extension'],
            sha256=hashlib.sha256(original).hexdigest(), size_bytes=len(original))
        self._observer_staged = True
        return helper
    def load_launch_facts(self, root, job, work, *, home, args, context, selected_code, source_facts, policy):
        from . import pi_namespace
        if (list(args) != self._args or self._binding is None
                or (str(home), str(work)) != (self._binding['home'], self._binding['workspace'])):
            raise ValueError('Pi accepted launch argv/home/work changed')
        return pi_namespace.load_facts(root, job, work, policy=policy, native_home=home,
            native_argv=self.launcher, context=context, selected_code=selected_code, source_facts=source_facts,
            observation=self._observation_proof if self._observer_staged else None)
    def wrap_launch(self, args, view_facts):
        from . import pi_namespace
        if list(args) != self._args: raise ValueError('Pi accepted launch argv changed')
        return pi_namespace.wrap_from_facts(view_facts, args)
    def prepare_launch(self, args, work, attempt, env, view_facts, context, *, diagnostic=None):
        from . import checkpoint
        from .pi_transport import runtime_resource_mode
        mode = runtime_resource_mode(view_facts.get('resource_policy'))
        if mode != self.settings['native_options'].get('resource_mode', 'closed'):
            raise ValueError('Pi launch resource profile changed from its frozen declaration')
        if self._observer_staged:
            required = bool((self.settings.get('worker_fragment_policy') or {}).get('max_context_windows')
                is not None and self._job is not None and checkpoint.ordinary_worker(self._job))
            self._observation_proof['required'] = required
            view_facts = dict(view_facts, observation=dict(self._observation_proof))
            self._launch_evidence = dict(kind=('pi_cli_closed_resources_v1' if mode == 'closed'
                else 'pi_cli_configured_extensions_v1'),
                launcher=str(view_facts['launcher']), resource_policy=copy.deepcopy(view_facts['resource_policy']),
                resources=[dict(row) for row in view_facts['resources']],
                observer=dict(self._observation_proof), observer_source=dict(self._observer_source), source_readonly=True)
        self._notice_writable = not view_facts.get('readonly', True) and self._observer_staged
        channel = dict(status='unavailable', reason='not_preflighted' if self._notice_writable else 'helper_unavailable')
        self._notice_channel = channel
        if self._turn_input is not None:
            from .turn_input import ENV
            view_facts['mounts']['mappings'][str(self._turn_input['visible'])] = self._turn_input['inbox']
            env[ENV] = str(self._turn_input['visible'])
        return self.wrap_launch(args, view_facts), channel
    def prepare_execution(self,root,step,record,work,home,args,*,resume_position,job,intent,settings,
                          source_facts,selected_code,material_sources):
        from . import native_execution, pi_namespace
        if (list(args)!=self._args or self._binding is None
                or (str(home),str(work))!=(self._binding['home'],self._binding['workspace'])):
            raise ValueError('Pi Local differs from its owner-bound native attempt')
        return native_execution.prepare_from_facts(root,step,record,work,home,args,
            resume_position=resume_position,job=job,intent=intent,settings=settings,
            source_facts=source_facts,selected_code=selected_code,material_sources=material_sources,
            projection=pi_namespace.execution_projection(
                observer_source=self._observation_proof['extension'] if self._observer_staged else None,
                private_metadata_sources=tuple(self._observation_proof[k] for k in ('metadata', 'path'))
                    if self._observer_staged else ()))
    def validate_resume_session(self, session, workspace, home):
        from .pi_sessions import load_resume_original
        # This is the operation owner's explicit acquisition boundary. Binding,
        # argv construction and metering consume these facts without reloading.
        mode = validate_configuration(self.settings.get('native_options')).get('resource_mode', 'closed')
        self._resume_original = load_resume_original(session, workspace, home, resource_mode=mode)
        return session
    def recovery_capability(self, kind):
        supported = kind in {'resume', 'automatic_recovery', 'technical_restart'}
        return dict(status='supported' if supported else 'unsupported',
            reason=None if supported else 'pi_recovery_unsupported')
    def retain_participant_evidence(self, outcome, intent, process):
        """Native's stopped-writer boundary; all source facts are already owned."""
        if not self.complete_authorship:
            return
        binding = outcome['native_identity']
        try:
            session = binding['session']
            if (session is None or self._meter is None or not self._meter.finished
                    or self._meter.digest is None
                    or self._meter.verified_offset != self._meter.offset or self._launch_evidence is None
                    or self._controlled_exit is None or not isinstance(process, dict)):
                raise ValueError('pi_authorship_runtime_proof_unavailable')
            proof = dict(version=1, kind=PARTICIPANT_PROOF, capability=PARTICIPANT_SCOPE,
                session=copy.deepcopy(session), launch=copy.deepcopy(self._launch_evidence),
                transport=copy.deepcopy(self._controlled_exit), persisted=self._meter.observation.controlled_facts(),
                prefix_verified=True, intent_sha256=_digest(intent),
                outcome_sha256=_digest(_outcome_facts(outcome)), process=copy.deepcopy(process))
            binding.update(participant_scope=PARTICIPANT_SCOPE, controlled=proof)
            _participant(outcome, intent, self._binding['workspace'])
            self.validate_participant_process(outcome, process)
            binding.pop('limitation', None)
        except (ValueError, TypeError, KeyError, AttributeError):
            binding.pop('controlled', None)
            binding.update(participant_scope='unsupported', limitation='Pi controlled native proof incomplete.')

    def acquire_participant_session(self, outcome, intent, workspace):
        if not self.complete_authorship:
            raise ValueError('Pi frozen native authorship unsupported')
        _participant(outcome, intent, workspace)
        binding = outcome['native_identity']
        return copy.deepcopy(dict(binding['session'], controlled=binding['controlled']))

    @staticmethod
    def validate_participant_proof(row, metadata, outcome, intent, workspace):
        identity = _participant(outcome, intent, workspace)
        binding = outcome['native_identity']
        expected = dict(binding['session'], controlled=binding['controlled'])
        if metadata.get('session') != expected or row.get('session') != expected:
            raise ValueError('Pi retained participant proof changed')
        return identity

    @staticmethod
    def validate_participant_process(outcome, process):
        proof = (outcome.get('native_identity') or {}).get('controlled') or {}
        if (proof.get('process') != process or not isinstance(process, dict)
                or not isinstance(process.get('boot_id'), str) or not process['boot_id']
                or any(type(process.get(key)) is not int or process[key] <= 0
                    for key in ('pid', 'start_ticks', 'pgid', 'sid', 'ppid'))):
            raise ValueError('Pi native participant writer changed')

    @staticmethod
    def authorship_scope(binding):
        return binding.get('backend') == 'pi' and binding.get('participant_scope') == PARTICIPANT_SCOPE
    @staticmethod
    def authorship_proof_kind(binding):
        if not Pi.authorship_scope(binding): raise ValueError('Pi native participant scope unsupported')
        return PARTICIPANT_PROOF
    @staticmethod
    def validate_participant_event(event, outcome, intent, workspace):
        # Event semantics only. Acquisition/retained proof acceptance is owned
        # by its separate hook; do not repeat that whole operation here.
        from .pi_sessions import validate_session_event
        binding = outcome.get('native_identity') or {}
        session = binding.get('session')
        validate_session_event(event, outcome.get('thread_id'))
        if (not Pi.authorship_scope(binding) or not isinstance(session, dict)
                or not isinstance(binding.get('controlled'), dict)
                or binding['controlled'].get('kind') != PARTICIPANT_PROOF
                or event != outcome.get('native_thread_event') or event['workspace'] != str(workspace)
                or event['profile'] != intent.get('native_profile')
                or any(event[key] != session.get(key) for key in ('thread_id', 'path', 'workspace', 'profile'))):
            raise ValueError('Pi participant event changed')
        return ('pi_native_session', session['thread_id'])
    def validate_session_event(self, event, thread):
        from .pi_sessions import validate_session_event
        validate_session_event(event, thread)
        if self._resume_original is None:raise ValueError('Pi session event requires its acquired original')
        session = self._resume_original['session']
        if session['thread_id'] != thread or any(event[k] != session[k] for k in ('path', 'workspace', 'profile')):
            raise ValueError('Pi session event differs from its exact original')
    def format_rejection(self, event): return False
    def format_observation(self, event): return 'activity'
    @staticmethod
    def response_guidance(schema): return ''
    @staticmethod
    def compose_instructions(prompt, instruction, transport): return instruction + '\n' + prompt
    def terminal_message(self, event): return None
    def terminal_binding(self, event): return None
    @staticmethod
    def terminal_reconciliation_candidate(outcome): return False
    @staticmethod
    def acquire_terminal_evidence(record, outcome, *, output_path=None): return None
    @staticmethod
    def reconcile_terminal(outcome, *, meter, evidence, original_record): return outcome
    @staticmethod
    def terminal_failure_evidence(failures):
        return [{k: f[k] for k in ('category', 'event_type', 'retry_after_seconds', 'not_accepted') if k in f} for f in failures]
    @classmethod
    def readonly_capability(cls, settings):
        return dict(status='unsupported', reason='Pi auxiliary readonly calls have no supported permission/evidence adapter', permissions=cls(settings).permissions(settings))
    def launch_readonly(self, *args): raise ValueError('Pi auxiliary readonly invocation unsupported')
    def acquire_response_file(self, pending, *, max_bytes): raise ValueError('Pi auxiliary response-file invocation unsupported')
    def public_events(self, value):
        kind = value.get('type')
        if kind == 'session': return [dict(type='session.started')]
        if kind == 'agent_settled': return [dict(type='turn.completed')] if value.get('aborted') is False else []
        if kind in {'tool_execution_start', 'tool_execution_update', 'tool_execution_end'}:
            name = value.get('toolName')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,127}', name): return []
            if kind == 'tool_execution_end' and type(value.get('isError')) is not bool: return []
            status = ('failed' if value['isError'] else 'completed') if kind == 'tool_execution_end' else 'started' if kind == 'tool_execution_start' else 'updated'
            return [dict(type='tool', tool=name, status=status)]
        result = []; usage = None
        if kind == 'message_end':
            message = value.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant': return []
            content = message.get('content')
            if isinstance(content, list) and len(content) <= 1024:
                # End snapshots contain the SDK's explicit public text and
                # thinking blocks. Ignore deltas so reconnects do not repeat
                # the same thinking, and never inspect tool arguments.
                blocks = []
                for block in content:
                    if not isinstance(block, dict): continue
                    field = {'text': 'text', 'thinking': 'thinking'}.get(block.get('type')) if isinstance(block.get('type'), str) else None
                    if field and isinstance(block.get(field), str):
                        public_kind = 'message' if field == 'text' else 'thinking'
                        if blocks and blocks[-1]['type'] == public_kind:
                            blocks[-1]['text'] += block[field]
                        else: blocks.append(dict(type=public_kind, text=block[field]))
                if sum(len(block['text']) for block in blocks) <= 1024 * 1024: result.extend(blocks)
            usage = message.get('usage')
        elif kind == 'message_update': usage = value.get('usage')
        elif kind == 'compaction_end' and isinstance(value.get('result'), dict): usage = value['result'].get('usage')
        elif kind == 'entry_appended' and isinstance(value.get('entry'), dict) and value['entry'].get('type') in {'usage', 'compaction', 'branch_summary'}:
            usage = value['entry'].get('usage')
        converted = usage_tokens(usage)
        # Pi streams valid all-zero usage placeholders before measured tokens.
        # Suppress only public noise; native accounting keeps its own originals.
        if converted is not None and any(converted.values()): result.append(dict(type='usage', usage=converted))
        return result
