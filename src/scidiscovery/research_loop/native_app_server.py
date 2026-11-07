"""One owned Codex app-server invocation, with a bounded metadata projection.

This adapter is executed inside the existing scientific namespace/process group.
It never opens a rollout or attaches another observer to an active thread. RPC
submission is single shot; an uncertain response is never retried. Tool events
are rejected at their header before JSON decoding.
"""
from __future__ import annotations

import json
import codecs
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tempfile
import tomllib

MAX_EVENT = 65536
MAX_HEADER = 2048
IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,256}\Z')
SCOPE = 'codex_app_server_single_agent_v1'
_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"')
_SCALAR = re.compile(rb'(?:-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null)')
_STRING_BOUNDARY=re.compile(rb'["\\\x00-\x1f]')


def effective_service_tier(requested,reported):
    """Only explicit standard routing maps native null to ``default``.

    Codex protocol config_types.rs defines the ``default`` request sentinel as
    selecting no service tier. An omitted request continues to mean inheritance.
    Callers must separately establish that the response field was present.
    """
    return 'default' if requested=='default' and reported is None else reported


def service_tier_classification(result):
    if 'serviceTier' not in result:return 'missing'
    value=result['serviceTier']
    if value is None:return 'null'
    return value if value in ('default','priority','flex') else 'other'


class AssistantSpool:
    """Exact assistant UTF-8 bytes; only its public preview is bounded."""
    def __init__(self):
        self.stream=tempfile.TemporaryFile()
        self.digest=hashlib.sha256();self.size=0;self.preview=b''

    def write(self,data):
        self.stream.write(data);self.digest.update(data);self.size+=len(data)
        if len(self.preview)<4096:self.preview+=data[:4096-len(self.preview)]

    def binding(self): return dict(sha256=self.digest.hexdigest(),size_bytes=self.size)
    def close(self): self.stream.close()


class _LineReader:
    def __init__(self,stream,prefix):
        self.stream,self.buffer,self.position=stream,prefix,0
        self.ended=prefix.endswith(b'\n')

    def fill(self):
        if self.position==len(self.buffer) and not self.ended:
            self.buffer=self.stream.readline(65536);self.position=0
            self.ended=not self.buffer or self.buffer.endswith(b'\n')
        return self.position<len(self.buffer)

    def peek(self): return self.buffer[self.position] if self.fill() else None
    def pop(self):
        value=self.peek()
        if value is None:raise ValueError('Native assistant item incomplete')
        self.position+=1;return value
    def whitespace(self):
        while self.peek() in (32,9,10,13):self.pop()


def assistant_message(stream,prefix):
    """Stream one genuine agentMessage string, never hydrate its JSON text."""
    return _stream_message(stream,prefix,dict(method=True,params=dict(threadId=True,turnId=True,
        item=dict(id=True,type=True,phase=True,text='spool'))))


def terminal_message(stream,prefix):
    """Select terminal controls while opaque hydrated items remain raw bytes."""
    return _stream_message(stream,prefix,dict(method=True,params=dict(threadId=True,
        turn=dict(id=True,status=True,error={}))))


