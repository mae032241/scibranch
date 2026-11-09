"""Native agent adapters. Execution, budgets and scientific verdicts stay in control.

The protocol adapter runs exact operator argv, reads one JSON request from stdin,
and accepts JSONL ``session``, ``usage``, ``result`` and ``completed`` events.
Usage is cumulative for this invocation; absent counters remain unknown. A result
event contains ``result`` (the scientific JSON object). Completion is explicit.
This transport does not attest complete authorship or implement session resume.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import hashlib
import copy
import shutil
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from .native_usage import BasicObservation, SessionUsage, tokens, transport_diagnostic

DEFAULT_BACKEND = 'codex'

JSON_RESPONSE_INSTRUCTION = 'Return only one JSON object as your final response, with no Markdown fences or surrounding prose.'


class Backend(ABC):
    """Complete provider boundary for one accepted native invocation.

    Host clocks, scientific decisions and experiment executors remain outside
    this interface. Unsupported interactions return explicit capability facts
    or raise before submission; optional telemetry remains unknown.
    """
    def subagent_capability(self):
        """Optional native capability; older providers remain unsupported."""
        return dict(status='unsupported', reason='native_subagents_unsupported')

    @property
    @abstractmethod
    def name(self): ...
    @property
    @abstractmethod
    def standard(self): ...
    @property
    @abstractmethod
    def supports_resume(self): ...
    @property
    @abstractmethod
    def complete_authorship(self): ...
    @property
    @abstractmethod
    def instruction_transport(self): ...
    @abstractmethod
    def launch(self, profile, work, pending, schema, instruction): ...
    @abstractmethod
    def resume(self, args, profile, work, pending, schema, session): ...
    @abstractmethod
    def request(self, prompt, **context): ...
    @abstractmethod
    def meter(self, home, work, session=None, *, context_state=None):
        """Return a provider-owned opaque collector; control must not inspect its attributes."""
        ...
    @abstractmethod
    def event(self, value): ...
    @abstractmethod
    def public_events(self, value): ...
    @abstractmethod
    def identity(self, meter, session): ...
    @abstractmethod
    def transport_args(self, args): ...
    @abstractmethod
    def event_selector(self,*,source): ...
    @abstractmethod
    def for_job(self, job): ...
    @abstractmethod
    def apply_home(self, env, home, *, isolated): ...
    @abstractmethod
    def actual_home(self, env, work, home, *, isolated): ...
    @abstractmethod
    def initial_notice(self, namespace_launcher): ...
    @abstractmethod
    def stage_notice(self, work): ...
    @abstractmethod
    def prepare_launch(self, args, work, attempt, env, view_facts, context, *, diagnostic=None): ...
    @abstractmethod
    def configure_observation(self, meter, work, attempt, thread, channel, *, required): ...
    @abstractmethod
    def finish_observation(self, meter, *, required): ...
    @abstractmethod
    def terminal_usage(self, event, meter): ...
    @abstractmethod
    def retain_terminal(self, meter, observation, terminal): ...
    @abstractmethod
    def recovery_capability(self, kind): ...
    @abstractmethod
    def authorship_scope(self, binding): ...
    @abstractmethod
    def validate_participant_event(self, event, outcome, intent, workspace): ...
    @abstractmethod
    def authorship_proof_kind(self, binding): ...
    @abstractmethod
    def prepare_execution(self, root, step, record, work, home, args, *, resume_position, job, intent, settings): ...
    @abstractmethod
    def validate_resume_session(self, session, workspace, home): ...
    @abstractmethod
    def format_rejection(self, event): ...
    @abstractmethod
    def format_observation(self, event): ...
    @abstractmethod
    def validate_session_event(self, event, thread): ...
    @abstractmethod
    def validate_configuration(self, options): ...
    @abstractmethod
    def permissions(self, settings): ...
    @abstractmethod
    def launcher_contract(self, launcher, *, retained): ...
    @abstractmethod
    def project_skills(self, root, settings, job): ...
    @abstractmethod
    def namespace_launcher(self, settings): ...
    @abstractmethod
    def validate_profile(self, profile): ...
    @abstractmethod
    def observation_capability(self, fact): ...
    @abstractmethod
    def observe(self, meter, event): ...
    @abstractmethod
    def snapshot_observation(self, meter):
        """Expose public observation facts from the opaque collector.

        Control consumes turn identity through ``turn_id`` here. An absent or
        None value remains unbound and does not authorize original-call replay.
        """
        ...
    @abstractmethod
    def poll_observation(self, meter): ...
    @abstractmethod
    def observation_fault(self, meter): ...
    @abstractmethod
    def response_guidance(self, schema): ...
    @abstractmethod
    def compose_instructions(self, prompt, instruction, transport): ...
    @abstractmethod
    def terminal_message(self, event): ...
    @abstractmethod
    def terminal_binding(self, event): ...
    @abstractmethod
    def terminal_reconciliation_candidate(self, outcome): ...
    @abstractmethod
    def acquire_terminal_evidence(self, record, outcome, *, output_path=None): ...
    @abstractmethod
    def reconcile_terminal(self, outcome, *, meter, evidence, original_record): ...
    @abstractmethod
    def terminal_failure_evidence(self, failures): ...
    @property
    @abstractmethod
    def schema_transport(self): ...
    @property
    @abstractmethod
    def active_schema_transport(self): ...
    @property
    @abstractmethod
    def terminal_source(self): ...
    @property
    @abstractmethod
    def supports_native_schema_flag(self): ...
    @staticmethod
    @abstractmethod
    def default_launcher(): ...
    @staticmethod
    @abstractmethod
    def profile_defaults(): ...
    @staticmethod
    @abstractmethod
    def declaration_options(options, *, base): ...
    @abstractmethod
    def public_options(self, options): ...
    @staticmethod
    @abstractmethod
    def configuration_fields(): ...
    @classmethod
    @abstractmethod
    def readonly_capability(cls, settings): ...
    @abstractmethod
    def launch_readonly(self, profile, work, pending, schema, instruction): ...
    @abstractmethod
    def acquire_response_file(self, pending, *, max_bytes): ...
    @staticmethod
    @abstractmethod
    def default_home(): ...
    @abstractmethod
    def default_resume_policy(self): ...
    @abstractmethod
    def validate_resume_policy(self, value): ...
    @abstractmethod
    def project_policy(self, settings): ...
    @abstractmethod
    def default_experiment_mode(self): ...
    @abstractmethod
    def skill_loading_contract(self, launcher, *, retained=None): ...


def _native_options(supplied):
    supplied = {} if supplied is None else supplied
    if not isinstance(supplied, dict) or set(supplied)-{'workspace','network','web','subagents','sandbox_workspace_write','features'}:
        raise ValueError('Unsupported native agent option')
    options = dict(workspace='workspace-write', network=False, web='disabled', subagents=False)
    options.update(supplied)
    if not isinstance(options['workspace'],str) or options['workspace'] not in {'read-only','workspace-write','danger-full-access'}:
        raise ValueError('Unsupported native workspace permission mode')
    if type(options['network']) is not bool or type(options['subagents']) is not bool:
        raise ValueError('Native network and subagents declarations must be booleans')
    if not isinstance(options['web'],str) or options['web'] not in {'disabled','cached','live'}:
        raise ValueError('Native web must be disabled, cached or live')
    sandbox = options.get('sandbox_workspace_write', {})
    if (not isinstance(sandbox, dict) or set(sandbox)-{'writable_roots','network_access','exclude_tmpdir_env_var','exclude_slash_tmp'}
            or any(type(item) is not bool for key,item in sandbox.items() if key!='writable_roots')
            or ('writable_roots' in sandbox and (not isinstance(sandbox['writable_roots'],list)
                or any(not isinstance(p,str) or not Path(p).is_absolute() for p in sandbox['writable_roots'])))):
        raise ValueError('Unsupported native workspace configuration')
    if 'network_access' in sandbox and sandbox['network_access'] != options['network']:
        raise ValueError('Conflicting native network declarations')
    features = options.get('features', {})
    if not isinstance(features,dict) or set(features)-{'fast_mode'} or any(type(v) is not bool for v in features.values()):
        raise ValueError('Unsupported native feature declaration')
    return options


def configuration(backend='codex', launcher=None, native_options=None, *, existing=None):
    if not isinstance(backend,str) or backend not in _PROVIDERS:
        raise ValueError('Unsupported native backend')
    if launcher is None:
        launcher = _PROVIDERS[backend].default_launcher()
    if (not isinstance(launcher, (list, tuple)) or not launcher
            or any(not isinstance(item, str) or not item or '\0' in item for item in launcher)):
        raise ValueError('Native launcher must be a nonempty argv')
    options = _PROVIDERS[backend].validate_configuration(native_options)
    frozen = dict(native_backend=backend, native_launcher_argv=list(launcher),
                  native_options=options if native_options is not None else None)
    frozen['native_permissions'] = permission_summary(frozen)
    adapter = selected(frozen)
    retained=(existing or {}).get('native_capabilities',{}).get('native_launcher_contract',...)
    if existing is not None and retained is ...:
        raise ValueError('Frozen native namespace launcher contract is missing')
    namespace=adapter.launcher_contract(list(launcher),retained=retained)
    frozen['native_capabilities'] = dict(native_launcher_contract=namespace,response_schema_transport=adapter.schema_transport,
        session_resume=adapter.supports_resume, complete_native_authorship=adapter.complete_authorship)
    return frozen


def permission_summary(settings):
    """Distinguish desired options from the access policy actually selected.

    These are native policy declarations, not a same-user security boundary or
    a promise that a tool, network destination or helper is available.
    """
    return selected(settings).permissions(settings)


def _codex_permissions(settings):
    options = settings.get('native_options')
    fields = ('workspace','network','web','subagents')
    requested = {key:options[key] for key in fields} if options is not None else {}
    launcher = settings['native_launcher_argv']
    native = len(launcher)==1 and Path(launcher[0]).name=='codex'
    if not native:
        return dict(requested=requested,effective=dict.fromkeys(fields,'unknown'),
                    enforcement=dict.fromkeys(fields,'hint_only'),
                    limitations=['The launcher owns access controls; declarations do not establish isolation.'])
    if options is None:
        return dict(requested=requested,effective=dict.fromkeys(fields,'unknown'),
                    enforcement=dict.fromkeys(fields,'inherited_native_configuration'),
                    limitations=['This retained instance has no frozen framework permission declaration.'])
    mode = options['workspace']
    network = True if mode=='danger-full-access' else False if mode=='read-only' else options['network']
    effective = dict(requested,network=network)
    enforcement = dict(workspace='unrestricted' if mode=='danger-full-access' else 'native_sandbox',
                       network='unsupported' if mode=='danger-full-access' else 'native_sandbox',
                       web='native_configuration',subagents='native_configuration')
    limitations = []
    if mode=='danger-full-access':
        limitations.append('danger-full-access provides no filesystem or network sandbox.')
        if not options['network']:
            limitations.append('The requested offline setting is not enforced in danger-full-access mode.')
    if mode=='read-only' and options['network']:
        limitations.append('The workspace-write network option does not enable networking in read-only mode.')
    return dict(requested=requested,effective=effective,enforcement=enforcement,limitations=limitations)


def selected(settings, *, schema_transport=None):
    backend=settings.get('native_backend','codex')
    provider=_PROVIDERS.get(backend) if isinstance(backend,str) else None
    if provider is None:raise ValueError('Unsupported frozen native backend')
    return provider(settings,schema_transport=schema_transport)


def _toml(value):
    if isinstance(value,dict):
        return '{'+','.join(json.dumps(k)+'='+_toml(v) for k,v in value.items())+'}'
    if isinstance(value,(list,tuple)):
        return '['+','.join(_toml(item) for item in value)+']'
    return json.dumps(value, ensure_ascii=False)


def _usage(value):
    return tokens(dict(cached_input_tokens=0, **value)) if isinstance(value,dict) and 'cached_input_tokens' not in value else tokens(value)


def _observe_meter(meter,event):
    if 'session' in event: meter.bind_thread(event['session'])
    if 'live_usage' in event: meter.observe(event['live_usage'])
    if 'turn' in event: meter.bind_turn(event['turn'])
    if event.get('compaction'): meter.invalidate_context(freshness_boundary=True)
    if 'native_call' in event: meter.observe_call(event['native_call'])
    if 'context' in event:
        meter.poll()
        meter.observe_context(event['context'])


class CodexEvents:
    """Select native tagged controls and compact public messages at their header.

    Command/tool bodies stay only in the original byte log. This selector never
    decodes, validates or searches their JSON. Native exec's tagged root and item
    headers are the producer contract; unrecognized controls confer no facts.
    """
    HEADER_BYTES = 512
    VIEW_BYTES = 65536
    _string = rb'"(?:[^"\\]|\\.)*"'
    _root = re.compile(rb'^\s*\{\s*"type"\s*:\s*"([a-z_.]+)"')
    _message = re.compile(rb'^\s*\{\s*"type"\s*:\s*"item\.completed"\s*,\s*"item"\s*:\s*\{\s*'
        rb'(?:"id"\s*:\s*' + _string + rb'\s*,\s*)?"type"\s*:\s*"agent_message"')

    def __init__(self):
        self.pending = b''
        self.selected = None
        self.kind = None
        self.nonempty=False

    def feed(self, data, *, final=False):
        events = []
        if final and (self.pending or self.selected is not None or self.kind):
            data += b'\n'
        for part in data.splitlines(keepends=True):
            complete = part.endswith(b'\n')
            if not self.nonempty and part.lstrip(b' \r\n\t'):self.nonempty=True
            if self.selected is not False:
                allowance = self.VIEW_BYTES if self.selected else self.HEADER_BYTES
                taken = min(len(part), max(0, allowance + 1 - len(self.pending)))
                self.pending += part[:taken]
                if self.selected is None:
                    root = self._root.match(self.pending)
                    if root:
                        self.kind = root[1].decode('ascii')
                        if self.kind.startswith('item.'):
                            if self._message.match(self.pending):
                                self.selected = True
                            elif len(self.pending) > self.HEADER_BYTES or complete:
                                self.selected = False
                        else:
                            self.selected = self.kind in {'thread.started', 'thread.observed', 'turn.completed',
                            'turn.failed', 'turn.incomplete', 'turn.interrupted', 'turn.started',
                            'context.reported', 'context.compacted', 'transport.diagnostic', 'error'}
                    elif len(self.pending) > self.HEADER_BYTES or complete:
                        self.selected = False
                    # The same chunk may contain a selected message/control.
                    if self.selected and taken < len(part):
                        self.pending += part[taken:taken + max(0, self.VIEW_BYTES + 1 - len(self.pending))]
                if self.selected and len(self.pending) > self.VIEW_BYTES:
                    self.selected = False
                if self.selected is False:
                    self.pending = b''
            if complete:
                if self.selected:
                    try:
                        value = json.loads(self.pending)
                        if isinstance(value, dict): events.append(value)
                        elif self.nonempty:events.append(dict(type='opaque.activity'))
                    except (ValueError, RecursionError):
                        if self.nonempty:events.append(dict(type='opaque.activity'))
                elif self.kind and self.kind.startswith('item.'):
                    events.append(dict(type=self.kind))  # Activity only, with no behavioral body.
                elif self.nonempty:events.append(dict(type='opaque.activity'))
                self.pending, self.selected, self.kind = b'', None, None
                self.nonempty=False
        return events


class _CanonicalType:
    """Find only the root discriminator, independent of root key order."""
    def __init__(self):
        self.state='start';self.key=None;self.kind=None;self.invalid=False
        self.saw_type=False
        self.quoted=None;self.escape=False;self.word=bytearray();self.depth=0
        self.container_string=False;self.container_escape=False

    def feed(self,data):
        for char in data:
            if self.kind is not None and self.kind!='result' or self.invalid:return
            state=self.state
            if state=='start':
                if char in b' \r\n\t':continue
                if char!=123:self.invalid=True;return
                self.state='key';continue
            if state=='key':
                if char in b' \r\n\t':continue
                if char==125:self.state='done';continue
                if char!=34:self.invalid=True;return
                self.quoted='key';self.word=bytearray(b'"');self.escape=False;self.state='quoted';continue
            if state=='quoted':
                if self.quoted in ('key','type'):
                    self.word.append(char)
                    if len(self.word)>512:self.invalid=True;return
                if self.escape:self.escape=False;continue
                if char==92:self.escape=True;continue
                if char!=34:continue
                if self.quoted=='skip':self.state='after';continue
                try:word=json.loads(bytes(self.word))
                except (ValueError,RecursionError):self.invalid=True;return
                if self.quoted=='key':
                    self.key=word;self.state='colon'
                    if word=='type':
                        if self.saw_type:self.invalid=True;return
                        self.saw_type=True
                elif isinstance(word,str):self.kind=word;self.state='after'
                else:self.invalid=True
                continue
            if state=='colon':
                if char in b' \r\n\t':continue
                if char!=58:self.invalid=True;return
                self.state='value';continue
            if state=='value':
                if char in b' \r\n\t':continue
                if self.key=='type':
                    if char!=34:self.invalid=True;return
                    self.quoted='type';self.word=bytearray(b'"');self.escape=False;self.state='quoted';continue
                if char==34:self.quoted='skip';self.escape=False;self.state='quoted';continue
                if char in (123,91):
                    self.depth=1;self.container_string=False;self.container_escape=False;self.state='container';continue
                if char in (44,125):self.invalid=True;return
                self.state='scalar';continue
            if state=='container':
                if self.container_string:
                    if self.container_escape:self.container_escape=False
                    elif char==92:self.container_escape=True
                    elif char==34:self.container_string=False
                elif char==34:self.container_string=True
                elif char in (123,91):self.depth+=1
                elif char in (125,93):
                    self.depth-=1
                    if not self.depth:self.state='after'
                continue
            if state=='scalar':
                if char in b' \r\n\t':self.state='after'
                elif char==44:self.state='key'
                elif char==125:self.state='done'
                continue
            if state=='after':
                if char in b' \r\n\t':continue
                if char==44:self.state='key'
                elif char==125:self.state='done'
                else:self.invalid=True;return
            elif state=='done' and char not in b' \r\n\t':self.invalid=True;return


class CanonicalEvents:
    """Protocol-owned observer: opaque tools never enter a JSON decoder.

    Only explicitly tagged scientific ``result`` payloads retain the original
    full JSON semantics and have no compact-view byte limit.
    """
    VIEW_BYTES=65536
    _string=re.compile(rb'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"')
    _scalar=re.compile(rb'(?:-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null)')
    _metadata={'session','usage','context','native_call','compaction','turn.started','completed','failed','error','message','transport.diagnostic'}

    def __init__(self,source):
        self.source=Path(source);self.source_identity=None
        self.position=0;self.line_start=0;self.nonempty=False;self.header=_CanonicalType()

    def _original_line(self,length):
        descriptor=os.open(self.source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        with os.fdopen(descriptor,'rb') as stream:
            info=os.fstat(stream.fileno());identity=(info.st_dev,info.st_ino)
            if not stat.S_ISREG(info.st_mode) or self.source_identity is not None and identity!=self.source_identity:
                raise ValueError('Canonical original stream changed')
            self.source_identity=identity
            stream.seek(self.line_start);data=stream.read(length)
        if len(data)!=length:raise ValueError('Canonical original stream not published')
        return data

    @classmethod
    def _project(cls,line):
        meter={key:True for key in ('input_tokens','cached_input_tokens','output_tokens')}
        shape=dict(type=True,session_id=True,turn_id=True,usage=meter,call_id=True,status=True,text=True,
            diagnostic={key:True for key in ('phase','code','original_call_state','thread_bound','turn_bound')},
            context={key:True for key in ('status','total_tokens','model_context_window','reported_at','observed_at',
                'thread_id','turn_id','current_window')})
        position=0
        def whitespace():
            nonlocal position
            while position<len(line) and line[position] in b' \r\n\t':position+=1
        def skip(depth=0):
            nonlocal position
            if depth>16:raise ValueError('Canonical metadata nesting invalid')
            whitespace()
            if position>=len(line):raise ValueError('Canonical metadata incomplete')
            if line[position]==34:
                match=cls._string.match(line,position)
                if match is None:raise ValueError('Canonical string framing invalid')
                position=match.end();return
            if line[position] in (123,91):
                object_value=line[position]==123;closing=125 if object_value else 93
                position+=1;whitespace()
                if position<len(line) and line[position]==closing:position+=1;return
                while True:
                    if object_value:
                        whitespace();key=cls._string.match(line,position)
                        if key is None:raise ValueError('Canonical opaque key framing invalid')
                        position=key.end();whitespace()
                        if position>=len(line) or line[position]!=58:raise ValueError('Canonical opaque separator invalid')
                        position+=1
                    skip(depth+1);whitespace()
                    if position>=len(line):raise ValueError('Canonical opaque container incomplete')
                    delimiter=line[position];position+=1
                    if delimiter==closing:return
                    if delimiter!=44:raise ValueError('Canonical opaque separator invalid')
            match=cls._scalar.match(line,position)
            if match is None:raise ValueError('Canonical scalar framing invalid')
            position=match.end()
        def value(selected,depth=0):
            nonlocal position
            if depth>16:raise ValueError('Canonical metadata nesting invalid')
            whitespace()
            if selected is None:skip();return None
            if position>=len(line):raise ValueError('Canonical metadata incomplete')
            if isinstance(selected,dict) and line[position]==123:
                position+=1;whitespace();output={};seen=set()
                if position<len(line) and line[position]==125:position+=1;return output
                while True:
                    whitespace();match=cls._string.match(line,position)
                    if match is None:raise ValueError('Canonical metadata key invalid')
                    key=json.loads(match[0]);position=match.end();whitespace()
                    if key in seen:raise ValueError('Canonical metadata key duplicated')
                    seen.add(key)
                    if position>=len(line) or line[position]!=58:raise ValueError('Canonical metadata separator invalid')
                    position+=1;child=selected.get(key);found=value(child,depth+1)
                    if child is not None:output[key]=found
                    whitespace()
                    if position>=len(line):raise ValueError('Canonical metadata incomplete')
                    delimiter=line[position];position+=1
                    if delimiter==125:return output
                    if delimiter!=44:raise ValueError('Canonical metadata separator invalid')
            if selected is not True:skip();return None
            match=(cls._string if line[position]==34 else cls._scalar).match(line,position)
            if match is None:raise ValueError('Canonical metadata scalar invalid')
            position=match.end();return json.loads(match[0])
        projected=value(shape);whitespace()
        if position!=len(line):raise ValueError('Canonical metadata trailing bytes')
        return projected

    def feed(self,data,*,final=False):
        events=[]
        def finish():
            length=self.position-self.line_start;kind=self.header.kind
            if not self.nonempty:return
            if self.header.invalid:events.append(dict(type='opaque.activity'))
            elif kind=='tool':events.append(dict(type='tool'))
            elif kind=='result':
                try:
                    value=json.loads(self._original_line(length))
                    events.append(value if isinstance(value,dict) and value.get('type')=='result' else dict(type='opaque.activity'))
                except (ValueError,RecursionError):events.append(dict(type='opaque.activity'))
            elif kind in self._metadata and length<=self.VIEW_BYTES:
                try:events.append(self._project(self._original_line(length)))
                except (ValueError,RecursionError):events.append(dict(type='opaque.activity'))
            else:events.append(dict(type='opaque.activity'))
        for part in data.splitlines(keepends=True):
            complete=part.endswith(b'\n')
            if not self.nonempty and part.lstrip(b' \r\n\t'):self.nonempty=True
            self.header.feed(part);self.position+=len(part)
            if complete:
                finish();self.line_start=self.position;self.header=_CanonicalType();self.nonempty=False
        if final and self.position>self.line_start:
            finish();self.line_start=self.position;self.header=_CanonicalType();self.nonempty=False
        return events


class Codex(Backend):
    name = 'codex'
    standard = False
    schema_transport = None
    active_schema_transport = None
    supports_native_schema_flag = False
    terminal_source = 'turn.completed'
    supports_resume = True

    @staticmethod
    def default_launcher(): return ['codex']

    @staticmethod
    def profile_defaults():
        return dict(model='gpt-6.1-sol', reasoning_effort='high', service_tier='default')

    @staticmethod
    def declaration_options(options, *, base):
        if not isinstance(options,dict) or set(options)-{'sandbox_workspace_write','features'}:
            raise ValueError('agent.native must contain supported native options'+(
                ': unknown '+', '.join(sorted(set(options)-{'sandbox_workspace_write','features'}))
                if isinstance(options,dict) else ''))
        options=copy.deepcopy(options)
        sandbox=options.get('sandbox_workspace_write')
        if sandbox is not None:
            if not isinstance(sandbox,dict) or set(sandbox)-{'writable_roots','network_access','exclude_tmpdir_env_var','exclude_slash_tmp'}:
                raise ValueError('agent.native.sandbox_workspace_write must contain supported fields')
            roots=sandbox.get('writable_roots')
            if roots is not None:
                if not isinstance(roots,list) or any(not isinstance(item,str) or not item.strip() for item in roots):
                    raise ValueError('agent.native.sandbox_workspace_write.writable_roots must be an array of paths')
                sandbox['writable_roots']=[str((Path(base)/Path(item).expanduser()).resolve()) for item in roots]
        return options

    @staticmethod
    def configuration_fields():
        return (
            dict(path='agent.native.sandbox_workspace_write.writable_roots',label='原生额外可写目录',kind='strings',
                description='可写工作目录模式使用。每项是允许额外写入的服务器目录，不会自动开放其他路径。'),
            dict(path='agent.native.sandbox_workspace_write.network_access',label='原生 sandbox 联网',kind='boolean',
                description='需与程序联网声明一致，否则检查失败。通常直接设置程序联网权限即可。'),
            dict(path='agent.native.sandbox_workspace_write.exclude_tmpdir_env_var',label='排除 TMPDIR',kind='boolean',
                description='从原生可写临时目录中排除 TMPDIR。留空由原生配置决定。'),
            dict(path='agent.native.sandbox_workspace_write.exclude_slash_tmp',label='排除 /tmp',kind='boolean',
                description='从原生可写临时目录中排除 /tmp。留空由原生配置决定。'),
            dict(path='agent.native.features.fast_mode',label='原生快速模式',kind='boolean',
                description='覆盖原生 fast_mode 功能开关。留空不覆盖原生配置。'),
        )

    READONLY_ENVIRONMENT_KEYS = (
        'PATH','HOME','CODEX_HOME','LANG','LC_ALL','OPENAI_API_KEY','CODEX_API_KEY',
        'OPENAI_BASE_URL','OPENAI_ORG_ID','OPENAI_PROJECT_ID','HTTP_PROXY','HTTPS_PROXY',
        'ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy',
        'SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE','NODE_EXTRA_CA_CERTS',
    )

    @classmethod
    def readonly_capability(cls,settings):
        permissions=cls(settings).permissions(settings)
        launcher=settings['native_launcher_argv']
        supported=(launcher==['codex'] or len(launcher)==1 and Path(launcher[0]).is_absolute())
        supported=(supported and permissions['enforcement'].get('workspace')=='native_sandbox'
            and permissions['enforcement'].get('network')=='native_sandbox'
            and permissions['effective']==dict(workspace='read-only',network=False,web='disabled',subagents=False))
        return dict(status='supported' if supported else 'unsupported',permissions=permissions,
            reason='' if supported else '当前启动器无法落实原生只读与禁网防护；仅支持默认 Codex 或单一绝对 Codex 路径，现有配置与缓存仍可读取。')

    def launch_readonly(self,profile,work,pending,schema,instruction):
        if self.readonly_capability(self.settings)['status']!='supported':
            raise ValueError('Native readonly invocation unsupported')
        args=self.launch(profile,work,pending,schema,instruction)
        args[-1:-1]=['-c','features.shell_tool=false','-c','tools.view_image=false',
            '-c','shell_environment_policy.inherit="none"']
        return args,{key:os.environ[key] for key in self.READONLY_ENVIRONMENT_KEYS if key in os.environ}

    def acquire_response_file(self,pending,*,max_bytes):
        path=Path(pending)
        if not path.is_file():raise ValueError('Native response file is unavailable')
        with path.open('rb') as stream:value=stream.read(max_bytes+1)
        if len(value)>max_bytes:raise ValueError('Native response file exceeds the output limit')
        return value

    def public_options(self,options):
        return {key:item for key,item in options.items() if key in {'workspace','network','web','subagents','features'}}

    @staticmethod
    def default_home():
        return Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))

    def default_resume_policy(self):
        if not self.standard:return None
        from . import native_recovery
        invoked=shutil.which(self.launcher[0])
        if invoked is None:raise ValueError('The selected standard native launcher is unavailable')
        home=self.default_home()
        if not home.is_absolute():
            raise ValueError('Frozen native output repair requires an absolute inherited CODEX_HOME')
        return dict(launcher=[str(Path(invoked).absolute())], policy=dict(max_attempts=2,
            protocol='codex_exec_resume',usage_scope='turn',output_repair=True,output_only=True,
            execution_wake=True,native_home=str(home.resolve()),automatic_recovery=native_recovery.policy({})))

    def validate_resume_policy(self,value):
        if value.get('protocol')!='codex_exec_resume' or value.get('usage_scope')!='turn':
            raise ValueError('Resume requires explicit finite attempts and qualified turn-scoped launcher protocol')

    def project_policy(self,settings):
        from . import native_execution
        return native_execution.project_policy(settings)

    def default_experiment_mode(self): return 'local' if self.standard else 'external'

    def skill_loading_contract(self,launcher,*,retained=None):
        from . import skill_supply
        return skill_supply.codex_skill_loading_contract(launcher,retained)

    def __init__(self, settings, *, schema_transport=None):
        self.settings = settings
        self.launcher = settings['native_launcher_argv']
        self.options = settings.get('native_options')
        self.standard = len(self.launcher)==1 and Path(self.launcher[0]).name=='codex'
        self.app_server_transport = self.standard and self.options is not None
        self.supports_native_schema_flag = self.standard
        self.schema_transport = 'native_flag' if self.supports_native_schema_flag else 'scientific_task'
        self.active_schema_transport = self.schema_transport if schema_transport is None else schema_transport
        supported = ('native_flag','prompt') if self.standard else ('scientific_task',)
        if self.active_schema_transport not in supported:
            raise ValueError('Unsupported response schema transport for the native launcher')

    @property
    def instruction_transport(self):
        return 'developer' if self.standard or self.options is not None else 'prompt'

    @property
    def complete_authorship(self):
        return self.standard and not (self.options or {}).get('subagents',False)

    def subagent_capability(self):
        return dict(status='supported' if self.standard else 'unsupported',
            reason=None if self.standard else 'native_subagents_unsupported')

    def launch(self, profile, work, pending, schema, instruction):
        configs = []
        for field,key in (('reasoning_effort','model_reasoning_effort'),('service_tier','service_tier')):
            if profile.get(field) is not None:
                configs.extend(['-c',key+'='+json.dumps(profile[field])])
        args = [*self.launcher,'exec','--json','--output-last-message',str(pending),
                '--model',profile['model'],*configs,'--sandbox',(self.options or {}).get('workspace','workspace-write'),'-C',str(work),'-']
        if self.standard or self.options is not None:
            extra = ['--skip-git-repo-check',
                     '-c','project_doc_max_bytes=0','-c','features.multi_agent='+str((self.options or {}).get('subagents',False)).lower(),
                     '-c','features.fast_mode='+str((self.options or {}).get('features',{}).get('fast_mode',False)).lower(),
                     '-c','approval_policy="never"','-c','developer_instructions='+json.dumps(instruction)]
            if self.active_schema_transport=='native_flag':extra.extend(['--output-schema',str(schema)])
            if self.options is not None:
                # Framework settings are one declaration; auth remains in the
                # inherited home. Native user config cannot override this run.
                sandbox = dict(writable_roots=[], exclude_tmpdir_env_var=False, exclude_slash_tmp=False)
                sandbox.update(self.options.get('sandbox_workspace_write',{}))
                sandbox['network_access'] = self.options['network']
                extra.extend(['--ignore-user-config','-c','web_search='+json.dumps(self.options['web']),
                              '-c','sandbox_workspace_write='+_toml(sandbox)])
            args[-1:-1] = extra
        return args

    def resume(self, args, profile, work, pending, schema, session):
        configs = [item for index,item in enumerate(args) if index>len(self.launcher) and args[index-1]=='-c']
        global_args = ['-C',str(work),'--sandbox',(self.options or {}).get('workspace','workspace-write')]
        for config in configs:global_args.extend(['-c',config])
        for flag in ('--skip-git-repo-check','--ignore-user-config'):
            if flag in args:global_args.append(flag)
        position = len(self.launcher)+1+len(global_args)
        schema_args = ['--output-schema',str(schema)] if self.active_schema_transport=='native_flag' else []
        return ([*self.launcher,'exec',*global_args,'resume','--json','--model',profile['model'],
                 *schema_args,'--output-last-message',str(pending),session['thread_id'],'-'],position)

    def budget_hook_args(self, args, definition, *, trusted=False):
        if not self.standard or self.options is None:
            raise ValueError('Budget hook requires standard Codex with declared native permissions')
        extra=[]
        for name, handlers in definition.items():
            if name not in {'PostToolUse', 'PostModelRequest', 'PostCompact'}:
                raise ValueError('Unsupported internal native hook')
            extra.extend(['-c', 'hooks.'+name+'='+_toml(handlers)])
        if trusted:extra.append('--dangerously-bypass-hook-trust')
        position=len(self.launcher)+1
        if args[len(self.launcher)]!='exec':raise ValueError('Budget hook requires this backend native exec invocation')
        return [*args[:position],*extra,*args[position:]]

    def hook_inventory_args(self, args, work):
        """Same native config/cwd; metadata-only app-server, never a model turn."""
        if not self.standard or self.options is None:
            raise ValueError('Native hook inventory requires declared standard Codex')
        configs=[]
        for index,item in enumerate(args[:-1]):
            if item=='-c':configs.extend(['-c',args[index+1]])
        for flag,key in (('--sandbox','sandbox_mode'),('--model','model')):
            if flag in args:configs.extend(['-c',key+'='+json.dumps(args[args.index(flag)+1])])
        # app-server does not accept exec's --ignore-user-config flag. The
        # metadata-only namespace applies its equivalent exact file overlay.
        return [*self.launcher,'-C',str(work),*configs,'app-server','--stdio']

    def request(self, prompt, **context):
        if self.standard:
            if context.get('response_schema') is None:prompt += '\n'+JSON_RESPONSE_INSTRUCTION
            if self.active_schema_transport=='prompt' and context.get('formal_review') is True:
                prompt += '\n'+context['schema'].read_text(encoding='utf-8')
        return prompt.encode()

    def transport_args(self, args):
        if not self.app_server_transport:
            return list(args)
        from . import native_app_server
        # Validate supported argv before starting an outer native process.
        native_app_server.invocation(args)
        return [sys.executable, str(Path(native_app_server.__file__)), '--', *args]

    def meter(self, home, work, session=None, *, context_state=None):
        return SessionUsage(home,work,session=session,context_state=context_state)

    def observe(self,meter,event): return _observe_meter(meter,event)
    def _usage_scope(self, value):
        if (self.options or {}).get('subagents') is True:
            return dict(value, usage_scope='native_main_thread', subagent_usage_coverage='unknown',
                unreported_usage='Subagent usage, running requests and reporting delay are unmeasured.')
        return value

    def snapshot_observation(self,meter): return self._usage_scope(meter.snapshot())
    def poll_observation(self,meter): return self._usage_scope(meter.poll())
    def observation_fault(self,meter): return meter.fault

    def event(self, value):
        kind = value.get('type')
        if kind=='transport.diagnostic':
            selected=transport_diagnostic(value.get('diagnostic'))
            return dict(transport_diagnostic=selected) if selected is not None else {}
        item=value.get('item')
        if kind=='tool' or isinstance(item,dict) and item.get('type') in {'command_execution','mcp_tool_call','web_search','file_change'}:
            return dict(activity=True,tool_action=True)
        if kind=='opaque.activity':return dict(activity=True)
        if kind in {'thread.started','thread.observed'}:return dict(session=value.get('thread_id'),thread_event=dict(value))
        if kind=='turn.started':
            return dict(turn=value['turn_id'],activity=True) if isinstance(value.get('turn_id'),str) else dict(activity=True)
        if kind=='context.reported':return dict(context=value.get('context'))
        if kind=='context.compacted':return dict(compaction=True,activity=True)
        if kind=='turn.completed':return dict(completed=True,usage=_usage(value.get('usage')),
            native_terminal=value.get('native_terminal') is True)
        if kind in {'turn.failed','error','turn.incomplete','turn.interrupted'}:
            from . import native_recovery
            rejection=value.get('rpc_rejection')
            if kind=='error' and isinstance(rejection,dict):
                code=rejection.get('code');stage=rejection.get('stage')
                if (type(stage) is int and stage in (1,2,3) and type(rejection.get('not_accepted')) is bool
                        and (code is None or type(code) is int and -(2**31)<=code<2**31)):
                    failure=dict(category='permanent' if code in {-32700,-32600,-32601,-32602} else 'unknown',
                        event_type='error',codes=['native_rpc_rejected'],statuses=[],retry_after_seconds=None,
                        message_patterns=[],not_accepted=rejection['not_accepted'],rpc_code=code,submission_stage=stage)
                    return dict(failed=True,failure=failure,terminal_failure=False)
            return dict(failed=True, failure=native_recovery.classify(value),terminal_failure=kind!='error')
        if isinstance(kind,str) and kind.startswith(('turn.','item.','thread.')):
            return dict(activity=True)
        return {}

    def identity(self, meter, session):
        return dict(backend=self.name,home=str(meter.home),path=str(meter.path) if meter.path else None,
                    session=meter.snapshot().get('session_binding'),
                    participant_scope=('codex_app_server_single_agent_v1' if self.app_server_transport else
                        'codex_exec_single_agent_v1') if self.complete_authorship else 'unsupported')

    def event_selector(self,*,source): return CodexEvents()

    def for_job(self, job):
        settings = self.settings
        if 'native_subagents' in job:
            value = job['native_subagents']
            if type(value) is not bool:
                raise ValueError('Frozen native subagent permission must be boolean')
            if value and self.subagent_capability()['status'] != 'supported':
                raise ValueError('Frozen native subagent permission is unsupported')
            settings = dict(settings, native_options=_native_options(dict(
                settings.get('native_options') or {}, subagents=value)))
        transport = 'prompt' if self.standard and not job.get('formal_review') else self.active_schema_transport
        return Codex(settings, schema_transport=transport) if settings is not self.settings or transport != self.active_schema_transport else self

    def apply_home(self, env, home, *, isolated):
        if isolated: env['CODEX_HOME'] = str(home)

    def actual_home(self, env, work, home, *, isolated):
        if isolated or not self.standard: return home
        value=Path(env['CODEX_HOME']) if env.get('CODEX_HOME') else Path.home()/'.codex'
        return (value if value.is_absolute() else Path(work)/value).resolve()

    def initial_notice(self, namespace_launcher):
        return dict(status='unavailable', reason=('unsupported_backend' if not self.standard else
            'undeclared_native_permissions' if self.options is None else
            'unsupported_namespace' if namespace_launcher is not None else 'not_preflighted'))

    def stage_notice(self, work):
        from . import budget_notice
        return budget_notice.stage_helper(work)

    def prepare_launch(self, args, work, attempt, env, view_facts, context, *, diagnostic=None):
        return _codex_notice_launch(self,args,work,attempt,env,view_facts,context,diagnostic=diagnostic)

    def configure_observation(self, meter, work, attempt, thread, channel, *, required):
        from . import budget_notice
        from .native_usage import MetadataUnavailable
        available=channel.get('status')=='available'
        path=(budget_notice.start_metadata(work,attempt,session=thread) if available else
            budget_notice.metadata_path(work,attempt))
        meter.configure(path,attempt,available=available)
        meter.capabilities={fact:self.observation_capability(fact) for fact in ('tokens','context','compactions','native_calls','terminal')}
        if not available and (self.app_server_transport or required):
            raise MetadataUnavailable('Native required metadata capability unavailable: '+str(channel.get('reason')))

    def finish_observation(self, meter, *, required):
        return self._usage_scope(meter.finish_context() if required else meter.poll())

    def terminal_usage(self, event, meter):
        current=event.get('usage')
        if event.get('completed') and event.get('native_terminal') and current is None:
            observation=meter.poll(); delivery=observation.get('metadata_delivery') or {}
            if (observation.get('coverage')=='live' and observation.get('has_observation') is True
                    and delivery.get('acknowledged') and not delivery.get('pending') and not delivery.get('fault')):
                current=observation['tokens']
        return current

    def terminal_message(self,event):
        item=event.get('item')
        if (event.get('type')=='item.completed' and isinstance(item,dict)
                and item.get('type')=='agent_message' and item.get('text_truncated') is not True
                and item.get('native_final') is not False
                and isinstance(item.get('text'),str)):
            return item['text']
        return None

    def terminal_binding(self,event):
        item=event.get('item')
        if (event.get('type')!='item.completed' or not isinstance(item,dict)
                or item.get('type')!='agent_message' or item.get('native_final') is not True):return None
        binding=item.get('final_binding')
        if (not isinstance(binding,dict) or type(binding.get('size_bytes')) is not int or binding['size_bytes']<0
                or not isinstance(binding.get('sha256'),str) or re.fullmatch(r'[0-9a-f]{64}',binding['sha256']) is None):return None
        return {key:binding[key] for key in ('sha256','size_bytes')}

    @staticmethod
    def terminal_failure_evidence(failures):
        return [{key:failure[key] for key in ('category','event_type','retry_after_seconds','not_accepted') if key in failure}
            for failure in failures]

    @staticmethod
    def terminal_reconciliation_candidate(outcome):
        return (isinstance(outcome,dict) and outcome.get('stop_reason')=='native_failure_event'
            and type(outcome.get('returncode')) is int and outcome['returncode']==0
            and outcome.get('native_started') is True and outcome.get('cleanup')=='observed_group_cleaned'
            and outcome.get('terminal_observed') is True)

    @staticmethod
    def acquire_terminal_evidence(record,outcome,*,output_path=None):
        """One bounded acquisition of exact process metadata and final bindings.

        New owners retain their compact projection. Older calls may be read
        through the same header selector as live execution; opaque tool rows
        are never decoded. Stream size does not constrain the original; missing
        compact proof still leaves its retained interpretation unchanged.
        """
        from . import contracts as c
        record=Path(record)
        facts=outcome.get('terminal_evidence')
        if facts is None:
            facts=dict(version=1,source='native_owner_v1',failures=[],completed_before=None,
                terminal=None,thread=None,activity=False,final=None,ambiguous=False)
            decoder=CodexEvents();finished=False
            try:
                with c.regular(record/'native.jsonl').open('rb') as stream:
                    while True:
                        chunk=stream.read(65536)
                        events=decoder.feed(chunk,final=not chunk)
                        for raw in events:
                            event=Codex.event(None,raw)
                            if finished and any(key in event for key in ('session','activity','completed','failed')):
                                facts['ambiguous']=True
                            if event.get('failure') is not None:
                                facts['failures'].extend(Codex.terminal_failure_evidence([event['failure']]))
                            if len(facts['failures'])>256:return None
                            if event.get('failed') and event.get('terminal_failure'):facts['ambiguous']=True
                            facts['activity']=facts['activity'] or bool(event.get('activity'))
                            if 'session' in event:facts['thread']=event['session']
                            text=Codex.terminal_message(None,raw)
                            binding=Codex.terminal_binding(None,raw)
                            if binding is not None:facts['final']=binding
                            elif text is not None:
                                encoded=text.encode('utf-8')
                                facts['final']=dict(sha256=hashlib.sha256(encoded).hexdigest(),size_bytes=len(encoded))
                            if event.get('completed'):
                                facts['terminal']=event.get('usage');facts['completed_before']=len(facts['failures'])
                                finished=True
                        if not chunk:break
            except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):return None
        if (not isinstance(facts,dict) or facts.get('version')!=1 or facts.get('source')!='native_owner_v1'
                or not isinstance(facts.get('final'),dict)):return None
        final=facts['final'];size=final.get('size_bytes');digest=final.get('sha256')
        if type(size) is not int or size<0 or not isinstance(digest,str):return None
        candidates=[record/name for name in ('science-output.json','result.pending.json')
            if (record/name).exists() or (record/name).is_symlink()]
        if output_path is not None:candidates.append(Path(output_path))
        bindings=[]
        try:
            for path in candidates:
                digest=hashlib.sha256();observed_size=0
                with c.regular(path).open('rb') as stream:
                    while True:
                        data=stream.read(65536)
                        if not data:break
                        digest.update(data);observed_size+=len(data)
                bindings.append(dict(sha256=digest.hexdigest(),size_bytes=observed_size))
        except (OSError,ValueError):return None
        return dict(facts=facts,outputs=bindings)

    @staticmethod
    def reconcile_terminal(outcome,*,meter,evidence,original_record):
        """Pure reconciliation of produced values; no path or metadata reads."""
        from . import native_recovery
        if not Codex.terminal_reconciliation_candidate(outcome) or not isinstance(evidence,dict):return outcome
        facts=evidence.get('facts');outputs=evidence.get('outputs')
        if not isinstance(facts,dict) or not isinstance(outputs,list):return outcome
        metering=outcome.get('metering') or {};failures=facts.get('failures');before=facts.get('completed_before')
        final=facts.get('final')
        if (facts.get('version')!=1 or facts.get('source')!='native_owner_v1'
                or facts.get('ambiguous') is not False or facts.get('terminal')!=meter or tokens(meter) is None
                or not isinstance(failures,list) or not failures or len(failures)>256
                or any(not isinstance(item,dict) for item in failures)
                or type(before) is not int or not 0<=before<=len(failures)
                or native_recovery.combine(failures,completed_before=before) is not None
                or facts.get('thread')!=outcome.get('thread_id')
                or facts.get('activity') is not outcome.get('native_activity_observed')
                or not isinstance(metering,dict) or metering.get('tokens')!=meter or metering.get('live_reason') is not None
                or not isinstance(final,dict) or not outputs or any(binding!=final for binding in outputs)):
            return outcome
        effective=dict(outcome,stop_reason=None,native_failure=None,
            raw_ref=dict(path=str(Path(original_record)/'science-output.json'),size_bytes=final['size_bytes']))
        effective['metering']=dict(metering,coverage='terminal',reason=None,source=Codex.terminal_source)
        return effective

    def retain_terminal(self, meter, observation, terminal):
        meter.terminal_tokens=dict(terminal)
        cursor=observation.get('context_windows',{}).get('cursor')
        if isinstance(cursor,dict): cursor['terminal_tokens']=dict(terminal)

    def recovery_capability(self, kind):
        supported=(kind=='technical_restart' or kind=='resume' and self.supports_resume
            or kind=='consultation_resume' and self.app_server_transport and self.supports_resume
            or kind in {'automatic_recovery','format_fallback'} and self.standard)
        return dict(status='supported' if supported else 'unsupported',reason=None if supported else 'native_recovery_unsupported')

    @staticmethod
    def authorship_scope(binding):
        return binding.get('participant_scope') in {'codex_exec_single_agent_v1','codex_app_server_single_agent_v1'}

    @staticmethod
    def authorship_proof_kind(binding):
        if binding.get('participant_scope')=='codex_app_server_single_agent_v1': return 'native_app_server_hooks_v1'
        if binding.get('participant_scope')=='codex_exec_single_agent_v1': return 'native_metadata_hooks_v1'
        raise ValueError('Native authorship scope unsupported')

    @staticmethod
    def validate_participant_event(event, outcome, intent, workspace):
        if not isinstance(event,dict) or event.get('thread_id')!=outcome.get('thread_id') or event.get('type')!='thread.started':
            raise ValueError('Native participant thread event changed')
        binding=outcome.get('native_identity') or {}
        if binding.get('participant_scope')=='codex_app_server_single_agent_v1':
            expected=(intent.get('session') or {}).get('thread_id') if intent.get('context_mode')!='new_context' else None
            profile=intent.get('native_profile')
            effective=event.get('effective_profile')
            if (event.get('transport')!='codex_app_server' or event.get('rpc_request_id')!=2
                    or event.get('rpc_method')!=('thread/resume' if expected else 'thread/start')
                    or event.get('requested_thread_id')!=expected or event.get('workspace')!=str(workspace)
                    or not isinstance(profile,dict) or event.get('requested_profile')!=profile
                    or event.get('model')!=profile.get('model') or not isinstance(effective,dict)
                    or effective.get('model')!=profile.get('model')
                    or (profile.get('service_tier')=='default'
                        and event.get('service_tier_wire_classification') not in {'null','default'})
                    or any(profile.get(key) is not None and effective.get(key)!=profile[key]
                        for key in ('reasoning_effort','service_tier'))):
                raise ValueError('Native app-server participant launch proof changed')
        # This historical namespace identifies the same Codex thread across
        # transports; it is not a claim that this invocation used exec.
        return ('codex_exec',outcome['thread_id'])

    def prepare_execution(self, root, step, record, work, home, args, *, resume_position, job, intent, settings):
        from . import native_execution
        return native_execution.prepare(root,step,record,work,home,args,standard=self.standard,
            resume_position=resume_position,native_options=self.options,job=job,intent=intent,settings=settings)

    def validate_resume_session(self, session, workspace, home):
        from .native_usage import validate_session_binding
        if (session.get('home')!=str(home) or session.get('workspace')!=str(workspace)
                or Path(session['path']).resolve()!=Path(session['path'])
                or not Path(session['path']).is_relative_to(Path(session['home'])/'sessions')):
            raise ValueError('Original native session binding changed')
        return validate_session_binding(session,session['thread_id'],workspace,session['home'])

    def format_rejection(self,event):
        from . import native_recovery
        return native_recovery.format_rejection(event)

    def format_observation(self,event):
        kind=event.get('type')
        if kind in {'error','turn.failed'}:
            return 'rejection' if self.format_rejection(event) else 'activity'
        return 'startup' if kind in {'thread.started','thread.observed','turn.started'} else 'activity'

    def validate_session_event(self,event,thread):
        if not isinstance(event,dict) or event.get('type')!='thread.started' or event.get('thread_id')!=thread:
            raise ValueError('Original native thread start changed')

    @staticmethod
    def validate_configuration(options): return _native_options(options)
    def permissions(self,settings): return _codex_permissions(dict(settings,native_options=self.options))
    def launcher_contract(self,launcher,*,retained):
        from . import skill_supply
        return skill_supply.codex_native_launcher_contract(launcher,retained=retained)
    def project_skills(self,root,settings,job):
        from . import skill_supply
        return skill_supply.project(root,settings,job)
    def namespace_launcher(self,settings):
        from . import skill_supply
        return skill_supply.namespace_launcher(settings)
    def validate_profile(self,profile):
        if not isinstance(profile,dict) or not isinstance(profile.get('model'),str) or not profile['model']:
            raise ValueError('Native model declaration unavailable')
        return profile
    @staticmethod
    def response_guidance(schema):
        properties=schema['properties']
        fields=[name for name,rule in properties.items() if rule.get('type')=='string' and 'enum' not in rule]
        menu=['/'.join(fields)+':text','limitations:text[]']
        menu.extend(name+':'+ '|'.join(rule['enum']) for name,rule in properties.items() if 'enum' in rule)
        return '\nRequired: '+'; '.join(menu)
    @staticmethod
    def compose_instructions(prompt,instruction,transport):
        return instruction+'\n'+prompt if transport=='prompt' else prompt
    def observation_capability(self,fact):
        supported=(fact in {'tokens','terminal'} or fact=='context' and self.app_server_transport
            or fact=='native_calls' and self.standard and self.options is not None
            or fact=='compactions' and self.standard and self.options is not None)
        return dict(status='supported' if supported else 'unsupported',
            reason=None if supported else 'native_observation_unsupported')

    def public_events(self, value):
        """Strict public shapes; callers must sanitize text before publication."""
        kind=value.get('type')
        if kind=='thread.started':return [dict(type='session.started')]
        if kind=='turn.completed':
            usage=_usage(value.get('usage'))
            return [dict(type='turn.completed'), *([dict(type='usage',usage=usage)] if usage else [])]
        item=value.get('item')
        if kind not in {'item.started','item.updated','item.completed'} or not isinstance(item,dict):return []
        if item.get('type')=='agent_message' and isinstance(item.get('text'),str):
            return [dict(type='message',text=item['text'],
                **(dict(text_truncated=True) if item.get('text_truncated') is True else {}))]
        if item.get('type') in {'command_execution','mcp_tool_call','web_search','file_change'}:
            tool=item.get('tool') if item.get('type')=='mcp_tool_call' else item['type']
            if not isinstance(tool,str):return []
            return [dict(type='tool',tool=tool[:128],status=kind.removeprefix('item.'))]
        return []


class EventUsage(BasicObservation):
    """Backend-declared cumulative invocation usage; no invented session file."""
    path = None
    fault = None

    def __init__(self, *, session=None, context_state=None):
        self._basic_init()
        self.thread = None
        self.known = dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
        self.observed = False
        from .native_usage import context_windows
        retained=context_windows(context_state)
        self.windows=dict(retained,coverage='unknown',reason='context_capability_unavailable')

    def bind_thread(self, session):
        if not isinstance(session,str) or not session or len(session)>256:
            self.fault='native_session_identity_invalid'
        elif self.thread is not None and self.thread!=session:
            self.fault='native_session_identity_changed'
        else:self.thread=session

    def observe(self, usage):
        if usage is None:
            self.fault='invalid_token_counter'
        elif any(usage[k]<self.known[k] for k in self.known):
            self.fault='token_counter_regressed'
        else:
            self.known=dict(usage);self.observed=True

    def snapshot(self):
        return dict(source='protocol_invocation_usage',thread_id=self.thread,tokens=dict(self.known),
                    coverage='live' if self.observed and not self.fault else 'unknown',
                    reason=self.fault or (None if self.observed else 'token_counter_unreported'),
                    has_observation=self.observed,baseline_tokens=None,**self.basic_snapshot(),context_windows=dict(self.windows),
                    unreported_usage='Running requests and reporting delay are unmeasured.',individual_request_usage='unavailable')

    def poll(self):return self.snapshot()


class Protocol(Backend):
    name = 'protocol'
    terminal_source = 'protocol.completed'
    standard = False
    supports_resume = False
    complete_authorship = False
    supports_native_schema_flag = False
    schema_transport = 'protocol_request'
    active_schema_transport = 'protocol_request'
    instruction_transport = 'prompt'

    @staticmethod
    def default_launcher(): raise ValueError('Protocol backend requires an explicit launcher')

    @staticmethod
    def profile_defaults(): return dict(model=None,reasoning_effort=None,service_tier=None)
    @staticmethod
    def declaration_options(options,*,base):
        # Retain the common hint-only permission contract and reject unsupported
        # native extensions through this provider's existing validator.
        if not isinstance(options,dict) or set(options)-{'sandbox_workspace_write','features'}:
            raise ValueError('agent.native must contain supported native options'+(
                ': unknown '+', '.join(sorted(set(options)-{'sandbox_workspace_write','features'}))
                if isinstance(options,dict) else ''))
        Protocol.validate_configuration(options)
        return copy.deepcopy(options)
    @staticmethod
    def configuration_fields(): return ()
    @classmethod
    def readonly_capability(cls,settings):
        return dict(status='unsupported',reason='当前启动器无法落实原生只读与禁网防护；现有配置与缓存仍可读取。',
            permissions=cls(settings).permissions(settings))
    def launch_readonly(self,profile,work,pending,schema,instruction):
        raise ValueError('Native readonly invocation unsupported')
    def acquire_response_file(self,pending,*,max_bytes): return None

    def public_options(self,options):
        return {key:item for key,item in options.items() if key in {'workspace','network','web','subagents','features'}}

    @staticmethod
    def default_home(): return None
    def default_resume_policy(self): return None
    def validate_resume_policy(self,value):
        raise ValueError('The selected backend does not support continuation policies')
    def project_policy(self,settings):
        from .experiments import UnsupportedExecution
        raise UnsupportedExecution('The selected backend has no supported project namespace projection')
    def default_experiment_mode(self): return 'external'
    def skill_loading_contract(self,launcher,*,retained=None):
        raise ValueError('Custom native backend has no Skill loading contract')

    def __init__(self, settings, *, schema_transport=None):
        self.settings=settings
        if schema_transport is not None and schema_transport!=self.schema_transport:
            raise ValueError('Protocol backend owns its response schema transport')
    def resume(self,*args,**kwargs): raise ValueError('Native session resume unsupported by this backend')
    def transport_args(self,args): return list(args)
    def event_selector(self,*,source): return CanonicalEvents(source)
    def for_job(self,job):
        if 'native_subagents' not in job:return self
        value=job['native_subagents']
        if type(value) is not bool:
            raise ValueError('Frozen native subagent permission must be boolean')
        if value:
            raise ValueError('Frozen native subagent permission is unsupported')
        settings=dict(self.settings,native_options=self.validate_configuration(dict(
            self.settings.get('native_options') or {},subagents=False)))
        return Protocol(settings,schema_transport=self.active_schema_transport)
    def apply_home(self,env,home,*,isolated): pass
    def actual_home(self,env,work,home,*,isolated): return home
    def initial_notice(self,namespace_launcher): return dict(status='unavailable',reason='unsupported_backend')
    def stage_notice(self,work): raise ValueError('Native budget notice delivery unsupported')
    def prepare_launch(self,args,work,attempt,env,view_facts,context,*,diagnostic=None):
        from . import shared_input_views
        return shared_input_views.wrap_from_facts(view_facts,args),self.initial_notice(None)
    def configure_observation(self,meter,work,attempt,thread,channel,*,required):
        meter.capabilities={fact:self.observation_capability(fact) for fact in ('tokens','context','compactions','native_calls','terminal')}
        if required:
            from .native_usage import MetadataUnavailable
            raise MetadataUnavailable('Frozen context window limit requires unsupported native compaction capability')
    def finish_observation(self,meter,*,required): return meter.poll()
    def terminal_usage(self,event,meter): return event.get('usage')
    def terminal_message(self,event): return None
    def terminal_binding(self,event): return None
    @staticmethod
    def terminal_failure_evidence(failures):
        return [{key:failure[key] for key in ('category','event_type','retry_after_seconds','not_accepted') if key in failure}
            for failure in failures]
    @staticmethod
    def terminal_reconciliation_candidate(outcome): return False
    @staticmethod
    def acquire_terminal_evidence(record,outcome,*,output_path=None): return None
    @staticmethod
    def reconcile_terminal(outcome,*,meter,evidence,original_record): return outcome
    def retain_terminal(self,meter,observation,terminal): pass
    def recovery_capability(self,kind):
        supported=kind=='technical_restart'
        return dict(status='supported' if supported else 'unsupported',reason=None if supported else 'native_recovery_unsupported')
    @staticmethod
    def authorship_scope(binding): return False
    @staticmethod
    def authorship_proof_kind(binding): raise ValueError('Native authorship unsupported')
    @staticmethod
    def validate_participant_event(event,outcome,intent,workspace): raise ValueError('Native authorship unsupported')
    def prepare_execution(self,root,step,record,work,home,args,*,resume_position,job,intent,settings):
        from . import native_execution
        return native_execution.prepare(root,step,record,work,home,args,standard=False,
            resume_position=resume_position,native_options=self.settings.get('native_options'),job=job,intent=intent,settings=settings)
    def validate_resume_session(self,session,workspace,home): raise ValueError('Native resume unsupported')
    def format_rejection(self,event): return False
    def format_observation(self,event): return 'activity'
    def validate_session_event(self,event,thread): raise ValueError('Native session event unsupported')
    @staticmethod
    def validate_configuration(options):
        options=_native_options(options)
        if options.get('sandbox_workspace_write') or options.get('features'):
            raise ValueError('Protocol backend does not implement Codex native options')
        return options
    def permissions(self,settings):
        options=settings.get('native_options') or {}
        fields=('workspace','network','web','subagents')
        return dict(requested={key:options[key] for key in fields if key in options},
            effective=dict.fromkeys(fields,'unknown'),enforcement=dict.fromkeys(fields,'hint_only'),
            limitations=['The launcher owns access controls; declarations do not establish isolation.'])
    def launcher_contract(self,launcher,*,retained):
        if retained is not ... and retained is not None:
            raise ValueError('Frozen native namespace launcher identity changed; create a new instance')
        return None
    def project_skills(self,root,settings,job):
        from . import skill_supply
        return skill_supply.project(root,settings,job)
    def namespace_launcher(self,settings):
        from . import skill_supply
        return skill_supply.namespace_launcher(settings)
    def validate_profile(self,profile):
        if not isinstance(profile,dict): raise ValueError('Native profile unavailable')
        return profile
    @staticmethod
    def response_guidance(schema): return ''
    @staticmethod
    def compose_instructions(prompt,instruction,transport):
        return instruction+'\n'+prompt if transport=='prompt' else prompt
    def observation_capability(self,fact):
        supported=fact in {'tokens','context','native_calls','terminal'}
        return dict(status='supported' if supported else 'unsupported',
            reason=None if supported else 'native_observation_unsupported')
    def launch(self, *args):return list(self.settings['native_launcher_argv'])
    def request(self, prompt, *, profile, work, schema, formal_review=False, response_schema=None):
        permissions = permission_summary(self.settings)
        if response_schema is None:
            if not formal_review:raise ValueError('Ordinary protocol request requires the current response schema')
            response_schema=json.loads(schema.read_text())
        return (json.dumps(dict(protocol='scidiscovery-agent-v1',prompt=prompt,workspace=str(work),
            profile={k:v for k,v in profile.items() if v is not None},response_schema=response_schema,
            permissions=dict(declaration=self.settings.get('native_options') or {},enforcement='hint_only',
                requested=permissions['requested'],effective=permissions['effective'],
                limitation='The protocol launcher owns actual access controls; this declaration is not isolation.')),
            ensure_ascii=False,separators=(',',':'))+'\n').encode()
    def meter(self, home, work, session=None, *, context_state=None):
        if session is not None:raise ValueError('Protocol backend does not support resume')
        return EventUsage(context_state=context_state)
    def observe(self,meter,event): return _observe_meter(meter,event)
    def snapshot_observation(self,meter): return meter.snapshot()
    def poll_observation(self,meter): return meter.poll()
    def observation_fault(self,meter): return meter.fault
    def event(self, value):
        kind=value.get('type')
        if kind=='transport.diagnostic':
            selected=transport_diagnostic(value.get('diagnostic'))
            return dict(transport_diagnostic=selected) if selected is not None else {}
        if kind=='opaque.activity':return dict(activity=True)
        if kind=='tool':return dict(activity=True,tool_action=True)
        if kind=='session':return dict(session=value.get('session_id'))
        if kind=='usage':return dict(live_usage=_usage(value.get('usage')))
        if kind=='context':return dict(context=value.get('context'))
        if kind=='native_call':return dict(native_call={key:value.get(key) for key in ('call_id','status')})
        if kind=='turn.started':return dict(turn=value.get('turn_id'))
        if kind=='compaction':return dict(compaction=True)
        if kind=='result':return dict(result=value.get('result'))
        if kind=='completed':return dict(completed=True,usage=_usage(value.get('usage')))
        if kind in {'failed','error'}:return dict(failed=True,terminal_failure=True)
        if kind in {'message','tool'}:return dict(activity=True)
        return {}
    def identity(self, meter, session):
        return dict(backend=self.name,session_id=session,participant_scope='unsupported',
                    limitation='The protocol does not attest complete native authorship.')
    def public_events(self, value):
        kind=value.get('type')
        if kind=='session':return [dict(type='session.started')]
        if kind=='completed':return [dict(type='turn.completed')]
        if kind=='usage':
            usage=_usage(value.get('usage'))
            return [dict(type='usage',usage=usage)] if usage else []
        if kind=='message' and isinstance(value.get('text'),str):return [dict(type='message',text=value['text'])]
        if kind=='tool' and isinstance(value.get('tool'),str) and value.get('status') in {'started','updated','completed','failed'}:
            return [dict(type='tool',tool=value['tool'][:128],status=value['status'])]
        return []


_PROVIDERS={'codex':Codex,'protocol':Protocol}


def names():
    return tuple(_PROVIDERS)


def provider(name=DEFAULT_BACKEND):
    selected_provider=_PROVIDERS.get(name) if isinstance(name,str) else None
    if selected_provider is None:raise ValueError('Unsupported native backend')
    return selected_provider


def renderer(name):
    provider=_PROVIDERS.get(name)
    if provider is None: raise ValueError('Native response rendering unsupported')
    return provider


def outcome_provider(outcome):
    provider=_PROVIDERS.get(outcome.get('native_backend')) if isinstance(outcome,dict) else None
    if provider is None:raise ValueError('Native outcome provider unsupported')
    return provider


def participant_provider(binding):
    """Resolve retained proof formats explicitly, including sealed legacy facts."""
    if not isinstance(binding,dict):raise ValueError('Native participant identity unsupported')
    matches=[provider for provider in _PROVIDERS.values() if provider.authorship_scope(binding)]
    if len(matches)!=1: raise ValueError('Native participant identity unsupported')
    return matches[0]


def _codex_notice_launch(backend, args, work, attempt, env, view_facts, context, *, diagnostic=None):
    """Qualify only this internal hook in the exact final native namespace."""
    from . import budget_notice, shared_input_views
    channel=dict(status='unavailable',reason='hook_preflight_unavailable')
    def report(error):
        if diagnostic is not None:
            message=(type(error).__name__+': '+str(error)).encode('utf-8',errors='replace')[:1024]
            diagnostic(b'Budget notice preflight: '+message+b'\n')
    try:
        helper=Path(work)/'work'/budget_notice.HELPER
        view_facts['mounts']['mappings'][str(helper)]=helper
        check=shared_input_views.wrap_from_facts(dict(view_facts,worker_policy=None),[sys.executable,str(helper),
            '--work',str(work),'--attempt',str(attempt),'--check'])
        with shared_input_views.popen_arguments(check) as (argv,pass_fds):
            visible=subprocess.run(argv,cwd=work,env=env,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,pass_fds=pass_fds,
                timeout=min(3,context.remaining_seconds()))
        if visible.returncode or visible.stdout!=budget_notice.VISIBLE.encode()+b'\n':
            channel['reason']='helper_unavailable'
            report(ValueError('Helper visibility check failed; returncode='+str(visible.returncode)))
            return shared_input_views.wrap_from_facts(view_facts,args),channel
        declared=budget_notice.definition(work,attempt)
        candidate=backend.budget_hook_args(args,declared)
        metadata_facts=dict(view_facts,mounts=dict(view_facts['mounts'],
            mappings=dict(view_facts['mounts']['mappings'])))
        if '--ignore-user-config' in args:
            native_home=Path(env.get('CODEX_HOME',str(Path.home()/'.codex')))
            if not native_home.is_absolute():native_home=Path(work)/native_home
            native_config=native_home/'config.toml'
            # A missing config already has the same semantics. Do not create
            # a mountpoint in the inherited native home for an absent file.
            try:config_info=native_config.lstat()
            except FileNotFoundError:config_info=None
            if config_info is not None and not stat.S_ISREG(config_info.st_mode):
                report(ValueError('Metadata projection requires a directly ordinary native config file'))
                return shared_input_views.wrap_from_facts(view_facts,args),channel
            if config_info is not None:
                metadata_facts['mounts']['mappings'][str(native_config)]=Path(work)/'work'/budget_notice.EMPTY_CONFIG
        inventories=[]
        for invocation in (args,candidate):
            metadata=shared_input_views.wrap_from_facts(metadata_facts,backend.hook_inventory_args(invocation,work))
            with shared_input_views.popen_arguments(metadata) as (argv,pass_fds):
                inventories.append(budget_notice.list_hooks(argv,work,env,pass_fds=pass_fds,
                    timeout=min(5,context.remaining_seconds())))
        try:budget_notice.validate_hook_trust(*inventories,declared,work)
        except ValueError as error:
            channel['reason']='hook_trust_unqualified'
            report(error)
            return shared_input_views.wrap_from_facts(view_facts,args),channel
        if any(item['data'][0]['warnings'] for item in inventories):
            report(ValueError('Native hook warnings present; exact required definitions and trust qualified'))
        context.remaining_seconds()
        final_facts = view_facts
        final_args = backend.budget_hook_args(args,declared,trusted=True)
        if getattr(backend,'app_server_transport',False):
            # app-server has no ignore-user-config flag. Reuse the exact
            # qualified empty-file overlay in the actual scientific namespace.
            final_facts = metadata_facts
            final_args = backend.transport_args(final_args)
        return shared_input_views.wrap_from_facts(final_facts,final_args),dict(
            status='available',mechanism='codex_post_tool_use')
    except (OSError,ValueError,TypeError,KeyError,RecursionError,subprocess.TimeoutExpired) as error:
        report(error)
        return shared_input_views.wrap_from_facts(view_facts,args),channel
