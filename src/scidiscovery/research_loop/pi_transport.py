"""One-prompt RPC engine. Exact model/thinking precede submission.

The original v3 header precedes work in the retained stream. Settlement requires
accepted prompt, exact idle state, orderly stdin close, drain and clean exit.
No credential/config copying and no retry of an uncertain prompt.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

from .pi_sessions import (CONTEXT_EVENT, LINE_BYTES, context_boundary, decode,
    exact_path, session_context, validate_header)

HANDSHAKE_SECONDS = 15
SHUTDOWN_SECONDS = 5
REQUEST_BYTES = 64 * 1024 * 1024
CONTEXT_SECONDS = 2
ACCOUNTING_EVENTS = frozenset({'message_end', 'message_update', 'compaction_start', 'compaction_end', 'entry_appended'})
CONTROLLED_EXIT = 'pi.controlled_exit'



class TransportFault(ValueError): pass


def fault_event(error=None):
    # Do not stringify errors: they can contain private provider/config data.
    return dict(type='pi_transport_fault')


def _header_line(path, *, with_identity=False):
    path = exact_path(path)
    if path.resolve() != path or any(p.is_symlink() for p in (path, *path.parents)):
        raise TransportFault('session_header_path_unsafe')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            import stat
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise TransportFault('session_header_invalid')
            line = stream.readline(LINE_BYTES + 1)
    except FileNotFoundError: return None
    if len(line) > LINE_BYTES: raise TransportFault('session_header_invalid')
    return (line, (info.st_dev, info.st_ino)) if with_identity else line


def record(line):
    if len(line) > LINE_BYTES or not line.endswith(b'\n'): raise TransportFault('invalid_rpc_record')
    value = decode(line.decode('utf-8'))
    if not isinstance(value, dict) or not isinstance(value.get('type'), str): raise TransportFault('invalid_rpc_record')
    return value


class ContextRequests:
    """Coalesced optional SDK estimates; no request ID is a native turn ID."""
    def __init__(self):
        self.number = self.epoch = 0
        self.pending = None
        self.dirty = self.closed = False
        self.value = dict(status='unavailable')

    def boundary(self, value):
        invalidate, query = context_boundary(value)
        if invalidate:
            self.epoch += 1; self.value = dict(status='unavailable'); self.dirty = query

    def request(self, *, enabled):
        if not enabled or self.closed or not self.dirty or self.pending is not None: return None
        self.number += 1
        identity = 'context-' + str(self.number)
        self.pending = (identity, self.epoch); self.dirty = False
        return dict(id=identity, type='get_session_stats')

    def response(self, value, thread, path, observed_at):
        identity = value.get('id')
        if not isinstance(identity, str) or not identity.startswith('context-'): return False
        number = identity[len('context-'):]
        if (not number.isascii() or not number.isdecimal() or number.startswith('0')
                or len(number) > len(str(self.number)) or not 0 < int(number) <= self.number):
            raise TransportFault('unexpected_rpc_response')
        if self.pending is None or identity != self.pending[0]: return True
        _, epoch = self.pending; self.pending = None
        if not self.closed and epoch == self.epoch:
            self.value = (session_context(value.get('data'), thread, path, observed_at)
                if value.get('command') == 'get_session_stats' and value.get('success') is True
                else dict(status='unavailable'))
        return True

    def expire(self):
        self.pending = None; self.dirty = False; self.value = dict(status='unavailable')

    def stop(self):
        self.pending = None; self.dirty = False; self.closed = True


def resource_policy(mode='closed'):
    """Public projection facts, not private settings or scientific permission."""
    from .pi_backend import resource_mode
    if resource_mode(mode) == 'configured':
        return dict(profile='pi_configured_extensions_v1', configured_packages='native',
            custom_package_resolution=False, automatic_resource_network='offline',
            provider_requests='unchanged', native_network='unrestricted', inherited_system_prompts='absent',
            observation_scope='primary_session', token_accounting='lower_bound', complete_native_authorship=False)
    return dict(profile='pi_explicit_resources_v1', configured_packages='absent_or_empty',
        custom_package_resolution=False, automatic_resource_network='offline',
        provider_requests='unchanged', native_network='unrestricted', inherited_system_prompts='absent')


def runtime_resource_mode(policy):
    """Accept only exact owner-projected policies, without selector fallback."""
    for mode in ('closed', 'configured'):
        expected = resource_policy(mode)
        if (isinstance(policy, dict) and policy == expected
                and all(type(policy[key]) is type(value) for key, value in expected.items())):
            return mode
    raise TransportFault('resource_policy_missing_or_changed')


def validate_resource_settings(global_settings, project_settings, *, mode='closed'):
    """Pure NativeRuntimeProfile admission, not an SDK capability assertion.

    DefaultResourceLoader.reload resolves configured packages BEFORE applying
    --no-* resource filters. PI_OFFLINE prevents refresh/install, but a missing
    user npm managed path can still execute npmCommand to locate legacy global
    storage. No supported CLI switch disables that resolution per invocation.
    Closed mode refuses packages in either scope; configured delegates packages
    to the offline native resolver. Both modes refuse a custom npmCommand.
    """
    from .pi_backend import resource_mode
    mode = resource_mode(mode)
    for settings in (global_settings, project_settings):
        if not isinstance(settings, dict):
            raise TransportFault('Pi NativeRuntimeProfile requires object settings')
        if mode == 'closed' and 'packages' in settings and settings['packages'] != []:
            raise TransportFault('Pi NativeRuntimeProfile does not support configured package references; --offline and --no-skills do not disable package resolution')
        if 'npmCommand' in settings and settings['npmCommand'] not in (None, []):
            raise TransportFault('Pi NativeRuntimeProfile does not support custom package resolution (npmCommand)')


def validate_prompt_sources(present):
    """Pure admission of the owner's four fixed SDK path observations.

    get_commands cannot attest SYSTEM.md or APPEND_SYSTEM.md. noContextFiles
    suppresses AGENTS/CLAUDE only; never substitute a custom system prompt to
    hide these sources, since that would also replace Pi's default tool rules.
    """
    if (not isinstance(present, tuple) or len(present) != 4
            or any(type(value) is not bool or value for value in present)):
        raise TransportFault('Pi NativeRuntimeProfile does not support inherited system prompt sources')


def load_prompt_sources(agent, work):
    """Observe only DefaultResourceLoader's four fixed prompt candidates.

    Both global and project candidates must be absent, irrespective of trust or
    precedence. lstat refuses any existing entry, including dangling symlinks,
    without opening it or disclosing its name/content. No ancestor/tree search.
    """
    present = []
    for directory in (Path(agent), Path(work) / '.pi'):
        for name in ('SYSTEM.md', 'APPEND_SYSTEM.md'):
            try: (directory / name).lstat()
            except FileNotFoundError: present.append(False)
            else: present.append(True)
    validate_prompt_sources(tuple(present))


def load_resource_settings(agent, work, *, mode='closed'):
    """Effect-boundary owner: fixed SDK prompt paths and two settings originals.

    No settings copy, auth read, ancestor search, package/cache discovery, or
    SDK launcher. Absence means SDK defaults. Unsafe/malformed originals refuse
    rather than letting native parse failure silently discard a package policy.
    Same-user concurrent mutation remains trusted, as with native auth/runtime.
    """
    import stat
    load_prompt_sources(agent, work)
    values = []
    for path in (Path(agent) / 'settings.json', Path(work) / '.pi/settings.json'):
        path = exact_path(str(path))
        if path.resolve() != path or any(p.is_symlink() for p in (path, *path.parents)):
            raise TransportFault('Pi NativeRuntimeProfile requires exact ordinary settings paths')
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            values.append({}); continue
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > LINE_BYTES:
                raise TransportFault('Pi NativeRuntimeProfile settings must be bounded ordinary files')
            data = stream.read(LINE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (len(data) > LINE_BYTES or len(data) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise TransportFault('Pi NativeRuntimeProfile settings changed during acquisition')
        # SDK frontmatter/settings readers strip a UTF-8 BOM too.
        values.append(decode(data.decode('utf-8-sig')) if data else {})
    validate_resource_settings(*values, mode=mode)
    # Return only the explicit resource policy, never private settings contents.
    return resource_policy(mode)


def resource_proof(value):
    """Pure exact selected-manual identity, never a file/catalog read."""
    if not isinstance(value, list) or len(value) > 256:
        raise TransportFault('resource_binding_invalid')
    paths = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {'path'}:
            raise TransportFault('resource_binding_invalid')
        path = exact_path(row['path'])
        if path.name != 'SKILL.md' or str(path) in paths:
            raise TransportFault('resource_binding_invalid')
        paths.add(str(path))
    return tuple(dict(row) for row in value)


def validate_resources(data, resources, *, observation=None, mode='closed'):
    """Native inventory must equal the exact live owner selection before prompt.

    No command name, argv flag or user extension establishes permission. The
    controlled internal observer is the one exact original-source exception;
    it still requires a separate private startup frame before the sole prompt.
    """
    from .pi_backend import resource_mode
    mode = resource_mode(mode)
    if (not isinstance(data, dict) or set(data) != {'commands'} or not isinstance(data['commands'], list)
            or len(data['commands']) > 4096):
        raise TransportFault('resource_binding_mismatch')
    expected = {row['path'] for row in resources}
    seen, names = set(), set()
    internal = False
    from .pi_observation import command_proof
    observer_command = command_proof(observation['extension']) if observation is not None else None
    for command in data['commands']:
        if not isinstance(command, dict): raise TransportFault('resource_binding_mismatch')
        if observation is not None:
            if command == observer_command:
                if internal: raise TransportFault('resource_binding_mismatch')
                internal = True
                continue
        name = command.get('name'); description = command.get('description')
        source = command.get('sourceInfo')
        if mode == 'configured' and (not isinstance(source, dict) or source.get('path') not in expected):
            # Native extensions may register commands without sourceInfo. This
            # inventory is not an assertion that every package/hook/tool loaded.
            # An extension may not impersonate the internal observer or a Skill.
            if (not isinstance(name, str) or not name.strip() or name in names
                    or name.startswith('skill:') or command.get('source') != 'extension'
                    or observer_command is not None and (name == observer_command['name']
                        or isinstance(source, dict) and source.get('path') == observation['extension'])):
                raise TransportFault('resource_binding_mismatch')
            names.add(name)
            continue
        if (not isinstance(name, str) or not name.startswith('skill:') or name in names
                or not isinstance(description, str) or not description.strip()
                or not isinstance(source, dict) or not isinstance(source.get('path'), str)
                or source['path'] not in expected or source['path'] in seen):
            raise TransportFault('resource_binding_mismatch')
        from .skill_supply import _name
        try: _name(name[len('skill:'):])
        except ValueError: raise TransportFault('resource_binding_mismatch') from None
        path = source['path']
        if (command.get('source') != 'skill'
                # skills.js creates local/temporary/top-level source info for
                # an explicit file outside the user/project discovery roots;
                # resource-loader preserves it with the manual's own baseDir.
                # --skill does NOT label this source "cli". Configured/package
                # metadata overriding this exact source is not our selection.
                or source != dict(path=path, source='local', scope='temporary',
                    origin='top-level', baseDir=str(Path(path).parent))):
            raise TransportFault('resource_binding_mismatch')
        seen.add(path); names.add(name)
    if observer_command is not None and observer_command['name'] in names:
        raise TransportFault('resource_binding_mismatch')
    if seen != expected: raise TransportFault('resource_binding_mismatch')
    if observation is not None and observation['required'] and not internal:
        raise TransportFault('pi_observer_source_unavailable')
    return internal


class Binding:
    """Pure command correlation. Neither rc=0 nor agent_end is completion."""
    def __init__(self, provider, model, thinking, path, prompt, *, expected_session=None, resources=(), observation=None,
            resource_mode='closed'):
        self.provider, self.model, self.thinking = provider, model, thinking
        self.path, self.prompt = path, prompt
        self.pending = {}; self.number = 0; self.session_id = expected_session
        self.resumed = expected_session is not None
        self.prompt_sent = self.accepted = self.idle = False
        self.settled = None
        self.resources = resource_proof(list(resources))
        from .pi_backend import resource_mode as validate_mode
        self.resource_mode = validate_mode(resource_mode)
        self.resources_bound = False
        self.observation = observation
        self.observer_start = None
        self.observer_bound = False

    def command(self, kind, **fields):
        self.number += 1; identity = str(self.number); self.pending[identity] = kind
        return dict(id=identity, type=kind, **fields)

    def start(self): return self.command('get_state')

    def _model(self, value):
        if (not isinstance(value, dict) or value.get('provider') != self.provider
                or value.get('id') != self.model or not isinstance(value.get('api'), str)
                or not value['api'] or value.get('type') == 'virtual'
                or value['api'] in {'virtual', 'pi-virtual'}): raise TransportFault('model_binding_mismatch')

    def _state(self, state, *, bound):
        if (not isinstance(state, dict) or state.get('isStreaming') is not False
                or state.get('isCompacting') is not False or state.get('pendingMessageCount') != 0
                or state.get('sessionFile') != self.path or not isinstance(state.get('sessionId'), str)
                or not state['sessionId']): raise TransportFault('session_not_exact_idle')
        if self.session_id is not None and self.session_id != state['sessionId']: raise TransportFault('session_identity_changed')
        self.session_id = state['sessionId']
        if bound:
            self._model(state.get('model'))
            if state.get('thinkingLevel') != self.thinking: raise TransportFault('thinking_binding_mismatch')

    def response(self, value):
        identity = value.get('id')
        if not isinstance(identity, str): raise TransportFault('unexpected_rpc_response')
        kind = self.pending.pop(identity, None)
        if kind is None or value.get('command') != kind or value.get('success') is not True:
            raise TransportFault('rpc_command_failed')
        data = value.get('data')
        if kind == 'get_state':
            self._state(data, bound=self.resumed or self.prompt_sent or self.number > 1)
            if self.prompt_sent:
                if not self.accepted or self.settled is None: raise TransportFault('unaccepted_settlement')
                self.idle = True; return None
            if self.number == 1: return self.command('set_model', provider=self.provider, modelId=self.model)
            return self.command('get_commands')
        if kind == 'get_commands':
            if self.resources_bound or self.prompt_sent: raise TransportFault('resource_binding_mismatch')
            internal = validate_resources(data, self.resources, observation=self.observation, mode=self.resource_mode)
            if (internal != (self.observer_start is not None)
                    or internal and self.observer_start['session_id'] != self.session_id):
                raise TransportFault('pi_observer_start_or_source_missing')
            self.observer_bound = internal
            self.resources_bound = True; self.prompt_sent = True
            return self.command('prompt', message=self.prompt)
        if kind == 'set_model':
            self._model(data)
            return self.command('set_thinking_level', level=self.thinking)
        if kind == 'set_thinking_level': return self.command('get_state')
        if kind == 'prompt':
            if not isinstance(data, dict) or data.get('disposition') != 'started': raise TransportFault('prompt_not_started')
            self.accepted = True
            return self.command('get_state') if self.settled is not None else None
        raise TransportFault('unexpected_rpc_response')

    def event(self, value):
        kind = value['type']
        from .pi_observation import START, BOUND, validate_start
        if kind == START:
            if self.observation is None or self.observer_start is not None or self.prompt_sent:
                raise TransportFault('pi_observer_start_invalid')
            self.observer_start = validate_start(value, self.observation, session=self.session_id)
            return None
        if kind in {CONTEXT_EVENT, BOUND, CONTROLLED_EXIT, 'pi_transport_fault', 'session', 'pi.session_bound'}: raise TransportFault('invalid_rpc_record')
        if kind == 'thinking_level_changed' and not self.prompt_sent: return None
        if not self.prompt_sent: raise TransportFault('work_before_model_binding')
        if kind == 'agent_settled':
            if self.settled is not None: raise TransportFault('duplicate_settlement')
            self.settled = value
            return self.command('get_state') if self.accepted else None
        if self.settled is not None and kind != 'entry_appended': raise TransportFault('work_after_settlement')
        return None

    def finish(self, returncode):
        if returncode != 0 or not self.idle or not self.accepted or self.settled is None or self.pending:
            raise TransportFault('unverified_rpc_exit')
        return self.settled


def observation_start_frame(value):
    """Unwrap only our bounded private startup notification, never model text."""
    from .pi_observation import START
    if value.get('method') != 'notify' or not isinstance(value.get('message'), str): return None
    message = value['message']
    if not message.startswith('{') or len(message.encode('utf-8')) > 8192: return None
    try: frame = decode(message)
    except (ValueError, UnicodeError, RecursionError): return None
    if not isinstance(frame, dict) or frame.get('type') != START: return None
    if (set(value) != {'type', 'id', 'method', 'message', 'notifyType'}
            or value.get('notifyType') != 'info' or not isinstance(value.get('id'), str)
            or not 1 <= len(value['id']) <= 256):
        raise TransportFault('pi_observer_start_invalid')
    return frame


def observation_proof(value):
    """Pure exact internal-path exception, never arbitrary user extensions."""
    if (not isinstance(value, dict) or set(value) != {'nonce', 'path', 'workspace', 'metadata', 'extension', 'required'}
            or not isinstance(value.get('nonce'), str) or len(value['nonce']) != 64
            or any(c not in '0123456789abcdef' for c in value['nonce']) or type(value.get('required')) is not bool):
        raise TransportFault('pi_observer_proof_invalid')
    path, work, metadata, extension = (exact_path(value[k]) for k in ('path', 'workspace', 'metadata', 'extension'))
    if (metadata != path.parent / ('.pi-observation-' + value['nonce'] + '.json')
            or extension != path.parent / ('.scid-pi-observation-' + value['nonce'] + '.ts')):
        raise TransportFault('pi_observer_proof_invalid')
    return dict(value)


def _arguments(argv, *, observation=None, resource_mode='closed'):
    if not argv or argv[0] != '--' or len(argv) < 2: raise TransportFault('invalid_launcher')
    child = list(argv[1:])
    def option(flag):
        if child.count(flag) != 1 or child.index(flag) + 1 >= len(child): raise TransportFault('invalid_launcher')
        return child[child.index(flag) + 1]
    if option('--mode') != 'rpc': raise TransportFault('invalid_launcher')
    provider, model, thinking = option('--provider'), option('--model'), option('--thinking')
    from .pi_backend import validate_profile, RESOURCE_FLAGS, resource_flags
    flags = resource_flags(resource_mode)
    validate_profile(dict(model=provider + '/' + model, reasoning_effort=thinking))
    if (any(child.count(flag) != 1 for flag in flags)
            or any(flag in child for flag in set(RESOURCE_FLAGS) - set(flags))
            or any(flag in child for flag in ('--continue', '--resume', '--no-session', '--session-id'))
            or option('--tools') != 'read,bash,edit,write,grep,find,ls'):
        raise TransportFault('invalid_launcher')
    path = exact_path(option('--session'))
    if option('--session-dir') != str(path.parent): raise TransportFault('invalid_launcher')
    skills = []
    singles = {'--mode', '--tools', '--session', '--session-dir', '--provider', '--model', '--thinking'}
    seen = set(); index = 1
    while index < len(child):
        flag = child[index]; index += 1
        if flag in flags: continue
        if flag not in singles | {'--skill', '--extension'} or index >= len(child):
            raise TransportFault('invalid_launcher')
        value = child[index]; index += 1
        if flag == '--extension':
            if observation is None or value != observation['extension'] or flag in seen:
                raise TransportFault('invalid_launcher')
            seen.add(flag)
        elif flag == '--skill':
            manual = exact_path(value)
            if manual.name != 'SKILL.md' or value in skills: raise TransportFault('invalid_launcher')
            skills.append(value)
        elif flag in seen: raise TransportFault('invalid_launcher')
        else: seen.add(flag)
    if observation is not None and ('--extension' not in seen or str(path) != observation['path']):
        raise TransportFault('pi_observer_proof_invalid')
    return child, provider, model, thinking, str(path), tuple(skills)


def run(argv, prompt, emit, *, popen=subprocess.Popen, clock=time.monotonic):
    original = session = runtime_policy = observation = None
    resources = ()
    if argv and argv[0] == '--resource-policy':
        if len(argv) < 4: raise TransportFault('resource_policy_invalid')
        runtime_policy = decode(argv[1]); argv = argv[2:]
    if argv and argv[0] == '--resource-proof':
        if len(argv) < 4: raise TransportFault('resource_binding_invalid')
        resources = resource_proof(decode(argv[1])); argv = argv[2:]
    if argv and argv[0] == '--observation-proof':
        if len(argv) < 4: raise TransportFault('pi_observer_proof_invalid')
        observation = observation_proof(decode(argv[1])); argv = argv[2:]
        if observation['workspace'] != str(Path.cwd()): raise TransportFault('pi_observer_proof_invalid')
    if argv and argv[0] == '--resume-proof':
        if len(argv) < 4:raise TransportFault('invalid_resume_proof')
        session = decode(argv[1]); argv = argv[2:]
    mode = runtime_resource_mode(runtime_policy)
    child, provider, model, thinking, path, skills = _arguments(argv, observation=observation, resource_mode=mode)
    if skills != tuple(row['path'] for row in resources):
        raise TransportFault('resource_binding_mismatch')
    context = ContextRequests(); published_context = dict(status='unavailable')
    try:
        if session is None:
            if _header_line(path) is not None: raise TransportFault('session_not_fresh')
        else:
            from .pi_sessions import load_resume_original
            if (not isinstance(session, dict) or session.get('path') != path
                    or session.get('profile') != dict(model=provider + '/' + model,
                        reasoning_effort=thinking, service_tier=None)):
                raise TransportFault('resume_model_or_path_changed')
            original = load_resume_original(session, Path.cwd(), session.get('home'), resource_mode=mode)
        # The namespace launch owner acquired and admitted current settings.
        # This consumer never rereads them or substitutes an offline-only grant.
        # The exact policy was validated before argv parsing or Popen.
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        emit(fault_event()); return 1
    binding = Binding(provider, model, thinking, path, prompt,
        expected_session=session['thread_id'] if session is not None else None, resources=resources,
        observation=observation, resource_mode=mode)
    process = popen(child, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    failed = emitted_header = closing = stdout_eof = False
    accepted_header = accepted_identity = None
    buffered = []; buffered_bytes = 0
    pending = outgoing = b''
    deadline = clock() + HANDSHAKE_SECONDS
    context_deadline = None

    def fault(error=None):
        nonlocal failed, buffered_bytes
        if not failed:
            emit(fault_event(error))
            for event in buffered:
                if event['type'] in ACCOUNTING_EVENTS: emit(event)
            buffered.clear(); buffered_bytes = 0
        failed = True

    def publish(event, *, size=None):
        nonlocal buffered_bytes
        if emitted_header: emit(event); return
        buffered.append(event)
        buffered_bytes += size if size is not None else len(json.dumps(event, ensure_ascii=False).encode('utf-8'))
        if buffered_bytes > LINE_BYTES: raise TransportFault('session_header_unavailable')

    def publish_context():
        nonlocal published_context
        if context.value != published_context:
            publish(dict(type=CONTEXT_EVENT, context=dict(context.value)))
            published_context = dict(context.value)

    def send(command):
        nonlocal outgoing, context_deadline
        if command is not None and not stdout_eof:
            if command['type'] == 'get_session_stats': context_deadline = clock() + CONTEXT_SECONDS
            outgoing += (json.dumps(command, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            if len(outgoing) > REQUEST_BYTES + LINE_BYTES: raise TransportFault('rpc_write_limit')

    def header(*, final=False):
        nonlocal emitted_header, buffered_bytes, accepted_header, accepted_identity
        if binding.session_id is None or emitted_header and not final: return
        observed = _header_line(path, with_identity=True)
        line, identity = observed if observed is not None else (None, None)
        if emitted_header:
            if line != accepted_header or identity != accepted_identity: raise TransportFault('session_header_changed')
            return
        if line is None or not line.endswith(b'\n'):
            if final: raise TransportFault('session_header_incomplete')
            return
        value = validate_header(line, binding.session_id, Path.cwd())
        emit(value)
        emitted_header = True; accepted_header = line; accepted_identity = identity
        for event in buffered: emit(event)
        buffered.clear(); buffered_bytes = 0

    try:
        os.set_blocking(process.stdin.fileno(), False); os.set_blocking(process.stdout.fileno(), False)
        send(binding.start())
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map() or process.poll() is None:
                if context_deadline is not None and clock() >= context_deadline and not failed:
                    context.expire(); context_deadline = None; publish_context()
                if deadline is not None and clock() >= deadline: raise TransportFault('rpc_metadata_or_shutdown_timeout')
                for key, _ in selector.select(.1):
                    if key.fileobj is process.stdin:
                        if stdout_eof: continue
                        try: count = os.write(process.stdin.fileno(), outgoing[:65536])
                        except BlockingIOError: continue
                        outgoing = outgoing[count:]
                        if not outgoing: selector.unregister(process.stdin)
                        continue
                    try: chunk = os.read(process.stdout.fileno(), 65536)
                    except BlockingIOError: continue
                    if not chunk:
                        stdout_eof = True; selector.unregister(process.stdout)
                        context.stop(); context_deadline = None; outgoing = b''
                        if process.stdin in [item.fileobj for item in selector.get_map().values()]: selector.unregister(process.stdin)
                        if pending: fault()
                        if not binding.idle: fault()
                        continue
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        value = None; retained = False
                        try:
                            value = record(line + b'\n')
                            if failed:
                                if value['type'] in ACCOUNTING_EVENTS: emit(value)
                                continue
                            if value['type'] == 'response':
                                if context.response(value, binding.session_id, path, time.time()):
                                    if context.pending is None: context_deadline = None
                                    publish_context()
                                else:
                                    command = binding.response(value)
                                    if command is not None and command['type'] == 'prompt':
                                        if original is not None:
                                            from .pi_sessions import recheck_prefix
                                            # Popen and the exact idle/model/thinking handshake
                                            # authorize only native append-only setup here;
                                            # prelaunch admission above still requires exact EOF.
                                            recheck_prefix(original, phase='native_setup')
                                        if binding.observer_bound:
                                            publish(binding.observer_start)
                                        publish(dict(type='pi.session_bound', thread_id=binding.session_id,
                                            path=path, workspace=str(Path.cwd()),
                                            profile=dict(model=provider + '/' + model,
                                                reasoning_effort=thinking, service_tier=None)))
                                    send(command)
                                    if binding.idle: context.stop(); context_deadline = None
                                header()
                            elif value['type'] == 'extension_ui_request':
                                frame = observation_start_frame(value)
                                if frame is not None:
                                    binding.event(frame)
                                elif value.get('method') in {'select', 'confirm', 'input', 'editor'}:
                                    send(dict(type='extension_ui_response', id=value.get('id'), cancelled=True))
                            else:
                                from .pi_observation import START
                                if value['type'] == START: raise TransportFault('pi_observer_start_invalid')
                                if value['type'] == 'agent_settled': send(context.request(enabled=binding.prompt_sent))
                                send(binding.event(value))
                                context.boundary(value); publish_context(); header()
                                from .pi_observation import START
                                if value['type'] not in {'agent_settled', START}:
                                    retained = True; publish(value, size=len(line))
                            if binding.prompt_sent and not closing:
                                deadline = ((deadline or clock() + HANDSHAKE_SECONDS)
                                    if binding.settled is not None else None)
                        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
                            fault(error)
                            if not retained and value is not None and value['type'] in ACCOUNTING_EVENTS: emit(value)
                    if len(pending) > LINE_BYTES: raise TransportFault('rpc_record_limit')
                    if not failed: send(context.request(enabled=binding.prompt_sent and binding.settled is None))
                if not failed and not emitted_header: header()
                if outgoing and not closing and process.stdin not in [key.fileobj for key in selector.get_map().values()]:
                    selector.register(process.stdin, selectors.EVENT_WRITE)
                if (binding.idle or failed) and not closing:
                    closing = True
                    if process.stdin in [key.fileobj for key in selector.get_map().values()]: selector.unregister(process.stdin)
                    process.stdin.close()
                    deadline = clock() + SHUTDOWN_SECONDS
            process.wait(timeout=SHUTDOWN_SECONDS)
        header(final=True)
        if not emitted_header or failed: raise TransportFault('unverified_rpc_transport')
        settled = binding.finish(process.returncode)
        emit(settled)
        if binding.observer_bound:
            # Bridge-owned receipt, never accepted from native stdout. Inventory
            # and startup were accepted before prompt; idle, LF/EOF drain and
            # actual clean child exit have now all been observed, not predicted.
            import hashlib
            from .pi_observation import command_proof
            emit(dict(type=CONTROLLED_EXIT, version=1, argv=child,
                session_id=binding.session_id, path=path, workspace=str(Path.cwd()),
                profile=dict(model=provider + '/' + model, reasoning_effort=thinking, service_tier=None),
                resource_policy=runtime_policy, resources=list(resources),
                observer_start=binding.observer_start, observer_command=command_proof(observation['extension']),
                prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(), disposition='started',
                settled=settled, idle=True, stdin_closed=process.stdin.closed,
                stdout_eof=stdout_eof, drained=not pending and not outgoing,
                pending_commands=len(binding.pending), returncode=process.returncode))
        return 0
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, subprocess.TimeoutExpired) as error:
        fault(error); return 1
    finally:
        if not process.stdin.closed: process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=SHUTDOWN_SECONDS)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=SHUTDOWN_SECONDS)
        process.stdout.close()


def main(argv=None):
    def emit(value):
        sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()
    try:
        prompt = sys.stdin.buffer.read(REQUEST_BYTES + 1)
        if len(prompt) > REQUEST_BYTES: raise TransportFault('request_limit')
        return run(sys.argv[1:] if argv is None else argv, prompt.decode('utf-8'), emit)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        emit(fault_event(error)); return 1


if __name__ == '__main__': raise SystemExit(main())