def _stream_message(stream,prefix,shape):
    reader=_LineReader(stream,prefix);spools=[]

    def skip_string():
        if reader.pop()!=34:raise ValueError('Native opaque string framing invalid')
        while True:
            if not reader.fill():raise ValueError('Native opaque string framing incomplete')
            match=_STRING_BOUNDARY.search(reader.buffer,reader.position)
            if match is None:reader.position=len(reader.buffer);continue
            reader.position=match.start();tag=reader.pop()
            if tag==34:return
            if tag!=92:raise ValueError('Native opaque string control invalid')
            escape=reader.pop()
            if escape==117:
                for _ in range(4):reader.pop()

    def skip_value():
        # Only JSON framing is consumed. No opaque key, string, tool argument,
        # result, or nested item is decoded, searched or retained.
        reader.whitespace();tag=reader.peek()
        if tag==34:skip_string();return
        if tag in (123,91):
            reader.pop();depth=1
            boundaries=re.compile(rb'[{}\[\]"]')
            while depth:
                if not reader.fill():raise ValueError('Native opaque container framing incomplete')
                match=boundaries.search(reader.buffer,reader.position)
                if match is None:reader.position=len(reader.buffer);continue
                reader.position=match.start();tag=reader.peek()
                if tag==34:skip_string();continue
                reader.pop()
                depth+=1 if tag in (123,91) else -1
            return
        while reader.peek() not in (None,32,9,10,13,44,125,93):reader.pop()

    def string(sink=None,*,metadata=False):
        if reader.pop()!=34:raise ValueError('Native assistant string invalid')
        collected=bytearray() if metadata else None
        decoder=codecs.getincrementaldecoder('utf-8')()
        def write(data,*,escaped=False):
            nonlocal decoder
            if escaped:
                decoder.decode(b'',final=True);decoder=codecs.getincrementaldecoder('utf-8')()
            else:decoder.decode(data,final=False)
            if sink is not None:sink.write(data)
            if collected is not None:
                collected.extend(data)
                if len(collected)>256:raise ValueError('Native assistant metadata invalid')
        while True:
            if not reader.fill():raise ValueError('Native assistant string incomplete')
            match=_STRING_BOUNDARY.search(reader.buffer,reader.position)
            end=match.start() if match else len(reader.buffer)
            if end>reader.position:
                write(reader.buffer[reader.position:end]);reader.position=end
            if match is None:continue
            tag=reader.pop()
            if tag==34:
                decoder.decode(b'',final=True)
                return collected.decode('utf-8') if collected is not None else None
            if tag!=92:raise ValueError('Native assistant control character invalid')
            escape=reader.pop()
            simple={34:b'"',92:b'\\',47:b'/',98:b'\b',102:b'\f',110:b'\n',114:b'\r',116:b'\t'}
            if escape in simple:write(simple[escape],escaped=True);continue
            if escape!=117:raise ValueError('Native assistant escape invalid')
            def hex4():
                raw=bytes(reader.pop() for _ in range(4))
                if not re.fullmatch(rb'[0-9a-fA-F]{4}',raw):raise ValueError('Native assistant unicode invalid')
                return int(raw,16)
            code=hex4()
            if 0xD800<=code<=0xDBFF:
                if reader.pop()!=92 or reader.pop()!=117:raise ValueError('Native assistant surrogate invalid')
                low=hex4()
                if not 0xDC00<=low<=0xDFFF:raise ValueError('Native assistant surrogate invalid')
                code=0x10000+((code-0xD800)<<10)+low-0xDC00
            elif 0xDC00<=code<=0xDFFF:raise ValueError('Native assistant surrogate invalid')
            write(chr(code).encode('utf-8'),escaped=True)

    def value(selected,depth=0):
        if depth>32:raise ValueError('Native assistant metadata nesting invalid')
        reader.whitespace();tag=reader.peek()
        if selected is None:skip_value();return None
        if selected=='spool' and tag!=34:raise ValueError('Native assistant text must be a string')
        if selected is True and tag in (123,91):raise ValueError('Native assistant metadata must be scalar')
        if tag==34:
            if selected=='spool':
                spool=AssistantSpool();spools.append(spool);string(spool);return spool
            return string(metadata=selected is True)
        if tag==123:
            reader.pop();reader.whitespace();output={} if isinstance(selected,dict) else None;seen=set()
            if reader.peek()==125:reader.pop();return output
            while True:
                reader.whitespace();key=string(metadata=output is not None)
                if output is not None:
                    if key in seen:raise ValueError('Native assistant metadata duplicated')
                    seen.add(key)
                reader.whitespace()
                if reader.pop()!=58:raise ValueError('Native assistant metadata separator invalid')
                child=selected.get(key) if output is not None else None
                found=value(child,depth+1)
                if output is not None and child is not None:output[key]=found
                reader.whitespace();delimiter=reader.pop()
                if delimiter==125:return output
                if delimiter!=44:raise ValueError('Native assistant metadata separator invalid')
        if tag==91:
            reader.pop();reader.whitespace()
            if reader.peek()==93:reader.pop();return None
            while True:
                value(None,depth+1);reader.whitespace();delimiter=reader.pop()
                if delimiter==93:return None
                if delimiter!=44:raise ValueError('Native assistant array invalid')
        raw=bytearray()
        while reader.peek() not in (None,32,9,10,13,44,125,93):
            raw.append(reader.pop())
            if len(raw)>256:raise ValueError('Native assistant scalar invalid')
        if not raw:raise ValueError('Native assistant scalar missing')
        return json.loads(raw) if selected is True else None
    try:
        projected=value(shape);reader.whitespace()
        if reader.peek() is not None:raise ValueError('Native assistant trailing bytes')
        return projected
    except BaseException:
        for spool in spools:spool.close()
        raise


