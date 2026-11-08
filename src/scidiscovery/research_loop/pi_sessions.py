"""Exact-path Pi v3 accounting, composed from the original Pi session engine.

No discovery, authentication reads or context-to-billing conversion. Resume uses
one exact private original and a frozen append baseline, never historical debit.
Live and persisted records overlap; terminal reconciliation is post-exit.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .native_usage import FIELDS, MAX_SAFE_INTEGER, context as context_projection, context_windows

LINE_BYTES = READ_BYTES = 1024 * 1024
ANCHOR_BYTES = 4096
MAX_ENTRIES = 65536
RESUME_BYTES = 64 * 1024 * 1024
SESSION_NAME = 'pi-session.jsonl'
CATEGORIES = ('assistant', 'toolResult', 'compaction', 'branch_summary', 'usage')
CONTEXT_EVENT = 'pi_context_usage'
METADATA = frozenset({'model_change', 'thinking_level_change', 'context_edit',
    'custom', 'custom_message', 'label', 'session_info'})


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def decode(line):
    if isinstance(line, bytes):line = line.decode('utf-8')
    return json.loads(line, object_pairs_hook=_object,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid_json_constant')))


def exact_path(value):
    if not isinstance(value, (str, os.PathLike)): raise ValueError('Pi requires an exact absolute path')
    text = os.fspath(value)
    if (not isinstance(text, str) or '\0' in text or not text.startswith('/')
            or text.startswith('//') or os.path.normpath(text) != text):
        raise ValueError('Pi requires an exact normalized absolute path')
    return Path(text)


def session_path(home, workspace, session=None):
    home, workspace = exact_path(home), exact_path(workspace)
    path = home / 'sessions' / SESSION_NAME
    if session is not None:
        if (not isinstance(session, dict) or session.get('backend') != 'pi'
                or session.get('home') != str(home) or session.get('workspace') != str(workspace)
                or session.get('path') != str(path)):
            raise ValueError('Original Pi session binding changed')
    return path


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64: return False
    try: return datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    except ValueError: return False


def validate_header(line, thread, workspace):
    if not isinstance(line, (bytes, str)) or len(line) > LINE_BYTES: raise ValueError('Pi header invalid')
    header = decode(line)
    if (not isinstance(header, dict)
            or set(header) - {'type', 'version', 'id', 'timestamp', 'cwd', 'parentSession'}
            or header.get('type') != 'session' or type(header.get('version')) is not int
            or header['version'] != 3 or not isinstance(thread, str)
            or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,254}[A-Za-z0-9])?', thread)
            or header.get('id') != thread or header.get('cwd') != str(exact_path(workspace))
            or not _stamp(header.get('timestamp')) or header.get('parentSession') is not None):
        raise ValueError('Pi fresh session identity/header changed')
    return header


def validate_session_event(event, thread):
    """Pure bridge receipt check, not authorship or submission authority."""
    if (not isinstance(event, dict) or set(event) != {'type', 'thread_id', 'path', 'workspace', 'profile'}
            or event.get('type') != 'pi.session_bound' or event.get('thread_id') != thread):
        raise ValueError('Original Pi session event changed')
    exact_path(event['path']); exact_path(event['workspace'])
    from .pi_backend import validate_profile
    validate_profile(event['profile'])
    return event


def validate_resume_binding(session, workspace, home):
    """Pure lexical/original proof validation; filesystem acquisition is separate."""
    path = session_path(home, workspace, session)
    from .pi_backend import validate_profile
    validate_profile(session.get('profile'))
    validate_header(session.get('header'), session.get('thread_id'), workspace)
    identity, size, digest = session.get('identity'), session.get('size_bytes'), session.get('sha256')
    if (not isinstance(identity, list) or len(identity) != 2
            or any(type(n) is not int or n < 0 for n in identity)
            or type(size) is not int or not 0 < size <= RESUME_BYTES
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('Original Pi session proof changed')
    return path


def validate_content(content, role):
    if isinstance(content, str) and role not in {'assistant', 'toolResult'}:return
    if not isinstance(content, list):raise ValueError('Pi resume content invalid')
    allowed = {'assistant': {'text', 'thinking', 'toolCall'}, 'system': {'text'}}.get(role, {'text', 'image'})
    for block in content:
        if not isinstance(block, dict) or block.get('type') not in allowed:
            raise ValueError('Pi resume content invalid')
        kind = block['type']
        fields = {'text': ('text',), 'thinking': ('thinking',), 'image': ('data', 'mimeType'),
            'toolCall': ('id', 'name')}[kind]
        if (any(not isinstance(block.get(k), str) for k in fields)
                or kind == 'toolCall' and (not block['id'] or not isinstance(block.get('arguments'), dict))):
            raise ValueError('Pi resume content invalid')


def validate_message(message, profile):
    if (not isinstance(message, dict) or type(message.get('timestamp')) is not int
            or not 0 <= message['timestamp'] <= MAX_SAFE_INTEGER
            or message.get('role') not in {'system', 'user', 'assistant', 'toolResult', 'custom',
                'bashExecution', 'compactionSummary', 'branchSummary'}):
        raise ValueError('Pi resume message invalid')
    role = message['role']
    if role in {'system', 'user', 'assistant', 'toolResult', 'custom'}:
        validate_content(message.get('content'), role)
    if role == 'assistant':
        provider, model = profile['model'].split('/', 1)
        if (not isinstance(message.get('api'), str) or not message['api']
                or message.get('provider') != provider or message.get('model') != model
                or message.get('thinkingLevel', profile['reasoning_effort']) != profile['reasoning_effort']
                or message.get('stopReason') not in {'stop', 'toolUse', 'length', 'error', 'aborted'}
                or 'deferred' in message or usage_tokens(message.get('usage')) is None):
            raise ValueError('Pi resume model or pending request changed')
    elif role == 'toolResult':
        if (any(not isinstance(message.get(k), str) or not message[k] for k in ('toolCallId', 'toolName'))
                or type(message.get('isError')) is not bool
                or 'usage' in message and usage_tokens(message['usage']) is None):
            raise ValueError('Pi resume tool result invalid')
    elif role == 'system':
        sections = message.get('sections', {})
        if not isinstance(sections, dict) or any(v is not None and not isinstance(v, str) for v in sections.values()):
            raise ValueError('Pi resume system message invalid')
        for key in ('toolsAdded', 'toolsRemoved'):
            tools = message.get(key, [])
            if (not isinstance(tools, list) or any(not isinstance(t, dict) or not isinstance(t.get('name'), str)
                    or key == 'toolsAdded' and (not isinstance(t.get('description'), str)
                        or not isinstance(t.get('parameters'), dict)) for t in tools)):
                raise ValueError('Pi resume tool declaration invalid')
    elif role == 'custom':
        if not isinstance(message.get('customType'), str) or type(message.get('display')) is not bool:
            raise ValueError('Pi resume custom message invalid')
    elif role == 'bashExecution':
        if (any(not isinstance(message.get(k), str) for k in ('command', 'output'))
                or any(type(message.get(k)) is not bool for k in ('cancelled', 'truncated'))):
            raise ValueError('Pi resume bash message invalid')
    elif role in {'compactionSummary', 'branchSummary'} and not isinstance(message.get('summary'), str):
        raise ValueError('Pi resume summary invalid')


class ResumeEntries:
    """Strict context admission before the SDK's skip/migrate/coerce loader."""
    def __init__(self, profile):
        self.profile = profile; self.seen = set(); self.editable = {}; self.tools = set()
        self.conversation = False; self.leaf = None

    def entry(self, value):
        if (not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id']
                or len(value['id']) > 256 or value['id'] in self.seen or len(self.seen) >= MAX_ENTRIES
                or not _stamp(value.get('timestamp')) or 'parentId' not in value
                or value['parentId'] is not None and value['parentId'] not in self.seen):
            raise ValueError('Pi resume entry identity invalid')
        kind = value.get('type'); identity = value['id']
        if kind == 'message':
            message = value.get('message'); validate_message(message, self.profile)
            role = message['role']
            if role in {'user', 'assistant', 'toolResult', 'custom'}:self.editable[identity] = role
            self.conversation = self.conversation or role in {'user', 'assistant'}
            if role == 'assistant':
                for block in message['content']:
                    if block['type'] == 'toolCall':
                        if block['id'] in self.tools:raise ValueError('Pi resume duplicate tool call')
                        self.tools.add(block['id'])
            if role == 'toolResult':
                if message['toolCallId'] not in self.tools:raise ValueError('Pi resume orphan tool result')
                self.tools.remove(message['toolCallId'])
        elif kind == 'model_change':
            if self.profile['model'] != str(value.get('provider')) + '/' + str(value.get('modelId')):
                raise ValueError('Pi resume model changed')
        elif kind == 'thinking_level_change':
            if value.get('thinkingLevel') != self.profile['reasoning_effort']:raise ValueError('Pi resume thinking changed')
        elif kind == 'compaction':
            if (not isinstance(value.get('summary'), str) or type(value.get('tokensBefore')) is not int
                    or value['tokensBefore'] < 0 or value.get('firstKeptEntryId') not in self.seen | {identity}):
                raise ValueError('Pi resume compaction invalid')
            if 'systemMessage' in value:
                validate_message(value['systemMessage'], self.profile)
                if value['systemMessage']['role'] != 'system':raise ValueError('Pi resume compaction system invalid')
        elif kind == 'context_edit':
            replacement = value.get('replacement')
            if value.get('targetId') not in self.editable or 'replacement' not in value:
                raise ValueError('Pi resume context edit invalid')
            if replacement is not None:
                if not isinstance(replacement, dict) or 'content' not in replacement:
                    raise ValueError('Pi resume context edit invalid')
                if not isinstance(replacement['content'], str):
                    validate_content(replacement['content'], self.editable[value['targetId']])
        elif kind == 'custom_message':
            if not isinstance(value.get('customType'), str) or type(value.get('display')) is not bool:
                raise ValueError('Pi resume custom entry invalid')
            validate_content(value.get('content'), 'custom'); self.editable[identity] = 'custom'
        elif kind == 'branch_summary':
            if not isinstance(value.get('summary'), str) or value.get('fromId') not in self.seen:
                raise ValueError('Pi resume branch summary invalid')
        elif kind == 'custom':
            # Virtual model routing and extension state cannot silently change
            # an exact physical model's frozen binding on native reload.
            raise ValueError('Pi resume extension state unsupported')
        elif kind not in {'usage', 'label', 'session_info'}:
            raise ValueError('Pi resume entry unsupported')
        if 'usage' in value and (kind not in {'usage', 'compaction', 'branch_summary'}
                or usage_tokens(value['usage']) is None):
            raise ValueError('Pi resume entry usage invalid')
        if kind == 'usage' and usage_tokens(value.get('usage')) is None:
            raise ValueError('Pi resume entry usage invalid')
        self.seen.add(identity); self.leaf = identity


