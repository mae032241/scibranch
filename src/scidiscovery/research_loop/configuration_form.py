"""Typed, lossless host form for the shared research declaration.

No configuration file is required. Omission is meaningful: this module never
supplies preparation defaults and never starts research or reads credentials.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, time
from html import escape
import json
import math
from pathlib import Path

from . import research_config, skill_supply, backends


@dataclass(frozen=True)
class Field:
    path: str
    label: str
    kind: str = 'string'
    choices: tuple = ()
    required: bool = False
    placeholder: str = ''
    description: str = ''


# Every declaration leaf is here, or inside an explicitly structured extension.
FIELDS = [
    Field('version', '声明版本', 'integer', placeholder='1'),
    Field('research.root', '新实例目录', required=True),
    Field('research.objective', '已有任务原件路径'),
    Field('research.public_root', '公开材料目录', required=True),
    Field('research.user_updates', '已有全局补充原件', 'strings'),
    Field('research.source_restrictions', '来源限制声明', 'array'),
    Field('project.base_root', '已有工程的准确 Git 根目录'),
    Field('project.editable', '允许修改的工程相对目录', 'strings'),
    Field('project.scratch', '临时工作相对目录', 'strings'),
    Field('project.outputs', '工程输出相对目录', 'strings'),
    Field('skills.sets', '可选择的研究技能集合', 'table'),
    Field('agent.backend', 'Agent 后端', choices=backends.names(), placeholder=backends.DEFAULT_BACKEND),
    Field('agent.launcher', '启动命令参数（逐项）', 'strings'),
    Field('agent.model', '全局模型', placeholder=research_config.DEFAULT_MODEL),
    Field('agent.reasoning_effort', '全局推理力度', placeholder=research_config.DEFAULT_REASONING_EFFORT),
    Field('agent.service_tier', '全局服务档位', placeholder=research_config.DEFAULT_SERVICE_TIER),
    *[Field(f'agent.roles.{role}.{key}', f'{role} 角色{label}')
      for role in 'HWAE' for key, label in [('model', '模型'), ('reasoning_effort', '推理力度'), ('service_tier', '服务档位'), ('worker', '命名研究者'), ('skill_set', '技能集合')]],
    Field('agent.workers', '命名研究者配置', 'table'),
    Field('agent.native', '后端原生选项', 'table'),
    Field('permissions.workspace', '文件系统权限', choices=('read-only', 'workspace-write', 'danger-full-access')),
    Field('permissions.network', '网络权限', 'boolean'),
    Field('permissions.web', '网页检索权限', choices=('disabled', 'cached', 'live')),
    Field('permissions.subagents', '子 Agent 权限', 'boolean'),
    Field('budget.max_tokens', '总 tokens 预算', 'integer', required=True),
    Field('budget.max_seconds', '总活动时间（秒）', 'number', required=True),
    Field('budget.max_steps', '总节点步数', 'integer', required=True),
    Field('budget.candidate_extra_slots', 'W 提名预览额外名额', 'integer'),
    Field('budget.portfolio_review_after_cycles', '组合复核周期数', 'integer'),
    *[Field(f'budget.node_targets.{role}.{key}', f'{role} 节点软目标：{label}', kind)
      for role in 'HWAE' for key, label, kind in [('max_observed_tokens', 'tokens', 'integer'), ('max_seconds', '秒', 'number')]],
    Field('budget.role_shares', '角色预算份额策略', 'table'),
    *[Field(f'budget.breadth.{key}', label, kind) for key, label, kind in [
        ('max_w_seconds', 'W 软目标秒数', 'number'), ('max_w_observed_tokens', 'W 软目标 tokens', 'integer'),
        ('max_requested_repairs', '请求修复次数', 'integer'), ('initial_calls', '首次探索调用数', 'integer')]],
    Field('budget.correction.review_after', '补正复核阈值', 'integer'),
    Field('budget.correction.pause_after', '补正暂停阈值', 'integer'),
    Field('executor.mode', '实验执行归属', choices=('local', 'external')),
    Field('executor.config.executor', '实验执行插件', placeholder='plugin:binding'),
    Field('executor.config.config_file', '插件配置文件', placeholder='./plugin.toml'),
    Field('executor.config.configuration', '插件内联配置（仅支持此方式的插件）', 'table'),
    Field('executor.resource_policy', '累计实验资源策略', 'table'),
    Field('delivery', '交付策略及 adapter 配置', 'table'),
    *[Field(f'policies.{key}', label, 'boolean') for key, label in [
        ('formal_review', '正式独立复核'), ('execution_authority', '实验执行授权合同'),
        ('candidate_audit', '候选审计'), ('decision_contract', '科学决策合同')]],
    Field('policies.worker_context', 'Worker 上下文', choices=('same_session', 'new_context')),
    Field('policies.selection', '选择策略', 'table'),
    Field('policies.resume', '原调用恢复策略', 'table'),
    Field('policies.history_observer', '历史观察器及插件配置', 'table'),
    Field('ui.port', '实例网页端口（0 为自动）', 'integer'),
    Field('ui.open_browser', '打开浏览器', 'boolean'),
    Field('ui.approval_secret_file', '宿主操作密钥文件引用（仅路径）'),
]


def _fields(backend_name):
    # Providers describe their own native controls. Unknown declarations remain
    # editable as a generic table and are rejected by preparation validation.
    provider_fields=backends.provider(backend_name).configuration_fields() if backend_name in backends.names() else ()
    result=[field for field in FIELDS if not provider_fields or field.path!='agent.native']
    result=[Field(field.path,field.label,field.kind,backends.names(),field.required,field.placeholder,
        field.description) if field.path=='agent.backend' else field for field in result]
    return result+[Field(**field) for field in provider_fields]


# These are the actual policy schemas; unknown imported keys remain in the
# expert tree. No value is inserted merely because this section is displayed.
GROUP_FIELDS = {
    'budget.role_shares': [
        Field('budget.role_shares.version', '份额规则版本', 'integer', placeholder='1'),
        Field('budget.role_shares.min_w_share', '为实验（W）保留的最低份额', 'number', placeholder='0 到 1 之间'),
        Field('budget.role_shares.max_thinking_share', '方向与复核（H / A）最高份额', 'number', placeholder='0 到 1 之间'),
        Field('budget.role_shares.thinking_node_target_tokens', '单次方向 / 复核的 tokens 目标', 'integer'),
    ],
    'policies.resume': [
        Field('policies.resume.max_attempts', '同一原调用最多恢复次数', 'integer', placeholder='1 或 2'),
        Field('policies.resume.protocol', '恢复协议'),
        Field('policies.resume.usage_scope', '用量计量范围', choices=('turn',)),
        Field('policies.resume.output_repair', '允许修复输出格式', 'boolean'),
        Field('policies.resume.output_only', '恢复时只修复输出', 'boolean'),
        Field('policies.resume.native_home', '冻结的原生主目录（绝对路径）'),
        Field('policies.resume.automatic_recovery.enabled', '允许有界自动接续当前任务', 'boolean', placeholder='依后端能力与声明决定'),
        Field('policies.resume.automatic_recovery.max_continues', '提前停止的 continue 上限（次）', 'integer', placeholder='2'),
        Field('policies.resume.automatic_recovery.max_retries', '临时服务故障 retry 上限（次）', 'integer', placeholder='3'),
        Field('policies.resume.automatic_recovery.initial_backoff_seconds', '首次服务退避（秒）', 'number', placeholder='30'),
        Field('policies.resume.automatic_recovery.max_backoff_seconds', '单次服务退避上限（秒）', 'number', placeholder='300'),
        Field('policies.resume.automatic_recovery.jitter_fraction', '服务退避抖动比例（0–1）', 'number', placeholder='0.1'),
        Field('policies.resume.automatic_recovery.max_wait_seconds', '累计服务等待上限（秒）', 'number', placeholder='1800'),
    ],
}
ROLE_NAMES = dict(H='研究方向', W='实验与实现', A='综合评估', E='证据检索与提取')
ROLE_HELP = dict(H='提出或修订研究假设与方向。', W='在已选假设下设计、实现并运行有界实验。',
    A='独立评估证据与候选方案，决定下一步研究。', E='按明确问题检索或提取证据，保留来源与缺失信息。')


def _get(document, path, default=None):
    for key in path.split('.'):
        if not isinstance(document, dict) or key not in document:
            return default
        document = document[key]
    return document


def _put(document, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        if key not in document:
            document[key] = {}
        if not isinstance(document[key], dict):
            raise ValueError(f'{path}：父字段必须为对象')
        document = document[key]
    document[keys[-1]] = value


def _delete(document, path):
    keys = path.split('.')
    if len(keys) == 1:
        document.pop(keys[0], None)
    elif isinstance(document.get(keys[0]), dict):
        _delete(document[keys[0]], '.'.join(keys[1:]))
        if not document[keys[0]]:
            del document[keys[0]]


def blank(*, base):
    return dict(document={}, task_mode='text', task_text='', base=str(Path(base).resolve()))


def imported(document, *, base):
    if _get(document, 'research.objective') is not None and _get(document, 'research.objective_text') is not None:
        raise ValueError('任务输入：已有原件与任务原文只能选择一项')
    result = blank(base=base)
    result['document'] = copy.deepcopy(document)
    inline = _get(document, 'research.objective_text')
    result['task_mode'] = 'text' if inline is not None else 'file'
    result['task_text'] = inline if inline is not None else ''
    return result


def declaration(draft):
    document = copy.deepcopy(draft['document'])
    if draft['task_mode'] == 'text':
        _delete(document, 'research.objective')
        _put(document, 'research.objective_text', draft['task_text'])
    else:
        _delete(document, 'research.objective_text')
    return document


def load(path):
    path = Path(path).expanduser().resolve()
    return imported(research_config.read_declaration(path), base=path.parent)


def _single(fields, key, default=''):
    value = fields.get(key, default)
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError(f'{key}：字段不能重复')
        value = value[0]
    if not isinstance(value, str):
        raise ValueError(f'{key}：字段必须为文本')
    return value


def _typed(value, kind, label):
    try:
        if kind == 'boolean':
            if value not in ('true', 'false'):
                raise ValueError()
            return value == 'true'
        if kind == 'integer':
            return int(value) if value.strip() and all(c in '+-0123456789' for c in value) else _invalid()
        if kind == 'number':
            result = float(value)
            if not math.isfinite(result):
                raise ValueError()
            return result
        if kind == 'datetime':
            return datetime.fromisoformat(value)
        if kind == 'date':
            return date.fromisoformat(value)
        if kind == 'time':
            return time.fromisoformat(value)
        if kind == 'string':
            return value
    except (ValueError, OverflowError):
        raise ValueError(f'{label}：请输入有效的{dict(integer="整数", number="有限数字", boolean="布尔值").get(kind, kind)}') from None
    raise ValueError(f'{label}：不支持的类型')


def _invalid():
    raise ValueError()


class FormError(ValueError):
    """Recoverable typed entry error with every edited control retained."""
    def __init__(self, message, draft):
        super().__init__(message)
        self.draft = draft


class _Invalid(str):
    def __new__(cls, value, kind):
        item = super().__new__(cls, value)
        item.kind = kind
        return item

    def __getnewargs__(self):
        return str(self), self.kind


def _role_skill_items(document, role):
    """Resolve display state only; declaration validation remains authoritative."""
    profile = _get(document, f'agent.roles.{role}', {})
    if not isinstance(profile, dict):
        return []
    workers = _get(document, 'agent.workers', {})
    worker_name = profile.get('worker')
    worker = workers.get(worker_name, {}) if isinstance(workers, dict) and isinstance(worker_name, str) else {}
    name = profile.get('skill_set', worker.get('skill_set') if isinstance(worker, dict) else None)
    sets = _get(document, 'skills.sets', {})
    selected = sets.get(name, {}) if isinstance(sets, dict) and isinstance(name, str) else {}
    items = selected.get('items', []) if isinstance(selected, dict) else []
    return items if isinstance(items, list) and all(isinstance(item, str) for item in items) else []


def _skill_picker(fields, document, *, fixed_sets=None):
    """Translate successful controls into the existing named-set schema."""
    sets = _get(document, 'skills.sets', {}) if fixed_sets is None else fixed_sets
    available_sets = set(sets) if isinstance(sets, dict) else set()
    if fixed_sets is None:
        for section in ('agent.roles', 'agent.workers'):
            profiles = _get(document, section, {})
            if isinstance(profiles, dict):
                available_sets.update(profile['skill_set'] for profile in profiles.values()
                    if isinstance(profile, dict) and isinstance(profile.get('skill_set'), str))
    catalog_names = None
    def bind_items(role, items):
        nonlocal sets
        if fixed_sets is not None:
            raise ValueError('已有实例只能选择已冻结的技能集合，不能增加技能集合')
        if not isinstance(sets, dict):
            raise ValueError('研究技能集合必须为对象')
        name = next((name for name, value in sets.items() if value == dict(items=items)), None)
        if name is None:
            prefix = 'web-'+role.lower()
            name = prefix
            number = 2
            while name in available_sets:
                name = prefix+'-'+str(number)
                number += 1
            _put(document, 'skills.sets.'+name, dict(items=items))
            sets = _get(document, 'skills.sets')
            available_sets.add(name)
        _put(document, f'agent.roles.{role}.skill_set', name)
    for role in 'HWAE':
        choice = _single(fields, 'skill_picker.choice.'+role, 'keep')
        baseline_raw = _single(fields, 'skill_picker.baseline.'+role, '[]')
        try:
            baseline = json.loads(baseline_raw) if len(baseline_raw) <= 65536 else None
        except (ValueError, TypeError, RecursionError):
            baseline = None
        if (not isinstance(baseline, list) or len(baseline) > 256
                or any(not isinstance(item, str) for item in baseline) or len(set(baseline)) != len(baseline)):
            raise ValueError('研究技能：勾选基线无效，请重新打开表单')
        prefix = 'skill_picker.item.'+role+'.'
        selected = [_single(fields, name) for name in fields if name.startswith(prefix)]
        if len(selected) > 256 or len(set(selected)) != len(selected):
            raise ValueError('研究技能：勾选项不能重复或超过上限')
        changed = set(selected) != set(baseline)
        if choice == 'keep' and not changed:
            continue
        if choice == 'inherit':
            _delete(document, f'agent.roles.{role}.skill_set')
        elif choice.startswith('set:'):
            name = choice[4:]
            if name not in available_sets:
                raise ValueError('研究技能：请选择已有技能集合')
            _put(document, f'agent.roles.{role}.skill_set', name)
        elif choice == 'none':
            bind_items(role, [])
        elif choice in ('keep', 'custom'):
            if fixed_sets is not None:
                raise ValueError('已有实例只能选择已冻结的技能集合，不能重新勾选安装技能')
            if catalog_names is None:
                catalog_names = {item['name'] for item in skill_supply.catalog()['items']}
            if set(selected) - catalog_names:
                raise ValueError('研究技能：勾选的技能已不可用，请重新打开表单查看安装诊断')
            bind_items(role, sorted(selected))
        else:
            raise ValueError('研究技能：请选择支持的角色分配方式')


def parse(fields, *, base, _fixed_skill_sets=None):
    """Parse only typed visible controls, retaining all extra imported data."""
    selected_backend=_single(fields,'cfg.agent.backend') or backends.DEFAULT_BACKEND
    rendered_backend=_single(fields,'rendered_backend',selected_backend)
    form_fields = _fields(rendered_backend)
    saved_native=_single(fields,'native_drafts','{}')
    try:
        native_drafts=json.loads(saved_native) if len(saved_native)<=256_000 else None
    except (ValueError,TypeError,RecursionError):
        native_drafts=None
    if (not isinstance(native_drafts,dict) or len(native_drafts)>64
            or any(not isinstance(name,str) or not name or not isinstance(value,(dict,type(None)))
                for name,value in native_drafts.items())):
        raise ValueError('后端原生设置草稿无效，请重新打开配置表单')
    scalar_fields = form_fields + [field for group in GROUP_FIELDS.values() for field in group]
    allowed = {'present.'+f.path for f in scalar_fields} | {'cfg.'+f.path for group in GROUP_FIELDS.values() for f in group} | {'task_mode', 'task_text', 'rendered_backend', 'native_drafts'} | {'cfg.'+f.path for f in form_fields if f.kind not in ('table', 'array', 'strings')} | {'enabled.'+f.path for f in form_fields if f.kind in ('table', 'array', 'strings')}
    for name in fields:
        parts = name.split('.')
        picker = (len(parts) == 3 and parts[0] == 'skill_picker' and parts[1] in {'choice', 'baseline'} and parts[2] in 'HWAE' and len(parts[2]) == 1) or (len(parts) == 4 and parts[:2] == ['skill_picker', 'item'] and parts[2] in 'HWAE' and len(parts[2]) == 1 and parts[3].isdecimal())
        if name not in allowed and not picker and not any(name == 'cfg.'+f.path or (name.startswith('cfg.'+f.path+'.') and name[len('cfg.'+f.path+'.'):].isdecimal()) for f in form_fields if f.kind == 'strings') and not (len(parts) == 3 and parts[0] == 'node' and parts[1].isdecimal() and parts[2] in {'kind','parent','key','value'}):
            raise ValueError('不支持的表单字段：'+name)
    result = blank(base=base)
    result['task_mode'] = _single(fields, 'task_mode', 'text')
    if result['task_mode'] not in ('text', 'file'):
        raise ValueError('任务输入：请选择文本或已有原件')
    result['task_text'] = _single(fields, 'task_text')
    errors = []
    def typed(value, kind, label):
        try:
            return _typed(value, kind, label)
        except ValueError as error:
            errors.append(str(error))
            return _Invalid(value, kind)
    nodes = {}
    for name in fields:
        if name.startswith('node.') and name.endswith('.kind'):
            identity = name[5:-5]
            if not identity.isdecimal() or len(nodes) >= 2000:
                raise ValueError('结构字段数量或标识无效')
            prefix = f'node.{identity}.'
            nodes[identity] = dict(kind=_single(fields, prefix+'kind'),
                parent=_single(fields, prefix+'parent'), key=_single(fields, prefix+'key'),
                value=_single(fields, prefix+'value'))
    used = set()
    def tree(parent, kind, depth=0):
        if depth > 24:
            raise ValueError('结构字段嵌套超过上限')
        value = {} if kind == 'table' else []
        for identity, node in nodes.items():
            if node['parent'] != parent:
                continue
            if identity in used:
                raise ValueError('结构字段存在循环')
            used.add(identity)
            item = (tree(identity, node['kind'], depth+1) if node['kind'] in ('table', 'array')
                    else typed(node['value'], node['kind'], node['key'] or '数组项'))
            if kind == 'table':
                if not node['key'] or node['key'] in value:
                    raise ValueError('结构字段：键名必填且不能重复')
                value[node['key']] = item
            else:
                value.append(item)
        return value
    for field in form_fields:
        if field.kind == 'strings':
            prefix = 'cfg.'+field.path
            items = [_single(fields, name) for name in fields if name.startswith(prefix+'.')]
            addition = _single(fields, prefix)
            if addition:
                items.append(addition)
            if addition or _single(fields, 'enabled.'+field.path) == 'yes':
                _put(result['document'], field.path, items)
            continue
        if field.kind in ('table', 'array'):
            if _single(fields, 'enabled.'+field.path) == 'yes':
                value = tree('@'+field.path, field.kind)
                for child in GROUP_FIELDS.get(field.path, []):
                    raw = _single(fields, 'cfg.'+child.path)
                    if raw != '' or _single(fields, 'present.'+child.path) == 'yes':
                        key = child.path[len(field.path)+1:]
                        missing = object()
                        if _get(value, key, missing) is not missing:
                            raise ValueError(f'{child.label}：专用字段与扩展字段重复')
                        if child.choices and raw not in child.choices:
                            errors.append(f'{child.label}：请选择支持的选项')
                        _put(value, key, typed(raw, child.kind, child.label))
                _put(result['document'], field.path, value)
            continue
        raw = _single(fields, 'cfg.'+field.path)
        if raw != '' or _single(fields, 'present.'+field.path) == 'yes':
            if field.choices and raw not in field.choices:
                errors.append(f'{field.label}：请选择支持的选项')
            _put(result['document'], field.path, typed(raw, field.kind, field.label))
    # An explicit extra tree preserves empty groups and arbitrary imported keys.
    extras = tree('@extra', 'table')
    def merge(target, extra):
        for key, value in extra.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                merge(target[key], value)
            elif key in target:
                raise ValueError(f'{key}：扩展字段与标准字段重复')
            else:
                target[key] = value
    merge(result['document'], extras)
    # Disabled groups' children are deliberately omitted. Reject stray topology.
    allowed = {identity for identity, node in nodes.items() if node['parent'].startswith('@') and
               node['parent'][1:] in {f.path for f in form_fields if f.kind in ('table', 'array', 'strings')}}
    pending = set(allowed)
    while pending:
        parent = pending.pop()
        for identity, node in nodes.items():
            if node['parent'] == parent and identity not in allowed:
                allowed.add(identity)
                pending.add(identity)
    if set(nodes) - used - allowed:
        raise ValueError('结构字段没有有效的父对象')
    _skill_picker(fields, result['document'], fixed_sets=_fixed_skill_sets)
    if selected_backend!=rendered_backend:
        # Native extensions belong to the provider whose controls produced
        # them. Switching renders the new schema before any preparation call.
        native_drafts[rendered_backend]=_get(result['document'],'agent.native')
        _delete(result['document'],'agent.native')
        restored=native_drafts.pop(selected_backend,None)
        if restored is not None:_put(result['document'],'agent.native',restored)
        result['native_drafts']=native_drafts
        errors.append('后端已切换；原生设置按原后端保留。请检查当前后端的设置后再次提交。')
    elif native_drafts:
        result['native_drafts']=native_drafts
    if errors:
        raise FormError('；'.join(errors), result)
    return result


def parse_edit(fields, *, base, configuration):
    """Keep instance originals and application settings outside node editing."""
    if any(name in {'task_mode', 'task_text'} for name in fields):
        raise ValueError('任务原件不能在研究配置中修改')
    try:
        result = parse(fields, base=base, _fixed_skill_sets=_get(configuration, 'skills.sets', {}))
    except FormError as error:
        result = error.draft
        failure = error
    else:
        failure = None
    immutable = ('research', 'ui', 'version', 'project', 'skills')
    if any(key in result['document'] for key in immutable):
        raise ValueError('实例任务、材料原件、工程、技能集合和网页应用设置不能在研究配置中修改')
    for key in immutable:
        if key in configuration:
            result['document'][key] = copy.deepcopy(configuration[key])
    if failure:
        raise FormError(str(failure), result)
    return result


def _kind(value):
    if isinstance(value, _Invalid): return value.kind
    if isinstance(value, dict): return 'table'
    if isinstance(value, list): return 'array'
    if type(value) is bool: return 'boolean'
    if type(value) is int: return 'integer'
    if type(value) is float: return 'number'
    if isinstance(value, datetime): return 'datetime'
    if isinstance(value, date): return 'date'
    if isinstance(value, time): return 'time'
    if isinstance(value, str): return 'string'
    raise ValueError('声明包含不支持的字段类型')


def render(draft, *, edit=False):
    """Render a guided form; hidden steps retain successful, typed controls."""
    document = draft['document']
    form_fields=_fields(_get(document,'agent.backend',backends.DEFAULT_BACKEND))
    extras = copy.deepcopy(document)
    _delete(extras, 'research.objective_text')
    if edit:
        for key in ('research', 'ui', 'version', 'project', 'skills'):
            extras.pop(key, None)
    counter = [0]
    def tree(value, parent):
        contents = []
        items = value.items() if isinstance(value, dict) else enumerate(value)
        for key, item in items:
            counter[0] += 1
            identity = str(counter[0]); kind = _kind(item); prefix = 'node.'+identity+'.'
            scalar = str(item).lower() if type(item) is bool else (item.isoformat() if isinstance(item, (date,time)) else str(item))
            contents.append('<div class="config-node" data-node="'+identity+'" data-error-label="'+escape(str(key),quote=True)+'"'+(' data-invalid="true"' if isinstance(item, _Invalid) else '')+'>'
                '<input type="hidden" name="'+prefix+'parent" value="'+escape(parent, quote=True)+'">'
                '<label class="config-node-key">键名 <input name="'+prefix+'key" value="'+escape(str(key) if isinstance(value,dict) else '',quote=True)+'"></label>'
                '<label class="config-node-type">类型 <select name="'+prefix+'kind" class="config-kind">'+''.join(
                    '<option value="'+k+'"'+(' selected' if k==kind else '')+'>'+label+'</option>'
                    for k,label in [('string','文本'),('integer','整数'),('number','数字'),('boolean','布尔'),('table','对象'),('array','数组'),('datetime','日期时间'),('date','日期'),('time','时间')])+'</select></label>'
                '<label class="config-value">值 <input name="'+prefix+'value" value="'+escape(scalar if kind not in ('table','array') else '',quote=True)+'"></label>'
                '<button type="button" class="config-remove">删除此项</button>'
                '<div class="config-children" data-parent="'+identity+'">'+
                (tree(item, identity) if kind in ('table','array') else '')+'</div>'
                '<button type="button" class="config-add" data-parent="'+identity+'">添加子项</button></div>')
        return ''.join(contents)
    labels = {
        'research.root': '新研究保存到哪里', 'research.public_root': '研究可使用的材料目录',
        'research.objective': '任务文件路径', 'agent.model': '研究使用的模型',
        'agent.reasoning_effort': '留空时采用所选后端声明的默认推理力度。也可填写该模型和后端支持的其他值。',
        'budget.max_tokens': '模型用量（tokens）', 'budget.max_seconds': '活动时间（秒）',
        'budget.max_steps': '节点上限（步）', 'permissions.workspace': '文件读写范围',
        'permissions.network': '允许程序联网', 'permissions.web': '网页检索方式',
        'permissions.subagents': '允许创建子研究助手',
        'budget.max_output_item_bytes': '单项输出（字节）',
        'budget.max_output_total_bytes': '单节点输出合计（字节）',
        'budget.candidate_extra_slots': 'W 提名预览额外名额（个）',
        'budget.portfolio_review_after_cycles': '组合复核（周期）',
        'budget.breadth.max_w_seconds': 'W 时间目标（秒）',
        'budget.breadth.max_w_observed_tokens': 'W 用量目标（tokens）',
        'budget.breadth.max_requested_repairs': '最多请求修复（次）',
        'budget.breadth.initial_calls': '首次探索调用（次）',
        'budget.correction.review_after': '复核阈值（次）',
        'budget.correction.pause_after': '暂停阈值（次）',
        'budget.role_shares.version': '规则版本',
        'budget.role_shares.min_w_share': 'W 最低份额（0–1）',
        'budget.role_shares.max_thinking_share': 'H / A 最高份额（0–1）',
        'budget.role_shares.thinking_node_target_tokens': 'H / A 单次目标（tokens）',
    }
    hints = {
        'research.root': '将在服务器上新建此目录，保存任务、研究记录和结果。请选择尚不存在的目录；不能覆盖已有研究。',
        'research.public_root': '填写服务器上已存在的目录，放入允许研究使用的文件。即使暂时没有材料，也需先准备一个空目录。这里不会上传浏览器中的文件。',
        'research.objective': '读取服务器上的现有文本文件；原文会作为本次任务保存。',
        'agent.model': '留空时采用所选后端声明的默认模型；没有默认值的后端必须填写。各研究角色未单独指定时沿用此模型。',
        'agent.reasoning_effort': '留空时采用所选后端声明的默认推理力度。也可填写该模型和后端支持的其他值。',
        'agent.service_tier': '留空时采用所选后端声明的默认服务档位。也可填写该后端支持的其他值。',
        'budget.max_tokens': '单位：tokens，模型处理文本的用量，不是金额。必须由你填写；实际费用取决于模型及服务计价。',
        'budget.max_seconds': '单位：秒。累计研究活动时间，不是从创建起计算的日历时间；暂停等待不会自动恢复预算。',
        'budget.max_steps': '单位：步。约束研究节点及计入预算的恢复、操作、审计等工作；与时间、tokens 分别限制。',
        'permissions.workspace': '留空声明默认的可写工作目录（workspace-write）。这是后端原生权限；检查时会显示实际权限与限制。',
        'permissions.network': '留空默认不允许程序联网。与网页检索分别设置；实际能否限制由所选后端决定。',
        'permissions.web': '留空默认禁用网页检索。需要在线证据时，可按本次研究范围选择缓存或实时检索。',
        'permissions.subagents': '留空默认不允许创建子助手。这项控制角色内部再委派工作，与 H / W / A / E 分工不同。',
    }
    hints.update({
        'version': '当前声明格式版本为 1；新建时可留空。',
        'research.user_updates': '服务器上的原文文件路径，每项一个文件；用于保留你对全局任务的补充。留空且不声明列表时省略。',
        'research.source_restrictions': '声明材料原件的使用限制、定位和摘要。此项需要结构配置；无额外限制时保持未声明。',
        'project.base_root': '可选：已有普通 Git 工作树的准确绝对根目录，不支持 ~、相对路径或符号链接。创建时冻结 HEAD 身份；已有实例继续保留原工程。',
        'project.editable': '声明工程时必填非空列表；每项是相对工程根的目录，例如 src。不得含 .、..、.git，不得与其他声明目录交叠。',
        'project.scratch': '可选的临时工作目录，每项为规范相对目录；不与可修改目录或输出目录交叠。',
        'project.outputs': '可选的输出目录，每项为规范相对目录；保留原调用的输出采集与封存条件。',
        'skills.sets': '按集合名称填写对象，每个集合包含 items 文本数组。只有角色或命名研究者用 skill_set 明确选择该集合时才启用。插件提供的可选技能不会自动启用。',
        'agent.native': '可选：按所选后端说明声明原生扩展选项；键名、类型和相对路径由该后端验证。',
        'agent.workers': '命名研究者的模型和可选 skill_set 声明；技能集合名称必须来自本次研究已声明的集合。',
        'agent.backend': '留空使用默认后端。可选择已注册后端；检查时验证其模型、启动程序及能力声明。',
        'agent.launcher': '留空采用所选后端的默认启动命令；没有默认值时必须填写。第一项是可执行程序，之后每项是一个独立参数；不按空格拆分。',
        'budget.candidate_extra_slots': '单位：个，限制 W 提名预览中代表候选之外的额外名额，仅在启用搜索机制提名时使用；完整候选池仍保留。留空默认 2。',
        'budget.portfolio_review_after_cycles': '单位：研究周期，达到次数后提醒重新比较候选组合；不自动决定换路线或触发正式审核。留空不单独声明这项策略。',
        'budget.role_shares': '为实验保留用量，限制可选的方向讨论与复核占比。启用时须明确四项参数及全部角色阶段预算；不增加总预算。',
        'budget.role_shares.version': '当前规则要求版本 1。填写版本仅标识规则，不会自动补预算。',
        'budget.role_shares.min_w_share': '填 0 到 1 之间的小数，例如 0.65 表示 65%。与 H / A 最高份额相加不得超过 1。',
        'budget.role_shares.max_thinking_share': '填 0 到 1 之间的小数，例如 0.35 表示 35%。限制可选的 H / A 工作；必要复核仍按合同进行。',
        'budget.role_shares.thinking_node_target_tokens': '单位：tokens。设定一次 H / A 思考工作的目标；不替代角色阶段上限或总预算。',
        'budget.breadth.max_w_seconds': '单位：秒。一次 W 探索切片的时间软目标；自定义探索策略时需同时填写 tokens 和修复次数。',
        'budget.breadth.max_w_observed_tokens': '单位：tokens。一次 W 探索切片的用量软目标；留空整组时使用既有探索策略。',
        'budget.breadth.max_requested_repairs': '单位：次。限制一次探索中请求修复的次数；自定义策略时必须为正整数。',
        'budget.breadth.initial_calls': '单位：次。首次探索允许的已登记实验调用机会；可选，不能表示科学覆盖已完成。',
        'budget.correction.review_after': '单位：次。持续未取得进展达到此阈值时复核；需与暂停阈值一起填写。',
        'budget.correction.pause_after': '单位：次。补正达到此阈值时暂停；需与复核阈值一起填写。',
        'executor.mode': '留空采用所选后端声明的实验执行归属。指定执行插件时采用本地执行；检查页显示实际配置，实验仍需满足授权与插件条件。',
        'executor.config.executor': '填写已安装插件公开的绑定名称，例如 plugin:binding。创建配置不会运行实验或安装插件。',
        'executor.config.config_file': '服务器上的插件配置文件路径。通常优先使用插件提供的配置文件；相对路径按当前声明目录解析。',
        'executor.config.configuration': '仅对明确支持内联配置的插件使用。键名与类型以该插件说明为准。',
        'executor.resource_policy': '声明累计实验时间、存储等资源上限；需已配置实验执行授权与宿主执行插件。',
        'delivery': '仅需通过适配器交付结果时配置；涉及明确目标、输入、阶段预算和外部接口，按对应适配器说明导入。',
        'policies.formal_review': '是否声明正式独立复核合同。启用不会自动产生审核通过结论。',
        'policies.execution_authority': '是否要求实验执行授权合同；启用时也必须启用正式独立复核。此开关本身不授予执行许可。',
        'policies.candidate_audit': '是否要求候选审计；需同时配置带 audit 能力的隔离交付适配器。',
        'policies.decision_contract': '是否声明科学决策合同；研究结论仍由科学角色作出。',
        'policies.worker_context': 'same_session 保留同一会话，new_context 使用新上下文。此高级合同还需原调用恢复和全部角色阶段预算。',
        'policies.selection': '用于生产交付的候选选择合同；需已配置相应执行、观察与交付条件。',
        'policies.resume': '恢复已知原调用，不重新提交未知外部实验，不恢复已消耗预算。后端可为新配置声明有界恢复默认值；已有显式策略保持原义。自定义时需冻结启动程序及全部角色阶段预算。',
        'policies.resume.max_attempts': '仅支持 1 或 2 次。次数针对同一原调用；不是重新开始研究。',
        'policies.resume.protocol': '填写所选后端支持的原调用恢复协议；检查时验证协议与能力。',
        'policies.resume.usage_scope': '当前合同要求 turn：按一次调用计量用量，保留原始预算消耗。',
        'policies.resume.output_repair': '允许在有明确失败边界时恢复以修复输出格式；不豁免其他恢复条件。',
        'policies.resume.output_only': '启用需同时允许修复输出；此授权仅允许输出补填。自动接续当前未完成任务须由下方独立策略明确允许。',
        'policies.resume.native_home': '仅在“只修复输出”启用时使用，必须是规范绝对路径。这里只保存路径引用。',
        'policies.resume.automatic_recovery.enabled': '独立允许支持的后端在原会话接续未完成任务，以及原请求明确尚未开始时的有限启动重试。不会新开上下文、扩大权限或重新提交未知外部调用。已有显式策略留空不新增此授权。',
        'policies.resume.automatic_recovery.max_continues': '单位：次。同一任务的原 attempt 链中，提前停止后最多追加的同会话工作次数；与临时故障重试分别累计，推进和重启均不清零。0 禁用此类接续。',
        'policies.resume.automatic_recovery.max_retries': '单位：次。同一任务的原 attempt 链中，对明确可恢复的临时限流、容量或连接故障最多重试次数；永久错误和未知原因不重试，推进和重启均不清零。0 禁用此类重试。',
        'policies.resume.automatic_recovery.initial_backoff_seconds': '单位：秒。首次服务退避的有限时长；后续指数增长，仍受单次及累计等待上限约束。',
        'policies.resume.automatic_recovery.max_backoff_seconds': '单位：秒。一次服务退避的有限上限；不能小于首次退避，也不能大于累计服务等待上限。',
        'policies.resume.automatic_recovery.jitter_fraction': '填 0 到 1 之间的小数，例如 0.1 表示最多 10% 的退避抖动。',
        'policies.resume.automatic_recovery.max_wait_seconds': '单位：秒。本次恢复链累计服务退避上限；成功推进和重启不清零已保存值。停机及控制程序未运行的空闲不计入实际服务等待。',
        'policies.history_observer': '仅为已接入的宿主历史观察插件配置；参数与路径按插件说明填写。',
        'ui.port': '留空或 0 自动选择可用端口；指定时为 1 到 65535。',
        'ui.open_browser': '留空默认不开浏览器；在远程服务器通常保持默认。',
        'ui.approval_secret_file': '可选的宿主管理密钥文件路径。这里只填写路径，不读取或复制密钥内容。',
    })
    for role in 'HWAE':
        hints[f'agent.roles.{role}.worker'] = '可选：填写 agent.workers 中准确的命名研究者。角色显式模型字段和技能集合覆盖该研究者的对应配置。'
        hints[f'agent.roles.{role}.skill_set'] = '可选：填写已声明或由明确选中的插件提供的准确集合名称；留空沿用明确引用的命名研究者配置，未引用时不启用。'
        for key in ('model', 'reasoning_effort', 'service_tier'):
            hints[f'agent.roles.{role}.{key}'] = '留空沿用上方全局'+dict(model='模型', reasoning_effort='推理力度', service_tier='服务档位')[key]+'。支持的值取决于该角色选择的模型和后端。'
        for key, unit in [('max_observed_tokens', 'tokens'), ('max_seconds', '秒')]:
            hints[f'budget.node_targets.{role}.{key}'] = '单位：'+unit+'。'+ROLE_NAMES[role]+'单次工作的软目标；启用阶段预算时四个角色的两项都需填写，不能扩大总预算。'
    placeholders = {'research.root': '/srv/research/new-study', 'research.public_root': '/srv/research/materials',
                    'research.objective': '/srv/research/task.txt'}
    choice_labels = {'true':'允许', 'false':'不允许', 'read-only':'只读文件',
        'workspace-write':'可写工作目录', 'danger-full-access':'不限制文件访问',
        'disabled':'禁用网页检索', 'cached':'仅使用缓存', 'live':'实时检索',
        'local':'由本地执行插件执行', 'external':'由外部执行',
        'same_session':'沿用同一会话', 'new_context':'使用新上下文',
        'turn':'按单次调用计量'}

    hints.update({field.path:field.description for field in form_fields if field.description})

    def control(field, *, advanced=False, matrix=False):
        value = _get(document, field.path)
        if value is not None:
            _delete(extras, field.path)
        label = escape(labels.get(field.path, field.label))
        if field.path.startswith('budget.node_targets.'):
            label = '单次工作 tokens 目标' if field.path.endswith('max_observed_tokens') else '单次工作时间目标（秒）'
        if field.path.startswith('agent.roles.'):
            label = dict(model='模型', reasoning_effort='推理力度', service_tier='服务档位', worker=field.label, skill_set=field.label)[field.path.split('.')[-1]]
        if matrix:
            label = escape(field.label)
        label += ' <span class="required-mark">必填</span>' if field.required else ''
        metadata = ''
        identity = 'config-'+field.path.replace('.', '-')
        help_id = identity+'-help'
        hint = ('<details class="config-help"><summary aria-label="'+escape(field.label,quote=True)+'：填写说明">说明</summary>'
            '<p id="'+help_id+'">'+escape(hints[field.path])+'</p></details>') if field.path in hints and not matrix else ''
        described = ' aria-describedby="'+('role-settings-help' if matrix else help_id)+'"' if matrix or hint else ''
        attributes = ' data-config-path="'+escape(field.path,quote=True)+'" data-error-label="'+escape(field.label,quote=True)+'"'+(' data-invalid="true"' if isinstance(value, _Invalid) else '')
        if field.kind == 'strings':
            if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
                raise ValueError(f'{field.label}：导入值必须为文本数组')
            entries = ''.join('<div class="config-string-row"><textarea rows="1" aria-label="'+escape(field.label,quote=True)+'第 '+str(index+1)+' 项" name="cfg.'+field.path+'.'+str(index)+'">'+escape(item)+'</textarea><button type="button" class="config-string-remove secondary-button">删除</button></div>' for index,item in enumerate(value or []))
            return '<div class="config-field config-list-setting"'+attributes+'><p class="field-label">'+label+metadata+'</p><label class="check-label"><input type="checkbox" name="enabled.'+field.path+'" value="yes"'+(' checked' if value is not None else '')+'>使用此列表（可保留为空）</label><div class="config-string-list" data-path="'+field.path+'">'+entries+'</div><textarea rows="2" name="cfg.'+field.path+'" placeholder="填写一项；留空不会添加。需要多项可使用下方按钮。" aria-label="'+escape(field.label,quote=True)+'新增项"></textarea><button type="button" class="config-string-add secondary-button" data-path="'+field.path+'">添加一项</button>'+hint+'</div>'
        if field.kind in ('table','array'):
            if value is not None and not isinstance(value, dict if field.kind=='table' else list):
                raise ValueError(f'{field.label}：导入类型不正确')
            configured = '<span class="configured-note">已配置</span>' if value is not None else '<span class="summary-note">可选</span>'
            body = value if value is not None else ({} if field.kind=='table' else [])
            if field.path in GROUP_FIELDS:
                remainder = copy.deepcopy(body)
                for child in GROUP_FIELDS[field.path]:
                    key = child.path[len(field.path)+1:]
                    missing = object()
                    if _get(remainder, key, missing) is not missing:
                        _delete(remainder, key)
                contents = '<div class="config-grid config-grid-four">'+''.join(control(child, advanced=True) for child in GROUP_FIELDS[field.path])+'</div>'
                contents += '<details class="expert-settings"><summary>其他已导入参数 / 专家编辑</summary><p class="note">标准参数请使用上方表单。这里保留其他键名与类型；未知字段会在检查配置时验证。</p>'
                contents += '<div class="config-children" data-parent="@'+field.path+'">'+tree(remainder, '@'+field.path)+'</div><button type="button" class="config-add secondary-button" data-parent="@'+field.path+'">添加扩展项</button></details>'
            else:
                contents = '<p class="note">此结构由插件或专门合同定义。已有值会完整保留；请根据插件说明导入 TOML，或在下面按原类型编辑。无需使用时保持未声明。</p>'
                contents += '<div class="config-children" data-parent="@'+field.path+'">'+tree(body,'@'+field.path)+'</div><button type="button" class="config-add secondary-button" data-parent="@'+field.path+'">添加项</button>'
            known = field.path in GROUP_FIELDS
            opening = '<fieldset class="structured-setting known-settings"'+attributes+'><legend>'+label+configured+'</legend>' if known else '<details class="structured-setting"'+attributes+'><summary>'+label+configured+'</summary>'
            return opening+\
                '<label class="check-label"><input type="checkbox" name="enabled.'+field.path+'" value="yes"'+(' checked' if value is not None else '')+'>使用这组自定义设置（关闭后不保存本组）</label>'+hint+'<noscript><p class="note">填写前请先启用本组；未启用时，本组内容不会保存。</p></noscript>'+contents+('</fieldset>' if known else '</details>')
        text = '' if value is None else (str(value).lower() if type(value) is bool else str(value))
        if field.choices or field.kind == 'boolean':
            choices = list(backends.names() if field.path=='agent.backend' else field.choices or ('true','false'))
            choices += [text] if text and text not in choices else []
            widget = '<select id="'+identity+'" name="cfg.'+field.path+'"'+described+'><option value="">未声明 / 沿用默认'+('（'+escape(field.placeholder)+'）' if field.placeholder else '')+'</option>'+''.join(
                '<option value="'+escape(choice,quote=True)+'"'+(' selected' if text==choice else '')+'>'+escape(choice_labels.get(choice,choice))+'</option>' for choice in choices)+'</select>'
        else:
            suggestions = ('low','medium','high') if field.path.endswith('reasoning_effort') else ('default','priority') if field.path.endswith('service_tier') else ()
            suggestion_id = identity+'-suggestions'
            widget = '<input id="'+identity+'" name="cfg.'+field.path+'" value="'+escape(text,quote=True)+'" placeholder="'+escape(placeholders.get(field.path,field.placeholder),quote=True)+'"'+described+(' inputmode="decimal"' if field.kind in ('integer','number') else '')+(' list="'+suggestion_id+'"' if suggestions else '')+'>'
            if suggestions:
                widget += '<datalist id="'+suggestion_id+'">'+''.join('<option value="'+choice+'">' for choice in suggestions)+'</datalist>'
        if value == '' and field.kind == 'string':
            widget += '<label class="check-label"><input type="checkbox" name="present.'+field.path+'" value="yes" checked>保留已导入的空文本（取消则省略）</label>'
        return '<div class="config-field"'+attributes+'><label'+(' class="config-sr-only"' if matrix else '')+' for="'+identity+'">'+label+metadata+'</label>'+widget+hint+'</div>'

    by_path = {field.path: field for field in form_fields}
    def fields(paths, *, advanced=False):
        return ''.join(control(by_path[path], advanced=advanced) for path in paths)

    def skill_picker():
        sets = _get(document, 'skills.sets', {})
        choices = set(sets) if isinstance(sets, dict) else set()
        if not edit:
            for section in ('agent.roles', 'agent.workers'):
                profiles = _get(document, section, {})
                if isinstance(profiles, dict):
                    choices.update(profile['skill_set'] for profile in profiles.values()
                        if isinstance(profile, dict) and isinstance(profile.get('skill_set'), str))
        if edit:
            catalog = dict(items=[], errors=[])
        else:
            try:
                catalog = skill_supply.catalog()
            except (OSError, ValueError, RuntimeError) as error:
                catalog = dict(items=[], errors=[dict(name='skills', message=str(error))])
        items = catalog['items']
        names = {item['name'] for item in items}
        contents = ['<details class="research-skill-settings"><summary>研究技能</summary>'
            '<fieldset class="config-group research-skill-picker" id="research-skill-picker"><legend class="config-sr-only">研究技能</legend>',
            '<p class="note">'+('已有实例的技能集合已冻结；可为后续节点选择已有集合，不能添加安装技能。沿用命名研究者时仍可能启用其技能。' if edit else
            '按列勾选分配给研究角色；默认均不启用。只有明确分配的角色才使用技能，不会自动分配给其他角色。更改勾选将保存为新集合，保留原有集合。')+'</p>']
        if not edit:
            if items:
                contents.append('<div class="config-matrix-scroll" role="region" aria-label="安装的研究技能与角色分配，可横向滚动" tabindex="0"><table class="skill-picker-matrix"><caption class="config-sr-only">按研究角色勾选已安装技能</caption><thead><tr><th scope="col">已安装技能</th>'+
                    ''.join('<th scope="col">'+role+' · '+ROLE_NAMES[role]+'</th>' for role in 'HWAE')+'</tr></thead><tbody>')
                for index, item in enumerate(items):
                    contents.append('<tr><th scope="row"><strong>'+escape(item['name'])+'</strong><p class="note">'+escape(item['description'])+'</p></th>')
                    for role in 'HWAE':
                        contents.append('<td><label class="check-label"><input type="checkbox" name="skill_picker.item.'+role+'.'+str(index)+'" value="'+escape(item['name'], quote=True)+'"'+(' checked' if item['name'] in _role_skill_items(document, role) else '')+'><span class="config-sr-only">'+escape(item['name'])+' 分配给 '+role+' · '+ROLE_NAMES[role]+'</span></label></td>')
                    contents.append('</tr>')
                contents.append('</tbody></table></div>')
            else:
                contents.append('<p class="note">当前没有可勾选的安装技能；已有集合与角色绑定仍会保留。</p>')
            for error in catalog['errors']:
                contents.append('<p class="note" data-skill-diagnostic="true">'+escape(error['name'])+'：'+escape(error['message'])+'</p>')
            if isinstance(sets, dict):
                for name, value in sets.items():
                    members = value.get('items', []) if isinstance(value, dict) else []
                    missing = [item for item in members if isinstance(item, str) and item not in names] if isinstance(members, list) else []
                    if missing:
                        contents.append('<p class="note" data-skill-diagnostic="true">集合 '+escape(name)+' 中暂不可勾选的技能：'+escape('、'.join(missing))+'。未修改勾选时保留原绑定；检查配置时验证实际资产。</p>')
        contents.append('<div class="config-grid config-grid-four">')
        for role in 'HWAE':
            path = f'agent.roles.{role}.skill_set'
            profile = _get(document, f'agent.roles.{role}', {})
            workers = _get(document, 'agent.workers', {})
            worker_name = profile.get('worker') if isinstance(profile, dict) else None
            worker = workers.get(worker_name, {}) if isinstance(workers, dict) and isinstance(worker_name, str) else {}
            explicit = profile.get('skill_set') if isinstance(profile, dict) else None
            inherited = worker.get('skill_set') if isinstance(worker, dict) else None
            current = ('集合 '+str(explicit) if explicit is not None else
                '继承 '+str(worker_name)+' 的集合 '+str(inherited) if inherited is not None else '未启用技能')
            # Original scalar controls remain successful, including imported empty
            # values; the selector only replaces a binding after an explicit edit.
            contents.append('<div hidden>'+control(by_path[path])+'</div>')
            if not edit:
                baseline = sorted(set(_role_skill_items(document, role)) & names)
                contents.append('<input type="hidden" name="skill_picker.baseline.'+role+'" value="'+escape(json.dumps(baseline, ensure_ascii=False), quote=True)+'">')
            identity = 'skill-picker-choice-'+role
            contents.append('<div class="config-field"><label for="'+identity+'">'+role+' · '+ROLE_NAMES[role]+' 技能分配</label><select id="'+identity+'" name="skill_picker.choice.'+role+'"><option value="keep" selected>保持当前：'+escape(current)+'</option>')
            if not edit:
                contents.append('<option value="custom">使用本列勾选</option><option value="none">不启用技能（覆盖继承）</option>')
            contents.append('<option value="inherit">沿用命名研究者（未指定时不启用）</option>')
            contents.extend('<option value="set:'+escape(name, quote=True)+'">已有集合：'+escape(name)+('（空集合，不启用技能）' if sets.get(name) == dict(items=[]) else '')+'</option>' for name in sorted(choices))
            contents.append('</select></div>')
        contents.append('</div></fieldset></details>')
        return ''.join(contents)

    rows = [] if edit else ['<noscript><p class="note">请依次填写下方任务与资源设置，再点击底部“检查配置”。</p></noscript><section class="setup-panel" data-setup-step="1" aria-labelledby="setup-task-title">'
        '<div class="section-heading"><span class="eyebrow">第一步 / 任务与材料</span><h2 id="setup-task-title" tabindex="-1">你想研究什么？</h2>'
        '<p>写清问题、期望得到的结果，以及需要遵守的限制。先准备任务和文件，再设置研究用量。</p></div>'
        '<div class="config-field task-mode"><label for="task-mode">如何提供任务</label><select id="task-mode" name="task_mode">'+''.join(
            '<option value="'+mode+'"'+(' selected' if draft['task_mode']==mode else '')+'>'+label+'</option>'
            for mode,label in [('text','直接写下任务'),('file','读取已有任务文件')])+'</select></div>'
        '<div class="config-field" data-task-panel="text"><label for="task-text">研究任务 <span class="required-mark">必填</span></label>'
        '<textarea id="task-text" name="task_text" rows="6" placeholder="描述你要解决的问题、已有条件和期望交付的结果。">'+escape(draft['task_text'])+'</textarea>'
        '<small class="field-help">这段原文会作为本次研究的任务保存。请不要填写密码或访问密钥。</small></div>'
        '<div data-task-panel="file">'+fields(['research.objective'])+'</div>'
        '<div class="path-heading"><h3>材料从哪里来，结果存到哪里？</h3><p class="note">以下都是运行 SciDiscovery 的服务器上的目录，不是浏览器本机目录。</p></div>'
        +'<div class="config-grid">'+fields(['research.public_root','research.root'])+'</div>'+
        '<details class="path-base"><summary>服务器路径怎么填写？</summary><p>可填写完整绝对路径；相对路径从此服务器目录开始：<code>'+escape(draft['base'])+'</code></p></details>'
        '<div class="setup-actions"><span class="note">下一步：选择模型，填写用量上限</span><button type="button" hidden data-setup-go="2">下一步：模型与预算 →</button></div></section>']
    primary = ['agent.model','agent.reasoning_effort','agent.service_tier','budget.max_tokens','budget.max_seconds','budget.max_steps']
    permissions = ['permissions.workspace','permissions.network','permissions.web','permissions.subagents']
    heading = ('<section class="setup-panel configuration-editor" aria-labelledby="setup-limits-title">'
        '<div class="section-heading"><h2 id="setup-limits-title" tabindex="-1">调整后续研究配置</h2>'
        '<p>保存后从下一个新节点生效。已经准备、正在运行或恢复的节点继续使用原配置；已消耗的用量仍保留。</p></div>' if edit else
        '<section class="setup-panel" data-setup-step="2" aria-labelledby="setup-limits-title">'
        '<div class="section-heading"><span class="eyebrow">第二步 / 模型与预算</span><h2 id="setup-limits-title" tabindex="-1">这次研究可以使用多少资源？</h2>'
        '<p>模型可以沿用统一默认值。三项总预算都需明确填写，达到任一限制会影响研究继续。</p></div>')
    rows.append(heading + '<fieldset class="config-group"><legend>统一模型</legend><div class="model-fields">'+fields(primary[:3])+'</div></fieldset>'
        '<fieldset class="config-group" id="budget-settings"><legend>研究总上限</legend><div class="budget-fields">'+fields(primary[3:])+'</div></fieldset>')
    used = set(primary+permissions+['research.root','research.public_root','research.objective'])
    role_paths = [f'agent.roles.{role}.{key}' for role in 'HWAE' for key in ('model','reasoning_effort','service_tier')]
    target_paths = [f'budget.node_targets.{role}.{key}' for role in 'HWAE' for key in ('max_observed_tokens','max_seconds')]
    configured = any(_get(document, path) is not None for path in role_paths+target_paths)
    rows.append('<details class="role-settings config-group" data-config-group="agent.roles" open><summary>角色模型与单次用量'
        + ('<span class="configured-note">已设置，折叠后仍保留</span>' if configured else '<span class="summary-note">可选，模型留空沿用统一设置</span>')
        + '</summary><div class="config-matrix-scroll" role="region" aria-label="角色模型与单次用量，可横向滚动" tabindex="0">'
        '<table class="config-role-matrix role-settings-matrix"><caption class="config-sr-only">四个研究角色的模型覆盖与单次工作软目标</caption><colgroup><col class="role-column"></colgroup><colgroup><col class="model-column"><col><col></colgroup><colgroup><col><col></colgroup>'
        '<thead><tr><th rowspan="2" scope="col">研究角色</th><th colspan="3" scope="colgroup">模型设置</th><th colspan="2" scope="colgroup" data-config-group="budget.node_targets">单次工作软目标</th></tr>'
        '<tr><th scope="col">模型</th><th scope="col">推理力度</th><th scope="col">服务档位</th><th scope="col">用量（tokens）</th><th scope="col">时间（秒）</th></tr></thead><tbody>')
    for role in 'HWAE':
        paths = [f'agent.roles.{role}.{key}' for key in ('model','reasoning_effort','service_tier')]
        paths += [f'budget.node_targets.{role}.{key}' for key in ('max_observed_tokens','max_seconds')]
        used.update(paths)
        rows.append('<tr><th scope="row">'+role+' · '+ROLE_NAMES[role]+'</th>'+''.join('<td>'+control(by_path[path], matrix=True)+'</td>' for path in paths)+'</tr>')
    rows.append('</tbody></table></div><details class="config-help role-settings-help"><summary>角色职责与用量说明</summary>'
        '<div id="role-settings-help"><p>角色模型、推理力度与服务档位留空时沿用统一设置。单次用量留空时不单独声明；若自定义，请填写四个角色的全部八项。单次软目标不能扩大总预算。</p>'
        + ''.join('<p><strong>'+role+' · '+ROLE_NAMES[role]+'</strong>：'+ROLE_HELP[role]+'</p>' for role in 'HWAE')
        + '<p>原调用恢复、预算份额与交付等设置可能要求完整的单次用量配置。</p></div></details></details>')
    rows.append(skill_picker())
    used.update(f'agent.roles.{role}.skill_set' for role in 'HWAE')
    rows.append(
        '<details class="permission-settings"><summary>研究助手可以访问什么？<span class="summary-note">权限与联网</span></summary>'
        '<p class="note">'+('以下为后续节点的权限声明；实验仍需满足自身的授权条件。' if edit else
        '权限沿用统一配置，检查时会列出生效值。实验执行仍需自身的授权条件。')+'</p><div class="config-grid config-grid-four">'+fields(permissions)+'</div></details>')
    rows.append('<details class="advanced-settings"><summary>更多研究配置<span class="summary-note">探索节奏、接入与恢复</span></summary>'
        + ('<p class="note">探索节奏、实验执行、交付及研究合同可在下面调整。折叠后仍完整保留填写的值。</p>' if edit else
        '<p class="note">日常创建只需上方任务、模型和总预算。下面可选配置不会自动增加权限或预算。已导入的值在折叠后仍完整保留。</p>'))
    categories = [
        ('补充任务与材料约束','research','已有全局补充原文、材料来源限制。'),
        ('已有工程','project','可选：在准确 Git 工程路径使用声明目录；创建时冻结工程身份，权限与执行条件仍需满足。'),
        ('研究技能集合','skills','可选：声明具名技能集合，再由角色或命名研究者明确选择。插件声明不会自动启用技能。'),
        ('后端接入与原生设置','agent','采用默认后端时可保持留空。更换后端、启动程序或原生环境时按其声明设置。'),
        ('探索节奏与用量分配','budget','设置实验探索切片、角色份额、补正阈值与输出体积限制；这些设置不增加总预算。'),
        ('实验执行插件与资源','executor','仅在本次研究需要实验执行时接入。配置本身不会授予实验执行许可。'),
        ('结果交付适配器','delivery','仅在需要向明确外部目标交付时配置，按适配器说明准备。'),
        ('独立复核与原调用恢复','policies','声明复核合同、恢复边界与已接入的历史观察插件。'),
        ('实例网页与宿主管理','ui','实例网页端口与宿主管理文件路径。'),
        ('声明格式','version','导入配置的格式版本。'),
    ]
    for title, prefix, explanation in categories:
        if edit and prefix in {'research', 'ui', 'version', 'project', 'skills'}:
            continue
        paths = [field.path for field in form_fields if field.path not in used and (field.path == prefix or field.path.startswith(prefix+'.'))]
        regular = [path for path in paths if by_path[path].kind not in ('table','array') or path in GROUP_FIELDS]
        expert = [path for path in paths if path not in regular]
        configured = any(_get(document, path) is not None for path in paths)
        rows.append('<details class="advanced-category" data-config-group="'+prefix+'"><summary>'+title+('<span class="configured-note">已配置</span>' if configured else '')+'</summary><p class="note">'+explanation+'</p>')
        if prefix == 'budget':
            for group_title, group_paths in [
                ('探索切片', [path for path in regular if path.startswith('budget.breadth.')]),
                ('补正阈值', [path for path in regular if path.startswith('budget.correction.')]),
                ('输出与候选', [path for path in regular if path not in {'budget.role_shares'} and not path.startswith(('budget.breadth.', 'budget.correction.'))]),
            ]:
                rows.append('<fieldset class="config-group"><legend>'+group_title+'</legend><div class="config-grid config-grid-four">'+fields(group_paths, advanced=True)+'</div></fieldset>')
            rows.append(fields(['budget.role_shares'], advanced=True))
        else:
            rows.append('<div class="config-grid config-grid-four">'+fields(regular, advanced=True)+'</div>')
        if expert:
            rows.append(fields(expert, advanced=True))
        rows.append('</details>')
    rows.append('<details class="advanced-category"><summary>其他导入字段 / 专家编辑</summary><p class="note">原样保留并校验额外字段；不支持的声明会在检查时提示。</p><div class="config-children" data-parent="@extra">'+tree(extras,'@extra')+'</div><button type="button" class="config-add secondary-button" data-parent="@extra">添加扩展字段</button></details></details>'
        + ('</section>' if edit else '<div class="setup-actions"><button type="button" hidden class="secondary-button" data-setup-go="1">← 返回任务与材料</button><span class="note">下一步会检查配置，尚不创建或运行研究。</span></div></section>'))
    rows.append('<script src="/assets/configuration.js"></script>')
    metadata='<input type="hidden" name="rendered_backend" value="'+escape(
        str(_get(document,'agent.backend',backends.DEFAULT_BACKEND) or backends.DEFAULT_BACKEND),quote=True)+'">'
    saved_native=draft.get('native_drafts',{})
    if saved_native:
        metadata+='<input type="hidden" name="native_drafts" value="'+escape(
            json.dumps(saved_native,ensure_ascii=False,separators=(',',':')),quote=True)+'">'
        metadata+=('<p class="note">已保留以下后端的原生设置草稿：'+escape('、'.join(saved_native))
            +'。切回对应后端时恢复；这些设置不传给当前后端。</p>')
    return '<div class="compact-configuration">'+metadata+'\n'.join(rows)+'</div>'


SCRIPT_SOURCE = r'''
(() => {
document.addEventListener('DOMContentLoaded', () => {
  const editor = document.getElementById('research-config-edit-form');
  if (editor) {
    const notice = document.querySelector('[role="alert"]');
    const invalid = Array.from(editor.querySelectorAll('[data-error-label]')).find(field =>
      field.dataset.invalid || (notice && (notice.textContent.includes(field.dataset.errorLabel) ||
        notice.textContent.includes(field.dataset.configPath || '__no_path__'))));
    if (invalid) {
      for (let node=invalid; node && node!==editor; node=node.parentElement) if(node.tagName==='DETAILS') node.open=true;
      invalid.querySelector('input:not([type="hidden"]),select,textarea')?.focus();
    } else if (notice) notice.focus();
  }
  const flow = document.querySelector('.setup-flow');
  if (!flow) return;
  const form = document.getElementById('research-config-form');
  const submit = form.querySelector('button[value="preview"]');
  submit.classList.add('setup-check');
  let current = Number(flow.dataset.startStep || 1);
  const mode = form.querySelector('[name="task_mode"]');
  const taskPanels = () => flow.querySelectorAll('[data-task-panel]').forEach(panel => { panel.hidden = panel.dataset.taskPanel !== mode.value; });
  taskPanels(); mode.addEventListener('change', () => { flow.querySelectorAll('[data-setup-missing]').forEach(note => note.remove()); taskPanels(); });
  function show(step, focus = true) {
    current = step; form.hidden = step === 3;
    flow.querySelectorAll('[data-setup-step]').forEach(panel => { panel.hidden = Number(panel.dataset.setupStep) !== step; });
    flow.querySelectorAll('[data-setup-marker]').forEach(marker => {
      const active = Number(marker.dataset.setupMarker) === step;
      marker.classList.toggle('is-current', active);
      marker.classList.toggle('is-complete', Number(marker.dataset.setupMarker) < step);
      if (active) marker.setAttribute('aria-current', 'step'); else marker.removeAttribute('aria-current');
    });
    submit.hidden = step !== 2;
    flow.querySelector('.setup-tools').hidden = step === 3;
    if (focus) flow.querySelector('[data-setup-step="'+step+'"] h2')?.focus();
  }
  flow.querySelectorAll('[data-setup-go]').forEach(button => { button.hidden = false; button.addEventListener('click', () => {
    const target = Number(button.dataset.setupGo);
    if (current === 1 && target === 2) {
      const names = [mode.value === 'text' ? 'task_text' : 'cfg.research.objective', 'cfg.research.public_root', 'cfg.research.root'];
      for (const name of names) {
        const control = form.elements.namedItem(name);
        if (!control.value.trim()) {
          flow.querySelectorAll('[data-setup-missing]').forEach(note => note.remove());
          const note=document.createElement('p'); note.dataset.setupMissing=''; note.className='field-error'; note.setAttribute('role','alert'); note.textContent='请先填写这一项，再继续。'; control.after(note); control.focus();
          control.addEventListener('input', () => note.remove(), {once:true}); return;
        }
      }
    }
    show(target);
  }); });
  const notice = flow.querySelector('[role="alert"]');
  if (notice && current !== 3) {
    const error = notice.textContent;
    const invalid = Array.from(form.querySelectorAll('[data-error-label]')).find(field => field.dataset.invalid || (field.dataset.errorLabel && error.includes(field.dataset.errorLabel)) || (field.dataset.configPath && error.includes(field.dataset.configPath)));
    current = invalid ? Number(invalid.closest('[data-setup-step]').dataset.setupStep) : (/任务|objective|public_root|fresh directory|research.root/.test(error) ? 1 : 2);
    if (invalid) { for (let node=invalid; node && node!==form; node=node.parentElement) if(node.tagName==='DETAILS') node.open=true; }
  }
  show(current, false);
  if (notice) notice.focus(); else if (current === 3) flow.querySelector('[data-setup-step="3"] h2')?.focus();
});
let next = Math.max(0, ...Array.from(document.querySelectorAll('.config-node')).map(node => Number(node.dataset.node)), ...Array.from(document.querySelectorAll('.config-string-list textarea')).map(node => Number(node.name.split('.').pop())));
const update = node => {
  const kind = node.querySelector('.config-kind').value;
  const composite = kind === 'table' || kind === 'array';
  node.querySelector('.config-value').hidden = composite;
  node.querySelector('.config-children').hidden = !composite;
  node.querySelector('.config-add').hidden = !composite;
};
document.querySelectorAll('.config-node').forEach(update);
const invalidate = e => {
  if (!e.target.closest('form[action$="/control/configure"]') || e.target.name === 'save_path') return;
  const create = document.querySelector('form[action$="/control/create"]');
  if (!create) return;
  create.querySelectorAll('button').forEach(button => {button.disabled=true;});
  let note=document.getElementById('config-edited-note');
  if (!note) {note=document.createElement('p');note.id='config-edited-note';e.target.closest('form').after(note);}
  note.textContent='参数已修改，请重新检查并预览。';
};
document.addEventListener('input',invalidate); document.addEventListener('change',invalidate);
document.addEventListener('change', e => {
  if (!e.target.name?.startsWith('skill_picker.item.')) return;
  const role = e.target.name.split('.')[2];
  const selector = document.getElementById('skill-picker-choice-'+role);
  if (selector) selector.value = 'custom';
});
const enableEditedGroup = e => {
  const group = e.target.closest('.structured-setting');
  if (!group || e.target.type === 'checkbox') return;
  const enabled = group.querySelector('input[type="checkbox"]');
  if (enabled) enabled.checked = true;
};
document.addEventListener('input', enableEditedGroup); document.addEventListener('change', enableEditedGroup);
document.addEventListener('click', e => { if (e.target.matches('.config-add,.config-remove,.config-string-add,.config-string-remove')) invalidate(e); });
document.addEventListener('change', e => {
  if (!e.target.matches('.config-kind')) return;
  const node = e.target.closest('.config-node');
  node.querySelector('.config-children').replaceChildren(); update(node);
});
document.addEventListener('click', e => {
  if (e.target.matches('.config-string-remove')) { e.target.closest('.config-string-row').remove(); return; }
  if (e.target.matches('.config-string-add')) {
    const path=e.target.dataset.path, container=Array.from(document.querySelectorAll('.config-string-list')).find(item=>item.dataset.path===path);
    const row=document.createElement('div'); row.className='config-string-row';
    const input=document.createElement('textarea'); input.rows=1; input.name='cfg.'+path+'.'+String(++next); row.append(input);
    const button=document.createElement('button'); button.type='button'; button.className='config-string-remove'; button.textContent='删除'; row.append(button);
    container.append(row); document.querySelector('[name="enabled.'+path+'"]').checked=true; return;
  }
  if (e.target.matches('.config-remove')) { e.target.closest('.config-node').remove(); return; }
  if (!e.target.matches('.config-add')) return;
  const group=e.target.closest('.structured-setting'), enabled=group && group.querySelector('input[type="checkbox"]'); if(enabled) enabled.checked=true;
  const parent = e.target.dataset.parent, id = String(++next), prefix = 'node.'+id+'.';
  const node = document.createElement('div'); node.className = 'config-node'; node.dataset.node = id;
  const hidden = document.createElement('input'); hidden.type = 'hidden'; hidden.name = prefix+'parent'; hidden.value = parent; node.append(hidden);
  const keyLabel = document.createElement('label'); keyLabel.className='config-node-key'; keyLabel.textContent='键名 ';
  const key = document.createElement('input'); key.name = prefix+'key'; key.placeholder = '对象键名（数组项留空）'; keyLabel.append(key); node.append(keyLabel);
  const kindLabel = document.createElement('label'); kindLabel.className='config-node-type'; kindLabel.textContent='类型 ';
  const kind = document.createElement('select'); kind.name = prefix+'kind'; kind.className = 'config-kind';
  for (const [value,label] of [['string','文本'],['integer','整数'],['number','数字'],['boolean','布尔'],['table','对象'],['array','数组'],['datetime','日期时间'],['date','日期'],['time','时间']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = label; kind.append(option);
  } kindLabel.append(kind); node.append(kindLabel);
  const label = document.createElement('label'); label.className='config-value'; label.textContent='值 ';
  const value=document.createElement('input'); value.name=prefix+'value'; label.append(value); node.append(label);
  const remove=document.createElement('button'); remove.type='button'; remove.className='config-remove'; remove.textContent='删除此项'; node.append(remove);
  const children=document.createElement('div'); children.className='config-children'; children.dataset.parent=id; node.append(children);
  const add=document.createElement('button'); add.type='button'; add.className='config-add'; add.dataset.parent=id; add.textContent='添加子项'; node.append(add);
  const container=Array.from(document.querySelectorAll('.config-children')).find(item => item.dataset.parent === parent);
  container.append(node); update(node);
});
})();
'''


def _options(draft):
    def reject_invalid(value):
        if isinstance(value, _Invalid):
            raise ValueError('参数仍有类型错误，请修改后重新检查')
        if isinstance(value, dict):
            for item in value.values(): reject_invalid(item)
        if isinstance(value, list):
            for item in value: reject_invalid(item)
    reject_invalid(draft['document'])
    for field in FIELDS:
        if field.required and _get(draft['document'], field.path) is None:
            raise ValueError(f'{field.label}：必填')
    if draft['task_mode'] == 'text' and not draft['task_text'].strip():
        raise ValueError('任务原文：必填')
    if draft['task_mode'] == 'file' and not _get(draft['document'], 'research.objective'):
        raise ValueError('已有任务原件路径：必填')
    for field in FIELDS:
        value = _get(draft['document'], field.path)
        if value is not None and field.kind not in ('array', 'table', 'strings'):
            if field.choices and value not in field.choices:
                raise ValueError(f'{field.label}：请选择支持的选项')
            _typed(str(value).lower() if type(value) is bool else str(value), field.kind, field.label)
    return research_config.normalize(research_config.from_document(declaration(draft), base=draft['base']))


def preview(draft):
    return research_config.controller.prepare(**_options(draft), validate_only=True)


def create(draft):
    return research_config.controller.prepare(**_options(draft))


validate = preview
prepare = create
initial_draft = blank


def export_toml(draft):
    """Optional export; inline tables preserve arbitrary nested declaration data."""
    def value(item):
        if isinstance(item, _Invalid): raise ValueError('参数仍有类型错误，请修改后重新保存')
        kind = _kind(item)
        if kind == 'table':
            return '{' + ', '.join(json.dumps(k,ensure_ascii=False)+' = '+value(v) for k,v in item.items()) + '}'
        if kind == 'array': return '['+', '.join(value(v) for v in item)+']'
        if kind == 'string': return json.dumps(item, ensure_ascii=False)
        if kind == 'boolean': return str(item).lower()
        if kind in ('datetime','date','time'): return item.isoformat()
        if kind == 'number' and not math.isfinite(item): raise ValueError('导出需要有限数字')
        return str(item)
    document = declaration(draft)
    # Export uses canonical explicit paths so another save location cannot alter
    # the declaration's meaning. The decoder remains the authority for paths.
    base = Path(draft['base']).expanduser().resolve()
    def path_at(table, key):
        if key in table:
            table[key] = str(research_config._path(table[key], base, key))
    research = document.get('research', {})
    for key in ('root', 'objective', 'public_root'):
        path_at(research, key)
    if 'user_updates' in research:
        research['user_updates'] = [str(research_config._path(v,base,'user_updates')) for v in research['user_updates']]
    for row in research.get('source_restrictions', []):
        path_at(row, 'source')
    agent = document.get('agent', {})
    launcher = agent.get('launcher')
    if launcher and ('/' in launcher[0] or launcher[0].startswith('~')):
        launcher[0] = str(research_config._path(launcher[0],base,'launcher'))
    sandbox = _get(document, 'agent.native.sandbox_workspace_write', {})
    if 'writable_roots' in sandbox:
        sandbox['writable_roots'] = [str(research_config._path(v,base,'writable_roots')) for v in sandbox['writable_roots']]
    adapter = _get(document, 'delivery.adapter', {})
    if isinstance(adapter, dict):
        path_at(adapter, 'adapter')
        if 'source_roots' in adapter:
            adapter['source_roots'] = [str(research_config._path(v,base,'source_roots')) for v in adapter['source_roots']]
        argv = adapter.get('launcher_argv')
        if argv and '/' in argv[0]:
            argv[0] = str(research_config._path(argv[0],base,'launcher_argv'))
    path_at(document.get('ui', {}), 'approval_secret_file')
    path_at(_get(document, 'executor.config', {}), 'config_file')
    return '\n'.join(json.dumps(k,ensure_ascii=False)+' = '+value(v) for k,v in document.items())+'\n'