def metadata_json(line):
    """Decode only declared public/control leaves; opaque leaves stay bytes.

    Framing advances past unselected JSON values without decoding strings or
    constructing their objects. In particular thread previews, errors, extra
    data, and instruction paths are never interpreted or retained.
    """
    turn = dict(id=True, status=True, items='empty')
    thread = dict(id=True, turns='empty')
    policy = dict(type=True, networkAccess=True, writableRoots='strings',
        excludeSlashTmp=True, excludeTmpdirEnvVar=True)
    result = dict(thread=thread, turn=turn, cwd=True, model=True, approvalPolicy=True,
        sandbox=policy, reasoningEffort=True, serviceTier=True)
    params = dict(threadId=True, turnId=True, turn=turn,
        tokenUsage=dict(last=dict(totalTokens=True), modelContextWindow=True),
        item=dict(id=True, type=True, text=True, phase=True))
    shape = dict(id=True, result=result, method=True, params=params, error=dict(code=True))
    position = 0

    def whitespace():
        nonlocal position
        while position < len(line) and line[position] in b' \r\n\t': position += 1

    def value(selected, depth=0):
        nonlocal position
        if depth > 32: raise ValueError('Native metadata nesting exceeded')
        whitespace()
        if position >= len(line): raise ValueError('Native metadata incomplete')
        start = position
        tag = line[position]
        if selected is True and tag in (123,91):raise ValueError('Native metadata scalar invalid')
        if tag == 123:
            position += 1
            output = {} if isinstance(selected, dict) else None
            whitespace()
            if position < len(line) and line[position] == 125:
                position += 1
                return output
            seen = set()
            while True:
                whitespace()
                match = _STRING.match(line, position)
                if match is None: raise ValueError('Native metadata object invalid')
                key = json.loads(match[0]) if output is not None else None
                if output is not None and (len(match[0]) > 256 or key in seen):
                    raise ValueError('Native metadata key invalid')
                if output is not None: seen.add(key)
                position = match.end(); whitespace()
                if position >= len(line) or line[position] != 58: raise ValueError('Native metadata separator invalid')
                position += 1
                child = selected.get(key) if output is not None else None
                found = value(child, depth + 1)
                if output is not None and child is not None: output[key] = found
                whitespace()
                if position >= len(line): raise ValueError('Native metadata incomplete')
                delimiter = line[position]; position += 1
                if delimiter == 125: return output
                if delimiter != 44: raise ValueError('Native metadata separator invalid')
        if tag == 91:
            position += 1; whitespace()
            output = [] if selected in ('empty', 'strings') else None
            if position < len(line) and line[position] == 93:
                position += 1
                return output
            if selected == 'empty': raise ValueError('Native metadata unexpectedly contains history/items')
            while True:
                found = value(True if selected == 'strings' else None, depth + 1)
                if output is not None:
                    if not isinstance(found,str) or len(output) >= 128: raise ValueError('Native metadata roots invalid')
                    output.append(found)
                whitespace()
                if position >= len(line): raise ValueError('Native metadata incomplete')
                delimiter = line[position]; position += 1
                if delimiter == 93: return output
                if delimiter != 44: raise ValueError('Native metadata separator invalid')
        match = (_STRING if tag == 34 else _SCALAR).match(line, position)
        if match is None: raise ValueError('Native metadata scalar invalid')
        position = match.end()
        if selected == 'present': return True
        return json.loads(line[start:position]) if selected is True else None

    projected = value(shape)
    whitespace()
    if position != len(line): raise ValueError('Native metadata trailing bytes')
    return projected