def open_original(home, path):
    home, path = exact_path(home), exact_path(path)
    if (home.resolve() != home or path.resolve() != path
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('Pi session original path is unsafe')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd); raise ValueError('Pi session original must be a private ordinary file')
    return os.fdopen(fd, 'rb')


def load_resume_original(session, workspace, home):
    """One bounded producer read, retained by the attempt owner for its meter."""
    path = validate_resume_binding(session, workspace, home)
    validator = ResumeEntries(session['profile']); digest = hashlib.sha256()
    with open_original(home, path) as stream:
        info = os.fstat(stream.fileno())
        if (list((info.st_dev, info.st_ino)) != session['identity']
                or info.st_size != session['size_bytes']):
            # Only this retained cursor belongs to the previous attempt's debit.
            # An otherwise valid append has no accounting owner here: refusing
            # it preserves that unknown, rather than forgiving or rebilling it.
            raise ValueError('Pi resume original replaced, resized or has unaccounted append')
        header = stream.readline(LINE_BYTES + 1)
        validate_header(header, session['thread_id'], workspace)
        if header.decode('utf-8') != session['header']:raise ValueError('Pi resume header changed')
        digest.update(header)
        while stream.tell() < info.st_size:
            line = stream.readline(min(LINE_BYTES + 1, info.st_size - stream.tell()))
            if not line or len(line) > LINE_BYTES or not line.endswith(b'\n'):
                raise ValueError('Pi resume partial or oversized line')
            digest.update(line); validator.entry(decode(line))
        if digest.hexdigest() != session['sha256']:raise ValueError('Pi resume original changed')
        if not header.endswith(b'\n') or not validator.conversation or validator.tools:
            raise ValueError('Pi resume empty or unsettled context')
        stream.seek(max(0, info.st_size - ANCHOR_BYTES)); anchor = stream.read(ANCHOR_BYTES)
        after = os.fstat(stream.fileno())
        if _signature(info) != _signature(after):
            raise ValueError('Pi resume original changed during admission')
    current = path.lstat()
    if (_signature(current) != _signature(info)
            or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1):
        raise ValueError('Pi resume original changed during admission')
    return dict(session=dict(session), info=info, header=header, anchor=anchor,
        digest=digest, seen=set(validator.seen), leaf=validator.leaf)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_mode, info.st_nlink)


