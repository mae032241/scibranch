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
import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import research_config, skill_supply, backends, model_catalog, supervision


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
    *[Field(f'budget.node_targets.{role}.{key}', f'{role} '+('原调用恢复／计量锚点' if role=='W' else '节点软目标')+f'：{label}', kind)
      for role in 'HWAE' for key, label, kind in [('max_observed_tokens', 'tokens', 'integer'), ('max_seconds', '秒', 'number')]],
    Field('budget.role_shares', '角色预算份额策略', 'table'),
    *[Field(f'budget.breadth.{key}', label, kind) for key, label, kind in [
        ('max_w_seconds', 'W 原调用计量锚点秒数', 'number'), ('max_w_observed_tokens', 'W 原调用计量锚点 tokens', 'integer'),
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
    Field('policies.worker_context_windows', 'W 每节点最多上下文窗口数', 'integer'),
    *[Field('policies.supervision.'+key, label, kind) for key, label, kind in [
        ('consecutive_calls', '同一机制连续调用间隔（次）', 'integer'),
        ('min_budget_fraction', '首次复核与后续最小间距（共享预算比例）', 'number'),
        ('max_automatic_reviews', '自动 A 最大次数（留空不设次数上限）', 'integer'),
        ('require_fresh_input', '自动 A 要求新问题或收据', 'boolean'),
        ('automatic_budget_fraction.tokens', '自动 A 累计 tokens 比例上限', 'number'),
        ('automatic_budget_fraction.seconds', '自动 A 累计时间比例上限', 'number')]],
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
    # A provider's labels, requirements and choices replace shared descriptors.
    return list({field.path: field for field in [*result, *(Field(**field) for field in provider_fields)]}.values())


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
        Field('policies.resume.automatic_recovery.max_attempts', '最大重试次数（retry + continue 总计）', 'integer', placeholder='8'),
        Field('policies.resume.automatic_recovery.max_continues', '提前停止的 continue 子上限（次）', 'integer', placeholder='8'),
        Field('policies.resume.automatic_recovery.max_retries', '临时服务故障 retry 子上限（次）', 'integer', placeholder='8'),
        Field('policies.resume.automatic_recovery.initial_backoff_seconds', '首次服务退避（秒）', 'number', placeholder='5'),
        Field('policies.resume.automatic_recovery.max_backoff_seconds', '单次服务退避上限（秒）', 'number', placeholder='300'),
        Field('policies.resume.automatic_recovery.jitter_fraction', '服务退避抖动比例（0–1）', 'number', placeholder='0'),
        Field('policies.resume.automatic_recovery.max_wait_seconds', '累计服务等待上限（秒）', 'number', placeholder='1800'),
    ],
}
ROLE_NAMES = dict(H='研究方向', W='实验与实现', A='综合评估', E='证据检索与提取')
ROLE_HELP = dict(H='提出或修订研究假设与方向。', W='在已选假设下设计、实现并运行有界实验。',
    A='普通 A 提供诊断建议，由 W 决定下一步；Final A 负责最终评估。', E='按明确问题检索或提取证据，保留来源与缺失信息。')


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


_UNLIMITED_BUDGET_PATHS = {'budget.max_tokens', 'budget.max_seconds'}


def _typed(value, kind, label, *, path=None):
    if path in _UNLIMITED_BUDGET_PATHS and value == 'unlimited':
        return value
    try:
        if kind == 'null':
            if value != '':
                raise ValueError()
            return None
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


