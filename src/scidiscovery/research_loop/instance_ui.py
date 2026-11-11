"""One explicit instance's loopback status, controls and optional decision host."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
from html.parser import HTMLParser
import hmac
import json
import os
import re
from pathlib import Path
import secrets
import shlex
import shutil
import threading
import tempfile
import time
from urllib.parse import parse_qs, quote, urlsplit
import uuid

from scidiscovery.browser_access import BrowserAccessDenied, BrowserAccessService, access_cookie, cookie_name
from scidiscovery import execution_security as security
from . import contracts as c, execution_authority as authority, instance_control as control
from . import instance_science as science
from . import instance_display as display, ui_saved_status as saved_status
from . import instance_render as render
from . import instance_live as live
from . import backends, budget, configuration_form, configuration_updates, progress, public_output, research_config, user_guidance
from . import ui_components as ui
from . import ui_mechanisms, ui_view, ui_translation, ui_errors, ui_guidance
from . import fork_ui
from . import protocol, workspace_catalog


_ENTRY_SCRIPT = "location.replace('./');"


@contextmanager
def _host_lock(root, *, workspace=False):
    directory = root / ('.scid-ui' if workspace else 'execution-authority')
    directory.mkdir(exist_ok=True)
    fd = os.open(directory / 'ui.lock',
                 os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('This instance already has a UI host') from error
        yield
    finally:
        os.close(fd)


class InstanceUI:
    def __init__(self, root, *, secret_file, instance_roots=(), approval_host=True, workspace=False, multi_workspace=False, translation_service=None, translation_config=None):
        root = Path(root).resolve()
        if workspace and not root.is_dir():
            raise ValueError('UI workspace must be an existing explicit directory')
        settings = {} if workspace else display.settings(root)
        self._initialize(root, settings=settings, secret_file=secret_file, instance_roots=instance_roots,
            approval_host=approval_host, workspace=workspace, multi_workspace=multi_workspace,
            translation_service=translation_service, translation_config=translation_config)

    @classmethod
    def _from_settings(cls, root, *, settings, secret_file, translation_service):
        instance = cls.__new__(cls)
        instance._initialize(root, settings=settings, secret_file=secret_file,
            approval_host=False, translation_service=translation_service)
        return instance

    def _initialize(self, root, *, settings, secret_file, instance_roots=(), approval_host=True,
                    workspace=False, multi_workspace=False, translation_service=None, translation_config=None):
        self.root = root
        self.workspace = workspace
        self.secret_file = Path(secret_file).resolve()
        self.instance_roots = [self.root]
        self.instance_identities = {}
        self.catalog_errors = []
        self.multi_workspace = bool(multi_workspace and workspace)
        self.workspace_roots = [self.root]
        self.workspaces = {}
        self.children = {}
        self.error_settings = settings
        self.approval_host = bool(approval_host and not workspace)
        self.approvals_configured = False
        self.approvals = False
        self._serving = False
        self._approval_endpoint_origin = None
        self.secret = security.read_secret_file(secret_file, label='instance UI')
        self.instance = ('workspace-' + uuid.uuid4().hex
                         if workspace else settings['goal_id'])
        self.boot = uuid.uuid4().hex
        self.browser = BrowserAccessService(secret=self.secret, ui_session_id=self.boot)
        self.management_token = secrets.token_urlsafe(32)
        self.read_token = secrets.token_urlsafe(32)
        self.form_nonces = {}
        self.form_nonce_ttl = 3600.0
        self.form_nonce_limit = 512
        self.control_lock = threading.Lock()
        self.control_result = None
        self.config_selections = {}
        self.fork_attempts = {}
        self.delete_requests = {}
        self.workspace_host = None
        self.deleted_roots = set()
        self.deleted_children = {}
        self.host = None
        self.live_cache = live.SnapshotCache()
        self.summary_cache = live.SnapshotCache()
        self.live_stop = threading.Event()
        self.live_gate = threading.BoundedSemaphore(live.MAX_CLIENTS)
        self.activity_lock = threading.Lock()
        self.activity = {}
        # Display-only memo of verified sealed reads (identity-checked per use)
        # and candidate projections keyed by the instance metadata fingerprint.
        self.cache_lock = threading.RLock()
        self.view_memo = ui_view.DisplayMemo(lock=self.cache_lock)
        self.status_memo = ui_view.DisplayMemo(lock=self.cache_lock)
        self.candidate_cache = ui_view.DisplayMemo(lock=self.cache_lock)
        self.history_pages = ui_view.DisplayMemo(lock=self.cache_lock)
        # One compact UI-observed context fact for the newest exact attempt.
        # Never used by control or written back into source context telemetry.
        self.context_display = None
        self._refresh_approval_host()
        self.translation = translation_service or ui_translation.TranslationService(self.root / ".scid-ui", config=translation_config)
        self.translation_nonce = secrets.token_urlsafe(32)
        if workspace:
            for item in self._catalog('instances'):
                try:
                    selected = workspace_catalog.record(item, 'instances')
                    if selected == self.root:
                        raise ValueError('Workspace cannot also be its registered instance')
                    if selected not in self.instance_roots:
                        self.instance_roots.append(selected)
                        self.instance_identities[selected] = item['goal_id']
                except Exception as error:
                    self.catalog_errors.append(str(error))
        for item in instance_roots:
            selected = Path(item).expanduser().resolve()
            try:
                settings = display.settings(selected)
                self._register_instance(selected, settings)
            except Exception as error:
                if selected not in self.instance_roots:
                    self.instance_roots.append(selected)
                self.catalog_errors.append(str(error))
        if self.multi_workspace:
            for item in self._catalog('workspaces'):
                try:
                    selected = workspace_catalog.record(item, 'workspaces')
                    if selected not in self.workspace_roots:
                        self.workspace_roots.append(selected)
                except Exception as error:
                    self.catalog_errors.append(str(error))

    def _catalog(self, kind):
        try:
            return workspace_catalog.load(self.root, kind)
        except Exception as error:
            self.catalog_errors.append(str(error))
            return []

    def _remember(self, kind, value, *, remove=False):
        try:
            workspace_catalog.remember(self.root, kind, value, remove=remove)
        except Exception as error:
            # Publishing research succeeded already. Preserve its navigation and
            # report this optional navigation persistence failure on the home page.
            self.catalog_errors.append('目录列表未保存：' + str(error))

    def _register_instance(self, root, settings):
        protocol.require_settings_format(settings)
        self._remember_instance(root, settings)

    def _remember_instance(self, root, settings):
        goal_id = settings['goal_id']
        with self.control_lock:
            if root in self.instance_identities and self.instance_identities[root] != goal_id:
                raise ValueError('Registered instance identity changed; refusing to rebind its root')
            self.instance_identities[root] = goal_id
            if root not in self.instance_roots:
                self.instance_roots.append(root)
            self.deleted_roots.discard(root)
        owner = self if self.workspace else self.workspace_host
        if owner is not None:
            if owner is not self:
                owner._remember_instance(root, settings)
            else:
                self._remember('instances', dict(root=str(root), goal_id=goal_id))

    def _forget_instance(self, root, goal_id):
        self.deleted_roots.add(root)
        if self.workspace:
            self._remember('instances', dict(root=str(root), goal_id=goal_id), remove=True)
        elif self.workspace_host is not None:
            self.workspace_host._forget_instance(root, goal_id)

    def _workspace_child(self, index):
        if not self.multi_workspace or not 0 < index < len(self.workspace_roots):
            raise ValueError('Select an explicitly registered workspace')
        with self.control_lock:
            root = self.workspace_roots[index]
            if not root.is_dir() or root.resolve() != root:
                raise ValueError('Registered workspace directory is unavailable or changed')
            if index not in self.workspaces:
                self.workspaces[index] = InstanceUI(root, secret_file=self.secret_file, workspace=True,
                    approval_host=False, translation_service=self.translation)
                self.workspaces[index].global_workspace = self
            child = self.workspaces[index]
            child.host = self.host
            child.live_stop = self.live_stop
            child.live_gate = self.live_gate
            return child

    def _refresh_approval_host(self):
        """Prepare the explicit UI host without changing any job permission."""
        if self.workspace:
            return
        with self.control_lock:
            self.approvals_configured = authority.host_contract(self.root) is not None
            if not self.approval_host or not self.approvals_configured:
                return
            if not self.approvals:
                authority.bind_secret(self.root, self.secret, source=self.secret_file)
                self.approvals = True
            if self._serving and self.host and self._approval_endpoint_origin != self.host:
                endpoint = dict(instance=self.instance, boot=self.boot, origin='http://' + self.host)
                endpoint['receipt_hmac'] = security.receipt_hmac(self.secret, security.canonical(endpoint))
                c.write(self.root / 'execution-authority/ui-endpoint.json', endpoint)
                self._approval_endpoint_origin = self.host

    def _browser(self, cookie, *, scope='maintenance'):
        self.browser.from_cookie(cookie, instance_id=self.instance, scope=scope)
        from http.cookies import SimpleCookie
        parsed = SimpleCookie(); parsed.load(cookie)
        return parsed[cookie_name(self.instance)].value

    def _page(self, title, body, *, live=True):
        marker = '<p class="connection" data-live-url="/live" role="status">正在连接工作台状态…</p>'
        return render.page(title, (marker if live else '') + body, live=live)

    def _problem(self, error, *, context='', record=False, reference=''):
        return ui_errors.present(error, self.root, context=context, settings=self.error_settings,
            record_diagnostic=record, reference=reference)

    @staticmethod
    def _error_page(problem, body):
        # Recovery pages must also work without the unreadable instance, live
        # stream or authenticated translation service that caused the failure.
        return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>'
            + html.escape(problem.title) + '</title><link rel="stylesheet" href="/assets/style.css"></head><body><main><h1>' + html.escape(problem.title) + '</h1>'
            + body + '</main></body></html>').encode()

    def _failure(self, error):
        problem = self._problem(error)
        return dict(error=dict(kind=problem.title, message=problem.cause + ' ' + problem.hint
            + (' 错误编号：' + problem.reference if problem.reference else '')))

    def _guarded(self, function, *args, clean=None, **options):
        try:
            return function(*args, **options)
        except Exception as error:
            return self._failure(error)

    def _fragment(self, renderer, *args, href='/results', **options):
        try:
            return renderer(*args, **options)
        except Exception as error:
            return ui.error_inline(self._failure(error)) + ui.link('打开完整页面', href)

    def _error_response(self, error, target, *, json_response=False, record=False):
        path = target.partition('?')[0]
        context = ('translation' if '/translation/' in path else 'load-config' if path.endswith('/load-config')
            else 'configuration' if path.endswith(('/configure', '/create', '/configuration', '/configuration-update'))
            else 'open' if path.endswith('/open') else 'budget' if path.endswith('/budget') else 'control')
        problem = self._problem(error, context=context, record=record)
        if isinstance(error, json.JSONDecodeError) and path.endswith(('/lookup', '/start')):
            problem = ui_errors.Problem(400, '翻译请求 JSON 无效', '请求体不是有效的 JSON。',
                '重新打开阅读页面后再提交翻译请求。', href='/reading', code='invalid_request')
        if json_response:
            return problem.status, json.dumps(problem.payload(), ensure_ascii=False).encode(), {'Content-Type': 'application/json; charset=utf-8'}
        return problem.status, self._error_page(problem, problem.markup()), {}

    def _control_error(self, token, action, form, error):
        context = 'configuration' if action in {'configure', 'create', 'configuration', 'configuration-update'} else action if action in {'open', 'load-config', 'budget'} else 'control'
        problem = self._problem(error, context=context, record=True)
        if action == 'configuration':
            fields = '<label>TOML 路径<input name="config_path" value="' + html.escape(form.get('config_path', ''), quote=True) + '"></label>'
            fields += '<label>TOML 内容<textarea name="config_text">' + html.escape(form.get('config_text', '')) + '</textarea></label>'
            return problem.status, self._error_page(problem, problem.markup() + self._form(token, action, fields, '另存为新 TOML', mode='save')), {}
        if action == 'delete':
            return self._delete_page(token, form=form, problem=problem)
        if action in {'fork-preview', 'fork-create'}:
            code = getattr(error, 'code', '')
            if code.startswith('fork_'):
                problem = ui_errors.Problem(409 if code in {'fork_preview_stale', 'fork_source_changed',
                    'fork_operation_conflict', 'fork_target_exists', 'fork_busy'} else 422,
                    '分支条件尚未满足', '本次分支尚未创建，或已发布结果需要按原操作编号核对。',
                    '保留原输入；检查来源、额度和操作编号后重新预览。', code=code)
            return self._fork_page(token, form.get('step', ''), draft=form, problem=problem,
                reconcile=action == 'fork-create')
        if action == 'configuration-update':
            state = configuration_updates.status(self.root)
            fields = {key: value for key, value in form.items() if key not in {'nonce', 'csrf', 'expected_revision', 'authorization_note'}}
            draft = configuration_form.imported(state['configuration'], base=self.root)
            try:
                draft = configuration_form.parse_edit(fields, base=self.root, configuration=state['configuration'])
            except Exception as parse_error:
                draft = getattr(parse_error, 'draft', draft)
            return self._configuration_page(token, draft=draft, problem=problem,
                expected_revision=form.get('expected_revision', str(state['revision'])),
                authorization_note=form.get('authorization_note', ''))
        if action in {'configure', 'create', 'load-config'}:
            selected = self.config_selections.get(token)
            draft = selected['draft'] if selected else configuration_form.blank(base=self.root)
            if action == 'configure':
                fields = {key: value for key, value in form.items() if key not in {'nonce', 'csrf', 'selection', 'save_path', 'mode'}}
                try:
                    draft = configuration_form.parse(fields, base=draft['base'])
                except Exception as parse_error:
                    draft = getattr(parse_error, 'draft', draft)
            selection = self._remember_config(token, draft, source=selected['source'] if selected else None)
            return self._new_research_page(token, draft=draft, selection=selection, notice=problem,
                config_path=form.get('config_path', ''), save_path=form.get('save_path', ''))
        fields = ''
        if action in {'open', 'workspace-open'}:
            fields = '<label>' + ('工作区目录' if action == 'workspace-open' else '实例目录') + '<input name="root" required value="' + html.escape(form['root'], quote=True) + '"></label>'
        elif action == 'budget':
            for key, label in [('max_tokens', '新的 tokens 总上限'), ('max_seconds', '新的时间总上限（秒）'),
                    ('max_steps', '新的阶段总上限'), ('authorization_note', '调整说明')]:
                fields += '<label>' + label + '<textarea name="' + key + '">' + html.escape(form[key]) + '</textarea></label>'
            if form.get('authorization_statement', '').strip():
                fields += '<input type="hidden" name="authorization_statement" value="' + html.escape(form['authorization_statement'], quote=True) + '">'
            fields += '<p class="note">提交即确认所填写的新上限；说明随变更记录保存。</p>'
        elif action == 'continue':
            fields = '<input type="hidden" name="ack_correction" value="' + html.escape(form['ack_correction'], quote=True) + '">'
            fields += '<label>继续本次研究的说明<textarea name="authorization_note">' + html.escape(form['authorization_note']) + '</textarea></label>'
        elif action == 'resume' and isinstance(error, ui_errors.InputError):
            fields = '<label>阶段编号<textarea name="step">' + html.escape(form['step']) + '</textarea></label>'
        elif action == 'guidance-submit':
            return self._guidance_page(token, draft=form, problem=problem)
        elif action.startswith('guidance-'):
            for key, value in form.items():
                if key not in {'nonce', 'csrf'}:
                    fields += '<label>' + html.escape(key) + '<textarea name="' + html.escape(key, quote=True) + '">' + html.escape(value) + '</textarea></label>'
        # Failed control receipts must first be reconciled on the operations
        # page; rendering a start/resume form here would invite blind replay.
        body = problem.markup() + (self._form(token, action, fields, '确认追加预算' if action == 'budget' else '修改后重新提交')
            if fields and not isinstance(error, ui_errors.ControlFailure) else '')
        return problem.status, self._error_page(problem, body), {}

    def _csrf(self, browser_token, action, nonce):
        return security.receipt_hmac(self.secret, security.canonical(
            ['scidiscovery.instance-control.v1', self.instance, self.boot, browser_token, action, nonce]))

    def _form_nonce(self, browser_token, action, identity='', *, bindings=None):
        """Bounded, stable credentials for this session/action/logical form only."""
        key = (browser_token, action, identity)
        with self.control_lock:
            now = time.monotonic()
            self.form_nonces = {key: row for key, row in self.form_nonces.items()
                if now - row[1] < self.form_nonce_ttl}
            if key not in self.form_nonces:
                while len(self.form_nonces) >= self.form_nonce_limit:
                    self.form_nonces.pop(next(iter(self.form_nonces)))
                self.form_nonces[key] = (secrets.token_urlsafe(32), now, dict(bindings or {}))
            return self.form_nonces[key][0]

    def _consume_form(self, browser_token, action, form, *, consume=True, replay=False):
        with self.control_lock:
            if not hmac.compare_digest(form['csrf'], self._csrf(browser_token, action, form['nonce'])):
                raise PermissionError('Instance control CSRF or nonce mismatch')
            match = next((key for key, row in self.form_nonces.items()
                if key[:2] == (browser_token, action) and hmac.compare_digest(row[0], form['nonce'])), None)
            if match is None or time.monotonic() - self.form_nonces[match][1] >= self.form_nonce_ttl:
                if match is not None:
                    self.form_nonces.pop(match)
                raise ui_errors.InputError('nonce', '表单已过期或已提交；填写内容已保留。',
                    '核对当前状态与原操作编号后，显式重新提交；工作台不会自动重试。')
            issued = self.form_nonces[match]
            if any(form.get(key) != value for key, value in issued[2].items()):
                raise ui_errors.InputError('nonce', '此表单绑定的原身份已变化。',
                    '重新打开原表单，核对原版本与原操作编号后显式提交。')
            if not self.workspace and action not in {'fork-create', 'delete'} and display.settings(self.root)['goal_id'] != self.instance:
                raise ui_errors.InputError('root', '此目录已属于另一个研究实例。', '从工作台按目录重新加载，核对新实例后操作。')
            if replay:
                # Keep this bounded credential for exact response-loss retries.
                # A later rendered form gets a fresh intent; the old nonce can
                # only recover its frozen submitted request.
                self.form_nonces.pop(match)
                self.form_nonces[(*match[:3], issued[0])] = (issued[0], issued[1],
                    {key: value for key, value in form.items() if key not in {'nonce', 'csrf'}})
            elif consume:
                self.form_nonces.pop(match)

    def _operation_id(self, browser_token, action, form):
        return self._csrf(browser_token, 'operation-' + action, form['nonce'])[:32]

    def _maintenance_blocked(self):
        return self._serving and not self.workspace and self.workspace_host is None

    def _require_maintenance_handoff(self, action):
        if action in {'delete', 'fork-preview', 'fork-create'} and self._maintenance_blocked():
            raise ui_errors.InputError('root', '独立实例页面正在持有此研究的维护锁。',
                '先关闭此独立实例网页服务，再从工作台按完整目录加载该研究并执行删除或分支。')

    def _control_form(self, browser_token, action, *, correction_trigger=None, label=None):
        fields = ''
        if action == 'continue':
            fields = '<input type="hidden" name="ack_correction" value="' + html.escape(correction_trigger or '', quote=True) + '">'
            fields += ('<label>说明为什么继续本次研究 <textarea name="authorization_note" required></textarea></label>'
                if correction_trigger else '<input type="hidden" name="authorization_note" value="">')
        return self._form(browser_token, action, fields,
            label or {'start': '开始研究', 'pause': '本阶段结束后暂停', 'continue': '继续研究'}[action])

    def _continue(self, **options):
        self.control_result = control.start_background(self.root, continue_run=True, **options)

    def _interrupt_form(self, browser_token, current):
        if not current:
            return ''
        if current.get('interrupt_requested'):
            return '<p class="note">立即中断已请求，正在等待原任务停止并记录清理结果；请勿重新提交。</p>'
        target = current.get('interrupt_target')
        if not target:
            return ('<p class="note">此原执行器未声明立即中断支持；目前只能请求本阶段结束后暂停。'
                '请在原宿主核对启动方式；仅原科学执行器仍在前台并由该终端管理时可用 Ctrl+C。'
                '停止网页终端不会停止后台研究；请核对原任务与清理结果，未知调用勿重新提交。</p>'
                if current.get('interrupt_support') == 'unsupported' else '')
        fields = ''.join('<input type="hidden" name="' + key + '" value="'
            + html.escape(value, quote=True) + '">' for key, value in (
                ('step', str(target['step'])), ('expected_attempt', str(target['attempt'])),
                ('native_intent_ref', json.dumps(target['native_intent_ref'], ensure_ascii=False))))
        return self._form(browser_token, 'interrupt', fields, '立即中断当前工作') + (
            '<p class="note">请求原任务停止并暂停后续研究。已用预算和原调用保留；外部调用状态仍须按原调用核对。</p>')

    def _operator_resume_form(self, browser_token, target, *, unexpected=False, same_session_only=False, request_recheck=False):
        def form(new_context, label):
            fields=''.join('<input type="hidden" name="'+key+'" value="'+html.escape(value,quote=True)+'">'
                for key,value in (('step',str(target['step'])),('expected_attempt',str(target['attempt'])),
                    ('native_intent_ref',json.dumps(target['native_intent_ref'],ensure_ascii=False)),
                    ('after_interrupt','1'),('new_context','1' if new_context else '0')))
            return self._form(browser_token,'resume',fields,label)
        if same_session_only:
            if request_recheck:
                return form(False,'核对并接续') + (
                    '<p class="note">请求控制层重查这一原任务的身份、进程、服务条件和剩余预算；通过核验后才接续同一原会话。原文件、已用预算与恢复次数保留。</p>')
            return form(False,'重试失败的原任务') + (
                '<p class="note">核对修复后的服务条件，接续同一原会话；保留原任务、已用预算与已消耗的恢复次数。未知实验仍须按原调用核对，不能重新提交。</p>')
        if unexpected:
            return form(False,'核对并恢复原任务') + form(True,'用新上下文恢复该原任务') + (
                '<p class="note">仍在运行的原工作只重新连接、收集，不启动替身。原本机 writer 确认停止后才恢复同一任务；'
                '新上下文无需完整旧会话或作者证明，已用预算、原文件与未知实验保留。探测或清理仍未知时会显示具体原因，不能重发原调用。</p>')
        return form(False,'接续已中断的工作') + (
            '<p class="note">在同一研究、同一逻辑任务下新增一次明确授权的助手工作；保留原文件、已用预算和上下文历史。'
            '支持且可验证时接续原会话；不支持时明确使用新上下文。未知实验先按原调用诊断、查询或对账，不能重发。</p>'
            '<details><summary>原会话不可接续时</summary>'
            '<p>原会话信息缺失或会话续接次数耗尽时，可显式选择新上下文；原预算、权限和未知调用约束继续生效。</p>'
            +form(True,'使用新上下文接续同一原任务')+'</details>')

    def _form(self, browser_token, action, fields, label, *, mode=None):
        class Bindings(HTMLParser):
            def __init__(self):
                super().__init__(); self.values = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and attrs.get('name') and (attrs.get('type') == 'hidden' or 'readonly' in attrs):
                    self.values[attrs['name']] = attrs.get('value', '')
        bindings = Bindings(); bindings.feed(fields)
        nonce = self._form_nonce(browser_token, action, fields, bindings=bindings.values)
        submit = ' name="mode" value="' + mode + '"' if mode else ''
        return ('<form method="post" action="/control/' + action + '">'
            '<input type="hidden" name="nonce" value="' + nonce + '">'
            '<input type="hidden" name="csrf" value="' + self._csrf(browser_token, action, nonce) + '">'
            + fields + '<button type="submit"' + submit + '>' + html.escape(label) + '</button></form>')

    def _admin(self, browser_token):
        if 'maintenance' not in self.browser.verify(browser_token, instance_id=self.instance).scopes:
            raise PermissionError('Local management access required')

    def _child(self, index):
        if not 0 < index < len(self.instance_roots) or self.instance_roots[index] in self.deleted_roots:
            raise ValueError('Select an explicitly opened instance')
        root = self.instance_roots[index]
        if root.resolve() != root:
            raise ValueError('Registered instance root changed')
        settings = protocol.require_settings_format(display.settings(root))
        return self._materialize_child(index, settings)

    def _materialize_child(self, index, settings):
        """Produce a browser child from this operation's accepted settings."""
        with self.control_lock:
            root = self.instance_roots[index]
            candidate = self.children.get(index)
            if candidate is None:
                candidate = InstanceUI._from_settings(root, settings=settings,
                    secret_file=self.secret_file, translation_service=self.translation)
            return self._child_from_settings(index, settings, candidate)

    def _child_from_settings(self, index, settings, candidate):
        """Consume accepted facts without reopening settings or probing paths."""
        root = self.instance_roots[index]
        expected = self.instance_identities.get(root)
        if expected is not None and expected != settings['goal_id']:
            raise ValueError('Registered instance identity changed; refusing to rebind its root')
        if candidate.instance != settings['goal_id']:
            raise ValueError('Opened instance identity changed; reopen requires its original identity')
        self.children[index] = candidate
        candidate.workspace_host = self
        candidate.host = self.host
        candidate.live_stop = self.live_stop
        candidate.live_gate = self.live_gate
        return candidate

    def _routed_child(self, index, route, cookie):
        retained = self.deleted_children.get(index) if route in {'delete', 'control/delete'} else None
        if retained is None:
            return self._child(index)
        if self.instance_roots[index] not in self.deleted_roots:
            child = self._child(index)
            try:
                child._browser(cookie, scope='read')
                return child
            except BrowserAccessDenied:
                pass
        return retained

    @staticmethod
    def _mounted(result, index):
        return InstanceUI._mount(result, '/instances/' + str(index), instance_list=True)

    @staticmethod
    def _mount(result, prefix, *, instance_list=False):
        status, body, headers = result
        if isinstance(body, live.Stream):
            body.mount = prefix + (getattr(body, 'mount', '') or '')
        if isinstance(body, bytes) and headers.get('Content-Type', 'text/html').startswith('text/html'):
            # Only host-authored HTML links/forms are mounted; scientific bytes
            # and checked download streams are returned exactly as sealed.
            if instance_list:
                body = body.replace(b'href="/instances"', b'href="__INSTANCE_LIST__"')
            body = body.replace(b'href="/', ('href="' + prefix + '/').encode())
            body = body.replace(b'action="/', ('action="' + prefix + '/').encode())
            for attribute in (b'data-live-url', b'data-progress', b'data-translation-url'):
                body = body.replace(attribute + b'="/', attribute + ('="' + prefix + '/').encode())
            body = body.replace(b'href="__INSTANCE_LIST__"', b'href="/instances"')
            body = body.replace(b'href="__WORKSPACE_HOME__"', b'href="/"')
        headers = dict(headers)
        if headers.get('Location', '').startswith('/') and not (instance_list and headers['Location'] == '/instances'):
            headers['Location'] = prefix + headers['Location']
        return status, body, headers

    @staticmethod
    def _translation_json(value):
        return 200, json.dumps(value, ensure_ascii=False).encode(), {'Content-Type': 'application/json; charset=utf-8'}

    def _translation_state(self, token):
        manager = 'maintenance' in self.browser.verify(token, instance_id=self.instance).scopes
        actions = ['lookup', 'start', 'configure'] if manager else ['lookup']
        return dict(self.translation.settings(), nonce=self.translation_nonce,
            csrf={action: self._csrf(token, 'translation-' + action, self.translation_nonce) for action in actions})

    def _reading_page(self, token, *, submitted=None, problem=None):
        self._admin(token)
        value = self.translation.settings()
        if submitted is not None:
            value.update({key: submitted[key] for key in ('target_language', 'backend', 'model',
                'reasoning_effort', 'service_tier', 'timeout_seconds') if key in submitted})
            value['enabled'] = submitted.get('enabled') == 'true'
        def field(name, label, *, placeholder='', type_='text'):
            return ('<label>' + label + '<input name="' + name + '" type="' + type_ + '" value="'
                + ui.esc(value.get(name) or '') + '" placeholder="' + ui.esc(placeholder) + '"></label>')
        fields = ('<input type="hidden" name="nonce" value="' + self.translation_nonce + '">'
            '<input type="hidden" name="csrf" value="' + self._csrf(token, 'translation-configure', self.translation_nonce) + '">'
            '<label class="check-label"><input type="checkbox" name="enabled" value="true"'
            + (' checked' if value.get('enabled') else '') + '>启用应用翻译模型</label>'
            + field('target_language', '目标语言', placeholder='zh-CN、en、ja…')
            + '<label>翻译后端<select name="backend">' + ''.join('<option value="' + ui.esc(backend) + '"'
                + (' selected' if value.get('backend') == backend else '') + '>' + ui.esc(label) + '</option>'
                for backend, label in [(name,name) for name in backends.names()]) + '</select></label>'
            + field('model', '翻译模型', placeholder='填写本机后端可用的模型名称')
            + '<details' + (' open' if problem and problem.field in {'reasoning_effort', 'service_tier', 'timeout_seconds', 'launcher_argv'} else '') + '><summary>高级设置</summary>'
            + field('reasoning_effort', '推理力度（可选）') + field('service_tier', '服务档位（可选）')
            + field('timeout_seconds', '单批超时（秒，最多 300）', type_='number')
            + '<label>启动命令参数（可选 JSON 数组）<textarea name="launcher_argv" placeholder="例如 [&quot;/path/to/launcher&quot;]">'
            + ui.esc(public_output.cleaner(self.root, settings={})((submitted or {}).get('launcher_argv', ''))) + '</textarea></label>'
            + '<label class="check-label"><input type="checkbox" name="reset_launcher" value="true">恢复所选后端的默认启动命令</label>'
            + '<p class="note">留空保留已保存的启动命令。请使用本机后端的登录设置；此处不填写凭据。</p></details>')
        body = ('<nav><a href="/">返回工作台</a></nav><details open><summary>阅读与翻译</summary>'
            '<p>翻译仅用于阅读。设置在工作台内跨实例共享，独立于研究模型与预算。原文和下载文件保持原样。</p>'
            '<p>点击翻译后才会调用模型。受支持的后端采用临时只读、禁网、禁网页与禁子代理设置，在独立临时目录运行。检测到工具事件会停止并拒绝译文；这不保证工具执行前全部禁用，也不是同用户强隔离。失败后仅手动重试。</p>'
            + '<p class="note">' + ('当前启动器支持此临时防护。' if value['supported'] else ui.esc(value['unsupported_reason'])) + '</p>'
             + (problem.markup() if problem else '') + '<form method="post" action="/translation/configure">' + fields + '<button type="submit">保存阅读设置</button></form></details>')
        return (problem.status if problem else 200), self._page(problem.title if problem else '阅读与翻译', body, live=False), {}

    def _translation_post(self, path, cookie, content_type, body):
        action = path.removeprefix('/translation/')
        if action not in {'lookup', 'start', 'configure'}:
            raise ValueError('Unknown translation action')
        token = self._browser(cookie, scope='read' if action == 'lookup' else 'maintenance')
        if len(body) > 256000:
            raise ValueError('Translation request is too large')
        if action == 'configure':
            if content_type != 'application/x-www-form-urlencoded':
                raise ValueError('Reading settings form required')
            form = parse_qs(body.decode('utf-8'), keep_blank_values=True, strict_parsing=True)
            allowed = {'nonce', 'csrf', 'enabled', 'backend', 'model', 'target_language', 'reasoning_effort',
                       'service_tier', 'timeout_seconds', 'launcher_argv', 'reset_launcher'}
            if set(form) - allowed or not {'nonce', 'csrf'} <= set(form) or any(len(values) != 1 for values in form.values()):
                raise ValueError('Reading settings contain invalid or duplicate fields')
            value = {key: values[0] for key, values in form.items()}
        else:
            if content_type != 'application/json':
                raise ValueError('Translation JSON required')
            value = json.loads(body)
            if not isinstance(value, dict) or set(value) != {'texts', 'nonce', 'csrf'}:
                raise ValueError('Exact translation request required')
        if not all(isinstance(value.get(key), str) for key in ('nonce', 'csrf')):
            raise PermissionError('Translation CSRF required')
        if not hmac.compare_digest(value['nonce'], self.translation_nonce) or not hmac.compare_digest(
                value['csrf'], self._csrf(token, 'translation-' + action, self.translation_nonce)):
            raise PermissionError('Translation CSRF mismatch')
        if action == 'configure':
            try:
                config = {key: item for key, item in value.items() if key not in {'nonce', 'csrf', 'launcher_argv', 'enabled', 'reset_launcher'}}
                config['enabled'] = value.get('enabled') == 'true'
                config['model'] = config.get('model') or None
                try:
                    config['timeout_seconds'] = int(config.get('timeout_seconds') or 120)
                except ValueError:
                    raise ValueError('Translation timeout_seconds must be between 1 and 300.') from None
                for key in ('reasoning_effort', 'service_tier'):
                    config[key] = config.get(key) or None
                if value.get('reset_launcher') == 'true':
                    config['launcher_argv'] = None
                if value.get('launcher_argv', '').strip():
                    config['launcher_argv'] = json.loads(value['launcher_argv'])
                self.translation.configure(config)
                return 303, b'', {'Location': '/reading'}
            except Exception as error:
                return self._reading_page(token, submitted=value, problem=self._problem(error, context='translation', record=True))
        return self._translation_json(getattr(self.translation, action)(value['texts']))

    def _instances_page(self, token):
        self._admin(token)
        if self.workspace:
            return self._workspace_page(token)
        body = '<nav><a href="/">返回总览</a><a href="/new">创建新研究</a></nav><p>这里仅显示你明确打开的实例。</p>'
        body += '<div id="live-instances">' + self._loaded_instances() + '</div>' + self._form(token, 'open',
            '<label>已有实例的完整目录 <input name="root" required></label>', '打开此实例')
        return 200, self._page('已有实例', body), {}

    def _configuration_page(self, token, *, text='', path='', result=None, draft=None, problem=None, expected_revision=None, authorization_note=''):
        self._admin(token)
        if self.workspace:
            return self._new_research_page(token)
        state = configuration_updates.status(self.root)
        settings = state['active_settings']
        view = public_output.settings_view(settings)
        original_view = public_output.settings_view(display.settings(self.root))
        clean = public_output.cleaner(self.root, settings=settings)
        current_budget = self._guarded(budget.ceilings, self.root)

        def text(value):
            return html.escape(clean(value, limit=64000))

        def facts(rows):
            return '<dl class="configuration-facts">' + ''.join(
                '<div><dt>' + label + '</dt><dd>' + text(value) + '</dd></div>' for label, value in rows) + '</dl>'

        if draft is None:
            draft = configuration_form.imported(state['configuration'], base=self.root)
        revision = state['revision'] if expected_revision is None else expected_revision
        status_note = ('已保存的配置等待下一个新节点生效。下方表单显示待生效配置；当前节点继续使用原配置。'
            if state['pending'] else '下方表单显示当前配置。修改并保存后，从下一个新节点生效。')
        body = (self._navigation('/settings') + '<p class="settings-intro">调整这项研究接下来使用的模型、权限、预算、实验执行与交付安排。</p>'
            '<section class="configuration-status" role="status"><h2>' + ('配置待生效' if state['pending'] else '当前配置已生效')
            + '</h2><p>' + status_note + '</p></section>')
        if problem:
            body += problem.markup()
        fields = ('<input type="hidden" name="expected_revision" value="' + html.escape(str(revision), quote=True) + '">'
            + '<p class="note">相对路径基准：<code>' + html.escape(str(draft['base'])) + '</code>；推荐填写绝对路径。导入 TOML 时相对其所在目录。</p>'
            + configuration_form.render(draft, edit=True)
            + '<label>调整说明<textarea name="authorization_note" rows="2">' + html.escape(authorization_note)
            + '</textarea></label><p class="note">调整总预算时请填写原因；其他配置可留空。</p>')
        body += self._form(token, 'configuration-update', fields, '保存，从下一个节点生效').replace(
            '<form method="post"', '<form id="research-config-edit-form" method="post"', 1)
        body += ('<p class="note">任务原件、研究目录及已绑定的材料保持原有身份。补充研究关注点可使用<a href="/guidance">方向建议</a>。</p>'
            '<p class="note">工程目录与技能集合在创建时冻结；角色仍可选择本次研究已有的具名技能集合或命名研究者。</p>'
            '<details class="configuration-active"><summary>查看当前生效配置与权限说明</summary>'
            '<section class="configuration-section"><h2>模型与角色</h2>' + facts([
                ('运行后端', view.get('native_backend', '未声明')),
                ('全局模型', view.get('model', '未声明')),
                ('推理力度', view.get('reasoning_effort') or '后端未指定'),
                ('服务档位', view.get('service_tier') or '后端未指定')])
            + '<div class="table-scroll"><table class="configuration-roles"><thead><tr><th scope="col">研究角色</th><th scope="col">模型</th><th scope="col">推理力度</th><th scope="col">服务档位</th></tr></thead><tbody>')
        for role, label in ui_guidance.TARGETS[1:]:
            profile = (view.get('role_profiles') or {}).get(role, {})
            body += '<tr><th scope="row">' + label + '</th>' + ''.join('<td>' + text(value) + '</td>' for value in (
                profile.get('model') or view.get('model', '未声明'),
                profile.get('reasoning_effort') or view.get('reasoning_effort') or '后端未指定',
                profile.get('service_tier') or view.get('service_tier') or '后端未指定')) + '</tr>'
        body += ('</tbody></table></div></section><section class="configuration-section"><h2>预算与用量</h2>'
            + ('<p class="note">当前预算暂不可读，请到总览或控制请求记录核对。</p>' if 'error' in current_budget else
                '<h3>当前生效总上限</h3>' + facts([('模型用量上限', '不限' if current_budget['max_tokens'] is None else str(current_budget['max_tokens']) + ' tokens'),
                    ('活动时间上限', '不限' if current_budget['max_seconds'] is None else str(current_budget['max_seconds']) + ' 秒'),
                    ('研究阶段数上限', str(current_budget['max_steps']) + ' 阶段')]))
            + '<details><summary>查看创建时的原始总上限</summary>'
            + facts([('模型用量上限', '不限' if original_view.get('max_tokens', '未声明') is None else str(original_view.get('max_tokens', '未声明')) + ' tokens'),
                ('活动时间上限', '不限' if original_view.get('max_seconds', '未声明') is None else str(original_view.get('max_seconds', '未声明')) + ' 秒'),
                ('研究阶段数上限', str(original_view.get('max_steps', '未声明')) + ' 阶段')])
            + '</details><p><a href="/#live-usage">查看已用额度</a> · <a href="/operations">核对预算调整记录</a></p></section>'
            '<section class="configuration-section"><h2>权限与实验执行</h2>'
            '<p>权限声明与后端生效值分别列出；执行授权仍需满足此实例的合同条件。</p>'
            '<div class="table-scroll"><table class="permission-review"><thead><tr><th scope="col">访问项目</th><th scope="col">配置声明</th><th scope="col">后端生效值</th><th scope="col">控制方式</th></tr></thead><tbody>')
        permissions = view.get('native_permissions', {})
        meanings = {'read-only':'只读文件', 'workspace-write':'可写工作目录', 'danger-full-access':'不限制文件访问',
            'disabled':'禁用', 'cached':'仅缓存', 'live':'实时检索', 'unknown':'由启动器控制，框架无法确认', True:'允许', False:'不允许'}
        for key, label in [('workspace', '文件读写'), ('network', '程序联网'), ('web', '网页检索'), ('subagents', '子研究助手')]:
            body += '<tr><th scope="row">' + label + '</th>' + ''.join('<td>' + text(meanings.get(value, value)) + '</td>' for value in (
                permissions.get('requested', {}).get(key, '未声明'), permissions.get('effective', {}).get(key, 'unknown'),
                {'hint_only':'仅为启动器提示', 'inherited_native_configuration':'沿用原生配置', 'unrestricted':'不限制',
                    'native_sandbox':'原生访问控制', 'unsupported':'后端不支持此限制', 'native_configuration':'原生配置'}.get(
                    permissions.get('enforcement', {}).get(key), '未声明'))) + '</tr>'
        body += '</tbody></table></div>'
        if permissions.get('limitations'):
            body += '<ul class="permission-limit">' + ''.join('<li>' + text(item) + '</li>' for item in permissions['limitations']) + '</ul>'
        body += facts([('实验模式', {'local':'本地执行', 'external':'外部执行'}.get(view.get('experiment_mode'), '未声明')),
            ('实验执行授权合同', '已配置' if settings.get('execution_authority_policy') else '未配置'),
            ('本次页面的执行审批入口', '可用' if self.approvals else '不可用')])
        if self.approvals:
            body += '<p><a href="/requests">核对实验执行请求</a></p>'
        body += ('</section><section class="configuration-section"><h2>交付与后续调整</h2>'
            + facts([('交付策略', '已配置' if settings.get('delivery_policy') else '未配置专用交付策略')])
            + '<p><a href="/final-results">查看研究结果与交付状态</a> · <a href="/guidance">提交方向建议</a></p>'
            '<p class="note">上方配置修改用于后续新节点，当前节点及原调用恢复继续沿用各自配置。</p></section>'
            '<details class="effective-details"><summary>查看当前配置详情与后端能力</summary><pre>'
            + text(json.dumps(view, ensure_ascii=False, indent=2)) + '</pre></details></details>')
        return (problem.status if problem else 200), self._page('当前研究配置', body, live=False), {}

    def _workspace_page(self, token):
        self._admin(token)
        body = ''
        if getattr(self, 'global_workspace', None) is not None:
            body += '<nav><a href="__WORKSPACE_HOME__">← 全部工作区</a></nav>'
        workspaces = ''
        if self.multi_workspace:
            workspaces += ('<details class="workspace-switcher"' + (' open' if len(self.workspace_roots) > 1 else '') + '><summary>其他工作区'
                + ('（' + str(len(self.workspace_roots) - 1) + ' 个）' if len(self.workspace_roots) > 1 else '') + '</summary>'
                '<p class="note">工作区只是分组保存研究的目录；通常使用默认工作区即可。默认工作区：' + html.escape(str(self.root)) + '</p><ul>')
            for index, root in enumerate(tuple(self.workspace_roots)[1:], 1):
                try:
                    child = self._workspace_child(index)
                    link = '/workspaces/' + str(index) + '/manage?token=' + child.management_token
                    workspaces += '<li><a href="' + html.escape(link, quote=True) + '">' + html.escape(str(root)) + '</a> · ' + str(len(child.instance_roots) - 1) + ' 个已登记实例</li>'
                except Exception as error:
                    workspaces += '<li>' + html.escape(str(root)) + ' · ' + html.escape(str(error)) + '</li>'
            workspaces += '</ul>' + self._form(token, 'workspace-open', '<label>工作区目录<input name="root" required placeholder="已有工作区的完整目录"></label>', '添加工作区') + '</details>'
        if self.catalog_errors:
            body += '<p class="note" role="alert">' + '<br>'.join(html.escape(item) for item in dict.fromkeys(self.catalog_errors)) + '</p>'
        body += ('<p class="setup-intro">准备一项新研究，或继续已有的工作。每个研究单独保存任务、预算、进度和结果。</p>'
            '<div class="cards"><section><h2>从一个研究问题开始</h2><p>写下任务，指定材料，再决定模型和用量上限。创建后，由你开始研究。</p>'
            '<a class="primary" href="/new">创建新研究 →</a></section>'
            '<section><h2>加载已有实例</h2><p>填写已有实例的完整目录，加载后即可进入管理。</p>')
        body += self._form(token, 'open', '<label>实例目录<input name="root" required placeholder="已有实例的完整目录"></label>', '加载实例')
        body += '</section></div><section><h2>已加载的实例</h2><p class="note">显式加载的实例目录保存在此工作区；重启后可继续访问。不会搜索其他目录。</p>'
        body += '<div id="live-instances">' + self._loaded_instances() + '</div>'
        body += '</section>' + workspaces + '<details><summary>工作台信息</summary><p class="note">工作台目录：' + html.escape(str(self.root)) + '</p></details>'
        return 200, self._page('研究工作台', body), {}

    def _new_research_page(self, token, *, draft=None, selection='', preview=None, notice='', frozen='', config_path='', save_path=''):
        self._admin(token)
        if draft is None:
            draft = configuration_form.blank(base=self.root)
        body = ('<nav class="setup-nav"><a href="/">← 研究工作台</a><a href="/instances">打开已有研究</a></nav>'
            '<p class="setup-intro">把问题交给研究助手之前，先明确任务、材料和用量。创建后，由你另行开始研究。</p>'
            '<div class="setup-flow" data-start-step="'+('3' if preview is not None else '1')+'">'
            '<ol class="setup-progress" aria-label="创建研究的步骤">'
            '<li data-setup-marker="1"><span>1</span>任务与材料</li>'
            '<li data-setup-marker="2"><span>2</span>模型与预算</li>'
            '<li data-setup-marker="3"><span>3</span>确认创建</li></ol>')
        if notice:
            body += ('<div class="setup-notice">' + notice.markup() + '</div>' if isinstance(notice, ui_errors.Problem)
                else '<p class="setup-notice" role="alert" tabindex="-1">' + html.escape(notice) + '</p>')
        selected = self.config_selections.get(token) if selection else None
        source = selected.get('source') if selected else None
        if source:
            body += '<p class="import-notice">已导入配置：<code>'+html.escape(str(source['path']))+'</code>。请逐步检查任务、模型和预算；所有高级参数均已保留。</p>'
        fields = '<input type="hidden" name="selection" value="' + html.escape(selection, quote=True) + '">'
        fields += '<p class="note">相对路径基准：<code>' + html.escape(str(draft['base'])) + '</code>；推荐填写绝对路径。导入 TOML 相对其所在目录。</p>'
        fields += configuration_form.render(draft)
        body += self._form(token, 'configure', fields, '检查配置，进入确认 →', mode='preview').replace(
            '<form ', '<form id="research-config-form" ', 1)
        if preview is not None:
            clean = public_output.cleaner(self.root, settings={})
            settings_view = public_output.settings_view(preview)
            permissions = settings_view.get('native_permissions', {})
            task_preview = clean(preview['objective'], limit=len(preview['objective']))
            meanings = {'read-only':'只读文件', 'workspace-write':'可写工作目录', 'danger-full-access':'不限制文件访问',
                'disabled':'禁用', 'cached':'仅缓存', 'live':'实时检索', 'unknown':'由启动器控制，框架无法确认', True:'允许', False:'不允许'}
            def meaning(value):
                return html.escape(str(meanings.get(value, value)))
            def path_summary(value):
                path = Path(value)
                short = '…/' + '/'.join(path.parts[-2:]) if len(path.parts) > 3 else str(path)
                return ('<span class="review-path">'+html.escape(short)+'</span>'
                    '<details class="review-full-path"><summary>查看完整绝对路径</summary><code>'+html.escape(str(path))+'</code></details>')
            body += ('<section class="setup-panel confirmation" data-setup-step="3" aria-labelledby="setup-confirm-title">'
                '<div class="section-heading"><span class="eyebrow">第三步 / 确认创建</span><h2 id="setup-confirm-title" tabindex="-1">检查这次研究的安排</h2>'
                '<p>配置已通过检查。确认后将创建研究目录，模型仍未开始工作。</p></div>'
                '<h3>研究任务</h3><div class="finding">'+html.escape(task_preview[:1800])+'</div>')
            if len(task_preview) > 1800:
                body += '<details><summary>展开完整研究任务</summary><div class="finding">'+html.escape(task_preview)+'</div></details>'
            research = draft['document'].get('research', {})
            root = Path(research.get('root', '')).expanduser()
            root = root if root.is_absolute() else Path(draft['base']) / root
            body += ('<dl class="review-summary">'
                '<div><dt>材料目录</dt><dd>'+path_summary(preview['public_root'])+'</dd></div>'
                '<div><dt>新研究保存到</dt><dd>'+path_summary(root.resolve())+'</dd></div>'
                '<div><dt>全局模型</dt><dd>'+html.escape(str(preview['model']))+'</dd></div>'
                '<div><dt>推理力度 / 服务档位</dt><dd>'+html.escape(str(preview.get('reasoning_effort') or '后端未指定'))+' / '+html.escape(str(preview.get('service_tier') or '后端未指定'))+'</dd></div>'
                '<div><dt>模型用量上限</dt><dd>'+('不限' if preview['max_tokens'] is None else html.escape(str(preview['max_tokens']))+' tokens')+'</dd></div>'
                '<div><dt>活动时间上限</dt><dd>'+('不限' if preview['max_seconds'] is None else html.escape(str(preview['max_seconds']))+' 秒')+'</dd></div>'
                '<div><dt>研究阶段数上限</dt><dd>'+html.escape(str(preview['max_steps']))+' 阶段</dd></div></dl>'
                '<h3>研究助手的实际权限</h3><p class="note">下面区分声明与后端生效值；实验执行仍受自身授权条件限制。</p>'
                '<table class="permission-review"><thead><tr><th scope="col">访问项目</th><th scope="col">配置声明</th><th scope="col">生效值</th></tr></thead><tbody>')
            for key, label in [('workspace','文件读写'),('network','程序联网'),('web','网页检索'),('subagents','子研究助手')]:
                body += '<tr><th scope="row">'+label+'</th><td>'+meaning(permissions.get('requested',{}).get(key,'未声明'))+'</td><td>'+meaning(permissions.get('effective',{}).get(key,'unknown'))+'</td></tr>'
            body += '</tbody></table>'
            if permissions.get('limitations'):
                limitations = {
                    'The launcher owns access controls; declarations do not establish isolation.': '访问控制由启动器负责；这里的声明不代表已建立隔离。',
                    'This retained instance has no frozen framework permission declaration.': '此已有实例没有冻结框架权限声明，实际权限需检查原生配置。',
                    'danger-full-access provides no filesystem or network sandbox.': '当前未限制文件访问，也没有程序联网隔离。',
                    'The requested offline setting is not enforced in danger-full-access mode.': '当前文件访问模式下，关闭程序联网的声明无法生效。',
                    'The workspace-write network option does not enable networking in read-only mode.': '只读模式不会因可写工作目录的联网选项而启用程序联网。',
                }
                body += '<p class="permission-limit">权限限制说明：</p><ul class="permission-limit">'+''.join('<li>'+html.escape(limitations.get(item,item))+'</li>' for item in permissions['limitations'])+'</ul>'
            advanced = []
            for label, predicate in [
                ('角色专用模型', bool(draft['document'].get('agent',{}).get('roles'))),
                ('原生后端设置', bool(draft['document'].get('agent',{}).get('native'))),
                ('实验执行配置', bool(draft['document'].get('executor'))),
                ('交付配置', bool(draft['document'].get('delivery'))),
                ('复核与恢复规则', bool(draft['document'].get('policies'))),
                ('材料补充或限制', bool(research.get('user_updates') or research.get('source_restrictions'))),
                ('分阶段或搜索用量', any(key not in {'max_tokens','max_seconds','max_steps'} for key in draft['document'].get('budget',{}))),
                ('网页管理设置', bool(draft['document'].get('ui'))),
            ]:
                if predicate: advanced.append(label)
            body += '<p class="advanced-review">专门设置：'+('、'.join(advanced) if advanced else '未另行声明，沿用统一配置')+'。可返回第二步展开高级设置核对。</p>'
            body += '<details class="effective-details"><summary>查看完整生效配置与后端能力</summary><pre>' + html.escape(json.dumps(settings_view, ensure_ascii=False, indent=2)) + '</pre></details>'
            body += '<div class="setup-actions"><button type="button" hidden class="secondary-button" data-setup-go="2">← 返回修改</button>'
            body += self._form(token, 'create', '<input type="hidden" name="selection" value="' + html.escape(selection,quote=True) + '">', '确认创建研究')+'</div>'
            body += '<p class="note">创建不会调用模型。进入实例后点击“开始研究”才会运行。</p></section>'
        body += ('<details class="setup-tools"' + (' open' if isinstance(notice, ui_errors.Problem) and (config_path or save_path or notice.field in {'config_path', 'save_path'}) else '') + '><summary>配置文件工具<span class="summary-note">可选 · 导入、导出或保存</span></summary>'
            '<div class="tools-grid"><div><h3>导入已有配置</h3><p class="note">从服务器上的 TOML 文件开始，导入后仍需检查并确认。</p>'
            + self._form(token, 'load-config', '<label>服务器上的配置文件路径<input name="config_path" required value="' + html.escape(config_path, quote=True) + '" placeholder="/srv/research/research.toml"></label>', '导入配置')
            + '</div><div><h3>保留当前配置</h3><p class="note">下载配置，或在服务器排他新建 TOML；已有文件请另存。都不会开始研究。</p>'
            '<button class="secondary-button" form="research-config-form" name="mode" value="export" type="submit">下载 TOML 配置</button>'
            '<label>保存到服务器的文件路径<input form="research-config-form" name="save_path" value="' + html.escape(save_path, quote=True) + '" placeholder="/srv/research/research.toml"></label>'
            '<button class="secondary-button" form="research-config-form" name="mode" value="save" type="submit">保存到服务器</button></div></div></details>'
            +frozen+'</div>')
        return (notice.status if isinstance(notice, ui_errors.Problem) else 200), self._page(
            notice.title if isinstance(notice, ui_errors.Problem) else '创建你的研究', body), {}

    def _save_toml(self, path, text):
        """Create a new declaration only; never resolve the final path component."""
        path = Path(path).expanduser().absolute()
        if path.suffix.lower() != '.toml' or path.resolve() == self.secret_file:
            raise ValueError('Select a research TOML declaration file')
        data = text.encode('utf-8')
        if len(data) > 256000:
            raise ValueError('Research TOML exceeds its byte budget')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        identity = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, 'wb') as destination:
                destination.write(data)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    path.unlink()
            except OSError:
                pass
            raise
        return path

    def _selected_config(self, token, selection):
        selected = self.config_selections.get(token)
        if not selected or not selection or not hmac.compare_digest(selection, selected['selection']):
            raise ValueError('请先检查并预览本次研究配置。')
        return selected

    def _remember_config(self, token, draft, *, source=None, previewed=False, task_source=None):
        selection = secrets.token_urlsafe(32)
        self.config_selections[token] = dict(selection=selection, draft=draft, source=source,
            previewed=previewed, task_source=task_source)
        return selection

    def _draft_task(self, draft):
        if draft['task_mode'] != 'file':
            return None
        objective = Path(draft['document'].get('research', {}).get('objective', '')).expanduser()
        objective = objective if objective.is_absolute() else Path(draft['base']) / objective
        if objective.resolve() == self.secret_file:
            raise ValueError('Select a research task file')
        return objective, c.regular(objective, limit=256000).read_bytes()

    def _import_config(self, token, path, *, notice=''):
        path = Path(path).expanduser().absolute()
        if path.suffix.lower() != '.toml' or path.resolve() == self.secret_file:
            raise ValueError('Select a research TOML declaration file')
        path.lstat()  # Preserve missing-file identity before regular()'s type check.
        source = c.regular(path, limit=256000).read_bytes()
        path = path.resolve()
        document = research_config.read_declaration(path)
        proof = dict(path=path, source=source)
        research = document.get('research', {})
        if isinstance(research, dict) and isinstance(research.get('objective'), str):
            objective = Path(research['objective']).expanduser()
            objective = objective if objective.is_absolute() else path.parent / objective
            if objective.resolve() == self.secret_file:
                raise ValueError('Select a research task file')
            proof.update(objective=objective, task_source=c.regular(objective, limit=256000).read_bytes())
        if c.regular(path, limit=256000).read_bytes() != source:
            raise ValueError('配置已变化，请重新加载研究配置。')
        draft = configuration_form.imported(document, base=path.parent)
        selection = self._remember_config(token, draft, source=proof)
        return self._new_research_page(token, draft=draft, selection=selection, notice=notice)

    @staticmethod
    def _config_changed(source):
        if source is None:
            return False
        return (c.regular(source['path'], limit=256000).read_bytes() != source['source'] or
            'objective' in source and c.regular(source['objective'], limit=256000).read_bytes() != source['task_source'])

    @staticmethod
    def _navigation(current=None):
        # Research navigation; workspace creation and instance selection stay at the workspace.
        items = (('/', '总览'), ('/results', '研究过程'), ('/final-results', '结果'), ('/settings', '设置'))
        return ('<nav class="top-nav"><a class="back-link" href="/instances">← 工作台</a>' + ''.join(
            '<a href="' + href + '"' + (' aria-current="page"' if href == current else '') + '>' + label + '</a>'
            for href, label in items) + '</nav>')

    def _settings_page(self, token):
        manager = 'maintenance' in self.browser.verify(token, instance_id=self.instance).scopes
        groups = [('研究设置', [('/guidance', '方向建议', '补充关注点、比较选项或明确目标变化；用于尚未绑定的适用阶段。')])]
        if manager:
            groups[0][1].insert(0, ('/configuration', '当前研究配置', '统一调整模型、角色、权限、预算、实验执行与交付；从下一个新节点生效。'))
            groups.append(('阅读设置', [('/reading', '阅读与翻译', '选择阅读语言与翻译方式；在工作台内跨实例共享。')]))
            records = [('/operations', '控制请求记录', '核对启动、暂停和预算调整的处理状态。')]
            if self.approvals:
                records.append(('/requests', '实验执行请求', '逐项核对与决定已有实验执行请求。'))
            groups.append(('请求与记录', records))
        body = self._navigation('/settings') + '<p class="settings-intro">研究配置、方向建议与阅读偏好各在一个入口维护。</p>'
        if not manager:
            body += '<p class="note">当前为只读查看；方向建议可查看，提交建议和管理配置需要管理会话。</p>'
        body += '<div class="settings-groups">' + ''.join('<section class="settings-group"><h2>' + title
            + '</h2><ul class="settings-list">' + ''.join('<li>' + ui.link(label, href) + '<p class="note">' + note
                + '</p></li>' for href, label, note in rows) + '</ul></section>' for title, rows in groups) + '</div>'
        return 200, self._page('设置', body, live=False), {}

    def _guidance_page(self, token, *, draft=None, problem=None):
        manager = 'maintenance' in self.browser.verify(token, instance_id=self.instance).scopes
        body = (self._navigation('/settings') + '<p class="settings-intro">告诉研究者接下来优先检查什么、比较哪些方案，或明确提出目标变化。</p>'
            '<p>当前运行或已经准备的阶段保持原样。建议会交给下一次生成的、匹配所选角色的任务；恢复原任务时仍沿用原建议。研究者会判断建议的科学合理性，建议本身不授予实验执行权限。</p>')
        if problem:
            body += problem.markup()
        if manager:
            body += '<section class="guidance-create"><h2>添加方向建议</h2>' + self._form(token, 'guidance-submit', ui_guidance.fields(draft), '提交建议') + '</section>'
        else:
            body += '<p class="note">当前为只读查看，提交或修改建议需要管理会话。</p>'
        rows = user_guidance.list_guidance(self.root)
        body += '<section class="guidance-history"><h2>已提交的建议</h2>'
        if not rows:
            body += '<p class="note">尚无方向建议。研究会继续按原任务推进。</p>'
        clean = public_output.cleaner(self.root)
        statuses = {'pending': '等待适用阶段', 'applied': '已交给研究者', 'revoked': '已撤回'}
        for row in reversed(rows):
            body += '<article class="guidance-record"><h3>' + ui.esc(statuses.get(row['status'], row['status'])) + '</h3>' + render.excerpt(clean(row['text']), 4000)
            body += '<dl class="guidance-metadata">' + ''.join('<div><dt>' + label + '</dt><dd>' + ui.esc(value) + '</dd></div>' for label, value in [
                ('交给谁', dict(ui_guidance.TARGETS).get(row['target'], row['target'])),
                ('类型', dict(ui_guidance.KINDS).get(row['kind'], row['kind'])),
                ('范围', dict(ui_guidance.SCOPES).get(row['scope'], row['scope'])),
                ('最早生效阶段', str(row.get('effective_step') or '下一适用阶段')),
                ('首次交付阶段', '第 ' + str(row['applied_step']) + ' 阶段' if row.get('applied_step') else '尚未交付')]) + '</dl>'
            if row['status'] == 'applied':
                body += '<p class="note">已交付仅表示建议进入了研究任务，不表示科学结论已采纳。</p>'
            if row.get('future_revocation'):
                body += '<p class="note">已撤回后续建议：从第 ' + ui.esc(str(row['future_revocation']['effective_step'])) + ' 阶段起不再用于新任务；已经绑定的任务保留原建议。</p>'
            hidden = ('<input type="hidden" name="guidance_id" value="' + ui.esc(row['guidance_id']) + '">'
                '<input type="hidden" name="expected_revision" value="' + str(row['revision']) + '">')
            if manager and row['status'] == 'pending':
                body += '<details><summary>修改待交付的文字</summary>' + self._form(token, 'guidance-edit', hidden + '<label>建议内容<textarea name="text" required>' + html.escape(row['text']) + '</textarea></label>', '保存修改') + '</details>'
            if manager and (row['status'] == 'pending' or row['status'] == 'applied' and row.get('scope') == 'research' and not row.get('future_revocation')):
                body += self._form(token, 'guidance-revoke', hidden, '撤回后续建议')
            body += '</article>'
        body += '</section>' + ('<script src="/assets/guidance.js"></script>' if manager else '')
        return (problem.status if problem else 200), self._page('方向建议', body, live=False), {}

    def _operations_page(self, token):
        self._admin(token)
        body = self._navigation() + '<p>启动、暂停和预算调整的请求处理情况。处理结束不代表科学目标已完成。</p>'
        body += '<div id="live-operations">' + self._operation_rows(self._snapshot()['operations']) + '</div>'
        return 200, self._page('控制请求记录', body), {}

    def _operation_rows(self, rows):
        labels = {'accepted':'请求已接受', 'running':'控制请求正在处理', 'completed':'控制请求已结束', 'failed':'处理失败', 'applied':'已应用'}
        body = ''
        for row in reversed(rows):
            result = row.get('result') or {}
            stopped = result.get('status') in {'native_stopped', 'native_outcome_unsealed', 'seal_pending',
                'output_repair_required', 'output_repair_unavailable'}
            label = ('研究推进已停止，原任务待核对' if stopped else '控制请求已结束；请核对研究状态') \
                if row['kind'] == 'controller' and row['status'] == 'applied' else labels.get(row['status'], '请求已记录')
            body += '<section><h2>' + label + '</h2><p>' + ('预算调整' if row['kind'] in {'budget', 'budget_amend'} else '研究控制') + '</p>'
            operation_id = row.get('operation_id')
            if isinstance(operation_id, str) and len(operation_id) == 32 and all(char in '0123456789abcdef' for char in operation_id):
                body += '<p class="note">请求编号：<code>' + operation_id + '</code></p>'
                if stopped or row.get('error'):
                    body += '<p class="note">本机原回执（相对实例目录）：<code>host-control/operations/' + operation_id + '.json</code></p>'
            if stopped:
                body += '<p>控制停止代码：<code>' + ui.esc(result['status']) + '</code></p>'
                if type(result.get('attempt_step')) is int and result['attempt_step'] > 0:
                    body += '<p>原任务阶段：' + str(result['attempt_step']) + '</p>'
                body += '<p><a href="/">返回总览查看原任务原因与可用处理操作</a></p>'
            if row.get('error'):
                body += self._problem(ui_errors.ControlFailure('Saved operation failed'), context='control').markup()
            body += '</section>'
        return body or '<p>还没有控制请求。</p>'

    @staticmethod
    def _event_html(item):
        kind = item.get('type')
        text = item.get('text')
        if kind not in {'message', 'thinking'} or not isinstance(text, str) or not text.strip():
            return ''
        body = '<p>' + html.escape(text) + '</p>'
        if item.get('omitted_characters') or item.get('text_truncated') is True:
            body += '<small>本条公开文本已截短</small>'
        if kind == 'thinking':
            body = '<details><summary>思考摘要</summary>' + body + '</details>'
        return '<article class="event">' + body + '</article>'

    def _latest_step(self):
        return max(saved_status.visible_steps(self.root), default=0)

    @staticmethod
    def _ordinary_assessment(job):
        from . import ui_semantics
        return ui_semantics.terminal_finish(job)

    @staticmethod
    def _selected_result(saved, latest, conclusion, *, terminal=False):
        """Use the active saved source, or the latest ordinary terminal finish."""
        closeout = saved.get('closeout') or {}
        if saved.get('result_step') is not None:
            if (terminal and closeout.get('authorization_step') is not None
                    and closeout.get('status') != 'delivery_confirmed'):
                return None
            return saved['result_step']
        if closeout.get('mode') in {'model', 'final'}:
            return None
        return (latest if conclusion and conclusion['step'] == latest
            and conclusion['ordinary']
            and conclusion['payload'].get('next_action') == 'finish' else None)

    def _result_source(self):
        saved = saved_status.result(self.root)
        latest = saved['latest_step']
        conclusion = None
        if (saved['result_step'] is None
                and (saved['closeout'] or {}).get('mode') not in {'model', 'final'} and latest and (
                self.root / 'native-evidence' / f'{latest:03d}' / 'result-identity.json').exists()):
            node = display.node(self.root, latest)
            conclusion = dict(step=latest, role=node['job']['role'],
                ordinary=self._ordinary_assessment(node['job']), payload=node['payload'])
        saved['result_step'] = self._selected_result(saved, latest, conclusion)
        return saved

    def _final_step(self):
        return self._result_source()['result_step']

    def _progress_value(self, step, attempt, cursor, *, tail=False):
        observed_epoch = time.monotonic_ns()
        value = progress.read(self.root, step, attempt=attempt, cursor=cursor, tail=tail)
        state = value.get('state') if value.get('observation_state') != 'unavailable' else None
        lifecycle = {
            'unstarted': '阶段已准备，尚未开始', 'intent_present': '已记录任务意图，运行状态待确认',
            'running': '正在运行', 'unknown': '运行状态未确认；请核对原任务，勿重复提交',
            'native_outcome_present': '本次助手工作已结束，阶段结论尚未记录',
            'seal_present_unverified': '已有阶段结论；请查看阶段结论原文',
        }.get(state, '运行状态暂不可确认；请核对原任务，勿重复提交')
        stream = {'ready': '公开日志已连接', 'partial_line': '正在接收公开消息',
            'missing': '等待助手输出' if state == 'running' else '暂无公开日志',
            'unavailable': '公开进度暂不可用', 'replaced': '日志来源已变化',
            'truncated': '日志已截短'}.get(value['stream_state'], '公开日志状态待确认')
        value['progress_label'] = lifecycle + '；' + stream
        omissions = []
        if value.get('tail_omitted'):
            omissions.append('仅显示最近一批公开日志；更早消息可从固定阶段进度页读取')
        if value.get('omitted_oversize_lines'):
            omissions.append('已跳过 ' + str(value['omitted_oversize_lines']) + ' 条超长日志')
        if value.get('malformed_lines'):
            omissions.append('已跳过 ' + str(value['malformed_lines']) + ' 条不可解析日志')
        if value.get('has_more'):
            omissions.append('仍有公开日志，正在分批读取')
        value['omission_label'] = '；'.join(omissions)
        if value['omission_label']:
            value['progress_label'] += '；' + value['omission_label']
        context = dict(value.get('context') or {}, step=step, attempt=attempt)
        value['context_display'] = self._context_display_value(context, observed_epoch)
        value['token_details'] = ui_view.token_details(value.get('usage') or {}, context,
            context_display=value['context_display'])
        return value

    def _context_display_value(self, context, observed_epoch, *, producer_scope=False):
        """Retain UI-received facts only; unavailable current facts stay unavailable."""
        from . import native_usage
        sample = native_usage.context(context)
        sample = {key: value for key, value in sample.items() if key not in {'thread_id', 'turn_id'}}
        current = sample['status'] == 'last_reported'
        result = dict(status='current' if current else 'unavailable')
        step, attempt = context.get('step'), context.get('attempt')
        if type(step) is not int or step < 1 or type(attempt) is not int or attempt < 0:
            return result
        scope = (str(self.root), step, attempt)
        sample.update(step=step, attempt=attempt)
        with self.cache_lock:
            previous = self.context_display
            # A historical progress reader cannot evict the active attempt.
            if previous is not None and scope < previous['scope']:
                return result
            if previous is None or scope != previous['scope']:
                # Fixed progress URLs accept caller-selected attempts, including
                # attempts that do not exist. Only a valid sample or an exact
                # scope already obtained by the overview producer may advance
                # the retained display scope.
                if not current and not producer_scope:
                    return result
                previous = dict(scope=scope, epoch=-1, sample=None, last_updated_at=None)
            retained = previous['sample']
            ordered = observed_epoch >= previous['epoch']
            stamp = ui_view.context_timestamp(sample) if current else None
            if current and retained is not None:
                # Older persisted samples must not supersede a newer observed
                # sample even when their HTTP read happened to finish later or
                # an intervening numeric sample had no valid original time.
                prior_stamp = previous['last_updated_at']
                ordered = ordered and not (stamp is not None and prior_stamp is not None and stamp < prior_stamp)
            if ordered:
                previous['epoch'] = observed_epoch
                if current:
                    previous['sample'] = sample
                    if stamp is not None:
                        previous['last_updated_at'] = stamp
                self.context_display = previous
            retained = previous['sample']
            if retained is not None and (not current or not ordered):
                return dict(status='historical', current_status='unavailable' if not current else 'out_of_order',
                    sample={key: value for key, value in retained.items() if key != 'current_window'},
                    last_updated_at=previous['last_updated_at'])
            if current:
                result['last_updated_at'] = previous['last_updated_at']
        return result

    def _progress_page(self, step, attempt, cursor):
        value = self._progress_value(step, attempt, cursor)
        base = '/nodes/' + str(step) + '/progress?attempt=' + str(attempt)
        body = self._navigation() + '<p>第 ' + str(step) + ' 阶段 · ' + self._role(value['role']) + '</p>'
        body += '<p class="note">这里显示助手公开的工作文本和工具状态；阶段完成后请查看“阶段结论”。</p>'
        body += '<p><a href="' + base + '">重新读取公开进度</a> · <a href="/nodes/' + str(step) + '">查看此阶段</a></p>'
        body += '<section data-progress="/nodes/' + str(step) + '/events?attempt=' + str(attempt) + '" data-cursor="' + html.escape(value.get('cursor') or '', quote=True) + '"><h2>助手公开工作记录</h2><p data-stream-status>' + html.escape(value['progress_label']) + '</p><div data-events>'
        body += ''.join(self._event_html(item) for item in value['events'][-200:]) + '</div>'
        body += '<p class="note" data-progress-omissions data-omitted="' + str(max(0, len(value['events']) - 200)) + '">' + ('已保留最近 200 条消息；' + str(len(value['events']) - 200) + ' 条较早消息未展开。' if len(value['events']) > 200 else '') + '</p>'
        if not value['events']:
            body += '<p class="note">此阶段没有可显示的公开消息。</p>'
        body += '<details><summary>高级：公开事件详情</summary><pre data-raw-events>' + html.escape(json.dumps(value['events'][-200:], ensure_ascii=False, indent=2)) + '</pre></details></section>'
        if value.get('cursor') and not value.get('restart_required'):
            body += '<a href="' + base + '&amp;cursor=' + quote(value['cursor'], safe='') + '">手动读取下一批消息</a>'
        if value.get('restart_required'):
            body += '<p>日志来源已变化，请点击上方“重新读取公开进度”；系统不会重新提交研究任务。</p>'
        body += ui.token_monitor(value['token_details'], scope=f'第 {step} 阶段 · attempt {attempt} · 本次助手用量', live=True)
        return 200, self._page('实时进度', body, live=True), {}

    def _following_progress_page(self):
        body = self._navigation() + '<p>这里自动跟随当前阶段；打开各阶段的公开进度链接可固定查看历史记录。</p>'
        body += '<section data-progress="" data-follow-latest="true" data-cursor=""><h2 data-progress-heading>当前助手的公开工作</h2><p data-stream-status>正在读取当前阶段状态…</p><div data-events></div>'
        body += '<details><summary>高级：公开事件详情</summary><pre data-raw-events>[]</pre></details></section>'
        body += ui.token_monitor(scope='当前阶段 · 正在读取助手用量…', live=True)
        return 200, self._page('实时进度', body, live=True), {}

    @staticmethod
    def _role(role):
        return html.escape({'H': 'H · 假设', 'W': 'W · 实验', 'A': 'A · 综合评估', 'E': 'E · 证据'}.get(role, role))

    @staticmethod
    def _state(state):
        return {'prepared': '等待开始', 'running': '正在运行', 'waiting': '等待外部工作',
            'seal_present_unverified': '已有阶段结论', 'native_outcome_unsealed': '工作已结束，结论尚未记录',
            'native_not_submitted': '尚未提交', 'unknown': '原任务状态待核对'}.get(state, '状态待核对')

    @staticmethod
    def _node_view(node):
        value = {key: str(node[key])[:300] if isinstance(node[key], str) else node[key]
                for key in ('step', 'role', 'state', 'stop_reason', 'returncode', 'continuations') if key in node}
        if isinstance(node.get('stop_reason'), str):
            value['stop_reason_omitted_characters'] = max(0, len(node['stop_reason']) - 300)
        from .native_usage import transport_diagnostic
        diagnostic=transport_diagnostic(node.get('technical_diagnostic'))
        if diagnostic is not None:value['technical_diagnostic']=diagnostic
        return value

    def _clear_display_caches(self):
        """Clear shared display transactions without acquiring snapshot locks."""
        with self.cache_lock:
            for memo in (self.view_memo, self.status_memo, self.candidate_cache, self.history_pages):
                memo.clear()

    def _snapshot(self, *, force=False):
        def load():
            with self.cache_lock:
                observed_epoch = time.monotonic_ns()
                latest = self._latest_step()
                view = saved_status.status(self.root, after=max(0, latest - ui_view.WINDOW), limit=ui_view.WINDOW, memo=self.status_memo)
                context = (view.get('usage') or {}).get('context') or {}
                context_display = self._context_display_value(context, observed_epoch, producer_scope=True)
                conclusion = None
                for node in reversed(view['nodes']):
                    if node['state'] == 'seal_present_unverified':
                        sealed = display.node(self.root, node['step'])
                        job, payload = sealed['job'], sealed['payload']
                        conclusion = dict(step=node['step'], role=node['role'],
                            ordinary=self._ordinary_assessment(job), payload=payload)
                        break
                operations = saved_status.operations(self.root, memo=self.status_memo)
                final = self._selected_result(view, latest, conclusion, terminal=True)
                # One read-only projection per snapshot; components fail separately.
                projection = self._guarded(ui_view.project, self.root, view, final_step=final, memo=self.view_memo,
                                             clean=ui_view.cleaner(self.root))
                return dict(view=view, conclusion=conclusion, operation=operations[-1] if operations else None, operations=operations[-20:],
                            final=final, ui=projection, context_display=context_display)
        return self.live_cache.read(self.root, load, force=force)

    def _receipt(self, snapshot):
        receipt = snapshot['operation'] or self.control_result or {}
        operation_id = receipt.get('operation_id')
        if operation_id and (self.root / 'host-control/operations' / (operation_id + '.json')).is_file():
            receipt = c.read(self.root / 'host-control/operations' / (operation_id + '.json'))
        return receipt

    def _summary_snapshot(self, *, force=False):
        """Loaded-instance cards need saved status, not every scientific trail."""
        def load():
            with self.cache_lock:
                latest = self._latest_step()
                view = saved_status.status(self.root, after=max(0, latest - 1), limit=1, memo=self.status_memo)
                conclusion = None
                if view['nodes'] and view['nodes'][-1]['state'] == 'seal_present_unverified':
                    node = display.node(self.root, latest)
                    job = node['job']
                    conclusion = dict(step=latest, role=job['role'],
                        ordinary=self._ordinary_assessment(job), payload=node['payload'])
                operations = saved_status.operations(self.root, memo=self.status_memo)
                return dict(view=view, conclusion=conclusion, final=self._selected_result(view, latest, conclusion, terminal=True),
                    operation=operations[-1] if operations else None, operations=operations[-20:],
                    ui=dict(header=ui_view.header(self.root, view['objective']),
                        used_steps=view.get('used_steps', (view.get('closeout') or {}).get('used_steps', latest))))
        return self.summary_cache.read(self.root, load, force=force)

    def _loaded_instances(self, *, force=False):
        rows = []
        for index, root in enumerate(tuple(self.instance_roots)):
            if root in self.deleted_roots or not index and self.workspace:
                continue
            heading = '实例 ' + str(index) + ' · ' + root.name
            try:
                child = self._child(index) if index else self
                snapshot = child._summary_snapshot(force=force)
                view = snapshot['view']
                status = child._status(snapshot, child._receipt(snapshot))
                header = (snapshot.get('ui') or {}).get('header') or {}
                tokens = ui_view.usage_stats(view)['tokens']
                target = '/instances/' + str(index) + '/manage?token=' + child.management_token if index else '/'
                rows.append(ui.instance_card(dict(
                    heading=heading, title=header.get('title') or root.name, path=str(root), href=target,
                    state=status['state'], badge=status['badge'], headline=status['headline'], stage=status.get('stage'),
                    tokens=dict(percent=tokens['percent'], over=tokens['over'],
                                text=tokens['value'] + ' / ' + tokens['limit'] + ' tokens'
                                     + (' · ' + str(tokens['percent']) + '%' if tokens['percent'] is not None else '')))))
            except Exception as error:  # noqa: BLE001 - one unreadable instance never hides the others
                # Show the exact failure class and its path-cleaned message.
                rows.append(ui.instance_card(dict(heading=heading, path=str(root), retry='/instances',
                    error=self._failure(error)['error'])))
        if not rows:
            return '<p>还没有加载实例。你可以新建一个，或填写目录加载已有研究。</p>'
        return '<div class="instance-cards">' + ''.join(rows) + '</div>'

    def _presentation(self, snapshot, receipt):
        view = snapshot['view']
        current = view['nodes'][-1] if view['nodes'] else None
        state = current['state'] if current else 'ready'
        conclusion = snapshot['conclusion']
        waiting = bool(current and conclusion and conclusion['step'] == current['step']
                       and conclusion['payload'].get('next_action') == 'wait' and current.get('kind')!='consultation')
        completed = snapshot['final'] is not None or (view.get('closeout') or {}).get('status') in {'final_a_completed', 'delivery_confirmed', 'exported_unsubmitted'}
        # A durable sealed/terminal node outranks a transient accepted receipt.
        active = state in {'running', 'automatic_recovery_waiting'} or (state in {'ready', 'prepared'} and receipt.get('status') in {'accepted', 'running'})
        title = ('立即中断已请求：等待原任务清理' if current and current.get('interrupt_requested') else
            '最终结论已返回' if completed else '等待补充条件' if waiting else
            '暂停已请求：本阶段结束后停止' if view['paused'] and active else '研究已暂停' if view['paused'] else
            '研究正在进行' if active else '可以开始研究' if not current else self._state(state))
        return dict(current=current, state=state, waiting=waiting, completed=completed, active=active, title=title)

    def _status(self, snapshot, receipt, *, elapsed=None):
        """StatusSummary over the existing presentation; no new control semantics."""
        view = snapshot['view']
        display = self._presentation(snapshot, receipt)
        projection = snapshot.get('ui') or {}
        trail = projection.get('trail') or {}
        rounds = trail.get('rounds') or []
        clean = ui_view.cleaner(self.root)
        closeout = view.get('closeout')
        waiting_reason = None
        if display['waiting']:
            payload = snapshot['conclusion']['payload']
            sentence = ui_view.first_sentence(payload.get('route_reason') or payload.get('remaining_question') or payload.get('summary'))
            waiting_reason = clean(sentence, 300) if sentence else None
        completed_line = None
        latest = projection.get('latest_a')
        if display['completed'] and latest:
            number = next((item['index'] for item in rounds if latest in item['a']), None)
            prefix = ('第 ' + str(number) + ' 轮 · ') if number else ''
            action = latest.get('next_action')
            if action == 'deliver':
                label = (projection.get('labels') or {}).get(projection.get('recommended'))
                completed_line = prefix + 'A 决定交付 ' + (clean(label, 120) if label else '（候选未能对应已观察记录）')
            elif action:
                completed_line = prefix + 'A 决定' + ui_view.A_OUTCOMES.get(action, action)
        used = projection.get('used_steps')
        status = ui_view.status_summary(display, view,
            final_phase=closeout is not None and not display['completed'],
            exhausted=ui_view.exhausted_limits(view, used if isinstance(used, int) else None),
            waiting_reason=waiting_reason, current_round=rounds[0]['index'] if rounds else None,
            completed_line=completed_line, elapsed=elapsed, clean=clean,
            monitoring=self._monitoring(receipt))
        technical=self._technical_stop(snapshot,receipt)
        if technical is not None and not display['active']:
            failed=technical.get('phase')=='native_failure'
            status.update(state='technical_pause',badge='原生调用失败' if failed else '技术暂停',
                headline=('原生调用失败，自动恢复已停止；请修复服务条件后核对同一原任务。' if failed else
                    '原任务在模型启动前停止，尚未进入科学评审；请修复本机配置后核对同一原任务。'))
            display['active']=False
        return dict(status, display=display)

    @staticmethod
    def _monitoring(receipt):
        """A live controller request owns waiting; a finished one watches nothing."""
        return receipt.get('kind') == 'controller' and receipt.get('status') in {'accepted', 'running'}

    @staticmethod
    def _technical_stop(snapshot,receipt=None):
        nodes=snapshot.get('view',{}).get('nodes') or []
        current=nodes[-1] if nodes else None
        if current is None or current.get('state') in ui_view.SEALED or current.get('state') in {'running','automatic_recovery_waiting'}:return None
        safe=current.get('technical_stop')
        if not isinstance(safe,dict) or safe.get('phase') not in {'native_prelaunch','native_failure'}:return None
        failed=safe.get('phase')=='native_failure'
        reference=safe.get('native_intent_ref')
        # This is a request to the existing owner, not cached execution admission.
        # Only the owner's typed, current original binding can supply its fields.
        source_known=(failed and current.get('state') in {'automatic_recovery_stopped','automatic_recovery_paused','native_outcome_unsealed'}
            and type(safe.get('pending_step')) is int and safe['pending_step']==current.get('step')
            and type(safe.get('expected_attempt')) is int and safe['expected_attempt']>=0
            and safe['expected_attempt']==current.get('continuations',0)
            and safe.get('pending_role')==current.get('role') and current.get('role') in {'H','W','A','E'}
            and safe.get('repair_scope')=='worker_attempt' and safe.get('after_interrupt') is True
            and safe.get('new_context') is False and isinstance(reference,dict)
            and isinstance(reference.get('path'),str) and bool(reference['path']))
        blocked=dict(safe,recovery_available=False,supported_actions=[],show_recheck_form=False)
        if failed and not source_known:return blocked
        candidates=[receipt or snapshot.get('operation') or {}]
        if failed:
            candidates.append(snapshot.get('operation') or {})
            candidates.extend(reversed(snapshot.get('operations') or []))
        identity=('phase','pending_step','expected_attempt','reason')
        if failed:identity+=('pending_role','repair_scope','native_intent_ref','after_interrupt','new_context')
        for candidate in candidates:
            reported=(candidate.get('result') or {}).get('technical_stop')
            if not isinstance(reported,dict):continue
            if (failed and reported.get('phase')=='native_failure'
                    and all(reported.get(key)==safe.get(key) for key in ('pending_step','expected_attempt'))
                    and reported.get('native_intent_ref')!=reference):
                return blocked
            if (all(reported.get(key)==safe.get(key) for key in identity)
                    and reported.get('recovery_available') is True
                    and reported.get('supported_actions')==['worker-resume']):
                return reported
        return dict(blocked,show_recheck_form=bool(source_known))

    def _activity(self, current):
        """Latest public messages of the active stage via a bounded forward cursor."""
        step, attempt = current['step'], current.get('continuations') or 0
        key = (step, attempt)
        with self.activity_lock:
            entry = self.activity.get(key) or dict(cursor=None, messages=[], prepared_at=None, label='')
            for _ in range(1):  # One bounded window; cold views start at the tail.
                value = self._progress_value(step, attempt, entry['cursor'], tail=entry['cursor'] is None)
                if value.get('restart_required'):
                    entry = dict(cursor=None, messages=[], prepared_at=None, label='')
                    break
                if value.get('tail_omitted'):
                    entry['tail_omitted'] = True
                entry['label'] = value['progress_label'] + ('；仅显示最近日志窗口，较早或不完整首行已略去' if entry.get('tail_omitted') else '')
                entry['prepared_at'] = value.get('prepared_at', entry['prepared_at'])
                texts = [item['text'][:300] for item in value['events'] if item.get('type') == 'message' and item.get('text')]
                entry['messages'] = (entry['messages'] + texts)[-3:]
                entry['cursor'] = value.get('cursor') or entry['cursor']
                if not value.get('has_more'):
                    break
            self.activity = {key: entry}
        return dict(step=step, role_label=current.get('role_label') or ui_view.ROLE_LABELS.get(current['role'], current['role']),
                    elapsed=ui_view.elapsed_text(entry['prepared_at']), label=entry['label'], messages=list(entry['messages']),
                    href='/nodes/' + str(step) + '/progress?attempt=' + str(attempt))

    def _failure_fork_exit(self, step):
        """Show the existing maintenance handoff before offering a fork preview."""
        href = '/nodes/' + str(step) + '/fork'
        if self._maintenance_blocked():
            return ui.Safe('<p>在工作台打开本实例以预览分支：独立实例网页服务正在持有维护锁，'
                '请先关闭该服务，再从工作台按完整目录加载本实例。</p><p>'
                + ui.link('查看工作台分支操作说明', href) + '</p>')
        return ui.Safe('<p>' + ui.link('放弃本次尝试并分支重做', href, cls='secondary-button') + '</p>'
            '<p>先预览分支：保留第 ' + str(step) + ' 阶段开始前的已完成成果，'
            '用该阶段原始输入重做当前任务；不会重跑前一阶段。新分支须声明新预算，创建后保持暂停。'
            '预览仍会核对在途或未知外部调用；原尝试不删除，已用预算不退还。</p>')

    def _attention(self, token, manager, snapshot, status):
        view = snapshot['view']
        display = status['display']
        items = []
        current=display.get('current')
        diagnostic=(ui_errors.native_stop_problem(current.get('technical_diagnostic'),
            href='/nodes/'+str(current['step']))
            if current and current.get('state') not in ui_view.SEALED else None)
        if diagnostic is not None:
            items.append(dict(text=diagnostic.cause,href=diagnostic.href,action='核对同一原调用',
                form=ui.Safe('<details open><summary>技术停止原因与下一步</summary><p>'+
                    ui.esc(diagnostic.hint)+'</p></details>')))
        technical=self._technical_stop(snapshot)
        if technical is not None:
            failed=technical.get('phase')=='native_failure'
            items.append(dict(text=('原生调用失败，自动恢复已停止。请核对并修复服务条件后，由控制层重查同一原调用的恢复条件；原任务身份、此前成果和已用预算保留。' if failed else
                '当前为启动前技术暂停，未产生科学评审结论。先修复本机原生后端或必需控制观测配置；原任务身份、此前成果和已用预算保留。'),
                href='/nodes/'+str(technical['pending_step']),action='查看原任务与恢复条件' if failed else '查看原任务与启动条件'))
        if status['state'] == 'waiting':
            current = display['current']
            item = dict(text='A 记录了等待条件：' + (status['headline'].removeprefix('A 等待：')) + ' 请核对原因、补充材料或方向建议后再处理。',
                        href='/results/' + str(current['step']), action='查看等待原因与阶段结论')
            if manager and current['role'] == 'A' and snapshot['conclusion']['ordinary'] and not display['completed']:
                item['form'] = ui.Safe('<p class="note">确认后继续研究，将由综合评估助手重新判断下一步；原结论与已用预算保留。</p>'
                    + self._control_form(token, 'continue', correction_trigger=(view['correction'].get('current_pause') or {}).get('trigger_id')))
            items.append(item)
        if status['state'] == 'external_wait' and not (display['current'] or {}).get('native_failure'):
            current = display['current']
            receipt = self._receipt(snapshot)
            monitoring = self._monitoring(receipt)
            stop_reason = ((receipt.get('result') or {}).get('reason')
                if (receipt.get('result') or {}).get('status') == 'waiting_execution' else None)
            items.append(dict(text=status['headline'] + '。原调用不会被重新提交；唤醒只核对同一原调用的结果。',
                href='/nodes/' + str(current['step']), action='查看本阶段原任务',
                form=ui.Safe('<details class="execution-wait" data-details-key="overview-execution-wait" open>'
                    '<summary>等待中的外部实验</summary>' + str(ui.facts(ui_view.execution_wait_facts(
                        current.get('execution_wait') or {}, monitoring=monitoring, stop_reason=stop_reason)))
                    + '</details>')))
        elif status['state'] in {'external_wait', 'unsealed', 'not_submitted'} or (
                display['current'] and display['current'].get('native_failure')
                and display['current']['state'] not in ui_view.SEALED):
            current = display['current']
            failure_help = ui_view.native_failure_guidance(current)
            item = dict(text=(failure_help['summary'] + '。请按下方建议处理；原件和已用预算保留。'
                if failure_help else status['headline'] + '；请核对原任务，不会自动重新提交。'),
                href='/nodes/' + str(current['step']), action='查看原任务与续接条件')
            details = ui.facts(ui_view.native_facts(current, ui_view.cleaner(self.root)))
            if manager:
                attempt = current.get('continuations') or 0
                record = 'native-evidence/' + f'{current["step"]:03d}'
                if attempt:
                    record += '/continuations/' + f'{attempt:03d}'
                details += '<p class="note">本机原生诊断（相对实例目录）：<code>' + record + '/native-outcome.json</code></p>'
                if failure_help:
                    details += '<p class="note">需要详细原因时，请维护者在宿主机查看该次诊断；公开页面仅显示安全错误类别和已知错误码。</p>'
                    if view.get('paused'):
                        details += '<p>研究已暂停；当前失败尝试及此前成果均已保留。</p>'
                    elif status['state'] not in {'service_error','active','service_wait','pause_requested'}:
                        details += self._control_form(token, 'pause')
                    if (not display['active'] and not display['waiting']
                            and ui_view.native_failure_fork_available(current)):
                        details += self._failure_fork_exit(current['step'])
                elif current.get('native_failure') and (current['native_failure'].get('category') == 'unknown'):
                    details += '<p>错误原因尚未识别，请维护者在宿主机核对本次诊断和原调用状态；不要盲目重试。</p>'
                if ui_view.native_completed(current):
                    command = 'scid-research worker-resume --root ' + shlex.quote(str(self.root)) + ' --step ' + str(current['step'])
                    details += ('<p>使用上方“收集封存并继续研究”处理同一原任务；需要补填输出时仍按原 Worker 修复条件与预算执行。</p>'
                        '<p class="note">同一操作的命令：<code>' + ui.esc(command) + '</code></p>')
            item['form'] = ui.Safe(details)
            items.append(item)
        if status['state'] == 'unknown':
            items.append(dict(text='上一次任务的结果还不确定。请核对原任务，勿重复提交。', href='/progress', action='查看原任务进度'))
        if (view.get('closeout') or {}).get('status') == 'delivery_unknown':
            items.append(dict(text='交付结果待核对：请按原交付调用核对，勿重复提交。', href='/final-results', action='查看结果'))
        exhausted = ui_view.exhausted_limits(view, (snapshot.get('ui') or {}).get('used_steps'))
        if exhausted and not display['completed'] and not display['active']:
            items.append(dict(text='预算已用完：' + '、'.join(exhausted) + '。' + (
                '打开恢复页面，填写新上限与调整说明。预算生效后返回总览继续研究。' if manager else '调整预算需使用本地管理入口。'),
                **(dict(href='/budget-recovery#budget', action='恢复前追加预算') if manager else {})))
        if self.approvals:
            pending = self._guarded(self._pending_requests)
            if 'error' in pending:
                items.append(dict(text='实验执行请求暂不可读：' + pending['error']['kind'] + '：' + pending['error']['message'],
                                  href='/requests', action='查看实验执行请求'))
            elif pending['count']:
                items.append(dict(text=str(pending['count']) + ' 个实验执行请求等待决定。', href='/requests', action='逐项核对'))
        return items

    def _pending_requests(self):
        directory = self.root / 'execution-authority/requests'
        ids = sorted(path.name for path in directory.iterdir() if authority.ID.fullmatch(path.name)) if directory.exists() else []
        return dict(count=sum(1 for key in ids[-50:] if authority.status(self.root, key, secret=self.secret)['state'] == 'pending'))

    def _primary(self, token, manager, snapshot, status):
        state = status['state']
        view = snapshot['view']
        current = status['display']['current']
        if state in {'active','service_wait','pause_requested'}:
            if not manager:
                return ui.Safe('<span class="readonly-note">只读查看</span>') if state!='pause_requested' else None
            return ui.Safe((self._control_form(token, 'pause') if state!='pause_requested' else '')
                + self._interrupt_form(token, current))
        technical=self._technical_stop(snapshot)
        if technical is not None:
            if manager:
                if technical.get('phase')=='native_failure' and (technical.get('recovery_available') or technical.get('show_recheck_form')):
                    return ui.Safe(self._operator_resume_form(token,dict(step=technical['pending_step'],
                        attempt=technical['expected_attempt'],native_intent_ref=technical['native_intent_ref']),same_session_only=True,
                        request_recheck=not technical.get('recovery_available')))
                if technical.get('recovery_available'):
                    return ui.Safe(self._form(token,'resume','<input type="hidden" name="step" value="'
                        +str(technical['pending_step'])+'"><input type="hidden" name="expected_attempt" value="'
                        +str(technical['expected_attempt'])+'">','重查启动条件并续接同一原任务'))
            return ui.link('查看技术暂停与修复条件','/nodes/'+str(technical['pending_step']),cls='primary')
        if manager and current and current.get('operator_resume_target'):
            return ui.Safe(self._operator_resume_form(token,current['operator_resume_target']))
        if (manager and state not in {'active','service_wait','pause_requested'} and current
                and current.get('unexpected_recovery_target')):
            return ui.Safe(self._operator_resume_form(token,current['unexpected_recovery_target'],unexpected=True))
        if state == 'service_error':
            if manager and not view.get('paused'):
                return ui.Safe(self._form(token, 'pause', '', '暂停研究'))
            return ui.link('查看错误与处理方式', '/nodes/' + str(current['step']), cls='primary')
        if state == 'external_wait':
            if not manager:
                return ui.Safe('<span class="readonly-note">只读查看</span>')
            if self._monitoring(self._receipt(snapshot)):
                return ui.Safe(self._control_form(token, 'pause'))
            ready = ((current or {}).get('execution_wait') or {}).get('all_ready')
            label = '唤醒 W 继续研究' if ready else '恢复监视（出结果后自动唤醒 W）'
            if view.get('paused'):
                # A pause request would stop the wait loop at once; continue clears it first.
                trigger = (view['correction'].get('current_pause') or {}).get('trigger_id')
                return ui.Safe(self._control_form(token, 'continue', correction_trigger=trigger, label=label))
            return ui.Safe(self._control_form(token, 'start', label=label))
        if state == 'unsealed' and manager and ui_view.native_completed(current):
            return ui.Safe(self._form(token, 'resume', '<input type="hidden" name="step" value="'
                + str(current['step']) + '">', '收集封存并继续研究'))
        links = {'waiting': ('/results/' + str((status['display']['current'] or {}).get('step')), '查看等待原因'),
                 'unknown': ('/progress', '查看原任务进度'),
                 'unsealed': ('/progress', '核对原任务与未封存结论'),
                 'not_submitted': ('/progress', '核对原提交意图'), 'completed': ('/final-results', '查看结果'),
                 'budget_exhausted': ('/budget-recovery#budget', '恢复前追加预算'), 'final': ('/budget-recovery#budget', '恢复前追加预算')}
        if state in links:
            href, label = links[state]
            if state in {'budget_exhausted', 'final'} and not manager:
                return ui.Safe('<span class="readonly-note">只读查看</span>')
            return ui.link(label, href, cls='primary')
        if not manager:
            return ui.Safe('<span class="readonly-note">只读查看</span>') if state not in {'pause_requested', 'error'} else None
        trigger = (view['correction'].get('current_pause') or {}).get('trigger_id')
        if state in {'ready', 'idle'}:
            return ui.Safe(self._control_form(token, 'start', correction_trigger=trigger))
        if state == 'paused':
            return ui.Safe(self._control_form(token, 'continue', correction_trigger=trigger))
        return None

    def _notices(self, snapshot, receipt):
        notices = []
        current = snapshot['view']['nodes'][-1] if snapshot['view']['nodes'] else None
        sealed = bool(current and current['state'] in ui_view.SEALED)
        if receipt:
            texts = {'accepted':'已接受请求，正在安排研究工作。', 'running':'控制请求仍在处理；研究状态以上方阶段为准。',
                'completed':'本次控制请求已结束；研究结论以上方阶段为准。', 'applied':'本次控制请求已处理。',
                'failed':'本次请求未完成，请查看控制请求记录。', 'pause_requested':'暂停已请求；当前已接受的工作完成后停止。',
                'interrupt_requested':'立即中断已请求；等待原任务记录停止与清理结果。已用预算保留，未知外部调用请按原调用核对，勿重复提交。',
                'interrupt_unavailable':'所见原任务已不再是当前工作；已暂停后续研究，本次没有中断其他工作。请重新核对当前状态。'}
            texts['interrupt_unsupported'] = ('此冻结原执行器不支持立即中断；仅已请求阶段后暂停。'
                '请在原宿主核对启动方式；仅原科学执行器仍在前台并由该终端管理时可用 Ctrl+C。'
                '停止网页终端不会停止后台研究；请核对原任务与清理结果，未知调用勿重新提交。')
            notice = texts.get(receipt.get('status'), '控制请求已记录。')
            if receipt.get('kind') == 'controller' and receipt.get('status') == 'applied':
                result = receipt.get('result') or {}
                if result.get('status') == 'worker_resume_unavailable':
                    unavailable = {
                        'worker_resume_metadata_unavailable': '原会话信息不可确认；未启动接续。可核对原会话，或在已中断工作入口显式选择新上下文。',
                        'worker_resume_thread_unavailable': '原会话身份不可确认；未启动接续。可核对原会话，或显式选择新上下文；原实验未知状态不变。',
                        'operator_resume_session_limit_select_new_context': '原会话的有限续接次数已用完；可显式选择新上下文，原预算和权限继续生效。',
                        'operator_resume_special_scope_requires_existing_recovery': '此原任务属于 FINAL、作者收尾或受限补填；请使用该原范围已有的有限恢复入口，不能通过普通接续扩大权限。',
                        'worker_resume_budget_unavailable': '本次接续未取得剩余预算；请在预算恢复入口核对并明确调整原上限，已用消费保留。',
                        'unexpected_recovery_original_owner_running': '准确原 native、bridge 或 watchdog 仍在运行；未启动替身。无输出不能证明已停，请查看原进度或稍后核对同一目标。',
                        'unexpected_recovery_original_process_receipt_unavailable': '原进程回执缺失或不可读；尚不能确认本机 owner 已停止，未启动替身。请在原宿主核对该阶段与 attempt 的原回执。',
                        'unexpected_recovery_original_process_identity_invalid': '原进程身份字段不可核验；保持未知，未启动替身。请核对原回执，不要以数字 PID 或手写状态代替原身份。',
                        'unexpected_recovery_original_process_probe_unavailable': '原宿主的进程探测不可读取；保持未知，未启动替身。请核对原宿主进程访问条件后再次核对同一原任务。',
                        'unexpected_recovery_original_process_probe_invalid': '原宿主的进程探测格式不可确认；保持未知，未启动替身。请核对同一原宿主与原回执。',
                        'unexpected_recovery_original_group_probe_unavailable': '原进程组成员无法完整读取；尚未确认原 writer 全部停止，未启动替身。请核对原宿主后再次核对同一目标。',
                        'unexpected_recovery_original_group_probe_invalid': '原进程组成员信息无法核验；保持未知，未启动替身。请核对原宿主与原组回执。',
                        'unexpected_recovery_original_group_members_remain': '原进程组仍有成员，未确认原 writer 全部停止；未启动替身。请按原身份核对其工作与清理状态，不能盲 kill 或重发原调用。',
                        'unexpected_recovery_cleanup_unconfirmed': '原本机清理尚未得到确认；未启动替身。请核对准确原任务的进程和清理回执，外部实验仍按原 call ID 查询。',
                    }
                    notice = unavailable.get(result.get('reason'), '本次原任务接续未获准；请核对原阶段、attempt、清理结果和剩余预算，再显式操作。')
                    action=receipt.get('action') or {}
                    if type(action.get('step')) is int and type(action.get('expected_attempt')) is int:
                        notice+=' 核对目标：第 '+str(action['step'])+' 阶段 · attempt '+str(action['expected_attempt'])+'。'
                # Applied records the controller's return, not successful advancement.
                stopped = {
                    'native_stopped': '本次控制请求已停止推进：原生工作尚未封存阶段结论。请核对原任务，勿重复提交。',
                    'native_outcome_unsealed': '本次控制请求已停止推进：原任务的完成状态或阶段结论尚未核实（native_outcome_unsealed）。请核对原任务，勿重复提交。',
                    'seal_pending': '本次控制请求已停止推进：阶段结论封存待修复（seal_pending）。请核对原任务。',
                    'output_repair_required': '本次控制请求已停止推进：原任务输出待修复（output_repair_required）。请核对原任务。',
                    'output_repair_unavailable': '本次控制请求已停止推进：原任务输出暂无法修复（output_repair_unavailable）。请核对原任务。',
                    'closeout_native_unsealed': '本次控制请求已停止推进：原收尾任务尚未封存（closeout_native_unsealed）。请核对同一原任务，勿重复提交。',
                    'waiting_execution': '本次控制请求已结束，正在等待原实验执行；控制进程已停止监视（waiting_execution），实验本身不受影响。可在总览点击唤醒 W，勿重复提交。',
                }
                notice = stopped.get(result.get('status'), notice)
                technical=self._technical_stop(snapshot,receipt)
                if technical is not None:
                    notice=('本次控制请求因原生调用失败停止推进；请核对并修复服务条件，再重查同一原任务的恢复条件。' if technical.get('phase')=='native_failure' else
                        '本次控制请求因启动前技术问题停止推进；尚未进入科学评审。请修复本机配置，再核对同一原任务的续接条件。')
                if result.get('status') in stopped:
                    if sealed:
                        notice = '历史控制请求曾停止推进；当前阶段结论已封存，请以当前研究状态为准。'
                    elif ui_view.native_completed(current):
                        notice = '原控制请求已停止推进；当前原生已完成，待收集封存。请按当前原任务的处理操作继续。'
                # Reasons can contain private diagnostics. Only this exact static
                # controller reason has a public explanation; never render raw text.
                if (not sealed and not ui_view.native_completed(current) and result.get('status') == 'native_outcome_unsealed' and result.get('reason') ==
                        'Original execution lost its verified completion; preserved without replay'):
                    notice += ' 停止说明：原任务缺少可验证的完成记录；现有工作已保留，未重新执行。'
                if result.get('status') == 'closeout_native_unsealed' and not sealed and not ui_view.native_completed(current):
                    explanations = {
                        'Original closeout native failed; explicit worker-resume required for the same original task':
                            '原收尾任务已失败，未自动重试。如需续接，请用 worker-resume 精确选择同一原任务；仍须通过原权限与预算核验。',
                        'Original closeout native execution is unknown; reconcile the same original without replay':
                            '原收尾任务的完成状态尚未核实；请先核对同一原执行，不要重放或新建替代任务。',
                        'Original closeout native outcome remains unsealed; reconcile the same original without replay':
                            '原收尾任务的结果尚未核实；请核对同一原执行与留存记录，不要重复提交。',
                        'worker_fragment_checkpoint_unavailable':
                            '原 W 片段的 checkpoint 缺失；请核对同一原任务的留存记录，当前不能自动续接。',
                    }
                    explanation = explanations.get(result.get('reason'))
                    if explanation:
                        notice += ' 停止说明：' + explanation
                    if type(result.get('attempt_step')) is int and result['attempt_step'] > 0:
                        notice += ' 原任务步骤：' + str(result['attempt_step']) + '。'
            notices.append(notice)
        if current and not sealed and current.get('stop_reason'):
            diagnostic=ui_errors.native_stop_problem(current.get('technical_diagnostic'))
            notices.append('技术停止说明：'+diagnostic.cause if diagnostic is not None else
                '工作停止说明：' + ui_view.cleaner(self.root)(current['stop_reason'], 600))
        return notices

    def _snapshot_or_error(self, *, force=False):
        try:
            return self._snapshot(force=force)
        except Exception as error:  # noqa: BLE001 - the page keeps its other parts readable
            return dict(error=self._failure(error))

    def _dashboard_parts(self, token, snapshot):
        manager = 'maintenance' in self.browser.verify(token, instance_id=self.instance).scopes
        if 'error' in snapshot:
            error = snapshot['error']
            status = dict(state='error', badge=ui_view.STATES['error'],
                          headline=error['error']['kind'] + '：' + error['error']['message'][:160])
            attention=[dict(text='实例快照读取失败（' + error['error']['kind'] + '：'
                + error['error']['message'] + '）。本页面只读，没有写入任何记录；请核对实例记录后重新打开。',
                href='/results', action='查看阶段结论')]
            hero = ui.hero({}, status)
            return {'live-hero': hero, 'live-actions': ui.actions(attention=attention,
                        settings_href='/configuration#advanced-settings' if manager else None),
                    'live-usage': self._fragment(ui.stats, error), 'live-activity': '', 'live-trail': self._fragment(ui.trail, error),
                    'live-token-monitor': ''}
        view = snapshot['view']
        receipt = self._receipt(snapshot)
        display = self._presentation(snapshot, receipt)
        activity = None
        if display['active'] and display['current']:
            activity = self._guarded(self._activity, display['current'], clean=ui_view.cleaner(self.root))
        status = self._status(snapshot, receipt, elapsed=(activity or {}).get('elapsed'))
        projection = snapshot.get('ui') or {}
        if 'error' in projection:
            usage = ui_view.usage_stats(view, context_display=snapshot.get('context_display'))
            trail = projection
            header = projection
        else:
            usage = self._guarded(ui_view.usage_stats, view, used_steps=projection.get('used_steps'),
                                    candidates=projection.get('candidates'), context_display=snapshot.get('context_display'))
            trail = projection.get('trail') or {}
            header = projection.get('header') or {}
        headline=status['headline']
        hero_status=dict(status,headline=headline if len(headline)<=180 else headline[:179]+'…')
        hero = self._fragment(ui.hero, header, hero_status)
        primary=self._primary(token, manager, snapshot, status)
        actions = self._fragment(ui.actions, primary=primary,
                       attention=self._attention(token, manager, snapshot, status),
                       # Retry/recovery settings only matter when a service or task failed.
                       settings_href='/configuration#advanced-settings' if manager and status['state'] in {
                           'service_wait', 'service_error', 'unknown', 'error', 'not_submitted'} else None,
                       notices=([headline] if len(headline)>180 else [])+self._notices(snapshot, receipt))
        mechanisms = projection if 'error' in projection else (projection.get('mechanism_trails') or
            dict(error=dict(kind='Unavailable', message='机制记录暂不可读')))
        # A finished study shows every round as one line; otherwise the current round opens.
        return {'live-hero': hero, 'live-actions': actions, 'live-usage': self._fragment(ui.stats, usage) + self._fragment(ui_mechanisms.render, mechanisms, compact=True)
                    + self._execution_activity(view.get('live_executions')), 'live-activity': self._fragment(ui.activity, activity, href='/progress'),
                'live-trail': self._fragment(ui.trail, trail, older_href='/results', open_current=not display['completed']),
                'live-token-monitor': '' if 'error' in usage else self._fragment(ui.token_monitor,
                    usage['tokens'].get('details'), scope='整个研究 · 累计已记录用量')}

    @staticmethod
    def _execution_activity(value):
        if not isinstance(value,dict):return ''
        labels={'accepted':'已接受','submitted':'已提交，运行状态待观察','queued':'已观察排队',
            'running':'已观察运行','cancelling':'正在取消','known_success':'执行成功',
            'known_failed':'执行失败','not_submitted':'确认未提交','unknown':'状态未知'}
        coverage={'observed':'本地观察','partial':'部分覆盖，计数为下界','unknown':'观察不可用'}
        counts=value.get('counts') or {}
        totals=' · '.join(labels[key]+' '+str(counts[key]) for key in labels if counts.get(key))
        step=str(value.get('step'))
        body='<details class="execution-activity" data-details-key="'+ui.esc('w-executions-'+step)+'"><summary>W 实验执行状态 · 阶段 '+ui.esc(step)
        body+=' · '+ui.esc(coverage.get(value.get('coverage'),'观察不可用'))
        if totals:body+=' · '+ui.esc(totals)
        body+='</summary>'
        body+='<p>'+ui.esc(coverage.get(value.get('coverage'),'观察不可用'))+'；'+ui.esc(value.get('limitation',''))+'</p>'
        investment=value.get('research_investment')
        statistics='<details class="execution-statistics" data-details-key="'+ui.esc('w-execution-statistics-'+step)+'"><summary>调用统计</summary>'
        statistics+='<p class="note">原调用研究投入仅当前 W 阶段的 owner 观察；逐机制计数含继承，可能重叠，不可相加作为总数。投入不等于科学轮次、研究深度或科学认可。</p>'
        if isinstance(investment,dict):
            statistics+='<p>已接受原调用总数：'+ui.esc(investment['total_calls'])+'；当前连续投入：'+ui.esc(investment['consecutive_calls'])+' 次；方向：'+ui.esc(investment.get('direction') or '未提供')+'</p>'
            statistics+='<ul>'+''.join('<li>'+ui.esc(row['mechanism_ref'])+'：'+ui.esc(row['calls'])+' 次</li>' for row in investment['by_mechanism'])+'</ul>'
            if investment.get('omitted_count'):statistics+='<p>另有 '+str(investment['omitted_count'])+' 个机制计数未展开。</p>'
        else:statistics+='<p>研究投入未观测；缺失视图不表示零投入。</p>'
        statistics+='<p class="note">机制声明与原调用绑定版本见 W 已声明机制账本。</p></details>'
        rows=value.get('calls') or []
        if rows:
            body+='<div class="execution-cards">'
            omitted_for_size=0
            for index,row in enumerate(rows):
                age=ui_view.elapsed_text(row['updated_at'])
                display=row.get('display') or {}
                purpose=display.get('purpose') or '未提供'
                brief=purpose if len(purpose)<=140 else purpose[:140]+'…'
                description='<header class="execution-heading"><h3>'+ui.esc(display.get('entry') or '实验名称未提供')+'</h3>'
                description+='<span class="badge">'+ui.esc(labels[row['state']])+'</span></header>'
                description+='<p class="execution-purpose">'+ui.esc('目的：'+brief)+'</p>'
                metrics=display.get('metrics') or []
                result_status=display.get('result_status')
                result='<div class="execution-results"><h4>声明结果指标</h4>'
                if metrics:
                    result+='<ul>'+''.join('<li>'+ui.esc(item['name']+' = '+str(item['value'])+(' '+item['unit'] if item.get('unit') else ''))+'</li>' for item in metrics)+'</ul>'
                else:
                    result+='<p class="note">'+ui.esc({'available':'无可展示的声明指标','missing':'未提供声明结果',
                        'invalid':'声明结果不可用','unavailable':'声明结果不可用'}.get(result_status,'声明结果尚未提供'))+'</p>'
                if display.get('metrics_partial'):result+='<p class="note">仅显示部分指标</p>'
                result+='</div>'
                details_key='w-execution-'+step+'-'+row['call_id']
                details='<details class="execution-original" data-details-key="'+ui.esc(details_key)+'"><summary>原调用详情</summary><p>调用 ID：'+ui.esc(row['call_id'])+'<br>attempt：'+ui.esc('未知' if row['attempt'] is None else str(row['attempt']))+'</p>'
                if brief!=purpose:details+='<p>'+ui.esc('完整目的：'+purpose)+'</p>'
                if display.get('mechanism'):details+='<p>'+ui.esc('机制：'+display['mechanism'])+'</p>'
                binding=display.get('research_binding') or {}
                if binding:
                    details+='<p>'+ui.esc(binding['mechanism_ref']+' · revision '+str(binding['revision']))+'<br>'+ui.esc('机制问题：'+(binding.get('question') or '未提供'))+'</p>'
                if display.get('change_criterion'):details+='<p>'+ui.esc('改变判断的条件：'+display['change_criterion'])+'</p>'
                details+='</details>'
                markup='<article class="execution-card">'+description+result+'<p class="execution-updated note">最近记录：'+ui.esc(age+'前' if age else '时间未知')+'</p>'+details+'</article>'
                if len((body+markup+statistics).encode('utf-8'))>63*1024:
                    omitted_for_size=len(rows)-index;break
                body+=markup
            body+='</div>'
            if omitted_for_size:body+='<p>另有 '+str(omitted_for_size)+' 个调用因显示长度限制未展开；原件与后续封存记录保留。</p>'
        if value.get('omitted_count'):body+='<p>另有 '+str(value['omitted_count'])+' 个调用未在此列表展开。</p>'
        return body+statistics+'</details>'

    def _live_snapshot(self, token, mount, *, force=False):
        self.browser.verify(token, instance_id=self.instance)
        manager = 'maintenance' in self.browser.verify(token, instance_id=self.instance).scopes
        fragments = {}
        current = None
        list_mount = mount if self.workspace else re.sub(r'/instances/[1-9][0-9]*$', '', mount)
        instances_url = list_mount + '/instances'
        if self.workspace:
            self._admin(token)
            fragments['live-instances'] = self._loaded_instances(force=force)
        else:
            snapshot = self._snapshot_or_error(force=force)
            fragments = self._dashboard_parts(token, snapshot)
            projected = snapshot.get('ui') or {}
            mechanism_trails = (projected.get('mechanism_trails') if 'error' not in projected else projected)
            fragments['live-mechanisms'] = self._fragment(ui_mechanisms.render, mechanism_trails or
                dict(error=dict(kind='Unavailable', message='机制记录暂不可读')))
            fragments['live-mechanisms'] += self._fragment(ui.candidate_memory, projected.get('candidate_memory') or {})
            if 'error' not in snapshot:
                current = snapshot['view']['nodes'][-1] if snapshot['view']['nodes'] else None
                if manager:
                    fragments['live-operations'] = self._fragment(self._operation_rows, snapshot['operations'], href='/operations')
                    fragments['live-budget-navigation'] = self._budget_navigation(snapshot)
            if manager and not mount:
                fragments['live-instances'] = self._loaded_instances(force=force)
        if mount:
            for key, value in fragments.items():
                value = value.replace('href="/instances"', 'href="__WORKSPACE__"')
                for attribute in ('href', 'action', 'data-progress'):
                    value = value.replace(attribute + '="/', attribute + '="' + mount + '/')
                fragments[key] = value.replace('href="__WORKSPACE__"', 'href="' + instances_url + '"')
        return dict(fragments=fragments, mount=mount, instances_url=instances_url,
            current=dict(step=current['step'], role=self._role(current['role']), attempt=current.get('continuations',0),
                url=mount + '/nodes/' + str(current['step']) + '/events?attempt=' + str(current.get('continuations',0))) if current else None)

    def _budget_recovery_available(self, snapshot):
        if 'error' in snapshot:
            return False
        status = self._status(snapshot, self._receipt(snapshot))
        return snapshot['view'].get('closeout') is not None or (
            not status['display']['active'] and bool(ui_view.exhausted_limits(
                snapshot['view'], (snapshot.get('ui') or {}).get('used_steps'))))

    def _budget_navigation(self, snapshot):
        if self._budget_recovery_available(snapshot):
            return '<p id="budget"><a href="/budget-recovery#budget">恢复前追加预算</a></p>'
        return '<p><a href="/configuration#budget-settings">在当前研究配置中调整总预算</a></p>'

    def _more_actions(self, browser_token, snapshot, *, step=None, open_=False):
        """Secondary tools; lifecycle controls stay in the single overview action row."""
        manager = 'maintenance' in self.browser.verify(browser_token, instance_id=self.instance).scopes
        body = '<details class="more-actions" id="more-actions"' + (' open' if open_ else '') + '><summary>更多操作</summary>'
        if not manager:
            body += '<p class="note">当前为只读查看。使用本地管理入口可开始、暂停研究或申请调整预算。</p>'
            return body + '<p><a href="/?after=0">浏览全部阶段记录</a></p></details>'
        body += '<p class="note">以下操作仍受控制层原有条件约束；页面显示的状态不授予新的权限。</p>'
        if step is not None and self._technical_stop(snapshot) is None:
            body += self._form(browser_token, 'resume', '<input type="hidden" name="step" value="' + str(step) + '">', '继续此阶段原任务（须满足原有条件）')
        body += '<div id="live-budget-navigation">' + self._budget_navigation(snapshot) + '</div>'
        body += '<p><a href="/delete">删除实例</a> · <a href="/candidates">全候选历史</a> · <a href="/operations">控制请求记录</a> · <a href="/?after=0">浏览全部阶段记录</a>'
        if self.approvals:
            body += ' · <a href="/requests">实验执行请求</a>'
        return body + '</p></details>'

    def _budget_recovery_page(self, token):
        self._admin(token)
        snapshot = self._snapshot_or_error()
        body = self._navigation() + '<p class="settings-intro">为预算用完或已进入最终评估的研究追加总上限，以便后续继续研究。</p>'
        if self._budget_recovery_available(snapshot):
            parts = self._dashboard_parts(token, snapshot)
            for name in ('live-hero', 'live-actions', 'live-usage'):
                body += '<div id="' + name + '">' + parts[name] + '</div>'
            body += '<details class="budget-adjustment"><summary>恢复前追加预算</summary><div id="budget"><p>此处的预算调整用于后续恢复研究；与下一个新节点生效的配置修改分别记录。</p>'
            body += self._form(token, 'budget', '<label>新的 tokens 总上限<input name="max_tokens" type="number" min="1"></label><label>新的时间总上限（秒）<input name="max_seconds" type="number" step="any" min="0"></label><label>新的阶段总上限<input name="max_steps" type="number" min="1"></label><label>调整说明<textarea name="authorization_note" required></textarea></label><p class="note">提交即确认所填写的新上限；变更在安全边界处理，已用额度保留。生效后返回总览继续研究；提交本表不会启动研究。恢复仍须满足原有条件。</p>', '确认追加预算')
            body += '</div></details>'
        else:
            body += '<p>当前没有需要追加预算的恢复事项。<a href="/configuration#budget-settings">在当前研究配置中调整总预算</a>。</p>'
        body += '<p><a href="/">返回研究总览</a> · <a href="/operations">核对控制请求记录</a></p>'
        return 200, self._page('恢复前追加预算', body), {}

    _OVERVIEW_NOTES = (
        '阶段结论是 H/W/A/E 研究者记录的原文；记录完成不等于科学结论已获认可。',
        '模型用量为已观测值；用量未知的阶段只计下界，预留用量单列。',
        'W 的“本阶段实验”是封存观察中已接受的受管调用数，不等于独立实验设计数或科学覆盖。',
        '“最高/最低”为全部已观察数值（含失败记录），只在同一评估口径内、按作者声明的指标方向取值；不同口径不可直接比较，界面不提供科学排序或默认选择。',
        '★ A 推荐来自作者原文，✓ 已交付来自控制层交付记录，两者各自保留。',
        '是否达到研究目标，请结合结论原文中的局限与待解决问题判断。',
        '方向建议只交给研究者参考，不授予实验执行权限或额外预算。',
    )

    def _instance_page(self, browser_token, *, after=None, step=None):
        snapshot = self._snapshot_or_error()
        parts = self._dashboard_parts(browser_token, snapshot)
        if step is None and after is None:
            header = {} if 'error' in snapshot else ((snapshot.get('ui') or {}).get('header') or {})
            title = header.get('title') if 'error' not in header and header.get('title') else '研究总览'
            body = self._navigation('/')
            for name in ('live-hero', 'live-actions', 'live-usage', 'live-activity', 'live-trail', 'live-token-monitor'):
                body += '<div id="' + name + '">' + parts[name] + '</div>'
            state = None if 'error' in snapshot else self._status(snapshot, self._receipt(snapshot))['state']
            body += self._more_actions(browser_token, snapshot, open_=state in {'budget_exhausted', 'final'})
            body += ui.understanding(self._OVERVIEW_NOTES)
            body += '<details class="instance-info"><summary>实例信息</summary><p class="note">实例目录名：' + ui.esc(self.root.name) + '</p></details>'
            return 200, self._page(title, body), {}
        body = self._navigation()
        body += '<div id="live-usage">' + parts['live-usage'] + '</div>'
        view = saved_status.status(self.root, after=step-1 if step is not None else after, limit=1 if step is not None else 20)
        if step is not None and not view['nodes']:
            raise ValueError('This instance node does not exist')
        body += '<section><h2>' + ('固定阶段记录' if step else '研究阶段记录') + '</h2>'
        for node in view['nodes']:
            base = '/nodes/' + str(node['step'])
            body += '<article><h3>第 ' + str(node['step']) + ' 阶段 · ' + self._role(node['role']) + '</h3><p>' + self._state(node['state']) + '</p>'
            if node['state'] == 'seal_present_unverified':
                body += '<p><a href="/results/' + str(node['step']) + '">阅读阶段结论</a> · <a href="' + base + '/science">完整结论原文</a> · <a href="' + base + '/materials">下载研究文件</a></p>'
            else:
                body += '<p><a href="' + base + '/progress?attempt=' + str(node.get('continuations', 0)) + '">查看此阶段公开进度</a></p>'
            if node.get('native_failure') and node['state'] not in ui_view.SEALED:
                failure_help = ui_view.native_failure_guidance(node)
                if failure_help:
                    body += ui.facts([dict(label='错误含义', value='；'.join(failure_help['explanations'])),
                        *[dict(label='处理建议', value=advice) for advice in failure_help['advice']]])
            if 'maintenance' in self.browser.verify(browser_token, instance_id=self.instance).scopes:
                if ui_view.native_failure_fork_available(node):
                    body += self._failure_fork_exit(node['step'])
                elif node['state'] in ui_view.SEALED:
                    body += '<p><a href="' + base + '/fork">从这里分叉</a></p>'
            body += '</article>'
        if view.get('next_after') is not None:
            body += '<a href="/?after=' + str(view['next_after']) + '">查看后续阶段</a>'
        body += '</section>'
        body += '<div id="live-token-monitor">' + parts['live-token-monitor'] + '</div>'
        body += self._more_actions(browser_token, snapshot, step=step)
        return 200, self._page('研究阶段', body), {}

    def _fork_page(self, token, step, *, draft=None, preview=None, problem=None, reconcile=False):
        self._admin(token)
        attempted = self.fork_attempts.get(token)
        if draft is None and preview is None and attempted and attempted.get('step') == str(step):
            draft = attempted
            reconcile = True
        body = self._navigation() + '<p>重新执行所选逻辑任务，继承它开始前的准确输入。创建后保持暂停，请在新实例显式开始。</p>'
        body += ('<p>选择第 ' + str(step) + ' 阶段会重做第 ' + str(step) + ' 阶段，保留它开始前的已完成成果；'
            '不会重跑前一阶段。若要从更早任务重做，请先打开该阶段记录的分支入口。'
            '原尝试不删除，已用预算不退还；新分支须填写新的有限预算，预览会核对是否存在在途或未知外部调用。</p>')
        body += '<p class="note">方向指引保持原文；正式审查的中立输入限制优先。历史许可不授予新执行权限。</p>'
        if problem:
            body += problem.markup()
            body += '<p class="note">错误代码：' + html.escape(problem.code) + '</p>'
        if self._maintenance_blocked():
            body += ('<button type="button" disabled>分支暂不可用</button>'
                '<p class="note">独立实例网页服务正在持有维护锁。先关闭该服务，再从工作台按完整目录加载此研究，即可预览和创建分支。</p>')
            return (problem.status if problem else 200), self._page('从第 ' + str(step) + ' 阶段分叉', body, live=False), {}
        if preview:
            body += fork_ui.summary(preview)
        draft = dict(draft or {})
        if reconcile and draft.get('expected_preview_id') and draft.get('operation_id'):
            body += '<section><h2>核对已尝试的创建操作</h2><p>按原目标、原预览编号与原操作编号核对结果；此操作不要求重新读取父实例。</p>'
            body += self._form(token, 'fork-create', fork_ui.reconciliation_fields(draft), '核对原操作结果') + '</section>'
            # A new preview is a separately declared request. The immutable
            # recovery form above keeps the original operation identifiers.
            draft.update(operation_id=uuid.uuid4().hex, expected_preview_id='')
        if preview:
            draft['expected_preview_id'] = preview['preview_id']
        fields = fork_ui.fields(step, draft)
        body += self._form(token, 'fork-preview', fields, '预览分支')
        if preview and preview.get('status') == 'fork_preview_ready':
            body += self._form(token, 'fork-create', fields, '创建暂停的新分支')
        return (problem.status if problem else 200), self._page('从第 ' + str(step) + ' 阶段分叉', body, live=False), {}

    def _materials_page(self, step, after, directory=''):
        page = display.materials(self.root, step, after=after, directory=directory)
        base = '/nodes/' + str(step) + '/materials'

        def href(path, *, after=None):
            query = ('dir=' + quote(path, safe='')) if path else ''
            if after is not None:
                query += ('&' if query else '') + 'after=' + str(after)
            return base + ('?' + query if query else '')

        crumbs = [ui.link('研究文件根目录', base)]
        parts = directory.split('/') if directory else []
        for index, part in enumerate(parts):
            path = '/'.join(parts[:index + 1])
            crumbs.append(ui.link(part, href(path)) if index < len(parts) - 1
                else '<span aria-current="page">' + html.escape(part) + '</span>')
        body = self._navigation() + '<p>第 ' + str(step) + ' 阶段的研究文件，按原目录浏览并逐项下载。</p>'
        body += '<nav aria-label="研究文件目录">' + ' / '.join(crumbs) + '</nav>'
        if directory:
            body += '<p>' + ui.link('上一级', href('/'.join(parts[:-1]))) + '</p>'
        if page['items']:
            body += '<ul class="material-entries">'
            for item in page['items']:
                if item['kind'] == 'directory':
                    body += '<li class="material-directory">' + ui.link(item['name'] + '/', href(item['path']))
                    body += '（含 ' + str(item['file_count']) + ' 份研究文件）</li>'
                else:
                    body += '<li class="material-file">' + ui.link(item['name'], base + '/' + str(item['index']))
                    body += ' (' + str(item['size_bytes']) + ' bytes)</li>'
            body += '</ul>'
        elif page['total']:
            body += '<p>当前页没有项目。' + ui.link('返回本目录首页', href(directory)) + '</p>'
        else:
            body += '<p>当前目录没有研究文件或子目录。</p>'
        body += ('<p>当前目录共 ' + str(page['total']) + ' 项（子目录与文件），本阶段共 '
            + str(page['total_files']) + ' 份研究文件。</p>')
        if page['next_after'] is not None:
            body += ui.link('查看本目录后续项目', href(directory, after=page['next_after']))
        body += '<p>' + ui.link('返回第 ' + str(step) + ' 阶段', '/nodes/' + str(step)) + '</p>'
        return 200, self._page('研究文件下载', body, live=False), {}

    def _sealed_steps(self, after=0, limit=None):
        from . import fork_runtime, fork_sources
        inherited = ({row['step'] for row in fork_sources.validate_manifest(self.root)['prefix_entries']}
                     if fork_runtime.is_fork(self.root) else set())
        steps = sorted(inherited | {int(path.stem) for path in (self.root/'jobs').glob('*.json')
            if path.stem.isascii() and path.stem.isdecimal() and path.stem == f'{int(path.stem):03d}'
            and after < int(path.stem)})
        found = []
        for step in steps:
            record = self.root / 'native-evidence' / f'{step:03d}' / 'result-identity.json'
            if step > after and (step in inherited or record.exists() or record.is_symlink()):
                found.append(step)
            if limit is not None and len(found) == limit:
                break
        return found

    def _results_page(self, after, *, explicit=False):
        """Mechanism evidence trails above the author's chronological originals."""
        clean = ui_view.cleaner(self.root)
        reader = ui_view.Reader(self.root, self.view_memo)
        if explicit:
            found = self._sealed_steps(after, limit=21)
            shown, more = found[:20], (found[19] if len(found) > 20 else None)
            earlier = None
        else:
            found = self._sealed_steps()
            shown, more = found[-20:], None
            earlier = 0 if len(found) > 20 else None
        body = self._navigation('/results') + '<p>每个完成阶段的研究发现、建议和局限都保留在这里，按时间倒序排列。点击可阅读阶段结论并下载研究文件。</p>'
        # The shared snapshot owns acquisition and the one ledger projection.
        # Pagination below only reads the exact sealed originals it displays.
        snapshot=self._snapshot_or_error()
        projection=snapshot.get('ui') or {}
        mechanisms=(snapshot if 'error' in snapshot else projection if 'error' in projection else
            projection.get('mechanism_trails') or dict(error=dict(kind='Unavailable',message='机制记录暂不可读')))
        memory=projection.get('candidate_memory') or {}
        body += '<div id="live-mechanisms">' + ui_mechanisms.render(mechanisms) + ui.candidate_memory(memory) + '</div><h2>阶段原文记录</h2>'
        if not shown:
            body += '<p>还没有阶段结论。开始研究后，可先到“实时进度”查看工作情况。</p>'
        else:
            body += '<ol class="results-list">'
            for step in reversed(shown):
                row = self._guarded(ui_view.results_list_row, reader, step, clean, clean=clean)
                if 'error' in row:
                    body += ('<li>' + ui.link('第 ' + str(step) + ' 阶段', '/results/' + str(step))
                             + ui.error_inline(row, what='本阶段摘要') + '</li>')
                    continue
                body += ('<li><p class="role-head">' + ui.link('第 ' + str(step) + ' 阶段 · ' + row['role_label'], '/results/' + str(step))
                         + '</p><p class="one-line">' + ui.translatable(row['one_line']) + '</p></li>')
            body += '</ol>'
        if more is not None:
            body += '<a href="/results?after=' + str(more) + '">查看后续阶段结论</a>'
        if earlier is not None:
            body += '<a href="/results?after=0">查看更早的阶段结论（从第 1 阶段起）</a>'
        return 200, self._page('研究过程', body, live=True), {}

    _RESULT_NOTES = (
        '结论卡片只呈现作者原文与控制记录；记录完成不等于科学结论已获认可。',
        '★ A 推荐来自作者原文，✓ 已交付来自控制层交付记录；界面不自造“最佳”或“推荐”。',
        '候选比较仅显示本阶段输入范围内最近 W 的提名候选及 A 选中的候选，按首次观察顺序及评估口径分组；不同口径的数值不可直接比较，组内排序不代表科学排序。',
        '缺失指标与执行失败的候选保留在表中，不被过滤。',
        '是否达到研究目标，请结合局限与待解决问题判断；完整原文与研究文件可逐项下载核对。',
    )

    def _materials_overview(self, through, reader):
        """Exact sealed member counts per stage up to ``through`` (no member bytes read)."""
        steps = [step for step in self._sealed_steps() if step <= through]
        shown = steps[-ui_view.WINDOW:]
        return dict(rows=[(step, len(reader.node(step)['seal'].get('materials') or [])) for step in shown],
                    truncated=len(steps) > len(shown))

    def _candidates(self, step, *, job=None, reader=None, history_only=False):
        with self.cache_lock:
            reader = reader or ui_view.Reader(self.root, self.view_memo)
            job = job if job is not None else reader.node(step)['job']
            # The exact immutable input job and every referenced saved observation
            # identify this display projection; changing any source invalidates it.
            steps=ui_view.candidate_steps(job,step)
            sources=[]
            for number in steps:
                node=reader.node(number)
                # Internal path objects are acquisition handles, not JSON cache
                # identity. Include the owner's observed metadata and exact facts.
                identity={key:node.get(key) for key in ('job','payload','seal','result','research',
                    'origin_identity','candidate_reference_unresolved','navigation_fingerprint')}
                sources.append((number,identity,reader.observation(number)))
            key = json.dumps([str(self.root), job, sources, history_only], sort_keys=True)
            value = self.candidate_cache.get(key)
            if value is None:
                value = ui_view.candidate_projection(self.root, step, job=job, reader=reader, history_only=history_only)
                while len(self.candidate_cache) >= 8:
                    self.candidate_cache.pop(next(iter(self.candidate_cache)))
                self.candidate_cache[key] = copy.deepcopy(value)
            return copy.deepcopy(value)

    def _candidate_history(self, *, through=None, prefix=None, after=0):
        with self.cache_lock:
            if prefix is not None:
                saved = self.history_pages.get(prefix)
                if saved is None or saved['through'] != through:
                    raise ui_errors.InputError('prefix', '候选历史分页已过期。', '从候选历史入口重新打开。')
                identity = prefix
                missing = saved['missing']
                projection = copy.deepcopy(saved['projection'])
            else:
                numbers = self._sealed_steps()
                through = max(numbers, default=0) if through is None else through
                reader = ui_view.Reader(self.root, self.view_memo)
                entries, missing = [], []
                for step in numbers:
                    if step > through:
                        continue
                    try:
                        reader.node(step)
                        reader.observation(step)
                        entries.append(dict(step=step))
                    except Exception:
                        missing.append(step)
                job = dict(history=dict(nodes=entries))
                projection = self._candidates(through + 1, job=job, reader=reader, history_only=True)
                identity = uuid.uuid4().hex
                while len(self.history_pages) >= 8:
                    self.history_pages.pop(next(iter(self.history_pages)))
                self.history_pages[identity] = dict(through=through, missing=missing,
                                                  projection=copy.deepcopy(projection))
            total = len(projection['index'])
            projection['index'] = projection['index'][after:after + 20]
            rows = ui_view.candidate_rows(projection, clean=ui_view.cleaner(self.root), all_candidates=True)
            body = self._navigation() + '<p>全部已封存候选历史，按首次观察顺序分页；按评估口径与机制分组。全部已观察数值，含失败记录，不代表科学推荐。</p>'
            body += '<p>固定已封存前缀：第 ' + str(through) + ' 阶段 · <code>' + identity + '</code>；已观察 ' + str(total) + ' 个候选。</p>'
            if missing:
                body += '<p class="gap">部分阶段不可读，计数为下界；缺口阶段：' + ', '.join(map(str, missing)) + '。</p>'
            for gap in projection.get('unresolved', []):
                label = '推荐' if gap['field'] == 'recommendation' else '提名'
                body += '<p class="gap">第 ' + str(gap['step']) + ' 阶段有 ' + str(gap['count']) + ' 条' + label + '的候选来源不可读，未计为可用关联；' + ui.link('查看该阶段原文', '/results/' + str(gap['step'])) + '。</p>'
            body += ui.candidate_memory(projection.get('candidate_memory') or {})
            body += ui.candidate_table(dict(rows, all_candidates=True))
            if after + 20 < total:
                body += '<p><a href="/candidates?through=' + str(through) + '&amp;prefix=' + identity + '&amp;after=' + str(after + 20) + '">下一页候选</a></p>'
            return 200, self._page('全候选历史', body, live=False), {}

    def _delete_page(self, token, *, form=None, problem=None):
        self._admin(token)
        if self.workspace:
            raise PermissionError('Open an explicit instance to delete')
        if self._maintenance_blocked():
            body = self._navigation() + (problem.markup() if problem else '')
            body += ('<button type="button" disabled>删除暂不可用</button>'
                '<p class="note">独立实例网页服务正在持有维护锁。先关闭该服务，再从工作台按完整目录加载此研究，核对实例身份后删除。</p>')
            return (problem.status if problem else 200), self._page('删除实例', body, live=False), {}
        if form is None:
            if token in self.delete_requests:
                form = self.delete_requests[token]
                return self._delete_page(token, form=form, problem=problem)
            settings = display.settings(self.root)
            form = self.delete_requests.setdefault(token, dict(root=str(self.root), instance_id=settings['goal_id'],
                settings_ref=json.dumps(c.record_ref(self.root/'settings.json'),sort_keys=True), operation_id=uuid.uuid4().hex))
        body = self._navigation() + '<p>永久删除此精确实例。服务会拒绝运行中、未知结果、挂载、嵌套实例或仍由 UI 持锁的目标。独立实例 UI 请先关闭，再从工作台加载删除；工作台不会释放其它持有者的锁。</p>'
        if problem:
            body += problem.markup()
        fields = ''
        for key in ('root', 'instance_id', 'settings_ref', 'operation_id'):
            value = form[key]
            fields += '<label>' + {'root': '目标绝对路径', 'instance_id': 'goal_id', 'settings_ref': 'settings_ref', 'operation_id': 'operation_id'}[key] + '<input name="' + key + '" readonly value="' + html.escape(value, quote=True) + '"></label>'
        fields += '<label>确认删除上述实例<input name="confirmation" required placeholder="DELETE"></label>'
        body += self._form(token, 'delete', fields, '确认永久删除此实例')
        return (problem.status if problem else 200), self._page('删除实例', body, live=False), {}

    def _result_page(self, step, *, closeout, final=False):
        clean = ui_view.cleaner(self.root)
        reader = ui_view.Reader(self.root, self.view_memo)
        node = reader.node(step)
        job, payload, seal = node['job'], node['payload'], node['seal']
        full = ('<details class="full-text"><summary>查看完整原文（第 ' + str(step) + ' 阶段）</summary>'
                + render.result_body(step, job, payload, seal, clean=clean) + '</details>')
        role = job['role']
        from . import ui_semantics
        label = ui_semantics.role_label(job) or ui_view.ROLE_LABELS.get(role, role)
        memory=ui.candidate_memory(ui_view.candidate_memory(node,clean,loaded_nodes=reader._nodes.values()))
        if role != 'A':
            mechanisms = self._guarded(ui_view.mechanism_labels, reader, range(1, step + 1))
            node = dict(step=step, role=role, state='seal_present_unverified')
            line = self._guarded(lambda: ui_view.role_line(reader, node, clean,
                mechanisms={} if 'error' in mechanisms else mechanisms), clean=clean)
            if 'error' in line:
                line = dict(node, role_label=label, state_label='已有阶段结论', sealed=False, facts=[], links=[], error=line['error'])
            body = self._navigation('/results') + ui.role_line(line, expanded=True) + memory + full
            body += ui.understanding(self._RESULT_NOTES[:1] + (
                'W 的“本阶段实验”是封存观察中已接受的受管调用数，不等于独立实验设计数或科学覆盖。',
                '本阶段候选按首次观察顺序列出；“最高/最低”只在同一口径内按作者声明方向取值，不代表科学排序。'))
            return 200, render.page('第 ' + str(step) + ' 阶段 · ' + label, body), {}
        closeout_error = closeout is not None and 'error' in closeout
        closeout = None if closeout_error else closeout
        mechanisms = self._guarded(ui_view.mechanism_labels, reader, range(1, step + 1))
        mechanisms = {} if 'error' in mechanisms else mechanisms
        projection = self._guarded(self._candidates, step, job=job, reader=reader, clean=clean)
        card = self._guarded(lambda: ui_view.result_card(reader, step, closeout=closeout,
            projection=None if 'error' in projection else projection, clean=clean), clean=clean)
        delivered = ((closeout or {}).get('last_confirmed') or {}).get('candidate_id')
        files = ''
        if 'error' not in card:
            if closeout_error:
                card['delivery_state'], card['delivery_label'] = 'unknown', '交付状态未知'
            chosen = card.get('chosen')
            member = self._guarded(ui_view.candidate_file, reader, chosen, projection, clean=clean) \
                if chosen and 'error' not in projection else None
            if member and 'error' not in member:
                confirmed = ((closeout or {}).get('last_confirmed') or {}).get('artifact_ref')
                kind = '下载交付文件' if card['delivery_state'] == 'confirmed' and confirmed == member['reference'] else '下载推荐候选的研究文件'
                files += ui.link(kind + '：' + member['name'].rsplit('/', 1)[-1], '/nodes/' + str(member['step']) + '/materials/' + str(member['index']), cls='primary')
                files += '<p class="note">第 ' + ui.esc(member['step']) + ' 阶段研究文件</p>'
            files += ui.link('查看完整原文', '#full-text', cls='secondary-link')
        rows = self._guarded(lambda: ui_view.candidate_rows(projection, recommended=projection.get('recommended')
            if card.get('next_action') == 'audit' else (card.get('chosen') or {}).get('id')
            if 'error' not in card else None, delivered=delivered,
            audits=ui_view.audit_facts(self.root, projection, subject=payload), mechanisms=mechanisms, clean=clean), clean=clean) \
            if 'error' not in projection else projection
        table = memory + ui.candidate_table(rows, ident='candidates')
        if not final:
            table = ui.Safe('<details class="candidates-fold"><summary>候选比较（本阶段 W 提名及 A 选中）</summary>' + table + '</details>')
        materials = self._guarded(self._materials_overview, step, reader, clean=clean)
        section = '<section class="materials" id="materials"><h2>研究文件</h2>'
        if 'error' in materials:
            section += ui.error_inline(materials, what='研究文件清单')
        else:
            total = sum(count for _, count in materials['rows'])
            own = len(seal.get('materials') or [])
            scope = ('截至本阶段的最近 ' + str(ui_view.WINDOW) + ' 个已封存阶段共 ') if materials['truncated'] else '截至本阶段，各阶段共 '
            section += ('<p>本阶段 ' + str(own) + ' 份；' + scope + str(total) + ' 份研究文件。'
                        '按阶段进入后，可继续按原目录浏览并下载。</p><ul class="material-steps">')
            for number, count in materials['rows']:
                if count:
                    section += '<li>' + ui.link('第 ' + str(number) + ' 阶段 · ' + str(count) + ' 份', '/nodes/' + str(number) + '/materials') + '</li>'
            section += '</ul>'
        section += '</section>'
        body = self._navigation('/final-results' if final else '/results')
        body += ui.result_card(card, files=ui.Safe(files) if files else None) + table + section
        body += full.replace('<details class="full-text">', '<details class="full-text" id="full-text">', 1)
        body += ui.understanding(self._RESULT_NOTES)
        title = '最终结果 · 第 ' + str(step) + ' 阶段' if final else '第 ' + str(step) + ' 阶段 · ' + label
        # The hash-authorized host script only sorts rows inside one evaluation_key group.
        return 200, render.page(title, body, live=True), {}

    def get(self, target, *, host, cookie=None):
        if host != self.host:
            raise PermissionError('Exact local Host required')
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise PermissionError('Local relative request target required')
        if parsed.path.startswith('/workspaces/'):
            parts = parsed.path.split('/', 3)
            if len(parts) != 4 or not parts[2].isascii() or not parts[2].isdecimal():
                raise ValueError('Exact explicit workspace route required')
            child = self._workspace_child(int(parts[2]))
            return self._mount(child.get('/' + parts[3] + ('?' + parsed.query if parsed.query else ''),
                host=host, cookie=cookie), '/workspaces/' + parts[2])
        if parsed.path.startswith('/instances/'):
            parts = parsed.path.split('/', 3)
            if len(parts) != 4 or not parts[2].isascii() or not parts[2].isdecimal():
                raise ValueError('Exact explicit instance route required')
            index = int(parts[2])
            child = self._routed_child(index, parts[3], cookie)
            return self._mounted(child.get('/' + parts[3] + ('?' + parsed.query if parsed.query else ''), host=host, cookie=cookie), int(parts[2]))
        assets = {
            '/assets/style.css': (render.CSS, 'text/css; charset=utf-8'),
            '/assets/progress.js': (render.PROGRESS_SCRIPT, 'application/javascript; charset=utf-8'),
            '/assets/translation.js': (render.translation_client.SCRIPT, 'application/javascript; charset=utf-8'),
            '/assets/configuration.js': (configuration_form.SCRIPT_SOURCE, 'application/javascript; charset=utf-8'),
            '/assets/guidance.js': (ui_guidance.SCRIPT, 'application/javascript; charset=utf-8'),
            '/assets/entry.js': (_ENTRY_SCRIPT, 'application/javascript; charset=utf-8'),
        }
        if parsed.path in assets and not parsed.query:
            content, media_type = assets[parsed.path]
            return 200, content.encode(), {'Content-Type': media_type}
        if parsed.path in {'/manage', '/view'}:
            query = parse_qs(parsed.query, strict_parsing=True)
            values = query.get('token', [])
            expected = self.management_token if parsed.path == '/manage' else self.read_token
            if set(query) != {'token'} or len(values) != 1 or not hmac.compare_digest(values[0], expected):
                raise BrowserAccessDenied('本地浏览入口无效。')
            token = self.browser.issue(self.instance, maintenance=parsed.path == '/manage')
            # A committed local document ends a cross-site navigation chain.
            # A 303 here withholds the new Strict cookie from the destination.
            # The relative script target also remains inside mounted children.
            page = self._page('正在打开工作台', '<p>正在建立本地浏览会话…</p>'
                '<p><a href="/">进入工作台</a>（未自动跳转时点击）</p>'
                '<script src="/assets/entry.js"></script>', live=False)
            return 200, page, {'Set-Cookie': access_cookie(self.instance, token),
                'Referrer-Policy': 'no-referrer'}
        browser_token = self._browser(cookie, scope='read')
        if parsed.path == '/favicon.ico':
            return 204, b'', {}
        known = {'/', '/new', '/instances', '/configuration', '/guidance', '/operations', '/settings',
            '/budget-recovery', '/progress', '/final-results', '/results', '/session', '/reading',
            '/translation/state', '/live', '/requests', '/candidates', '/delete'}
        dynamic = (re.fullmatch(r'/nodes/[1-9][0-9]*(?:/(?:fork|progress|events|science|materials(?:/[1-9][0-9]*)?))?', parsed.path)
            or re.fullmatch(r'/results/[1-9][0-9]*', parsed.path)
            or re.fullmatch(r'/requests/[0-9a-f]{64}(?:/original)?', parsed.path))
        if parsed.path not in known and not dynamic:
            raise ValueError('Unknown instance UI path')
        if self.workspace and parsed.path not in {'/', '/new', '/instances', '/configuration', '/live', '/session', '/reading', '/translation/state'}:
            raise PermissionError('Open an explicit instance to access research pages')
        if parsed.path == '/session':
            if parsed.query:
                raise ValueError('Exact browser session route required')
            return 200, b'{"authenticated":true}', {'Content-Type': 'application/json; charset=utf-8'}
        if parsed.path in {'/reading', '/translation/state'}:
            if parsed.query:
                raise ValueError('Exact reading settings route required')
            if parsed.path == '/reading':
                return self._reading_page(browser_token)
            return self._translation_json(self._translation_state(browser_token))
        if parsed.path == '/live':
            if parsed.query:
                raise ValueError('Exact live workbench route required')
            if self.workspace:
                self._admin(browser_token)
            return 200, live.Stream(lambda mount, **options: self._live_snapshot(browser_token, mount, **options),
                stop=self.live_stop, gate=self.live_gate), {'Content-Type':'text/event-stream; charset=utf-8'}
        if self.workspace and parsed.path == '/':
            if parsed.query:
                raise ValueError('Exact workspace page required')
            return self._workspace_page(browser_token)
        if parsed.path == '/new':
            if parsed.query:
                raise ValueError('Exact new research page required')
            return self._new_research_page(browser_token)
        if parsed.path in {'/instances', '/configuration', '/guidance', '/operations', '/settings', '/budget-recovery'}:
            if parsed.query:
                raise ValueError('Exact instance page required')
            return {'/instances': self._instances_page, '/configuration': self._configuration_page,
                    '/guidance': self._guidance_page, '/operations': self._operations_page,
                    '/settings': self._settings_page, '/budget-recovery': self._budget_recovery_page}[parsed.path](browser_token)
        if parsed.path == '/delete':
            if parsed.query:
                raise ValueError('Unknown instance UI path')
            return self._delete_page(browser_token)
        if parsed.path == '/candidates':
            query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
            if set(query) - {'through', 'prefix', 'after'} or any(len(row) != 1 for row in query.values()):
                raise ValueError('Exact scientific result continuation required')
            if query and not {'through', 'prefix', 'after'} <= set(query):
                raise ValueError('Exact scientific result continuation required')
            if any(not query[key][0].isascii() or not query[key][0].isdecimal() for key in ('through', 'after') if key in query):
                raise ValueError('Exact scientific result continuation required')
            return self._candidate_history(through=int(query['through'][0]) if query else None,
                prefix=query['prefix'][0] if query else None, after=int(query['after'][0]) if query else 0)
        if parsed.path == '/progress':
            if parsed.query:
                raise ValueError('Exact progress page required')
            return self._following_progress_page()
        if parsed.path == '/final-results':
            if parsed.query:
                raise ValueError('Exact final result page required')
            try:
                saved = self._result_source()
                final = saved['result_step']
                if final is not None:
                    return self._result_page(final, final=True, closeout=saved['closeout'])
            except Exception as error:  # noqa: BLE001 - name the failure; other pages stay usable
                failure = self._failure(error)
                return 200, self._page('最终结果', self._navigation('/final-results') + '<section><h2>最终结论暂不可读</h2>'
                    + ui.error_inline(failure, what='最终结论') + '<a class="primary" href="/results">查看研究过程</a></section>'), {}
            return 200, self._page('最终结果', self._navigation('/final-results') + '<section class="empty"><h2>最终综合结论尚未返回</h2><p>你可以先阅读已完成阶段的发现与局限。</p><a class="primary" href="/results">查看研究过程</a></section>'), {}
        if parsed.path=='/results':
            query=parse_qs(parsed.query,strict_parsing=True) if parsed.query else {}
            if set(query)-{'after'} or any(len(values)!=1 for values in query.values()):
                raise ValueError('Exact scientific result continuation required')
            after=query.get('after',['0'])[0]
            if not after.isascii() or not after.isdecimal():raise ValueError('Exact scientific result continuation required')
            return self._results_page(int(after), explicit=bool(query))
        if parsed.path.startswith('/results/'):
            number=parsed.path.removeprefix('/results/')
            if parsed.query or not number.isascii() or not number.isdecimal() or int(number)<=0:
                raise ValueError('Exact sealed scientific result required')
            return self._result_page(int(number), closeout=self._guarded(saved_status.closeout, self.root))
        if parsed.path == '/':
            query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
            if set(query) - {'after'} or any(len(values) != 1 for values in query.values()):
                raise ValueError('Exact node continuation required')
            after = query.get('after', ['0'])[0]
            if not after.isascii() or not after.isdecimal():
                raise ValueError('Invalid node continuation')
            return self._instance_page(browser_token, after=int(after) if query else None)
        if parsed.path.startswith('/nodes/'):
            parts = parsed.path.removeprefix('/nodes/').split('/')
            number = parts[0]
            if not number.isascii() or not number.isdecimal() or int(number)<=0:
                raise ValueError('Exact instance node required')
            step=int(number)
            if len(parts)==1 and not parsed.query:
                return self._instance_page(browser_token,step=step)
            if parts[1:] == ['fork'] and not parsed.query:
                return self._fork_page(browser_token, step)
            if parts[1:] in (['progress'], ['events']):
                query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
                if set(query) - {'attempt', 'cursor'} or any(len(values) != 1 for values in query.values()):
                    raise ValueError('Exact progress continuation required')
                attempt = query.get('attempt', ['0'])[0]
                if not attempt.isascii() or not attempt.isdecimal():
                    raise ValueError('Exact progress attempt required')
                cursor = query.get('cursor', [None])[0]
                if parts[1:] == ['events']:
                    value = self._progress_value(step, int(attempt), cursor)
                    return 200, json.dumps(value, ensure_ascii=False).encode(), {'Content-Type': 'application/json; charset=utf-8'}
                return self._progress_page(step, int(attempt), cursor)
            if parts[1:]==['science'] and not parsed.query:
                return 200,display.science(self.root,step),{'Content-Type':'application/json; charset=utf-8'}
            if parts[1:]==['materials']:
                query=parse_qs(parsed.query,strict_parsing=True,keep_blank_values=True) if parsed.query else {}
                if set(query)-{'after', 'dir'} or any(len(values)!=1 for values in query.values()):
                    raise ValueError('Exact scientific deliverable continuation required')
                after=query.get('after',['0'])[0]
                if not after.isascii() or not after.isdecimal():
                    raise ValueError('Exact scientific deliverable continuation required')
                return self._materials_page(step,int(after),query.get('dir',[''])[0])
            if len(parts)==3 and parts[1]=='materials' and not parsed.query:
                if not parts[2].isascii() or not parts[2].isdecimal() or int(parts[2])<=0:
                    raise ValueError('Exact scientific deliverable index required')
                original,name=display.artifact(self.root,step,int(parts[2]))
                return 200,original,{'Content-Type':'application/octet-stream',
                    'Content-Disposition':"attachment; filename=scientific-deliverable; filename*=UTF-8''"+quote(name,safe='')}
            raise ValueError('Exact instance node required')
        if not self.approvals:
            raise ui_errors.InputError('approval', '此实例未启用实验审批入口。', '使用研究总览和原任务进度核对当前状态。')
        if parsed.path == '/requests':
            query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
            if set(query) - {'after'} or any(len(values) != 1 for values in query.values()):
                raise ValueError('Exact request continuation required')
            after = query.get('after', [''])[0]
            if after and not authority.ID.fullmatch(after):
                raise ValueError('Invalid request continuation')
            directory = self.root / 'execution-authority/requests'
            paths = sorted(directory.iterdir()) if directory.exists() else []
            ids = [path.name for path in paths if authority.ID.fullmatch(path.name) and path.name > after]
            links = ''.join('<li><a href="/requests/' + key + '">' + key + '</a></li>' for key in ids[:20])
            more = ('<a href="/requests?after=' + ids[19] + '">Next requests</a>') if len(ids) > 20 else ''
            return 200, self._page('Execution requests', '<a href="/">Instance</a><p>Decisions grant no execution or additional budget by themselves.</p><ul>' + links + '</ul>' + more), {}
        parts = parsed.path.strip('/').split('/')
        if parsed.query or len(parts) not in (2, 3) or parts[0] != 'requests':
            raise ValueError('Unknown instance UI path')
        request_id = parts[1]
        value, ref = authority.request(self.root, request_id)
        if len(parts) == 3:
            if parts[2] != 'original':
                raise ValueError('Unknown request original')
            return 200, self.root / ref['path'], {'Content-Type': 'application/json; charset=utf-8'}
        state = authority.status(self.root, request_id, secret=self.secret)
        tokens = authority.form_tokens(self.root, request_id, secret=self.secret,
                                       boot=self.boot, browser_token=browser_token)
        # Full mechanical binding stays available as an exact original download.
        compact = dict(state=state['state'], subject=value['subject']['material']['path'],
            target=value['target'], resources=value['resources'], policy=value['policy']['outcome'],
            expires_at=value['expires_at'], scope=authority.SCOPE)
        body = '<pre>' + html.escape(json.dumps(compact, ensure_ascii=False, indent=2)) + '</pre>'
        body += '<p><a href="/requests/' + request_id + '/original">Complete frozen request</a></p>'
        successor=authority.request_successor(self.root,ref)
        if successor is not None:
            body += '<p><a href="/requests/' + Path(successor['path']).parent.name + '">Explicit replacement request</a> (the original request and decision remain unchanged).</p>'
        manager = 'maintenance' in self.browser.verify(browser_token, instance_id=self.instance).scopes
        if manager and state['state'] == 'pending':
            options = ''.join('<option value="' + item + '">' + item + '</option>' for item in value['options'])
            body += ('<form method="post" action="/requests/' + request_id + '/decision">'
                '<input type="hidden" name="nonce" value="' + tokens['nonce'] + '">'
                '<input type="hidden" name="csrf" value="' + tokens['csrf'] + '">'
                '<label>Decision <select name="selected_option">' + options + '</select></label>'
                '<label>Reason <textarea name="rationale" required></textarea></label>'
                '<button type="submit">Record decision</button></form>')
        if manager and state['state'] in {'pending', 'decided'}:
            body += ('<form method="post" action="/requests/' + request_id + '/revoke">'
                '<input type="hidden" name="nonce" value="' + tokens['nonce'] + '">'
                '<input type="hidden" name="csrf" value="' + tokens['csrf'] + '">'
                '<label>Reason <textarea name="rationale" required></textarea></label>'
                '<button type="submit">Revoke request</button></form>')
        return 200, self._page('Exact execution decision', body), {}

    def post(self, target, *, host, origin, cookie, content_type, body):
        security.require_same_origin(host, origin, self.host)
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('Exact instance control or decision form required')
        if parsed.path.startswith('/workspaces/'):
            parts = parsed.path.split('/', 3)
            if len(parts) != 4 or not parts[2].isascii() or not parts[2].isdecimal():
                raise ValueError('Exact explicit workspace route required')
            child = self._workspace_child(int(parts[2]))
            return self._mount(child.post('/' + parts[3], host=host, origin=origin, cookie=cookie,
                content_type=content_type, body=body), '/workspaces/' + parts[2])
        if parsed.path.startswith('/instances/'):
            parts = parsed.path.split('/', 3)
            if len(parts) != 4 or not parts[2].isascii() or not parts[2].isdecimal():
                raise ValueError('Exact explicit instance route required')
            index = int(parts[2])
            child = self._routed_child(index, parts[3], cookie)
            return self._mounted(child.post('/' + parts[3], host=host, origin=origin, cookie=cookie,
                content_type=content_type, body=body), int(parts[2]))
        if parsed.path.startswith('/translation/'):
            return self._translation_post(parsed.path, cookie, content_type, body)
        browser_token = self._browser(cookie)
        parts = parsed.path.strip('/').split('/')
        if content_type != 'application/x-www-form-urlencoded':
            raise ValueError('Exact instance control or decision form required')
        if len(parts) == 2 and parts[0] == 'control':
            action = parts[1]
            schemas = {
                'delete': {'root', 'instance_id', 'settings_ref', 'operation_id', 'confirmation'},
                'pause': set(), 'interrupt': {'step', 'expected_attempt', 'native_intent_ref'},
                'start': set(), 'resume': {'step'}, 'continue': {'ack_correction', 'authorization_note'},
                'create': {'selection'}, 'load-config': {'config_path'}, 'configure': None, 'configuration-update': None,
                'open': {'root'}, 'workspace-open': {'root'}, 'configuration': {'config_path', 'config_text', 'mode'},
                'budget': {'max_tokens', 'max_seconds', 'max_steps', 'authorization_note'},
                'guidance-submit': {'text', 'target', 'kind', 'scope', 'effective_step'},
                'guidance-edit': {'guidance_id', 'expected_revision', 'text'},
                'guidance-revoke': {'guidance_id', 'expected_revision'},
                'fork-preview': fork_ui.FIELDS, 'fork-create': fork_ui.FIELDS,
            }
            if action not in schemas:
                raise ValueError('Unknown instance UI path')
            if self.workspace and action not in {'open', 'workspace-open', 'configuration', 'create', 'load-config', 'configure'}:
                raise PermissionError('Open an explicit instance to use research controls')
            form = parse_qs(body.decode('utf-8'), keep_blank_values=True, strict_parsing=True)
            if any(len(form.get(key, [])) != 1 for key in ('nonce', 'csrf')):
                raise ValueError('Control form contains missing or duplicate credentials')
            # Authenticate the action before interpreting its other fields.
            # A configure form cannot become create permission by changing its
            # action URL, even when the original form has extra hidden fields.
            if not hmac.compare_digest(form['csrf'][0], self._csrf(browser_token, action, form['nonce'][0])):
                raise PermissionError('Instance control CSRF or nonce mismatch')
            expected = schemas[action]
            if action == 'budget' and 'authorization_statement' in form:
                expected = expected | {'authorization_statement'}
            if action == 'resume' and 'expected_attempt' in form:
                expected = expected | {'expected_attempt'}
            if action == 'resume' and 'after_interrupt' in form:
                expected = expected | {'expected_attempt','after_interrupt','native_intent_ref','new_context'}
            if (not {'nonce', 'csrf'} <= set(form) or
                    action == 'configuration-update' and 'expected_revision' not in form or
                    expected is not None and set(form) != expected | {'nonce', 'csrf'} or
                    any(len(values) != 1 for values in form.values())):
                raise ValueError('Control form contains missing or duplicate fields')
            form = {key: values[0] for key, values in form.items()}
            try:
                self._require_maintenance_handoff(action)
                # Export is read-only and leaves its source form visible. The
                # next mutating submit still atomically consumes this nonce.
                self._consume_form(browser_token, action, form,
                    consume=not (action == 'configure' and form.get('mode') == 'export'),
                    replay=action in {'start', 'resume', 'continue', 'budget', 'fork-create', 'delete'})
                return self._control_post(browser_token, action, form)
            except Exception as error:
                return self._control_error(browser_token, action, form, error)
        if not re.fullmatch(r'/requests/[0-9a-f]{64}/(?:decision|revoke)', parsed.path):
            raise ValueError('Unknown instance UI path')
        if self.workspace:
            raise PermissionError('Open an explicit instance to use research controls')
        if not self.approvals:
            raise ui_errors.InputError('approval', '此实例未启用实验审批入口。', '使用研究总览和原任务进度核对当前状态。')
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or len(parts) != 3
                or parts[0] != 'requests' or parts[2] not in {'decision', 'revoke'}
                or content_type != 'application/x-www-form-urlencoded'):
            raise ValueError('Exact execution decision form required')
        form = parse_qs(body.decode('utf-8'), keep_blank_values=True, strict_parsing=True)
        fields = {'nonce', 'csrf', 'rationale'} | ({'selected_option'} if parts[2] == 'decision' else set())
        if set(form) != fields or any(len(values) != 1 for values in form.values()):
            raise ValueError('Decision form contains missing or duplicate fields')
        form = {key: values[0] for key, values in form.items()}
        if parts[2] == 'decision':
            authority.record_decision(self.root, parts[1], secret=self.secret, boot=self.boot,
                browser_token=browser_token, **form)
        else:
            expected = authority.form_tokens(self.root, parts[1], secret=self.secret,
                boot=self.boot, browser_token=browser_token)
            if not all(hmac.compare_digest(form[key], expected[key]) for key in ('nonce', 'csrf')):
                raise PermissionError('Execution revocation CSRF or nonce mismatch')
            authority.revoke(self.root, parts[1], secret=self.secret, reason=form['rationale'])
        return 303, b'', {'Location': '/requests/' + parts[1]}


    def _control_post(self, browser_token, action, form):
        self._require_maintenance_handoff(action)
        def integer(field, *, optional=False):
            try:
                return int(form[field]) if form[field] or not optional else None
            except ValueError:
                raise ui_errors.InputError(field, '此字段必须填写整数。', '修改此字段后重新提交。') from None

        location = '/'
        if action == 'delete':
            from . import instance_delete
            if form['root'] != str(self.root) or form['confirmation'] != 'DELETE':
                raise ui_errors.InputError('confirmation', '删除目标或确认文字不匹配。', '核对目标绝对路径、身份和原操作编号；填写 DELETE。')
            instance_delete.delete_instance(self.root, instance_id=form['instance_id'],
                settings_ref=json.loads(form['settings_ref']), operation_id=form['operation_id'])
            parent = self.workspace_host
            if parent is None:
                self.live_stop.set()
            self._clear_display_caches()
            for cache in (self.live_cache, self.summary_cache):
                with cache.lock:
                    cache.value = None
                    cache.checked = 0.0
            if parent is not None:
                # Keep positional mount identities stable for other open tabs.
                for index, child in tuple(parent.children.items()):
                    if child is self:
                        parent.deleted_children[index] = parent.children.pop(index)
                if not self.root.exists():
                    parent._forget_instance(self.root, self.instance)
                for cache in (parent.summary_cache, parent.live_cache):
                    with cache.lock:
                        cache.value = None
                        cache.checked = 0.0
            return 303, b'', {'Location': '/instances'}
        if action in {'fork-preview', 'fork-create'}:
            from . import fork_service
            step = integer('step')
            child_root = Path(form['child_root']).expanduser().absolute()
            options = fork_ui.options(form)
            if action == 'fork-preview':
                preview = fork_service.preview(self.root, step, child_root, **options)
                return self._fork_page(browser_token, step, draft=form, preview=preview)
            self.fork_attempts[browser_token] = {key: form[key] for key in fork_ui.FIELDS}
            receipt = fork_service.create(self.root, step, child_root, **options,
                expected_preview_id=form['expected_preview_id'], operation_id=form['operation_id'])
            child_root = child_root.resolve()
            settings = display.settings(child_root)
            self._register_instance(child_root, settings)
            child = self._materialize_child(self.instance_roots.index(child_root), settings)
            link = '/instances/' + str(self.instance_roots.index(child_root)) + '/manage?token=' + child.management_token
            return 200, self._page('新分支已创建并暂停', '<p>新分支已创建；开始研究须进入新实例后显式操作。</p>'
                + '<p><a href="' + html.escape(link, quote=True) + '">打开新实例</a></p><pre>'
                + html.escape(json.dumps({key: receipt[key] for key in ('status', 'operation_id', 'child_identity') if key in receipt}, ensure_ascii=False)) + '</pre>', live=False), {}
        elif action == 'continue':
            self._continue(ack_correction=form['ack_correction'] or None,
                           authorization_note=form['authorization_note'] or None,
                           operation_id=self._operation_id(browser_token, action, form))
        elif action == 'start':
            self.control_result = control.start_background(self.root,
                operation_id=self._operation_id(browser_token, action, form))
        elif action == 'resume':
            options={}
            if 'after_interrupt' in form:
                if form['after_interrupt'] != '1' or form['new_context'] not in {'0','1'}:
                    raise ValueError('Exact operator continuation mode required')
                options.update(after_interrupt=True,native_intent_ref=json.loads(form['native_intent_ref']),
                    new_context=form['new_context']=='1')
            self.control_result = control.start_background(self.root, resume_step=integer('step'),
                expected_attempt=integer('expected_attempt',optional=True) if 'expected_attempt' in form else None,
                operation_id=self._operation_id(browser_token, action, form),**options)
        elif action == 'pause':
            self.control_result = control.request_pause(self.root)
        elif action == 'interrupt':
            self.control_result = control.request_interrupt(self.root, step=integer('step'),
                expected_attempt=integer('expected_attempt'),
                native_intent_ref=json.loads(form['native_intent_ref']))
        elif action == 'budget':
            values = {}
            for key, convert, message in [('max_tokens', int, 'max_tokens must be a positive integer'),
                    ('max_seconds', float, 'max_seconds must be positive and finite'),
                    ('max_steps', int, 'max_steps must be a positive integer')]:
                try:
                    values[key] = convert(form[key]) if form[key] else None
                except ValueError:
                    raise ValueError(message) from None
            self.control_result = control.request_budget_amendment(self.root,
                **values,
                operation_id=self._operation_id(browser_token, action, form),
                authorization_note=form['authorization_note'],
                authorization_statement=form['authorization_statement'] if form.get('authorization_statement', '').strip() else (
                    form['authorization_note'] if values['max_steps'] is not None else None))
            location = '/operations'
        elif action.startswith('guidance-'):
            if action == 'guidance-submit':
                if form['kind'] == 'goal_change' and (form['target'] != 'global' or form['scope'] != 'research'):
                    raise ui_errors.InputError('kind', '修改目标必须面向所有研究角色，并用于研究的后续适用阶段。', '选择所有研究角色和研究范围，并填写明确的生效阶段。')
                if form['kind'] == 'goal_change' and not form['effective_step'].strip():
                    raise ui_errors.InputError('effective_step', '修改目标必须填写生效阶段。', '填写一个正整数阶段编号。')
                effective_step = integer('effective_step', optional=True)
                if effective_step is not None and effective_step < 1:
                    raise ui_errors.InputError('effective_step', '生效阶段必须是正整数。', '填写大于或等于 1 的阶段编号，或为补充建议留空。')
                user_guidance.submit(self.root, form['text'], target=form['target'], kind=form['kind'],
                    scope=form['scope'], effective_step=effective_step)
            elif action == 'guidance-edit':
                user_guidance.edit(self.root, form['guidance_id'], form['text'], expected_revision=integer('expected_revision'))
            else:
                user_guidance.revoke(self.root, form['guidance_id'], expected_revision=integer('expected_revision'))
            location = '/guidance'
        elif action == 'workspace-open':
            if not self.multi_workspace:
                raise PermissionError('Workspace registration requires the global workbench')
            selected = Path(form['root']).expanduser().resolve()
            if not selected.is_dir():
                raise ValueError('UI workspace must be an existing explicit directory')
            self._remember('workspaces', dict(root=str(selected)))
            with self.control_lock:
                if selected not in self.workspace_roots:
                    self.workspace_roots.append(selected)
                index = self.workspace_roots.index(selected)
            if index:
                child = self._workspace_child(index)
                location = '/workspaces/' + str(index) + '/manage?token=' + child.management_token
        elif action == 'open':
            selected = Path(form['root']).expanduser().resolve()
            selected.stat()  # A missing chosen instance is a 404, not a type error.
            settings = display.settings(selected)
            self._register_instance(selected, settings)
            index = self.instance_roots.index(selected)
            if index:
                child = self._materialize_child(index, settings)
                location = '/instances/' + str(index) + '/manage?token=' + child.management_token
            else:
                location = '/'
        elif action == 'load-config':
            return self._import_config(browser_token, form['config_path'])
        elif action == 'configure':
            mode = form.get('mode')
            if mode not in {'preview', 'export', 'save'}:
                raise ValueError('Select an explicit configuration action')
            fields = {key: value for key, value in form.items() if key not in {'nonce', 'csrf', 'selection', 'save_path', 'mode'}}
            selected = self._selected_config(browser_token, form['selection']) if form.get('selection') else None
            source = selected['source'] if selected else None
            if self._config_changed(source):
                return self._import_config(browser_token, source['path'], notice='原配置或任务已变化，请重新检查并确认。')
            base = selected['draft']['base'] if selected else self.root
            draft = selected['draft'] if selected else configuration_form.blank(base=base)
            try:
                draft = configuration_form.parse(fields, base=base)
                self._draft_task(draft)
            except Exception as error:
                draft = getattr(error, 'draft', draft)
                selection = self._remember_config(browser_token, draft, source=source)
                return self._new_research_page(browser_token, draft=draft, selection=selection,
                    notice=self._problem(error, context='configuration', record=True), save_path=form.get('save_path', ''))
            if mode == 'export':
                text = configuration_form.export_toml(draft)
                return 200, text.encode('utf-8'), {'Content-Type': 'application/toml; charset=utf-8',
                    'Content-Disposition': 'attachment; filename="research.toml"'}
            if mode == 'save':
                path = self._save_toml(form.get('save_path', ''), configuration_form.export_toml(draft))
                return self._import_config(browser_token, path, notice='配置已保存。创建实例前请检查并预览。')
            try:
                task_source = self._draft_task(draft)
                preview = configuration_form.preview(draft)
                if self._draft_task(draft) != task_source:
                    raise ValueError('任务原文已变化，请重新检查并预览。')
            except Exception as error:
                selection = self._remember_config(browser_token, draft, source=source)
                return self._new_research_page(browser_token, draft=draft, selection=selection,
                    notice=self._problem(error, context='configuration', record=True), save_path=form.get('save_path', ''))
            selection = self._remember_config(browser_token, draft, source=source, previewed=True, task_source=task_source)
            return self._new_research_page(browser_token, draft=draft, selection=selection, preview=preview)
        elif action == 'create':
            selected = self._selected_config(browser_token, form['selection'])
            if not selected['previewed']:
                raise ValueError('请先检查并预览本次研究配置。')
            source = selected['source']
            if self._config_changed(source):
                return self._import_config(browser_token, source['path'], notice='原配置或任务已变化，请重新检查并确认。')
            draft = selected['draft']
            if self._draft_task(draft) != selected['task_source']:
                task_source = self._draft_task(draft)
                preview = configuration_form.preview(draft)
                if self._draft_task(draft) != task_source:
                    raise ValueError('任务原文已变化，请重新检查并预览。')
                selection = self._remember_config(browser_token, draft, source=source, previewed=True, task_source=task_source)
                return self._new_research_page(browser_token, draft=draft, selection=selection,
                    preview=preview, notice='任务原文已变化，请重新确认后创建。')
            configuration_form.create(draft)
            root = Path(selected['draft']['document']['research']['root']).expanduser()
            selected_root = (Path(selected['draft']['base']) / root).resolve() if not root.is_absolute() else root.resolve()
            self.config_selections.pop(browser_token, None)
            settings = display.settings(selected_root)
            self._register_instance(selected_root, settings)
            index = self.instance_roots.index(selected_root)
            child = self._materialize_child(index, settings)
            location = '/instances/' + str(index) + '/manage?token=' + child.management_token
        elif action == 'configuration-update':
            state = configuration_updates.status(self.root)
            fields = {key: value for key, value in form.items() if key not in {'nonce', 'csrf', 'expected_revision', 'authorization_note'}}
            draft = configuration_form.parse_edit(fields, base=self.root, configuration=state['configuration'])
            configuration_updates.submit(self.root, draft['document'], base=self.root,
                expected_revision=integer('expected_revision'), authorization_note=form.get('authorization_note', ''))
            self._refresh_approval_host()
            location = '/configuration'
        elif action == 'configuration':
            path = Path(form['config_path']).expanduser().absolute()
            if path.suffix.lower() != '.toml' or path.resolve() == self.secret_file:
                raise ValueError('Select a research TOML declaration file')
            if path.exists() or path.is_symlink():
                c.regular(path, limit=256000)
            text, mode = form['config_text'], form['mode']
            if mode not in {'load', 'check', 'save', 'prepare'}:
                raise ValueError('Select an explicit TOML action')
            if not text or mode == 'load':
                text = c.regular(path, limit=256000).read_text(encoding='utf-8')
            if len(text.encode()) > 256000:
                raise ValueError('Research TOML exceeds its byte budget')
            result = dict(status='config_loaded')
            if mode in {'check', 'prepare'}:
                # Draft editing remains available before the declaration
                # is valid. Only check/prepare enter scientific validation.
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.toml', dir=path.parent) as source:
                    source.write(text); source.flush()
                    options = research_config.load(source.name)
                checked = research_config.prepare(**options, validate_only=True)
                result = public_output.settings_view(checked)
                result['source'] = 'research_config'
            if mode == 'save':
                self._save_toml(path, text)
                result = dict(status='config_saved')
            elif mode == 'prepare':
                prepared = research_config.prepare(**options)
                selected = Path(options['root']).resolve()
                self._register_instance(selected, prepared)
                result = dict(status='prepared', goal_id=prepared['goal_id'])
            return self._configuration_page(browser_token, text=text, path=str(path), result=result)
        if action in {'start', 'resume', 'continue', 'pause', 'interrupt', 'budget'}:
            receipt = self.control_result or {}
            result = receipt.get('result') or {}
            if receipt.get('status') == 'failed' or result.get('status') in {
                    'native_stopped', 'native_outcome_unsealed', 'seal_pending', 'output_repair_required',
                    'output_repair_unavailable'}:
                raise ui_errors.ControlFailure('Control receipt failure: ' + json.dumps(receipt, ensure_ascii=False))
        return 303, b'', {'Location': location}