def recheck_prefix(original, *, phase='prelaunch'):
    """Rehash the admitted original; only the authorized child may append setup.

    This check never advances the owner's proof or meter cursor. Native setup
    bytes remain in this attempt's tail for normal accounting/reconciliation.
    """
    if phase not in {'prelaunch', 'native_setup'}:raise ValueError('Pi resume check phase invalid')
    session, expected = original['session'], original['info']
    with open_original(session['home'], session['path']) as stream:
        info = os.fstat(stream.fileno())
        if ((info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)
                or info.st_size < expected.st_size or info.st_size > RESUME_BYTES
                or phase == 'prelaunch' and info.st_size != expected.st_size):
            raise ValueError('Pi resume original replaced, resized or has unaccounted append')
        digest = hashlib.sha256(); remaining = expected.st_size
        while remaining:
            chunk = stream.read(min(READ_BYTES, remaining))
            if not chunk:raise ValueError('Pi resume original truncated')
            digest.update(chunk); remaining -= len(chunk)
        if digest.digest() != original['digest'].digest():
            raise ValueError('Pi resume original changed')
        if phase == 'native_setup':
            validator = ResumeEntries(session['profile'])
            validator.seen = set(original['seen']); leaf = original['leaf']
            fields = {'model_change': {'provider', 'modelId'},
                'thinking_level_change': {'thinkingLevel'}}
            while stream.tell() < info.st_size:
                line = stream.readline(min(LINE_BYTES + 1, info.st_size - stream.tell()))
                if not line or len(line) > LINE_BYTES or not line.endswith(b'\n'):
                    raise ValueError('Pi resume setup partial or oversized line')
                value = decode(line)
                if (not isinstance(value, dict) or not isinstance(value.get('type'), str)
                        or value['type'] not in fields
                        or set(value) != {'type', 'id', 'parentId', 'timestamp'} | fields[value['type']]
                        or any(not isinstance(value[key], str) for key in fields[value['type']])
                        or value.get('parentId') != leaf):
                    raise ValueError('Pi resume work or unknown metadata before binding')
                validator.entry(value); leaf = validator.leaf
        current = Path(session['path']).lstat()
        after = os.fstat(stream.fileno())
        if (_signature(current) != _signature(info)
                or _signature(after) != _signature(info)):
            raise ValueError('Pi resume original changed')