def parse(fields, *, base, _fixed_skill_sets=None, refresh_backend=False):
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
    saved_launchers=_single(fields,'launcher_drafts','{}')
    def unique_launchers(pairs):
        result={}
        for name,value in pairs:
            if name in result:raise ValueError()
            result[name]=value
        return result
    try:
        launcher_drafts=json.loads(saved_launchers,object_pairs_hook=unique_launchers) if len(saved_launchers)<=256_000 else None
    except (ValueError,TypeError,RecursionError):
        launcher_drafts=None
    if (not isinstance(launcher_drafts,dict) or len(launcher_drafts)>64
            or any(not isinstance(name,str) or not name or
                (value is not None and (not isinstance(value,list) or any(not isinstance(item,str) for item in value)))
                for name,value in launcher_drafts.items())):
        raise ValueError('后端启动命令草稿无效，请重新打开配置表单')
    scalar_fields = form_fields + [field for group in GROUP_FIELDS.values() for field in group]
    allowed = {'present.'+f.path for f in scalar_fields} | {'cfg.'+f.path for group in GROUP_FIELDS.values() for f in group} | {'task_mode', 'task_text', 'rendered_backend', 'native_drafts', 'launcher_drafts', 'supervision_baseline', 'advanced_toml', 'advanced_fixed', 'cfg.policies.supervision.mode'} | {'cfg.'+f.path for f in form_fields if f.kind not in ('table', 'array', 'strings')} | {'enabled.'+f.path for f in form_fields if f.kind in ('table', 'array', 'strings')}
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
    def typed(value, kind, label, *, path=None):
        try:
            return _typed(value, kind, label, path=path)
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
            if node['kind'] == 'null' and any(child['parent'] == identity for child in nodes.values()):
                raise ValueError('空值字段不能包含子项')
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
            if (field.path in GROUP_FIELDS and _single(fields, 'enabled.'+field.path) != 'yes'
                    and _single(fields, 'present.'+field.path) != 'yes'):
                pending_group = {}
                for child in GROUP_FIELDS[field.path]:
                    raw = _single(fields, 'cfg.'+child.path)
                    if raw != '':
                        _put(pending_group, child.path[len(field.path)+1:], typed(raw, child.kind, child.label))
                if pending_group:
                    result.setdefault('group_drafts', {})[field.path] = pending_group
                    errors.append(field.label+'：修改本组前请明确勾选声明')
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
            _put(result['document'], field.path, typed(raw, field.kind, field.label, path=field.path))
    # An explicit extra tree preserves empty groups and arbitrary imported keys.
    extras = tree('@extra', 'table')
    def merge(target, extra, prefix=''):
        for key, value in extra.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                merge(target[key], value, prefix+key+'.')
            elif key in target:
                errors.append(f'{prefix}{key}：高级设置与表单字段重复，请只保留一处')
            else:
                target[key] = value
    merge(result['document'], extras)
    # Settings outside the form are edited as TOML and merged losslessly.
    advanced = _single(fields, 'advanced_toml')
    result['advanced_text'] = advanced
    result['advanced_fixed'] = _fixed_values(_single(fields, 'advanced_fixed', '[]'))
    advanced_valid = True
    if advanced.strip():
        try:
            merge(result['document'], tomllib.loads(advanced))
        except tomllib.TOMLDecodeError as error:
            advanced_valid = False
            errors.append('高级设置（TOML）格式错误：'+str(error))
    for keys, value in result['advanced_fixed']:
        target = result['document']
        for key in keys[:-1]:
            target = target.setdefault(key, {})
            if not isinstance(target, dict):
                errors.append('.'.join(keys)+'：高级设置中的父字段不是对象')
                break
        else:
            if keys[-1] not in target:
                target[keys[-1]] = copy.deepcopy(value)
    mode = _single(fields, 'cfg.policies.supervision.mode')
    supervision_paths = [field.path for field in form_fields if field.path.startswith('policies.supervision.')]
    observed = {path: _single(fields, 'cfg.'+path) for path in [*supervision_paths, 'policies.supervision.mode']}
    try:
        baseline = json.loads(_single(fields, 'supervision_baseline', '{}'))
    except (ValueError, TypeError, RecursionError):
        raise ValueError('自动复核草稿无效，请重新打开表单') from None
    if not isinstance(baseline, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in baseline.items()):
        raise ValueError('自动复核草稿无效，请重新打开表单')
    changed_supervision = any(raw != baseline.get(path, '') for path, raw in observed.items())
    if mode:
        if mode not in SUPERVISION_MODE_FIELD.choices:
            errors.append(SUPERVISION_MODE_FIELD.label+'：请选择支持的选项')
            _put(result['document'], SUPERVISION_MODE_FIELD.path, mode)
        else:
            current = _get(result['document'], 'policies.supervision')
            if isinstance(current, dict) and 'mode' in current:
                errors.append('policies.supervision.mode：高级设置与表单字段重复，请只保留一处')
            if current is not None and not isinstance(current, dict):
                errors.append('policies.supervision：高级设置中的父字段不是对象')
            else:
                selected = copy.deepcopy(current) if isinstance(current, dict) else _default_supervision()
                selected['mode'] = mode
                _put(result['document'], 'policies.supervision', selected)
    if changed_supervision:
        current = _get(result['document'], 'policies.supervision')
        if current is None or isinstance(current, dict):
            selected = _default_supervision()
            def retain(target, source):
                for key, value in source.items():
                    if isinstance(value, dict) and isinstance(target.get(key), dict):
                        retain(target[key], value)
                    else:
                        target[key] = copy.deepcopy(value)
            retain(selected, current or {})
            _put(result['document'], 'policies.supervision', selected)
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
    if selected_backend!=rendered_backend and not advanced_valid:
        # Unparsed options still belong to the rendered provider. Keep that
        # context until the text can be corrected before attempting a switch.
        _put(result['document'],'agent.backend',rendered_backend)
    if selected_backend!=rendered_backend and advanced_valid:
        # Exact launch argv belongs to the provider whose controls produced it,
        # including absolute executables and custom wrappers.
        launcher_drafts[rendered_backend]=_get(result['document'],'agent.launcher')
        _delete(result['document'],'agent.launcher')
        restored_launcher=launcher_drafts.pop(selected_backend,None)
        if restored_launcher is not None:_put(result['document'],'agent.launcher',restored_launcher)
        result['launcher_drafts']=launcher_drafts
        # Native extensions belong to the provider whose controls produced
        # them. Switching renders the new schema before any preparation call.
        native_drafts[rendered_backend]=_get(result['document'],'agent.native')
        _delete(result['document'],'agent.native')
        restored=native_drafts.pop(selected_backend,None)
        if restored is not None:_put(result['document'],'agent.native',restored)
        result['native_drafts']=native_drafts
        # Regenerate the text so the previous provider's options leave it.
        del result['advanced_text']
        result.pop('advanced_fixed', None)
        if not refresh_backend:
            errors.append('后端已切换；原生设置按原后端保留。请检查当前后端的设置后再次提交。')
    elif native_drafts:
        result['native_drafts']=native_drafts
    if launcher_drafts:
        result['launcher_drafts']=launcher_drafts
    if errors:
        raise FormError('；'.join(errors), result)
    return result


def parse_edit(fields, *, base, configuration, refresh_backend=False):
    """Keep instance originals and application settings outside node editing."""
    if any(name in {'task_mode', 'task_text'} for name in fields):
        raise ValueError('任务原件不能在研究配置中修改')
    try:
        result = parse(fields, base=base, _fixed_skill_sets=_get(configuration, 'skills.sets', {}),
            refresh_backend=refresh_backend)
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
    if value is None: return 'null'
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


_BARE_KEY = re.compile(r'[A-Za-z0-9_-]+')