def identity(value):
    if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
        raise ValueError('Native identity unavailable')
    return value


def invocation(argv):
    """Translate only the existing supported exec contract; reject extensions."""
    if len(argv) < 3 or Path(argv[0]).name != 'codex' or argv[1] != 'exec':
        raise ValueError('App-server transport requires the standard Codex launcher')
    config, model, cwd, sandbox, pending, schema, resumed = [], None, None, None, None, None, None
    trusted, ignore = False, False
    index = 2
    while index < len(argv):
        key = argv[index]
        if key in ('-c', '--model', '-C', '--sandbox', '--output-last-message', '--output-schema'):
            if index + 1 >= len(argv):
                raise ValueError('Native option has no value')
            value = argv[index + 1]
            if key == '-c': config.extend(['-c', value])
            elif key == '--model': model = value
            elif key == '-C': cwd = value
            elif key == '--sandbox': sandbox = value
            elif key == '--output-last-message': pending = value
            elif key == '--output-schema': schema = value
            index += 2
            continue
        if key == '--dangerously-bypass-hook-trust': trusted = True
        elif key == '--ignore-user-config': ignore = True
        elif key in ('--json', '--skip-git-repo-check', '-'):
            pass
        elif key == 'resume' and resumed is None:
            resumed = ''
        elif resumed == '' and not key.startswith('-'):
            resumed = identity(key)
        else:
            raise ValueError('Unsupported native app-server launch option')
        index += 1
    if not model or not cwd or not pending or sandbox not in ('read-only', 'workspace-write', 'danger-full-access'):
        raise ValueError('Native app-server launch declaration unavailable')
    if resumed == '': raise ValueError('Native resume identity unavailable')
    values = {}
    def merge(target, update):
        for key,value in update.items():
            if isinstance(value,dict) and isinstance(target.get(key),dict): merge(target[key],value)
            else: target[key]=value
    for index in range(1, len(config), 2):
        parsed=tomllib.loads(config[index])
        # Dotted CLI assignments address a leaf, so successive feature/hook
        # assignments must preserve their siblings. Whole-table assignments
        # replace that table, matching the native config override operation.
        assignment=config[index].split('=',1)[0].strip()
        if '.' in assignment: merge(values,parsed)
        else: values.update(parsed)
    # Native values, including the exact bridge/config declarations, remain CLI
    # config overrides. The caller owns ignore-user-config's readonly overlay.
    command = [argv[0], '-C', cwd, *config, '-c', 'sandbox_mode='+json.dumps(sandbox)]
    if trusted: command.append('--dangerously-bypass-hook-trust')
    command.extend(['app-server', '--stdio'])
    return dict(command=command, model=model, cwd=cwd, sandbox=sandbox, pending=Path(pending),
        schema=Path(schema) if schema else None, resumed=resumed, config=values, ignore_user_config=ignore,
        trusted_hook_bypass=trusted)