def usage_tokens(value):
    fields = ('input', 'output', 'cacheRead', 'cacheWrite')
    if (not isinstance(value, dict) or any(type(value.get(k)) is not int
            or not 0 <= value[k] <= MAX_SAFE_INTEGER for k in fields)): return None
    total = sum(value[k] for k in fields)
    if (total > MAX_SAFE_INTEGER or 'totalTokens' in value
            and (type(value['totalTokens']) is not int or value['totalTokens'] != total)): return None
    return dict(input_tokens=value['input'] + value['cacheRead'] + value['cacheWrite'],
        cached_input_tokens=value['cacheRead'], output_tokens=value['output'])


def message_identity(message):
    stamp, role = message.get('timestamp'), message.get('role')
    if role not in ('assistant', 'toolResult'): raise ValueError('pi_message_identity_unavailable')
    parts = tuple(message.get(key) for key in
        (('provider', 'model', 'responseId') if role == 'assistant' else ('toolCallId',)))
    if (type(stamp) is not int or stamp < 0
            or any(p is not None and not isinstance(p, str) for p in parts)
            or role == 'toolResult' and not parts[0]): raise ValueError('pi_message_identity_unavailable')
    return (role, stamp, *parts)


def context_boundary(value):
    kind = value.get('type')
    if kind == 'entry_appended':
        entry = value.get('entry')
        if isinstance(entry, dict) and entry.get('type') in {
                'message', 'compaction', 'branch_summary', 'context_edit',
                'custom_message', 'model_change', 'thinking_level_change'}: return True, True
    if kind in {'message_start', 'message_update', 'compaction_start', 'auto_retry_start',
            'thinking_level_changed'}: return True, False
    return (True, True) if kind in {'message_end', 'compaction_end'} else (False, False)