def _default_supervision():
    """The current default table without TOML-incompatible empty values."""
    return {key: value for key, value in supervision.policy().items() if value is not None}
SUPERVISION_MODES = (('review', '开启：到期时由独立 A 复核方向'), ('notice', '只提示：到期时提醒 W，不调用 A'),
                     ('off', '关闭：不做自动方向复核'))
SUPERVISION_MODE_FIELD = Field('policies.supervision.mode', '自动方向复核',
    choices=tuple(key for key, _ in SUPERVISION_MODES))


def _toml_key(key):
    key = str(key)
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _toml_value(item):
    kind = _kind(item)
    if kind == 'null':
        raise ValueError('数组或内联对象中的空值（null）无法写成 TOML')
    if kind == 'table':
        return '{' + ', '.join(_toml_key(k) + ' = ' + _toml_value(v) for k, v in item.items()) + '}'
    if kind == 'array':
        return '[' + ', '.join(_toml_value(v) for v in item) + ']'
    if kind == 'string':
        return json.dumps(str(item), ensure_ascii=False)
    if kind == 'boolean':
        return str(item).lower()
    if kind in ('datetime', 'date', 'time'):
        return item.isoformat()
    if kind == 'number' and not math.isfinite(item):
        raise ValueError('TOML 需要有限数字')
    return str(item)


def _has_null(value):
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_has_null(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_null(item) for item in value)
    return False


def advanced_toml(document):
    """Readable TOML for settings outside the form.

    TOML has no null. A value that is null or contains null inside an array
    or inline table is kept verbatim as a separate fixed entry instead.
    """
    fixed = []
    lines = []
    def emit(table, keys):
        simple, nested = [], []
        for key, value in table.items():
            if isinstance(value, dict):
                nested.append((key, value))
            elif _has_null(value):
                fixed.append([[*keys, key], value])
            else:
                simple.append((key, value))
        prefix = '.'.join(_toml_key(key) for key in keys)
        if keys and (simple or not nested):
            lines.append(('\n' if lines else '') + '[' + prefix + ']')
        lines.extend(_toml_key(key) + ' = ' + _toml_value(value) for key, value in simple)
        for key, value in nested:
            emit(value, [*keys, key])
    emit(document, [])
    return '\n'.join(lines) + ('\n' if lines else ''), fixed


def _fixed_values(raw):
    """Values TOML cannot express travel as exact JSON beside the text."""
    try:
        value = json.loads(raw) if len(raw) <= 256_000 else None
    except (ValueError, TypeError, RecursionError):
        value = None
    if (not isinstance(value, list) or len(value) > 256 or any(
            not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], list) or not item[0]
            or any(not isinstance(key, str) for key in item[0]) for item in value)):
        raise ValueError('高级设置中保留的空值记录无效，请重新打开表单')
    return value


def load_catalogs(draft):
    """Acquire optional metadata only for the draft's exact selected Backend."""
    document=draft['document']
    backend=_get(document,'agent.backend',backends.DEFAULT_BACKEND) or backends.DEFAULT_BACKEND
    return {backend:model_catalog.load(backend,launcher=_get(document,'agent.launcher'),
        native=_get(document,'agent.native'),base=draft['base'])}


