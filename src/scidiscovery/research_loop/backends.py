"""Native agent adapters. Execution, budgets and scientific verdicts stay in control.

The protocol adapter runs exact operator argv, reads one JSON request from stdin,
and accepts JSONL ``session``, ``usage``, ``result`` and ``completed`` events.
Usage is cumulative for this invocation; absent counters remain unknown. A result
event contains ``result`` (the scientific JSON object). Completion is explicit.
This transport does not attest complete authorship or implement session resume.
"""
from __future__ import annotations

import json
from pathlib import Path

from .native_usage import SessionUsage, tokens

JSON_RESPONSE_INSTRUCTION = 'Return only one JSON object as your final response, with no Markdown fences or surrounding prose.'


def configuration(backend='codex', launcher=None, native_options=None, *, existing=None):
    if not isinstance(backend,str) or backend not in {'codex', 'protocol'}:
        raise ValueError('Native backend must be codex or protocol')
    if launcher is None:
        if backend != 'codex':
            raise ValueError('Protocol backend requires an explicit launcher')
        launcher = ['codex']
    if (not isinstance(launcher, (list, tuple)) or not launcher
            or any(not isinstance(item, str) or not item or '\0' in item for item in launcher)):
        raise ValueError('Native launcher must be a nonempty argv')
    supplied = {} if native_options is None else native_options
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
    if backend == 'protocol' and (sandbox or features):
        raise ValueError('Protocol backend does not implement Codex native options')
    frozen = dict(native_backend=backend, native_launcher_argv=list(launcher),
                  native_options=options if native_options is not None else None)
    frozen['native_permissions'] = permission_summary(frozen)
    adapter = selected(frozen)
    from . import skill_supply
    retained=(existing or {}).get('native_capabilities',{}).get('native_launcher_contract',...)
    if existing is not None and retained is ...:
        raise ValueError('Frozen native namespace launcher contract is missing')
    namespace=skill_supply.native_launcher_contract(backend,list(launcher),retained=retained)
    frozen['native_capabilities'] = dict(native_launcher_contract=namespace,response_schema_transport=adapter.schema_transport,
        session_resume=adapter.supports_resume, complete_native_authorship=adapter.complete_authorship)
    return frozen


def permission_summary(settings):
    """Distinguish desired options from the access policy actually selected.

    These are native policy declarations, not a same-user security boundary or
    a promise that a tool, network destination or helper is available.
    """
    options = settings.get('native_options')
    fields = ('workspace','network','web','subagents')
    requested = {key:options[key] for key in fields} if options is not None else {}
    launcher = settings['native_launcher_argv']
    native = (settings.get('native_backend','codex')=='codex'
              and len(launcher)==1 and Path(launcher[0]).name=='codex')
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
    if not isinstance(backend,str) or backend not in {'codex','protocol'}:raise ValueError('Unsupported frozen native backend')
    if backend == 'codex':return Codex(settings,schema_transport=schema_transport)
    adapter=Protocol(settings)
    if schema_transport is not None and schema_transport!=adapter.schema_transport:
        raise ValueError('Protocol backend owns its response schema transport')
    return adapter


def _toml(value):
    if isinstance(value,dict):
        return '{'+','.join(json.dumps(k)+'='+_toml(v) for k,v in value.items())+'}'
    if isinstance(value,(list,tuple)):
        return '['+','.join(_toml(item) for item in value)+']'
    return json.dumps(value, ensure_ascii=False)


def _usage(value):
    return tokens(dict(cached_input_tokens=0, **value)) if isinstance(value,dict) and 'cached_input_tokens' not in value else tokens(value)


class Codex:
    name = 'codex'
    terminal_source = 'turn.completed'
    supports_resume = True

    def __init__(self, settings, *, schema_transport=None):
        self.settings = settings
        self.launcher = settings['native_launcher_argv']
        self.options = settings.get('native_options')
        self.standard = len(self.launcher)==1 and Path(self.launcher[0]).name=='codex'
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
        extra=['-c','hooks.PostToolUse='+_toml(definition['PostToolUse'])]
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

    def meter(self, home, work, session=None):
        return SessionUsage(home,work,session=session)

    def event(self, value):
        kind = value.get('type')
        if kind=='thread.started':return dict(session=value.get('thread_id'))
        if kind=='turn.completed':return dict(completed=True,usage=_usage(value.get('usage')))
        if kind in {'turn.failed','error','turn.incomplete','turn.interrupted'}:
            from . import native_recovery
            return dict(failed=True, failure=native_recovery.classify(value))
        if isinstance(kind,str) and kind.startswith(('turn.','item.','thread.')):
            return dict(activity=True)
        return {}

    def identity(self, meter, session):
        return dict(backend=self.name,home=str(meter.home),path=str(meter.path) if meter.path else None,
                    participant_scope='codex_exec_single_agent_v1' if self.complete_authorship else 'unsupported')

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
            return [dict(type='message',text=item['text'])]
        if item.get('type') in {'command_execution','mcp_tool_call','web_search','file_change'}:
            tool=item.get('tool') if item.get('type')=='mcp_tool_call' else item['type']
            if not isinstance(tool,str):return []
            return [dict(type='tool',tool=tool[:128],status=kind.removeprefix('item.'))]
        return []


class EventUsage:
    """Backend-declared cumulative invocation usage; no invented session file."""
    path = None
    fault = None

    def __init__(self):
        self.thread = None
        self.known = dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
        self.observed = False

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
                    has_observation=self.observed,baseline_tokens=None,
                    unreported_usage='Running requests and reporting delay are unmeasured.',individual_request_usage='unavailable')

    def poll(self):return self.snapshot()


class Protocol:
    name = 'protocol'
    terminal_source = 'protocol.completed'
    standard = False
    supports_resume = False
    complete_authorship = False
    supports_native_schema_flag = False
    schema_transport = 'protocol_request'
    active_schema_transport = 'protocol_request'
    instruction_transport = 'prompt'

    def __init__(self, settings):self.settings=settings
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
    def meter(self, home, work, session=None):
        if session is not None:raise ValueError('Protocol backend does not support resume')
        return EventUsage()
    def event(self, value):
        kind=value.get('type')
        if kind=='session':return dict(session=value.get('session_id'))
        if kind=='usage':return dict(live_usage=_usage(value.get('usage')))
        if kind=='result':return dict(result=value.get('result'))
        if kind=='completed':return dict(completed=True,usage=_usage(value.get('usage')))
        if kind in {'failed','error'}:return dict(failed=True)
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