def session_context(data, thread, path, observed_at):
    """Native SDK estimate, advisory only; never manufacture turn/current_window."""
    if (not isinstance(data, dict) or not isinstance(thread, str) or not thread
            or data.get('sessionId') != thread or data.get('sessionFile') != str(path)):
        return dict(status='unavailable')
    usage = data.get('contextUsage')
    if (not isinstance(usage, dict) or type(usage.get('tokens')) is not int
            or not 0 <= usage['tokens'] <= MAX_SAFE_INTEGER
            or type(usage.get('contextWindow')) is not int
            or not 0 < usage['contextWindow'] <= MAX_SAFE_INTEGER
            or type(observed_at) not in (int, float) or not 0 <= observed_at <= MAX_SAFE_INTEGER):
        return dict(status='unavailable')
    return context_projection(dict(status='last_reported', total_tokens=usage['tokens'],
        model_context_window=usage['contextWindow'], observed_at=observed_at))


def validated_context(value):
    safe = context_projection(value)
    return safe if safe == value and safe.get('status') == 'last_reported' and 'observed_at' in safe else dict(status='unavailable')


def _zeros(): return dict.fromkeys(FIELDS, 0)


class UsageEvents:
    """Pure live engine: category sums and uncorrelated late lower bounds."""
    def __init__(self):
        self.live = {key: _zeros() for key in CATEGORIES}
        self.live_counts = dict.fromkeys(CATEGORIES, 0)
        self.live_missing = dict.fromkeys(CATEGORIES, 0)
        self.observed = False
        self.fault = None
        self.records = {}
        self.floors = {key: _zeros() for key in CATEGORIES}
        self.partial_open = False
        self.partial_tokens = _zeros()
        self.context = dict(status='unavailable')

    def unknown(self, reason): self.fault = self.fault or reason

    def begin(self):
        if self.partial_open: self.unknown('pi_partial_message_unfinished')
        self.partial_open = True
        self.partial_tokens = _zeros()

    def partial(self, value):
        if not self.partial_open: self.begin()
        converted = usage_tokens(value)
        if converted is None:
            self.unknown('pi_partial_usage_invalid'); return
        for key in FIELDS:
            latest = max(self.partial_tokens[key], converted[key])
            self.live['assistant'][key] += latest - self.partial_tokens[key]
            self.partial_tokens[key] = latest
        self.observed = True

    def add(self, value, *, category='assistant', identity=None):
        self.live_counts[category] += 1
        converted = usage_tokens(value)
        if identity is not None: self.records[identity] = converted
        if converted is None: self.live_missing[category] += 1
        if category == 'assistant' and self.partial_open:
            for key in FIELDS:
                if converted is not None and converted[key] < self.partial_tokens[key]:
                    self.unknown('pi_partial_usage_regressed')
                self.live[category][key] += max(self.partial_tokens[key], (converted or _zeros())[key]) - self.partial_tokens[key]
            self.partial_open = False
            self.partial_tokens = _zeros()
        elif converted is not None:
            for key in FIELDS: self.live[category][key] += converted[key]
        if converted is not None: self.observed = True

    def floor(self, value, *, category='assistant'):
        converted = usage_tokens(value)
        if converted is None:
            self.unknown('pi_late_usage_invalid'); return
        for key in FIELDS: self.floors[category][key] = max(self.floors[category][key], converted[key])
        self.observed = True


