"""Pure, bounded comparison of public operational declaration fields."""
from __future__ import annotations

import math


_MISSING = object()
_UNKNOWN = object()


def _get(document, path):
    for key in path.split('.'):
        if not isinstance(document, dict):
            return _UNKNOWN
        if key not in document:
            return _MISSING
        document = document[key]
    return document


def _same(left, right, *, kind=None):
    if kind == 'number' and all(type(value) is int or type(value) is float and math.isfinite(value)
            for value in (left, right)):
        return left == right
    if type(left) is not type(right):
        return False
    if left is _MISSING or left is _UNKNOWN:
        return left is right
    if left is None or type(left) in (str, bool, int, float):
        return left == right
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return False


def _value(value, field):
    path=field.path
    if value is _MISSING:
        return '继承全局或命名研究者（待校验）' if path.startswith('agent.roles.') else '未声明（默认值待校验）'
    if value is _UNKNOWN:
        return '未知（当前值不可用）'
    if value is None:
        return '空值（null）'
    if path == 'agent.launcher' and type(value) is list and all(type(item) is str for item in value):
        executable=value[0].replace('\\', '/').rsplit('/', 1)[-1] if value else ''
        return (executable + ' · ' + str(len(value)) + ' 项（路径与参数值省略）') if value else '空启动命令'
    if type(value) is bool:
        return 'true（允许 / 开启）' if value else 'false（不允许 / 关闭）'
    if type(value) is int or type(value) is float and math.isfinite(value):
        return str(value)
    if type(value) is str:
        # Public field metadata does not make arbitrary native content public.
        if path.startswith('agent.native.') and value not in field.choices:
            return '已声明（内容不在摘要展示）'
        return value[:160] + ('…' if len(value) > 160 else '')
    if type(value) is list and all(type(item) is str for item in value):
        return str(len(value)) + ' 项（内容不在摘要展示）'
    return '待修正（尚未验证）'


def rows(active, draft, *, fields, budget_available=True):
    """Compare supplied facts; do not normalize, query providers or read records.

    Field metadata comes from the form/provider. Arbitrary nested extensions,
    scientific inputs, private recovery homes and credentials are never expanded.
    """
    result, seen = [], set()
    for field in fields:
        path = field.path
        public = (path in ('agent.backend', 'agent.launcher', 'agent.model', 'agent.reasoning_effort', 'agent.service_tier')
            or path.startswith(('agent.roles.', 'agent.native.', 'permissions.', 'budget.', 'policies.worker_context', 'policies.supervision.'))
            or path.startswith('policies.resume.') and path != 'policies.resume.native_home')
        if not public or path in seen or field.kind in ('array', 'table'):
            continue
        seen.add(path)
        before, after = _get(active, path), _get(draft, path)
        if not budget_available and path in ('budget.max_tokens', 'budget.max_seconds', 'budget.max_steps'):
            before = _UNKNOWN
        if _same(before, after, kind=field.kind):
            continue
        old, new = _value(before, field), _value(after, field)
        if old == new:
            new += '（路径或参数已变化，见启动命令）' if path == 'agent.launcher' else '（声明内容已变化，见对应设置）'
        scope = ('研究总上限；已用额度保留' if path in ('budget.max_tokens', 'budget.max_seconds', 'budget.max_steps')
            else path.split('.')[2] + ' 角色的后续新节点' if path.startswith('agent.roles.')
            else '后续新节点；原调用恢复不变' if path.startswith('policies.resume.') else '后续新节点')
        result.append(dict(path=path, label=field.label, active=old, draft=new, scope=scope))
    return result