def selected_line(line):
    """Select typed controls/public assistant items before decoding their body.

    Item and delta methods carry arbitrary native tool/input/output bodies and
    are never decoded. The native terminal contract has an empty items array;
    unexpected hydrated terminal items fail closed instead of being inspected.
    """
    header = line[:MAX_HEADER]
    method = re.match(rb'^\s*\{\s*"method"\s*:\s*"([A-Za-z0-9_/]+)"', header)
    response = re.match(rb'^\s*\{\s*"id"\s*:\s*([123])\s*,', header)
    if response:
        return len(line) <= MAX_EVENT
    if method is None:
        return False
    name = method[1].decode('ascii')
    if name in {'thread/tokenUsage/updated', 'turn/started', 'turn/completed', 'error'}:
        if name in {'turn/started','turn/completed'}:return True
        return len(line) <= MAX_EVENT
    if name == 'item/completed':
        item = re.search(rb'"item"\s*:\s*\{\s*"type"\s*:\s*"([A-Za-z]+)"', header)
        # Some producer versions serialize the item id before its tag.
        if item is None:
            item = re.search(rb'"item"\s*:\s*\{\s*"id"\s*:\s*"[A-Za-z0-9_-]{1,256}"\s*,\s*"type"\s*:\s*"([A-Za-z]+)"', header)
        return item is not None and (item[1]==b'agentMessage' or item[1]==b'contextCompaction' and len(line)<=MAX_EVENT)
    return False


def native_lines(stream):
    """Stream full assistant finals; compact selection never limits originals."""
    while True:
        prefix=stream.readline(MAX_HEADER)
        if not prefix:return
        tag=re.search(rb'"item"\s*:\s*\{\s*(?:"id"\s*:\s*"[A-Za-z0-9_-]{1,256}"\s*,\s*)?"type"\s*:\s*"([A-Za-z]+)"',prefix)
        method=re.match(rb'^\s*\{\s*"method"\s*:\s*"item/completed"',prefix)
        if method is not None and tag is not None and tag[1]==b'agentMessage':
            yield assistant_message(stream,prefix)
            continue
        terminal=re.match(rb'^\s*\{\s*"method"\s*:\s*"turn/(?:started|completed)"',prefix)
        if terminal is not None:
            yield terminal_message(stream,prefix)
            continue
        selected=selected_line(prefix)
        line=prefix if selected else b''
        part=prefix
        while part and not part.endswith(b'\n'):
            part=stream.readline(65536)
            if selected:
                if len(line)+len(part)>MAX_EVENT:selected=False;line=b''
                else:line+=part
        if selected:
            value=metadata_json(line)
            if not isinstance(value, dict): raise ValueError('Native RPC shape invalid')
            yield value