def render(draft, *, edit=False, catalogs=None):
    """Render a guided form; hidden steps retain successful, typed controls."""
    document = draft['document']
    current_backend = _get(document,'agent.backend',backends.DEFAULT_BACKEND) or backends.DEFAULT_BACKEND
    form_fields=_fields(current_backend)
    catalogs = load_catalogs(draft) if catalogs is None else catalogs
    short_hints = {
        'research.root': '服务器上一个尚不存在的目录，用来保存这项研究的全部记录。',
        'research.public_root': '服务器上已存在的目录，研究只能读取其中的文件；没有材料也需要一个空目录。',
        'research.objective': '服务器上的文本文件，原文会保存为本次任务。',
        'agent.backend': '运行研究角色的智能体程序。',
        'agent.model': '所有角色默认使用的模型。',
        'budget.max_tokens': '例如 5000000，也可填 unlimited。',
        'budget.max_seconds': '例如 3600 表示 1 小时，也可填 unlimited。',
        'budget.max_steps': '例如 40。',
        'permissions.workspace': '默认：可写工作目录。',
        'permissions.network': '默认：不允许。',
        'permissions.web': '默认：禁用。',
        'permissions.subagents': '默认：不允许。',
        'executor.config.executor': '已安装插件的绑定名，例如 tcad_execution:tcad。',
        'executor.config.config_file': '该插件的配置文件，相对路径按声明目录解析。',
    }
    short_hints.update({field.path: field.description for field in form_fields if field.description})
    tier_help = next((field.description for field in form_fields if field.path=='agent.service_tier'), '')
    if tier_help:
        short_hints.update({f'agent.roles.{role}.service_tier': tier_help for role in 'HWAE'})
    extras = copy.deepcopy(document)
    _delete(extras, 'research.objective_text')
    if edit:
        for key in ('research', 'ui', 'version', 'project', 'skills'):
            extras.pop(key, None)
    labels = {
        'research.root': '新研究保存到哪里', 'research.public_root': '研究可使用的材料目录',
        'research.objective': '任务文件路径', 'agent.model': '研究使用的模型',
        'agent.reasoning_effort': '推理力度', 'agent.service_tier': '服务档位',
        'agent.backend': '后端',
        'budget.max_tokens': '模型用量（tokens）', 'budget.max_seconds': '活动时间（秒）',
        'budget.max_steps': '节点上限（步）', 'permissions.workspace': '文件读写范围',
        'permissions.network': '允许程序联网', 'permissions.web': '网页检索方式',
        'permissions.subagents': '允许创建子研究助手',
        'budget.max_output_item_bytes': '单项输出（字节）',
        'budget.max_output_total_bytes': '单节点输出合计（字节）',
        'budget.candidate_extra_slots': 'W 提名预览额外名额（个）',
        'budget.portfolio_review_after_cycles': '组合复核（周期）',
        'budget.breadth.max_w_seconds': 'W 原调用计量锚点（秒）',
        'budget.breadth.max_w_observed_tokens': 'W 原调用计量锚点（tokens）',
        'budget.breadth.max_requested_repairs': '最多请求修复（次）',
        'budget.breadth.initial_calls': '首次探索调用（次）',
        'budget.correction.review_after': '复核阈值（次）',
        'budget.correction.pause_after': '暂停阈值（次）',
        'budget.role_shares.version': '规则版本',
        'budget.role_shares.min_w_share': 'W 最低份额（0–1）',
        'budget.role_shares.max_thinking_share': 'H / A 最高份额（0–1）',
        'budget.role_shares.thinking_node_target_tokens': 'H / A 单次目标（tokens）',
    }
    placeholders = {'research.root': '/srv/research/new-study', 'research.public_root': '/srv/research/materials',
                    'research.objective': '/srv/research/task.txt'}
    choice_labels = {'true':'允许', 'false':'不允许', 'read-only':'只读文件',
        'workspace-write':'可写工作目录', 'danger-full-access':'不限制文件访问',
        'disabled':'禁用网页检索', 'cached':'仅使用缓存', 'live':'实时检索',
        'local':'由本地执行插件执行', 'external':'由外部执行',
        'same_session':'沿用同一会话', 'new_context':'使用新上下文',
        'turn':'按单次调用计量'}


    node_identity = 0
    def structured(value, parent):
        nonlocal node_identity
        rows = []
        for key, item in (value.items() if isinstance(value, dict) else enumerate(value)):
            node_identity += 1
            identity = str(node_identity)
            prefix = 'node.'+identity+'.'
            kind = _kind(item)
            scalar = '' if kind in ('null', 'table', 'array') else str(item).lower() if type(item) is bool else str(item)
            rows.append('<div class="config-node" data-node="'+identity+'"><input type="hidden" name="'+prefix+'parent" value="'+escape(parent, quote=True)+'">'
                '<label>键名 <input name="'+prefix+'key" value="'+escape(str(key) if isinstance(value, dict) else '', quote=True)+'"></label>'
                '<label>类型 <select class="config-kind" name="'+prefix+'kind">'+''.join('<option value="'+choice+'"'+(' selected' if choice==kind else '')+'>'+choice+'</option>' for choice in ('string','integer','number','boolean','null','table','array','datetime','date','time'))+'</select></label>'
                '<label class="config-value">值 <input name="'+prefix+'value" value="'+escape(scalar, quote=True)+'"></label><button type="button" class="config-remove">删除此项</button>'
                '<div class="config-children" data-parent="'+identity+'">'+(structured(item, identity) if kind in ('table','array') else '')+'</div><button type="button" class="config-add" data-parent="'+identity+'">添加子项</button></div>')
        return ''.join(rows)

    def control(field, *, advanced=False, matrix=False):
        value = _get(document, field.path)
        for path, pending in draft.get('group_drafts', {}).items():
            if field.path.startswith(path+'.'):
                value = _get(pending, field.path[len(path)+1:], value)
        if field.kind not in ('strings', 'array', 'table') and isinstance(value, (dict, list)):
            return '<p class="note">'+escape(field.label)+'：已导入的非标准结构保留在高级设置中。</p>'
        if value is not None and field.kind not in ('strings', 'array', 'table'):
            _delete(extras, field.path)
        label = escape(field.label if field.description else labels.get(field.path, field.label))
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
        hint = ('<small class="field-help" id="'+help_id+'">'+escape(short_hints[field.path])+'</small>') if field.path in short_hints and (not matrix or field.path.endswith('.service_tier')) else ''
        described = ' aria-describedby="'+('role-settings-help' if matrix else help_id)+'"' if matrix or hint else ''
        attributes = ' data-config-path="'+escape(field.path,quote=True)+'" data-error-label="'+escape(field.label,quote=True)+'"'+(' data-invalid="true"' if isinstance(value, _Invalid) else '')
        text = '' if value is None else (str(value).lower() if type(value) is bool else str(value))
        if field.kind in ('strings', 'array', 'table'):
            valid = isinstance(value, list if field.kind in ('strings', 'array') else dict)
            multiline = False
            if field.kind == 'strings' and valid:
                valid = all(isinstance(item, str) for item in value)
                multiline = valid and any('\r' in item or '\n' in item for item in value)
            if value is not None and (not valid or multiline):
                reason = '含换行的逐项文本' if multiline else '已导入的非标准值'
                return '<p class="note">'+label+'：'+reason+'保留在高级设置中。</p>'
            if valid:
                _delete(extras, field.path)
            enabled = '<label class="check-label"><input type="checkbox" name="enabled.'+field.path+'" value="yes"'+(' checked' if valid else '')+'>明确声明'+label+'（取消则省略表单部分）</label>'
            if valid and field.path in GROUP_FIELDS:
                enabled += '<input type="hidden" name="present.'+field.path+'" value="yes">'
            if field.kind == 'strings':
                widget = '<div class="config-string-list" data-path="'+field.path+'">'+''.join('<div class="config-string-row"><textarea rows="1" name="cfg.'+field.path+'.'+str(index)+'">'+escape(item)+'</textarea><button type="button" class="config-string-remove">删除</button></div>' for index, item in enumerate(value or []))+'</div><button type="button" class="config-string-add" data-path="'+field.path+'">添加一项</button>'
            elif field.path in GROUP_FIELDS:
                # Expert resume protocol, metering and native home stay in TOML.
                if valid:
                    _put(extras, field.path, copy.deepcopy(value))
                visible = [child for child in GROUP_FIELDS[field.path] if child.path.rsplit('.',1)[-1] not in {'protocol','usage_scope','native_home'}]
                widget = '<div class="config-grid">'+''.join(control(child) for child in visible)+'</div>'
                if _get(extras, field.path) == {}:
                    _delete(extras, field.path)
            else:
                widget = '<div class="config-children" data-parent="@'+field.path+'">'+structured(value or ({} if field.kind=='table' else []), '@'+field.path)+'</div><button type="button" class="config-add" data-parent="@'+field.path+'">添加一项</button>'
            return '<fieldset class="config-group structured-setting"'+attributes+'><legend>'+label+'</legend>'+enabled+widget+hint+'</fieldset>'
        if field.choices or field.kind == 'boolean':
            choices = list(backends.names() if field.path=='agent.backend' else field.choices or ('true','false'))
            choices += [text] if text and text not in choices else []
            widget = '<select id="'+identity+'" name="cfg.'+field.path+'"'+described+(' data-default="'+escape(backends.DEFAULT_BACKEND,quote=True)+'"' if field.path=='agent.backend' else '')+'><option value="">默认'+('（'+escape(field.placeholder)+'）' if field.placeholder else '')+'</option>'+''.join(
                '<option value="'+escape(choice,quote=True)+'"'+(' selected' if text==choice else '')+'>'+escape(choice_labels.get(choice,choice))+'</option>' for choice in choices)+'</select>'
        elif field.path.split('.')[-1] in ('model', 'reasoning_effort', 'service_tier') and field.path.startswith('agent.'):
            kind = dict(model='model', reasoning_effort='effort', service_tier='tier')[field.path.split('.')[-1]]
            scope = field.path.split('.')[2] if field.path.startswith('agent.roles.') else 'global'
            inherit = ('沿用上方设置' if scope != 'global' else
                '默认（'+(field.placeholder or '由后端决定')+'）' if field.placeholder else '默认（由后端决定）')
            widget = ('<select class="model-picker" data-picker="'+kind+'" data-scope="'+scope+'" data-inherit="'+escape(inherit,quote=True)+'" hidden aria-label="'+escape(field.label,quote=True)+'"></select>'
                '<input id="'+identity+'" name="cfg.'+field.path+'" value="'+escape(text,quote=True)+'" placeholder="'+escape(field.placeholder or ('精确模型 ID' if kind=='model' else ''),quote=True)+'" class="model-picker-text" data-picker-text="'+kind+'" data-scope="'+scope+'"'+described+'>')
        else:
            suggestions = ()
            suggestion_id = identity+'-suggestions'
            widget = '<input id="'+identity+'" name="cfg.'+field.path+'" value="'+escape(text,quote=True)+'" placeholder="'+escape(placeholders.get(field.path,field.placeholder),quote=True)+'"'+described+(' inputmode="decimal"' if field.kind in ('integer','number') and field.path not in _UNLIMITED_BUDGET_PATHS else '')+(' list="'+suggestion_id+'"' if suggestions else '')+'>'
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
            if not edit and not choices and _get(document, 'agent.workers') is None:
                # Only the matrix is meaningful without named sets or researchers.
                contents.append('<input type="hidden" id="'+identity+'" name="skill_picker.choice.'+role+'" value="keep">')
                continue
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
    model_paths = ['agent.backend','agent.model','agent.reasoning_effort','agent.service_tier']
    budget_paths = ['budget.max_tokens','budget.max_seconds','budget.max_steps']
    permissions = ['permissions.workspace','permissions.network','permissions.web','permissions.subagents']
    heading = ('<section class="setup-panel configuration-editor" aria-labelledby="setup-limits-title">'
        '<div class="section-heading"><h2 id="setup-limits-title" tabindex="-1">调整后续研究配置</h2>'
        '<p>保存后从下一个新节点生效。已经准备、正在运行或恢复的节点继续使用原配置；已消耗的用量仍保留。</p></div>' if edit else
        '<section class="setup-panel" data-setup-step="2" aria-labelledby="setup-limits-title">'
        '<div class="section-heading"><span class="eyebrow">第二步 / 模型与预算</span><h2 id="setup-limits-title" tabindex="-1">用哪个模型，最多用多少？</h2>'
        '<p>选好模型并填写三项上限即可创建。其余设置都有默认值，需要时再展开。</p></div>')
    catalog_note = ''.join('<p class="note" data-catalog-note="'+escape(name,quote=True)+'"'+('' if name==current_backend else ' hidden')+'>'
        + escape('模型列表来自 '+value['source']+(('（更新于 '+str(value['fetched_at'])+'）') if value.get('fetched_at') else '')+'；也可选择“自定义”填写精确 ID。'
                 if value.get('models') else (value.get('error') or '此后端没有模型列表，请填写精确模型 ID。'))
        + (' '+escape(value['warning']) if value.get('warning') else '')+'</p>'
        for name, value in catalogs.items())
    rows.append(heading + '<fieldset class="config-group"><legend>模型</legend><div class="config-grid config-grid-four model-fields">'+fields(model_paths)+'</div>'+catalog_note+'</fieldset>'
        '<fieldset class="config-group" id="budget-settings"><legend>研究总上限</legend><div class="budget-fields">'+fields(budget_paths)+'</div>'
        '<p class="note">三项都必须填写，达到任一上限研究就会停下来等你决定。tokens 是模型用量，不是金额；时间按研究活动累计，等待外部实验、服务退避和暂停的时间都不计入；步数统计研究阶段及恢复、审计等控制动作。H/A/E 可在下方设置单次工作软目标；普通 W 仅依据上下文窗口与总预算，不设置额外收口目标。</p></fieldset>')
    role_paths = [f'agent.roles.{role}.{key}' for role in 'HWAE' for key in ('model','reasoning_effort','service_tier')]
    configured = any(_get(document, path) is not None for path in role_paths)
    rows.append('<details class="role-settings config-group" data-config-group="agent.roles"'+(' open' if configured else '')+'><summary>按角色指定模型'
        + ('<span class="configured-note">已设置</span>' if configured else '<span class="summary-note">可选，留空沿用上面的模型</span>')
        + '</summary><div class="config-matrix-scroll" role="region" aria-label="角色模型，可横向滚动" tabindex="0">'
        '<table class="config-role-matrix role-settings-matrix"><caption class="config-sr-only">四个研究角色的模型覆盖</caption>'
        '<thead><tr><th scope="col">研究角色</th><th scope="col">模型</th><th scope="col">推理力度</th><th scope="col">服务档位</th></tr></thead><tbody>')
    for role in 'HWAE':
        paths = [f'agent.roles.{role}.{key}' for key in ('model','reasoning_effort','service_tier')]
        worker_name = _get(document, f'agent.roles.{role}.worker')
        inheritance = '继承来源：命名研究者 '+str(worker_name)+'，其未声明项沿用全局。' if worker_name is not None else '未覆盖项沿用全局设置。'
        rows.append('<tr><th scope="row">'+role+' · '+ROLE_NAMES[role]+'<p class="note">'+ROLE_HELP[role]+'</p><p class="note">'+escape(inheritance)+'</p></th>'+''.join('<td>'+control(by_path[path], matrix=True)+'</td>' for path in paths)+'</tr>')
    rows.append('</tbody></table></div><p class="note" id="role-settings-help">角色显式值优先，其次沿用命名研究者，再沿用全局模型、推理力度与服务档位。Final A 使用 A 的配置。</p></details>')
    rows.append(skill_picker())
    current_supervision = _get(document, 'policies.supervision')
    current_mode = current_supervision.get('mode') if isinstance(current_supervision, dict) else None
    if isinstance(current_mode, str) and current_mode:
        _delete(extras, SUPERVISION_MODE_FIELD.path)
    supervision_control = ('<div class="config-field" data-config-path="'+SUPERVISION_MODE_FIELD.path+'" data-error-label="'+SUPERVISION_MODE_FIELD.label+'">'
        '<label for="config-policies-supervision-mode">'+SUPERVISION_MODE_FIELD.label+'</label><select id="config-policies-supervision-mode" name="cfg.'+SUPERVISION_MODE_FIELD.path+'">'
        + ('' if isinstance(current_mode, str) and current_mode else '<option value="">默认（开启）</option>')
        + ('<option value="'+escape(current_mode, quote=True)+'" selected>已导入的不支持值：'+escape(current_mode)+'</option>' if isinstance(current_mode, str) and current_mode and current_mode not in SUPERVISION_MODE_FIELD.choices else '')
        + ''.join('<option value="'+key+'"'+(' selected' if key==current_mode else '')+'>'+escape(label)+'</option>' for key, label in SUPERVISION_MODES)
        + '</select><small class="field-help">W 连续做了较多实验后，是否请独立 A 检查研究方向。</small></div>')
    rows.append(
        '<details class="permission-settings"><summary>权限与自动复核<span class="summary-note">留空使用默认值</span></summary>'
        '<div class="config-grid config-grid-four">'+fields(permissions)+'</div><div class="config-grid">'+supervision_control+'</div>'
        '<details><summary>自动 A 次数、间距与累计比例</summary><p class="note">普通 A 返回建议，由 W 决定下一步；这些限制仅约束额外自动 A。</p><div class="config-grid">'+fields([field.path for field in form_fields if field.path.startswith('policies.supervision.')])+'</div></details></details>')
    target_paths = [f'budget.node_targets.{role}.{key}' for role in 'HAE' for key in ('max_observed_tokens','max_seconds')]
    rows.append('<details class="node-target-settings"'+(' open' if any(_get(document, path)=='unlimited' for path in _UNLIMITED_BUDGET_PATHS) else '')+'><summary>H / A / E 单次工作软目标</summary><p class="note">建议收口目标，不是额度或强制转 A 的门槛。</p><p class="note" data-worker-target-policy="context-only">普通 W 不设额外时间/tokens 收口目标，仅依据上下文窗口与总预算。</p><p class="note">已有 W 有限锚点保留在高级 TOML，仅作原调用恢复与计量绑定，不是普通 W 收口条件。当前完整声明校验仍要求保留 W 锚点；仅填写 H/A/E 不会自动补造 W 数值。</p>'
        + ''.join('<fieldset class="config-group"><legend>'+role+' · '+ROLE_NAMES[role]+'</legend><div class="config-grid">'+fields([path for path in target_paths if path.split('.')[2]==role])+'</div></fieldset>' for role in 'HAE')+'</details>')
    rows.append('<details class="recovery-settings"><summary>恢复与重试</summary><p class="note">仅恢复同一原调用，保留已耗预算；未知提交须核对原调用，不能重新提交。修改本组请明确勾选声明。</p>'+control(by_path['policies.resume'])+'</details>')
    rows.append('<details class="context-settings"><summary>W 上下文与窗口</summary><div class="config-grid">'+fields(['policies.worker_context','policies.worker_context_windows'])+'</div><p class="note">固定交接规则：W 上下文达到 90% 时准备安全检查点，原生压缩可以继续；仅在当前压缩后占用仍超过 70% 时交接。阈值不可调整，窗口数上限仍受原有检查，窗口不重置节点目标或预算。</p></details>')
    native_fields = [field.path for field in form_fields if field.path.startswith('agent.native.')]
    rows.append('<details class="backend-native-settings"><summary>后端原生设置与准确启动命令</summary><p class="note">启动命令逐项保存 argv，不按 shell 拆词；切换后端保留各自草稿。留空原生字段不覆盖后端设置。</p>'+control(by_path['agent.launcher'])+fields(native_fields)+'</details>')
    executor_paths = ['executor.config.executor','executor.config.config_file']
    configured = any(_get(document, path) is not None for path in executor_paths)
    rows.append('<details class="executor-settings"'+(' open' if configured else '')+'><summary>实验执行插件'
        + ('<span class="configured-note">已设置</span>' if configured else '<span class="summary-note">只在需要接入求解器等外部执行时填写</span>')
        + '</summary><div class="config-grid">'+fields(executor_paths)+'</div></details>')
    if edit:
        for key in ('research', 'ui', 'version', 'project', 'skills'):
            extras.pop(key, None)
    advanced_note = ''
    native_fields = [field for field in form_fields if field.path.startswith('agent.native.')]
    if native_fields:
        advanced_note += ('<details class="native-options"><summary>当前后端可用的原生选项（写在 <code>[agent.native]</code> 下）</summary><ul class="note">'
            + ''.join('<li><code>'+escape(field.path.removeprefix('agent.native.'))+'</code>：'+escape(field.description or field.label)+'</li>' for field in native_fields)
            + '</ul></details>')
    resume_defaults=(backends.provider(current_backend).resume_policy_defaults()
        if current_backend in backends.names() else None)
    if resume_defaults:
        advanced_note += ('<p class="note" data-resume-protocol="'+escape(resume_defaults['protocol'],quote=True)+'">'
            '默认恢复协议：<code>'+escape(resume_defaults['protocol'])+'</code>，由所选后端自动填写。'
            '只调整恢复次数或重试选项时无需填写协议；显式协议仍会校验，启动器支持情况在检查配置时验证。</p>')
    if 'advanced_text' in draft:
        advanced_text, fixed = draft['advanced_text'], draft.get('advanced_fixed', [])
    else:
        advanced_text, fixed = advanced_toml(extras)
    if fixed:
        advanced_note += ('<p class="note">以下设置包含空值（null），TOML 无法表示，已原样保留：'
            + escape('、'.join('.'.join(keys) for keys, _ in fixed))
            + '。如需修改，在上面写入同名键即可覆盖。</p>')
    configured = bool(advanced_text.strip() or fixed)
    rows.append('<details class="advanced-settings" id="advanced-settings"'+(' open' if 'advanced_text' in draft and configured else '')+'><summary>高级设置（TOML）'
        + ('<span class="configured-note">已有设置</span>' if configured else '<span class="summary-note">恢复策略、原生环境、工程、交付等</span>')
        + '</summary><p class="note">表单之外的所有设置都写在这里，格式与研究 TOML 文件相同，例如 <code>[policies.resume]</code>、<code>[agent.native]</code>、<code>[budget.breadth]</code>。'
        + '留空表示全部使用默认值。保存或检查时会完整校验；与上方表单重复的键会提示冲突。字段说明见 docs/RESEARCH_CONFIGURATION.md。</p>'
        + '<textarea name="advanced_toml" rows="'+str(min(30, max(6, advanced_text.count(chr(10))+2)))+'" spellcheck="false" class="advanced-toml" aria-label="高级设置（TOML）">'+escape(advanced_text)+'</textarea>'
        + '<input type="hidden" name="advanced_fixed" value="'+escape(json.dumps(fixed, ensure_ascii=False),quote=True)+'">'
        + advanced_note + '</details>'
        + ('</section>' if edit else '<div class="setup-actions"><button type="button" hidden class="secondary-button" data-setup-go="1">← 返回任务与材料</button><span class="note">下一步会检查配置，尚不创建或运行研究。</span></div></section>'))
    catalog_json = json.dumps({name: [dict(id=m['id'], label=m['label'], efforts=m['efforts'], tiers=m['tiers']) for m in value.get('models', [])]
        for name, value in catalogs.items()}, ensure_ascii=False).replace('<', '\\u003c')
    rows.append('<script type="application/json" id="model-catalog">'+catalog_json+'</script>')
    rows.append('<script src="/assets/configuration.js"></script>')
    baseline = {path: (str(value).lower() if type(value) is bool else str(value)) if (value := _get(document, path)) is not None and not isinstance(value, (dict, list)) else '' for path in [*(field.path for field in form_fields if field.path.startswith('policies.supervision.')), 'policies.supervision.mode']}
    if not isinstance(_get(document, SUPERVISION_MODE_FIELD.path), str):
        baseline[SUPERVISION_MODE_FIELD.path] = ''
    metadata='<input type="hidden" name="supervision_baseline" value="'+escape(json.dumps(baseline, ensure_ascii=False),quote=True)+'">'
    metadata+='<input type="hidden" name="rendered_backend" value="'+escape(
        str(_get(document,'agent.backend',backends.DEFAULT_BACKEND) or backends.DEFAULT_BACKEND),quote=True)+'">'
    saved_native=draft.get('native_drafts',{})
    if saved_native:
        metadata+='<input type="hidden" name="native_drafts" value="'+escape(
            json.dumps(saved_native,ensure_ascii=False,separators=(',',':')),quote=True)+'">'
        metadata+=('<p class="note">已保留以下后端的原生设置草稿：'+escape('、'.join(saved_native))
            +'。切回对应后端时恢复；这些设置不传给当前后端。</p>')
    saved_launchers=draft.get('launcher_drafts',{})
    if saved_launchers:
        metadata+='<input type="hidden" name="launcher_drafts" value="'+escape(
            json.dumps(saved_launchers,ensure_ascii=False,separators=(',',':')),quote=True)+'">'
        metadata+=('<p class="note">已保留以下后端的启动命令草稿：'+escape('、'.join(saved_launchers))
            +'。切回对应后端时恢复；当前后端使用自己的启动命令。</p>')
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
(() => {
  const source = document.getElementById('model-catalog');
  if (!source) return;
  const catalogs = JSON.parse(source.textContent);
  const backendSelect = document.querySelector('[name="cfg.agent.backend"]');
  const backend = () => (backendSelect && backendSelect.value) || (backendSelect && backendSelect.dataset.default) || 'codex';
  const text = (kind, scope) => document.querySelector('[data-picker-text="'+kind+'"][data-scope="'+scope+'"]');
  const picker = (kind, scope) => document.querySelector('select[data-picker="'+kind+'"][data-scope="'+scope+'"]');
  const modelFor = scope => {
    const own = text('model', scope);
    if (scope !== 'global' && own && own.value) return own.value;
    const global = text('model', 'global');
    return (global && (global.value || global.placeholder)) || '';
  };
  function fill(select) {
    const kind = select.dataset.picker, scope = select.dataset.scope, input = text(kind, scope);
    const models = catalogs[backend()] || [];
    let values;
    if (kind === 'model') values = models.map(model => [model.id, model.label === model.id ? model.id : model.label + ' · ' + model.id]);
    else {
      const model = models.find(item => item.id === modelFor(scope));
      values = (model ? (kind === 'effort' ? model.efforts : model.tiers) : []).map(value => [value, value]);
    }
    if (!values.length) { select.hidden = true; input.hidden = false; return; }
    select.replaceChildren();
    const add = (value, label) => { const option = document.createElement('option'); option.value = value; option.textContent = label; select.append(option); };
    add('', select.dataset.inherit);
    values.forEach(([value, label]) => add(value, label));
    add('__custom__', '自定义…');
    const known = !input.value || values.some(([value]) => value === input.value);
    select.value = known ? input.value : '__custom__';
    select.hidden = false; input.hidden = known;
  }
  const all = () => document.querySelectorAll('select[data-picker]').forEach(fill);
  document.addEventListener('change', event => {
    const select = event.target.closest('select[data-picker]');
    if (select) {
      const input = text(select.dataset.picker, select.dataset.scope);
      if (select.value === '__custom__') { input.hidden = false; input.focus(); }
      else { input.value = select.value; input.hidden = true; }
      input.dispatchEvent(new Event('change', {bubbles: true}));
      if (select.dataset.picker === 'model') document.querySelectorAll('select[data-picker]:not([data-picker="model"])').forEach(fill);
      return;
    }
    if (event.target === backendSelect) {
      const form = backendSelect.closest('form');
      if (form && ['research-config-form','research-config-edit-form'].includes(form.id)) {
        const refresh = document.createElement('button');
        refresh.type = 'submit'; refresh.name = 'mode'; refresh.value = 'refresh';
        refresh.hidden = true; refresh.formNoValidate = true;
        form.append(refresh); form.requestSubmit(refresh); return;
      }
      document.querySelectorAll('[data-catalog-note]').forEach(note => { note.hidden = note.dataset.catalogNote !== backend(); });
      all();
    }
  });
  all();
  const openHash = () => { const target = location.hash && document.querySelector(location.hash); if (target && target.tagName === 'DETAILS') target.open = true; };
  openHash(); window.addEventListener('hashchange', openHash);
})();
let next = Math.max(0, ...Array.from(document.querySelectorAll('.config-node')).map(node => Number(node.dataset.node)), ...Array.from(document.querySelectorAll('.config-string-list textarea')).map(node => Number(node.name.split('.').pop())));
const update = node => {
  const kind = node.querySelector('.config-kind').value;
  const composite = kind === 'table' || kind === 'array';
  node.querySelector('.config-value').hidden = composite || kind === 'null';
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
  // Optional policy groups require an explicit opt-in.
  if (enabled && group.querySelector('.config-string-list')) enabled.checked = true;
};
document.addEventListener('input', enableEditedGroup); document.addEventListener('change', enableEditedGroup);
document.addEventListener('click', e => { if (e.target.matches('.config-add,.config-remove,.config-string-add,.config-string-remove')) invalidate(e); });
document.addEventListener('change', e => {
  if (!e.target.matches('.config-kind')) return;
  const node = e.target.closest('.config-node');
  if (e.target.value === 'null') node.querySelector('.config-value input').value = '';
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
  for (const [value,label] of [['string','文本'],['integer','整数'],['number','数字'],['boolean','布尔'],['null','空值（null）'],['table','对象'],['array','数组'],['datetime','日期时间'],['date','日期'],['time','时间']]) {
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
            _typed(str(value).lower() if type(value) is bool else str(value), field.kind, field.label, path=field.path)
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
        if kind == 'null': raise ValueError('TOML 不支持空值（null）；请保留当前表单，或明确修改空值后再导出')
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
