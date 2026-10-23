"""Safe browser failures and bounded, private host diagnostics.

Exception text is private unless its producer has a deliberate public contract.
The fallback never reads instance settings, so damaged settings cannot break it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import threading
import traceback
import uuid

from scidiscovery.browser_access import BrowserAccessDenied
from scidiscovery.plugin_configuration import PluginConfigurationError
from . import public_output


@dataclass(frozen=True)
class Problem:
    status: int
    title: str
    cause: str
    hint: str
    field: str = ''
    href: str = '/'
    reference: str = ''
    code: str = 'ui_error'

    def payload(self):
        return dict(error=asdict(self))

    def markup(self):
        esc = html.escape
        return ('<section class="ui-error" role="alert" tabindex="-1"><h2>' + esc(self.title)
            + '</h2><p>' + esc(self.cause) + '</p><p><strong>处理方法：</strong>' + esc(self.hint)
            + '</p>' + ('<p>相关字段：<code>' + esc(self.field) + '</code></p>' if self.field else '')
            + ('<p class="note">错误编号：<code>' + esc(self.reference) + '</code></p>' if self.reference else '')
            + '<a href="' + esc(self.href, quote=True) + '">返回相关页面</a></section>')


class ControlFailure(RuntimeError):
    """A durable receipt reports failure, even if its transport was accepted."""


class InputError(ValueError):
    """A host-authored field failure; never pass submitted text as its message."""

    def __init__(self, field, public_message, hint):
        self.field = field
        self.public_message = public_message
        self.hint = hint
        super().__init__(public_message)


_LOCK = threading.Lock()
_MAX_RECORDS = 32
_MAX_BYTES = 16384


def _diagnostic(root, error, context, reference):
    """No URLs, cookies, posted inputs or traceback locals are collected."""
    directory = Path(root) / '.scid-ui' / 'errors'
    try:
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        with _LOCK:
            parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                try:
                    os.mkdir('errors', mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                descriptor = os.open('errors', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            try:
                os.fchmod(descriptor, 0o700)
                names = sorted((name for name in os.listdir(descriptor) if re.fullmatch(r'[0-9a-f]{32}\.json', name)),
                    key=lambda name: os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mtime_ns)
                for name in names[:max(0, len(names) - _MAX_RECORDS + 1)]:
                    os.unlink(name, dir_fd=descriptor)
                parts, remaining = [], 12000
                for line in traceback.TracebackException.from_exception(error, limit=20, capture_locals=False).format():
                    parts.append(line[:remaining]); remaining -= len(parts[-1])
                    if remaining <= 0:
                        break
                value = dict(reference=reference, time=datetime.now(timezone.utc).isoformat(),
                    context=context[:100], exception=type(error).__name__[:200], traceback=''.join(parts))
                # Bound encoded bytes as well as characters, including non-ASCII.
                while len((data := json.dumps(value, ensure_ascii=False).encode())) > _MAX_BYTES:
                    value['traceback'] = value['traceback'][:len(value['traceback']) // 2]
                fd = os.open(reference + '.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=descriptor)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
        return True
    except Exception:
        # An unwritable/corrupt diagnostic directory must not recurse through
        # the request boundary or replace the original public failure.
        return False


def record(root, error, *, context='translation'):
    """Return a saved private diagnostic reference, or None; never retry work."""
    reference = uuid.uuid4().hex
    return reference if _diagnostic(root, error, context, reference) else None


# Only exact host-authored messages are matched; dynamic exception summaries,
# plugin messages and Pydantic input reprs never become public text.
_KNOWN = {
    'Preparation requires a fresh directory': ('新实例目录已存在', '选择尚不存在的新实例目录；加载已有研究请使用“打开已有研究”。', 'research.root'),
    'Explicit objective file and public directory are required': ('任务或材料目录无效', '选择存在的任务原件和公开材料目录。', 'research.objective / research.public_root'),
    'The objective must contain original user text': ('研究任务为空', '填写非空的任务原文，或选择包含任务原文的文件。', 'research.objective'),
    'The objective text exceeds the 256000-byte limit': ('研究任务过大', '将任务原文缩减到 256000 字节以内。', 'task_text'),
    'Execution config conflicts with external launcher-owned execution': ('实验执行归属冲突', '使用本地插件时选择本地实验；外部启动器执行时移除本地执行器配置。', 'executor.mode'),
    'Execution authority requires the explicit formal review contract': ('实验授权缺少复核合同', '启用正式独立复核合同后再检查实验授权配置。', 'policies.formal_review'),
    'This research loop already has a running controller': ('研究控制器正在运行', '在研究过程页核对当前工作，等待原请求完成。', ''),
    '请先检查并预览本次研究配置。': ('配置尚未确认', '请先检查配置并进入确认步骤。', 'selection'),
    'Select a research TOML declaration file': ('配置文件选择无效', '请选择 TOML 配置文件；操作密钥不能作为配置导入。', 'config_path'),
    'Select a research task file': ('任务原件选择无效', '请选择研究任务原件；操作密钥不能作为任务原件。', 'research.objective'),
    'Expected an original regular file': ('文件类型不受支持', '请选择普通文件，并检查是否使用了符号链接。', ''),
    'File exceeds its declared byte budget': ('文件超过大小上限', '请缩小所选文件后重新提交。', ''),
    'Research TOML exceeds its byte budget': ('配置文件过大', '配置文件不能超过 256000 字节。', 'config_path'),
    '配置已变化，请重新加载研究配置。': ('配置原件已变化', '重新导入配置，再检查并确认创建。', 'config_path'),
    '任务原文已变化，请重新检查并预览。': ('任务原件已变化', '重新检查任务并预览，再确认创建。', 'research.objective'),
    'The selected backend requires an explicit model': ('模型尚未填写', '为所选后端填写模型名称。', 'agent.model'),
    'The selected standard native launcher is unavailable': ('研究启动命令不可用', '检查本机启动器安装，或在模型设置中指定可用的启动命令。', 'agent.launcher'),
    'An explicit operator authorization note is required': ('预算变更缺少原因', '填写本次预算调整的授权原因。', 'authorization_note'),
    'Specify a revised token, seconds or step ceiling': ('未填写新的预算上限', '至少填写 tokens、时间或阶段的一项新上限。', 'max_tokens / max_seconds / max_steps'),
    'max_tokens must be a positive integer': ('tokens 上限无效', '填写大于 0 的整数。', 'max_tokens'),
    'max_seconds must be positive and finite': ('时间上限无效', '填写大于 0 的有限秒数。', 'max_seconds'),
    'max_steps must be a positive integer': ('阶段上限无效', '填写大于 0 的整数。', 'max_steps'),
    'A step ceiling amendment requires the original operator statement': ('阶段变更缺少授权陈述', '填写调整阶段上限所需的原始授权陈述。', 'authorization_statement'),
    'The original operator statement must be nonempty': ('原始授权陈述为空', '填写调整阶段上限的原始授权陈述。', 'authorization_statement'),
    'Budget amendment requires no running or unknown original execution': ('当前不能调整预算', '先在研究过程页面核对正在运行或结果未知的原任务，不要重复执行。', ''),
    'Correction acknowledgment requires no active or unknown original': ('当前不能继续研究', '先核对正在运行或结果未知的原任务，再提交继续请求。', ''),
    'An exact prepared node is required': ('无法继续所选阶段', '在研究过程页面选择已有的准确阶段。', 'step'),
    'Correction acknowledgment needs an explicit operator decision': ('继续研究缺少说明', '填写继续本次研究的明确授权说明。', 'authorization_note'),
    'Correction acknowledgment requires the exact current published pause trigger': ('暂停条件已变化', '刷新总览，核对当前暂停原因后重新提交。', ''),
    'Translation model is required when enabled.': ('翻译模型尚未填写', '填写翻译模型名称，或关闭翻译。', 'model'),
    'Translation timeout_seconds must be between 1 and 300.': ('翻译超时设置无效', '填写 1 到 300 之间的整数秒数。', 'timeout_seconds'),
    'Translation backend must be codex or protocol.': ('翻译后端无效', '选择 Codex 或协议启动器。', 'backend'),
    'Translation protocol backend requires launcher_argv.': ('协议启动器命令为空', '为协议后端填写非空的 JSON 命令参数数组。', 'launcher_argv'),
    'Translation launcher_argv must be a nonempty bounded argument list.': ('翻译命令参数无效', '填写由非空字符串组成的 JSON 数组。', 'launcher_argv'),
    'Configure and enable an application translation model first.': ('尚未启用翻译模型', '打开阅读与翻译，填写并启用翻译模型后再点击翻译。', 'model'),
    'Translation cache could not be read.': ('翻译缓存读取失败', '检查工作台目录的读写权限和剩余磁盘空间。', ''),
    'Translation cache could not be saved.': ('翻译缓存保存失败', '检查工作台目录的读写权限和剩余磁盘空间。', ''),
    'Translation settings could not be saved.': ('阅读设置保存失败', '检查工作台目录的读写权限和剩余磁盘空间。', ''),
}

_REQUEST_ERRORS = {
    'Decision form length is invalid', 'Unknown translation action', 'Translation request is too large',
    'Reading settings form required', 'Reading settings contain invalid or duplicate fields',
    'Translation JSON required', 'Exact translation request required', 'Exact instance control or decision form required',
    'Control form contains missing or duplicate fields', 'Decision form contains missing or duplicate fields',
    'Exact execution decision form required', 'Exact explicit instance route required', 'Unknown instance control',
    'Select an explicit configuration action', 'Select an explicit TOML action',
    'Exact reading settings route required', 'Exact live workbench route required', 'Exact workspace page required',
    'Exact browser session route required',
    'Exact new research page required', 'Exact instance page required', 'Exact progress page required',
    'Exact final result page required', 'Exact scientific result continuation required',
    'Exact sealed scientific result required', 'Exact node continuation required', 'Invalid node continuation',
    'Exact instance node required', 'Exact progress continuation required', 'Exact progress attempt required',
    'Exact scientific deliverable continuation required', 'Exact scientific deliverable index required',
    'Exact request continuation required', 'Invalid request continuation', 'Select an explicitly opened instance',
}


def present(error, root, *, context='', settings=None, record_diagnostic=False, reference=''):
    href = {'configuration': '/new', 'load-config': '/new', 'open': '/instances', 'budget': '/#budget',
        'translation': '/reading', 'control': '/operations'}.get(context, '/')
    if isinstance(error, BrowserAccessDenied):
        return Problem(403, '浏览会话已失效或入口无效', '未能验证本次本地浏览会话。',
            '回到启动工作台的终端，打开本次输出的 management_url 完整链接，并允许本地 Cookie。', code='browser_session_invalid')
    if isinstance(error, PermissionError) and error.errno is None:
        return Problem(403, '浏览操作未获允许', '管理权限、同源检查或表单安全校验未通过。',
            '从本次管理入口重新打开相关页面，再提交表单；只读入口不能修改研究。', href=href, code='access_denied')
    if isinstance(error, FileExistsError):
        return Problem(409, '配置文件已存在', '保存仅创建新文件，已有文件和符号链接不会被覆盖。',
            '选择新的 TOML 路径，或下载配置。', field='save_path', href=href, code='file_exists')
    if isinstance(error, FileNotFoundError):
        title = '配置文件不存在' if context == 'load-config' else '实例目录或记录不存在' if context == 'open' else '所需文件不存在'
        return Problem(404, title, '未找到此次操作需要的文件或目录。', '检查所填路径和文件是否存在，再重新提交。',
            field='config_path' if context == 'load-config' else 'root' if context == 'open' else '', href=href, code='file_missing')
    if isinstance(error, (PermissionError, IsADirectoryError, NotADirectoryError)):
        return Problem(403 if isinstance(error, PermissionError) else 422, '无法访问所选文件或目录',
            '文件类型或本机读写权限不符合此次操作要求。', '检查路径类型及工作台进程的读写权限。', href=href)
    try:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else ''
    except Exception:
        message = ''
    clean = public_output.cleaner(root, settings={} if settings is None else settings)
    if isinstance(error, PluginConfigurationError):
        return Problem(422, '实验执行器配置有误', clean(error.public_message, 1000), clean(error.hint, 1000),
            clean(error.field_path, 200), href, code=error.code)
    if isinstance(error, InputError):
        return Problem(422, '表单字段无效', error.public_message, error.hint, error.field, href, code='invalid_field')
    if message in _REQUEST_ERRORS or isinstance(error, UnicodeDecodeError):
        return Problem(400, '请求格式有误', '请求包含缺少、重复或格式不正确的字段。',
            '重新打开相关页面，使用页面中的表单或链接提交。', href=href, code='invalid_request')
    if message in {'Unknown instance UI path', 'This instance node does not exist', 'Unknown request original'}:
        return Problem(404, '页面或记录不存在', '未找到所选页面或研究记录。', '返回研究过程或工作台选择已有记录。', href=href)
    if context == 'translation':
        for field in ('model', 'target_language', 'reasoning_effort', 'service_tier'):
            if message == 'Translation ' + field + ' must be a nonempty bounded string.':
                return Problem(422, '阅读设置字段无效', '此字段必须为非空且长度受限的文本。',
                    '检查此字段是否为空、过长或含有控制字符。', field, href)
    if context in {'configuration', 'load-config'} and isinstance(error, ValueError):
        from . import configuration_form
        for field in configuration_form.FIELDS:
            if message in {field.path + ' is required', field.path + ' must be a nonempty path string',
                    field.path + ' must be an array of paths'}:
                return Problem(422, '请检查' + field.label, '此字段缺少内容或格式不正确。',
                    '补全此字段并检查类型后重新提交。', field.path, href)
    if context == 'configuration' and isinstance(error, ValueError):
        from . import configuration_form
        if isinstance(error, configuration_form.FormError):
            for field in configuration_form.FIELDS:
                value = error.draft.get('document', {})
                for part in field.path.split('.'):
                    value = value.get(part) if isinstance(value, dict) else None
                if isinstance(value, configuration_form._Invalid):
                    return Problem(422, '请检查' + field.label, '此字段未通过类型检查。',
                        '填写有效的数字、布尔值或选项后重新检查配置。', field.path, href, code='invalid_field')
            return Problem(422, '表单参数类型不正确', '一个或多个参数未通过类型检查。',
                '检查表单中标记无效的参数，填写正确的数字、布尔值或选项后重新检查。', href=href, code='invalid_field')
        for field in configuration_form.FIELDS:
            if message in {field.label + '：必填', field.label + '：请选择支持的选项',
                    field.label + '：导入类型不正确', field.label + '：导入值必须为文本数组'}:
                return Problem(422, '请检查' + field.label, message, '修改此字段后重新检查配置。', field.path, href)
        if message in {'任务原文：必填', '已有任务原件路径：必填'}:
            return Problem(422, '研究任务尚未填写', message, '填写任务原文，或选择已有任务原件。',
                'task_text / research.objective', href)
    if context in {'configuration', 'load-config'} and isinstance(error, ValueError) and message.startswith('Invalid research TOML: '):
        return Problem(422, '配置文件 TOML 格式无效', '配置文件未通过 TOML 语法解析。',
            '检查引号、数组和表声明的语法，再重新导入配置。', 'config_path', href)
    known = _KNOWN.get(message) if isinstance(error, (ValueError, RuntimeError)) else None
    if known:
        title, hint, field = known
        return Problem(409 if isinstance(error, RuntimeError) else 422, title, title + '。', hint, field, href,
            code='operation_blocked' if isinstance(error, RuntimeError) else 'invalid_field')
    if isinstance(error, json.JSONDecodeError) and context == 'translation':
        return Problem(422, '翻译命令 JSON 无效', '命令参数不是有效的 JSON 数组。',
            '使用双引号包围每个参数，例如 ["/path/to/launcher"]。', 'launcher_argv', href)
    saved = False
    if record_diagnostic:
        reference = uuid.uuid4().hex
        saved = _diagnostic(root, error, context, reference)
    title = '研究控制请求失败' if isinstance(error, ControlFailure) else '工作台内部错误'
    hint = ('保留此错误编号，在本机检查工作台诊断记录；核对原操作状态后再决定下一步，勿重复提交。'
        if record_diagnostic else '核对保存的原记录与原操作编号后再决定下一步，勿重复提交。')
    if record_diagnostic and not saved:
        hint += ' 诊断未能保存，请检查工作台目录写入权限。'
    return Problem(409 if isinstance(error, ControlFailure) else 500, title,
        '此次操作未完成，详细原因已交由本机诊断记录。' if saved else '此次操作未完成；请核对原记录。', hint,
        href=href, reference=reference, code='control_failed' if isinstance(error, ControlFailure) else 'internal_error')