def serve(root, *, secret_file, port=None, instance_roots=(), workspace=False, multi_workspace=False,
          approval_host=True, translation_config=None, on_ready=None, stop_event=None):
    root = Path(root).resolve()
    options = dict(workspace=True) if workspace else {}
    ui = InstanceUI(root, secret_file=secret_file, instance_roots=instance_roots,
        multi_workspace=multi_workspace, approval_host=approval_host, translation_config=translation_config, **options)
    if port is None:
        port = 0 if workspace else display.settings(root).get('ui', {}).get('port', 0)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Never copy capability URLs/cookies into logs.

        def handle_request(self, method):
            body=None
            try:
                fields = ('Host', 'Cookie') + (('Origin', 'Content-Length', 'Content-Type') if method == 'POST' else ())
                if self.headers.get('Transfer-Encoding') or any(len(self.headers.get_all(name, [])) > 1 for name in fields):
                    raise PermissionError('Ambiguous request headers are not accepted')
                if method == 'GET':
                    result = ui.get(self.path, host=self.headers.get('Host'), cookie=self.headers.get('Cookie'))
                else:
                    length = self.headers.get('Content-Length')
                    # A transport form limit is not a record/history truncation.
                    if length is None or not length.isdecimal() or not 0 < int(length) <= 1048576:
                        raise ValueError('Decision form length is invalid')
                    result = ui.post(self.path, host=self.headers.get('Host'), origin=self.headers.get('Origin'),
                        cookie=self.headers.get('Cookie'), content_type=self.headers.get('Content-Type'),
                        body=self.rfile.read(int(length)))
                status, body, headers = result
                if isinstance(body,(science.Original, display.Original)):
                    body.check()
            except Exception as error:
                if isinstance(body, (science.Original, display.Original)):
                    body.close()
                status, body, headers = ui._error_response(error, self.path,
                    record=method == 'POST', json_response=(self.headers.get('Content-Type') == 'application/json'
                        or '/translation/state' in self.path.partition('?')[0]
                        or self.path.partition('?')[0].endswith('/session')
                        or '/events' in self.path.partition('?')[0]))
            try:
                self.send_response(status)
                self.send_header('Content-Type', headers.pop('Content-Type', 'text/html; charset=utf-8'))
                # Native forms retain their exact same-origin Origin in Chrome.
                # Capability-entry documents override this to no-referrer.
                for key, value in {'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
                        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy':
                        render.CSP, **headers}.items():
                    self.send_header(key, value)
                if isinstance(body, live.Stream):
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.close_connection = True
                    body.write(self.wfile)
                    return
                length=body.size if isinstance(body,(science.Original, display.Original)) else body.stat().st_size if isinstance(body,Path) else len(body)
                self.send_header('Content-Length',str(length))
                self.end_headers()
                if isinstance(body,(science.Original, display.Original)):
                    body.copy_to(self.wfile)
                elif isinstance(body,Path):
                    with c.regular(body).open('rb') as stream:
                        shutil.copyfileobj(stream,self.wfile)
                else:
                    self.wfile.write(body)
            finally:
                if isinstance(body,(science.Original, display.Original)):body.close()

        def do_GET(self):
            self.handle_request('GET')

        def do_POST(self):
            self.handle_request('POST')

    with _host_lock(root, workspace=workspace), ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        ui.host = '127.0.0.1:' + str(server.server_port)
        ui._serving = True
        ui._refresh_approval_host()
        endpoint = dict(status='ui_listening', instance=ui.instance,
            management_url='http://' + ui.host + '/manage?token=' + ui.management_token,
            read_url='http://' + ui.host + '/view?token=' + ui.read_token)
        watcher_stop = threading.Event()
        def watch_stop():
            while not watcher_stop.wait(.1):
                if stop_event.is_set():
                    server.shutdown()
                    return
        watcher = None
        try:
            if on_ready is None:
                print(json.dumps(endpoint, ensure_ascii=False), flush=True)
            else:
                on_ready(endpoint)
            if stop_event is not None:
                watcher = threading.Thread(target=watch_stop, daemon=True, name='scid-ui-stop')
                watcher.start()
            server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt:
            pass
        finally:
            ui._serving = False
            watcher_stop.set()
            ui.live_stop.set()
            ui.translation.close()
            if watcher is not None:
                watcher.join(timeout=2)
            if ui._approval_endpoint_origin:
                (root / 'execution-authority/ui-endpoint.json').unlink(missing_ok=True)
    return dict(status='ui_stopped')