class SessionUsage:
    """One append-only original; finite post-stat reads, no historical lookup."""
    def __init__(self, home, workspace, session=None, *, context_state=None, resume_original=None):
        self.home, self.workspace = exact_path(home), exact_path(workspace)
        self.path = session_path(home, workspace, session)
        self.thread = session['thread_id'] if session is not None else None
        self.resumed = session is not None
        self.profile = session['profile'] if session is not None else None
        self.digest = hashlib.sha256()
        self.verified_offset = None
        self.baseline_offset = 0
        self.context_state = context_windows(context_state)
        self.offset = 0
        self.pending = b''
        self.skipping = False
        self.identity = self.header = self.mtime = None
        self.anchor = b''
        self.reason = 'session_unavailable'
        self.fault = None
        self.known = _zeros()
        self.observed = self.finished = False
        self.persisted = {key: _zeros() for key in CATEGORIES}
        self.counts = dict.fromkeys(CATEGORIES, 0)
        self.seen = set()
        self.persisted_messages = {}
        self.events = None
        self.capabilities = {}
        self.observation = None
        self.metadata_callback = None
        self.original_leaf = resume_original['leaf'] if resume_original is not None else None
        self.original_count = len(resume_original['seen']) if resume_original is not None else 0
        if self.resumed:
            if resume_original is None or resume_original['session'] != session:
                raise ValueError('Pi resume meter requires the owner-acquired original')
            info = resume_original['info']
            self.offset = self.baseline_offset = session['size_bytes']
            self.identity = (info.st_dev, info.st_ino)
            self.header, self.anchor = resume_original['header'], resume_original['anchor']
            self.mtime = info.st_mtime_ns
            self.digest = resume_original['digest'].copy()
            self.seen = set(resume_original['seen'])
            self.reason = 'token_counter_unreported'

    def _open(self):
        return open_original(self.home, self.path)

    def session_binding(self):
        # Only stopped-writer, rehashed bytes can become a reusable proof.
        # Live incremental hashes/anchors alone cannot attest an early prefix.
        if (self.verified_offset != self.offset or self.identity is None
                or self.header is None or self.profile is None or self.fault or self.reason
                or self.pending or self.skipping):
            return None
        return dict(backend='pi', home=str(self.home), workspace=str(self.workspace), path=str(self.path),
            thread_id=self.thread, profile=dict(self.profile), header=self.header.decode('utf-8'),
            identity=list(self.identity), size_bytes=self.offset, sha256=self.digest.hexdigest())

    def bind_thread(self, thread):
        if (not isinstance(thread, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,254}[A-Za-z0-9])?', thread)):
            self.fault = 'native_thread_identity_invalid'
        elif self.thread is not None and self.thread != thread: self.fault = 'native_thread_identity_changed'
        else: self.thread = thread

    def use_events(self, events):
        self.events = events
        self._project()

    def _project(self):
        if self.events is not None: self.observed = self.observed or self.events.observed
        self.known = {key: sum(max(self.persisted[category][key],
            self.events.live[category][key] if self.events else 0,
            self.events.floors[category][key] if self.events else 0)
            for category in CATEGORIES) for key in FIELDS}

    def _entry(self, line):
        value = decode(line)
        if not isinstance(value, dict): raise ValueError('invalid_session_entry')
        entry_id = value.get('id')
        if (not isinstance(entry_id, str) or not entry_id or len(entry_id) > 256
                or entry_id in self.seen or len(self.seen) >= MAX_ENTRIES
                or not _stamp(value.get('timestamp')) or 'parentId' not in value
                or value['parentId'] is not None and (not isinstance(value['parentId'], str)
                    or value['parentId'] not in self.seen)):
            raise ValueError('invalid_session_entry_identity')
        self.seen.add(entry_id)
        if self.observation is not None: self.observation.entry(value)
        kind = category = value.get('type')
        usage = value.get('usage')
        if kind == 'message':
            message = value.get('message')
            if not isinstance(message, dict): raise ValueError('invalid_session_message')
            category = message.get('role')
            if category not in {'assistant', 'toolResult', 'user', 'system', 'custom',
                    'bashExecution', 'compactionSummary', 'branchSummary'}: raise ValueError('unknown_session_message')
            if category != 'assistant' and 'usage' not in message: return
            if category not in {'assistant', 'toolResult'}: raise ValueError('unknown_message_usage')
            identity = message_identity(message)
            if identity in self.persisted_messages: raise ValueError('pi_message_duplicated')
            usage = message.get('usage')
        elif kind in METADATA:
            if 'usage' in value: raise ValueError('unknown_entry_usage')
            return
        elif kind not in {'usage', 'compaction', 'branch_summary'}: raise ValueError('unknown_session_entry')
        converted = usage_tokens(usage)
        if converted is None: raise ValueError('pi_session_usage_unreported')
        if kind == 'message': self.persisted_messages[identity] = converted
        for key in FIELDS: self.persisted[category][key] += converted[key]
        self.counts[category] += 1
        self.observed = True

    def _poll_original(self):
        """Acquire one bounded append delta, without polling metadata recursively."""
        self.verified_offset = None
        if self.fault: return
        try:
            with self._open() as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                if self.identity is not None and (identity != self.identity or info.st_size < self.offset):
                    raise ValueError('session_log_replaced_or_truncated')
                line = stream.readline(min(LINE_BYTES + 1, info.st_size))
                if not line.endswith(b'\n') or len(line) > LINE_BYTES:
                    self.reason = 'session_partial_header'; return
                header = decode(line)
                thread = self.thread if self.thread is not None else header.get('id') if isinstance(header, dict) else None
                validate_header(line, thread, self.workspace)
                if self.header is not None and line != self.header: raise ValueError('session_header_changed')
                if self.identity is None:
                    self.identity, self.header = identity, line
                    self.bind_thread(thread)
                    self.offset = len(line)
                    self.digest.update(line)
                elif self.anchor:
                    stream.seek(self.offset - len(self.anchor))
                    if stream.read(len(self.anchor)) != self.anchor: raise ValueError('session_log_changed')
                    if info.st_size == self.offset and self.mtime != info.st_mtime_ns: raise ValueError('session_log_changed')
                stream.seek(self.offset)
                data = stream.read(min(READ_BYTES, info.st_size - self.offset))
                self.digest.update(data)
                self.offset += len(data)
                chunks = data.split(b'\n')
                for index, part in enumerate(chunks):
                    if not self.skipping:
                        self.pending += part
                        if len(self.pending) > LINE_BYTES:
                            self.pending = b''; self.skipping = True
                            self.fault = self.fault or 'session_line_oversized'
                    if index < len(chunks) - 1:
                        if not self.skipping:
                            try: self._entry(self.pending)
                            except (ValueError, TypeError, UnicodeError, RecursionError):
                                self.fault = self.fault or 'pi_session_usage_invalid'
                        self.pending = b''; self.skipping = False
                stream.seek(max(0, self.offset - ANCHOR_BYTES))
                self.anchor = stream.read(min(self.offset, ANCHOR_BYTES))
                self.mtime = info.st_mtime_ns
                self.reason = ('session_read_backlog' if self.offset < info.st_size
                    else 'session_partial_line' if self.pending or self.skipping
                    else None if self.observed else 'token_counter_unreported')
        except FileNotFoundError:
            if self.identity is not None: self.fault = 'session_log_missing'
            self.reason = 'session_unavailable'
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            self.fault = self.fault or 'session_log_changed_or_invalid'

    def poll(self):
        self.finished = False
        self._poll_original()
        if self.observation is not None: self.observation.poll()
        return self.snapshot()

    def refresh_pending_original(self):
        """Refresh mutable originals AFTER acquisition of a pending delivery.

        A large append is deferred across bounded reads within the delivery's
        existing deadline. Neither the frozen baseline nor billing is reset.
        A post-exit refresh must restore the stopped-writer proof/reconciliation;
        it cannot silently discard verification performed by terminal().
        """
        self._poll_original()
        if self.finished: self._finish_original()

    def _verify_stopped_original(self):
        """One bounded post-exit rehash, never a full scan on a live poll.

        Compare with the incremental observation, not a newly blessed digest.
        A rewrite invalidates coverage/proof without erasing known billing.
        """
        try:
            with self._open() as stream:
                info = os.fstat(stream.fileno())
                if ((info.st_dev, info.st_ino) != self.identity or info.st_size != self.offset
                        or info.st_size > RESUME_BYTES):
                    raise ValueError('session_terminal_snapshot_unavailable')
                digest = hashlib.sha256(); remaining = self.offset
                while remaining:
                    chunk = stream.read(min(READ_BYTES, remaining))
                    if not chunk:raise ValueError('session_terminal_snapshot_truncated')
                    digest.update(chunk); remaining -= len(chunk)
                after = os.fstat(stream.fileno())
                current = self.path.lstat()
                if (digest.digest() != self.digest.digest()
                        or _signature(info) != _signature(after)
                        or _signature(info) != _signature(current)):
                    raise ValueError('session_terminal_snapshot_changed')
            self.verified_offset = self.offset
        except (OSError, ValueError):
            self.fault = self.fault or 'session_terminal_original_changed_or_unverified'

    def terminal(self):
        self.poll()
        return self._finish_original()

    def _finish_original(self):
        self.finished = True
        if not self.fault and self.reason is None:
            self._verify_stopped_original()
        if self.events is not None and self.events.partial_open: self.reason = 'pi_partial_message_unfinished'
        if not self.fault and self.reason is None and self.events is not None:
            for identity, usage in self.events.records.items():
                persisted = self.persisted_messages.get(identity)
                if persisted is None or usage is not None and persisted != usage:
                    self.reason = 'pi_session_message_unreconciled'; break
            for category in CATEGORIES:
                if (self.counts[category] < self.events.live_counts[category]
                        or any(self.persisted[category][key] < max(self.events.live[category][key],
                            self.events.floors[category][key]) for key in FIELDS)):
                    self.reason = self.reason or 'pi_session_events_unreconciled'
        return self.snapshot()

    def snapshot(self):
        self._project()
        reason = self.fault or (self.events.fault if self.events else None) or self.reason
        result = dict(source='pi_session_usage', thread_id=self.thread, tokens=dict(self.known),
            coverage='unknown' if reason else 'terminal' if self.finished else 'live', reason=reason,
            has_observation=self.observed, baseline_tokens=_zeros() if self.resumed else None,
            baseline_offset=self.baseline_offset, offset=self.offset,
            session_binding=self.session_binding(),
            context=dict(status='unavailable'),
            context_estimate=(dict(self.events.context) if not reason and self.events else dict(status='unavailable')),
            context_estimate_scope='native_sdk_estimate_not_qualified_current_window',
            turn_id=None, calls=dict(completed=0, failed=0, cancelled=0),
            observation_capabilities={k: dict(v) for k, v in self.capabilities.items()},
            context_windows=dict(coverage='unknown',
                windows_started=(self.context_state or {}).get('windows_started', 0),
                reason='context_capability_unavailable'),
            reporting_delay_unmeasured=not (self.finished and reason is None),
            inflight_usage_unknown=not (self.finished and reason is None),
            unreported_usage=None if self.finished and reason is None else 'Running requests and reporting delay are unmeasured.',
            individual_request_usage='unavailable')
        if self.observation is not None:
            result.update(self.observation.snapshot())
        return result