class Transport:
    def __init__(self, declaration, *, emit, send):
        self.declaration, self.emit, self.send = declaration, emit, send
        self.stage = 1
        self.thread, self.turn, self.final = None, None, None
        self.finished = False
        self.completed = False

    def initialize(self):
        self.send(dict(id=1, method='initialize', params=dict(
            clientInfo=dict(name='scidiscovery-native', version='1'),
            capabilities=dict(experimentalApi=True))))

    def _thread_params(self):
        d = self.declaration
        value = dict(model=d['model'], cwd=d['cwd'], sandbox=d['sandbox'], approvalPolicy='never')
        # Native exec forwards its already-qualified session bypass through
        # this supported thread config override; the top-level CLI flag alone
        # does not apply it to app-server thread/start or thread/resume.
        if d.get('trusted_hook_bypass') is True:value['config']=dict(bypass_hook_trust=True)
        if d['config'].get('developer_instructions') is not None:
            value['developerInstructions']=d['config']['developer_instructions']
        if d['config'].get('service_tier') is not None: value['serviceTier'] = d['config']['service_tier']
        if d['resumed']:
            value.update(threadId=d['resumed'], excludeTurns=True)
        else:
            value.update(experimentalRawEvents=False, allowProviderModelFallback=False)
        return value

    def _turn_params(self, prompt):
        d = self.declaration
        value = dict(threadId=self.thread, input=[dict(type='text', text=prompt)],
            model=d['model'], approvalPolicy='never')
        if d['config'].get('model_reasoning_effort') is not None:
            value['effort'] = d['config']['model_reasoning_effort']
        if d['config'].get('service_tier') is not None:
            value['serviceTier'] = d['config']['service_tier']
        if d['schema'] is not None:
            value['outputSchema'] = json.loads(d['schema'].read_bytes())
        return value

    def _permissions_match(self, actual):
        return not self._permission_mismatches(actual)

    def _permission_mismatches(self, actual):
        if not isinstance(actual, dict): return ['sandbox_shape']
        mode = self.declaration['sandbox']
        expected_type = {'read-only':'readOnly', 'workspace-write':'workspaceWrite',
            'danger-full-access':'dangerFullAccess'}[mode]
        if actual.get('type') != expected_type: return ['sandbox_type']
        if mode == 'danger-full-access': return []
        mismatches=[]
        configured = self.declaration['config'].get('sandbox_workspace_write') or {}
        network = configured.get('network_access', False) if mode == 'workspace-write' else False
        if actual.get('networkAccess', False) != network:mismatches.append('sandbox_network')
        if mode == 'read-only': return mismatches
        roots = actual.get('writableRoots', [])
        declared = configured.get('writable_roots', [])
        if (not isinstance(roots, list) or any(not isinstance(path,str) for path in roots)
                or not set(roots).issubset({*declared, self.declaration['cwd']})
                or not set(declared).issubset(set(roots))):mismatches.append('sandbox_roots')
        if actual.get('excludeSlashTmp',False)!=configured.get('exclude_slash_tmp',False):mismatches.append('sandbox_slash_tmp')
        if actual.get('excludeTmpdirEnvVar',False)!=configured.get('exclude_tmpdir_env_var',False):mismatches.append('sandbox_tmpdir')
        return mismatches

    def receive(self, message, prompt):
        if 'id' in message:
            if message.get('id') != self.stage:
                raise ValueError('Native RPC submission response unavailable')
            if 'error' in message:
                error=message['error'];code=error.get('code') if isinstance(error,dict) else None
                if type(code) is not int or not -(2**31)<=code<2**31:code=None
                self.emit(dict(type='error',rpc_rejection=dict(code=code,stage=self.stage,
                    not_accepted=self.stage<3 or code in {-32700,-32600,-32601,-32602})))
                raise ValueError('Native RPC submission response unavailable')
            if not isinstance(message.get('result'),dict):
                raise ValueError('Native RPC submission response unavailable')
            result = message['result']
            if self.stage == 1:
                self.send(dict(method='initialized'))
                self.stage = 2
                self.send(dict(id=2, method='thread/resume' if self.declaration['resumed'] else 'thread/start',
                    params=self._thread_params()))
            elif self.stage == 2:
                thread = result.get('thread')
                if not isinstance(thread,dict):raise ValueError('Native thread launch metadata unavailable')
                self.thread=identity(thread.get('id'))
                requested_tier=self.declaration['config'].get('service_tier')
                effective_tier=effective_service_tier(requested_tier,result.get('serviceTier'))
                checks=[('history',thread.get('turns')!=[]),
                    ('cwd',result.get('cwd')!=self.declaration['cwd']),
                    ('model',result.get('model')!=self.declaration['model']),
                    ('approval_policy',result.get('approvalPolicy')!='never'),
                    ('resume_thread',bool(self.declaration['resumed'] and self.thread!=self.declaration['resumed'])),
                    ('reasoning_effort',self.declaration['config'].get('model_reasoning_effort') is not None
                        and result.get('reasoningEffort')!=self.declaration['config']['model_reasoning_effort']),
                    ('service_tier',requested_tier is not None
                        and ('serviceTier' not in result or effective_tier!=requested_tier))]
                mismatch_fields=[name for name,mismatch in checks if mismatch]
                mismatch_fields.extend(self._permission_mismatches(result.get('sandbox')))
                if mismatch_fields:
                    # Retain an actual created/resumed identity even when it
                    # fails qualification. It cannot confer a participant proof
                    # or authorize retrying an uncertain original submission.
                    self.emit(dict(type='thread.observed',thread_id=self.thread,transport='codex_app_server',
                        rpc_request_id=2,qualified=False,mismatch_fields=mismatch_fields,
                        service_tier_wire_classification=service_tier_classification(result)))
                    print('Native app-server launch mismatch fields: '+','.join(mismatch_fields),file=sys.stderr)
                    raise ValueError('Native thread launch did not match declaration')
                self.emit(dict(type='thread.started', thread_id=self.thread, transport='codex_app_server',
                    rpc_method='thread/resume' if self.declaration['resumed'] else 'thread/start',
                    rpc_request_id=2, requested_thread_id=self.declaration['resumed'],
                    model=result['model'], workspace=result['cwd'],
                    requested_profile=dict(model=self.declaration['model'],
                        reasoning_effort=self.declaration['config'].get('model_reasoning_effort'),
                        service_tier=self.declaration['config'].get('service_tier')),
                    effective_profile=dict(model=result['model'],reasoning_effort=result.get('reasoningEffort'),
                        service_tier=effective_tier),
                    service_tier_wire_classification=service_tier_classification(result)))
                self.stage = 3
                self.send(dict(id=3, method='turn/start', params=self._turn_params(prompt)))
            else:
                self._bind_turn(result.get('turn'))
                self.stage = 4
            return
        params = message.get('params')
        if not isinstance(params, dict): raise ValueError('Native notification metadata invalid')
        method = message.get('method')
        if method == 'error':
            # Error strings can contain request/tool bodies. Keep only the
            # typed fact and preserve the real terminal as the completion gate.
            self.emit(dict(type='error', error=dict(message='Native app-server error')))
            return
        if params.get('threadId') != self.thread:
            raise ValueError('Native notification thread changed')
        if method == 'turn/started':
            self._bind_turn(params.get('turn'))
            return
        if method in {'thread/tokenUsage/updated', 'item/completed'}:
            if identity(params.get('turnId')) != self.turn:
                raise ValueError('Native notification turn changed')
        if method == 'thread/tokenUsage/updated':
            usage = params.get('tokenUsage')
            last = usage.get('last') if isinstance(usage, dict) else None
            used = last.get('totalTokens') if isinstance(last, dict) else None
            window = usage.get('modelContextWindow') if isinstance(usage, dict) else None
            self.emit(dict(type='context.reported', context=dict(status='last_reported', total_tokens=used,
                model_context_window=window, observed_at=time.time(), thread_id=self.thread,
                turn_id=self.turn, current_window=True)))
        elif method == 'item/completed':
            item = params.get('item')
            if not isinstance(item, dict): raise ValueError('Native public item invalid')
            if item.get('type') == 'contextCompaction':
                self.emit(dict(type='context.compacted'))
            elif item.get('type') == 'agentMessage' and isinstance(item.get('text'), str):
                spool=AssistantSpool()
                for offset in range(0,len(item['text']),32768):spool.write(item['text'][offset:offset+32768].encode('utf-8'))
                self._assistant(item,spool)
            elif item.get('type')=='agentMessage' and isinstance(item.get('text'),AssistantSpool):
                self._assistant(item,item['text'])
            elif item.get('type')=='agentMessage':raise ValueError('Native assistant text unavailable')
        elif method == 'turn/completed':
            turn = params.get('turn')
            self._bind_turn(turn)
            if turn.get('status') == 'completed':
                if self.final is None: raise ValueError('Native final assistant message unavailable')
                descriptor=os.open(self.declaration['pending'],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
                with os.fdopen(descriptor,'wb') as stream:
                    self.final.stream.seek(0)
                    while True:
                        data=self.final.stream.read(65536)
                        if not data:break
                        stream.write(data)
                    stream.flush();os.fsync(stream.fileno())
                self.final.close();self.final=None
                # This event is an explicit real native terminal. Billing usage
                # remains owned by request hooks, never inferred from context.
                self.emit(dict(type='turn.completed', native_terminal=True))
                self.finished = True
                self.completed = True
            elif turn.get('status') in ('failed', 'interrupted'):
                self.emit(dict(type='turn.failed', error=dict(message='Native turn '+turn['status'])))
                self.finished = True
            else:
                raise ValueError('Native terminal status unavailable')

    def _assistant(self,item,spool):
        final=item.get('phase') in (None,'final_answer')
        preview=spool.preview.decode('utf-8',errors='ignore')
        projected=dict(type='agent_message',text=preview,text_truncated=len(preview.encode('utf-8'))!=spool.size,native_final=final)
        if final:
            if self.final is not None:self.final.close()
            self.final=spool
            projected.update(native_final=True,final_binding=spool.binding())
        else:spool.close()
        self.emit(dict(type='item.completed',item=projected))

    def close(self):
        if self.final is not None:self.final.close();self.final=None

    def _bind_turn(self, turn):
        if not isinstance(turn, dict): raise ValueError('Native turn metadata unavailable')
        current = identity(turn.get('id'))
        if self.turn is not None and current != self.turn: raise ValueError('Native turn changed')
        if self.turn is None:
            self.turn = current
            self.emit(dict(type='turn.started', turn_id=current))


def run(argv, prompt, *, output=sys.stdout):
    d = invocation(argv)
    def emit(value):
        output.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')
        output.flush()
    # Inherit the outer native group. The existing controller owns group
    # termination; this helper must never create an untracked new session.
    process = subprocess.Popen(d['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None)
    def send(value):
        process.stdin.write((json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n').encode())
        process.stdin.flush()
    transport = Transport(d, emit=emit, send=send)
    phase='initialization_submit'
    try:
        transport.initialize()
        messages=iter(native_lines(process.stdout))
        while True:
            phase='event_projection'
            try:message=next(messages)
            except StopIteration:break
            phase=({1:'initialization_response',2:'thread_response',3:'turn_response'}.get(message.get('id'),'rpc_response')
                if 'id' in message else {'turn/completed':'turn_completion','turn/started':'turn_start_notice',
                    'item/completed':'assistant_or_compaction','thread/tokenUsage/updated':'context_report'}.get(
                        message.get('method'),'native_notification'))
            try:transport.receive(message,prompt)
            finally:
                item=(message.get('params') or {}).get('item') or {}
                spool=item.get('text')
                if isinstance(spool,AssistantSpool) and spool is not transport.final:spool.close()
            if transport.finished: break
        if not transport.finished:
            print('Native app-server transport diagnostic: '+json.dumps(dict(error_stage='event_stream',
                error_code='native_terminal_unreported')),file=sys.stderr)
        return 0 if transport.completed else 1
    except Exception as error:
        error._scid_transport_stage=phase
        raise
    finally:
        transport.close()
        process.stdin.close()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)
        process.stdout.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['--']: argv = argv[1:]
    try:
        return run(argv, sys.stdin.read())
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        # Only fixed stages/codes are published; exception strings may contain
        # native request bodies, config bindings, or paths and are never printed.
        stage=getattr(error,'_scid_transport_stage','launch_or_declaration')
        if stage not in {'initialization_submit','event_projection','initialization_response','thread_response',
                'turn_response','rpc_response','turn_completion','turn_start_notice','assistant_or_compaction',
                'context_report','native_notification'}:stage='launch_or_declaration'
        codes={'Native terminal status unavailable':'terminal_status_unavailable',
            'Native final assistant message unavailable':'final_unavailable',
            'Native assistant text unavailable':'assistant_text_unavailable',
            'Native notification turn changed':'turn_identity_changed',
            'Native notification thread changed':'thread_identity_changed',
            'Native thread launch did not match declaration':'launch_binding_mismatch',
            'Native RPC submission response unavailable':'rpc_response_unavailable',
            'Native metadata scalar invalid':'metadata_scalar_invalid'}
        code=codes.get(str(error),'native_io_error' if isinstance(error,OSError) else 'native_projection_invalid')
        print('Native app-server transport diagnostic: '+json.dumps(dict(error_stage=stage,error_code=code)),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
