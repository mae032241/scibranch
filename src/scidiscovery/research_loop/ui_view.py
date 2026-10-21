"""Read-only workbench view models: plain data, no HTML, no writes.

Every projection reuses existing sealed/control readers. A failing projection
returns ``{"error": {...}}`` for its own component and never fails the page.
Control supplies no scientific ranking or default choice: candidate rows keep
first-observation order and only author/control records mark A or delivery.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import time

from . import contracts as c, instance_display as display
from . import native_usage, public_output

ROLE_LABELS = {'H': 'H · 假设', 'W': 'W · 实验', 'A': 'A · 综合评估', 'E': 'E · 证据'}
ROLE_VERBS = {'H': 'H 正在提出假设', 'W': 'W 正在实验', 'A': 'A 正在综合评估', 'E': 'E 正在整理证据'}
STATE_LABELS = {'prepared': '等待开始', 'running': '正在运行', 'waiting': '等待外部工作',
    'seal_present_unverified': '已有阶段结论', 'seal_present_originals_verified': '已有阶段结论',
    'native_outcome_unsealed': '工作已结束，结论尚未记录', 'native_not_submitted': '尚未提交',
    'native_intent_present_unknown': '原任务状态待核对', 'unknown': '原任务状态待核对'}
SEALED = {'seal_present_unverified', 'seal_present_originals_verified'}
A_OUTCOMES = {'continue_w': '继续实验', 'revise_h': '修订方向', 'extract': '补充证据', 'wait': '等待条件',
    'finish': '结束探索', 'deliver': '交付', 'audit': '请求审计'}
H_ACTIONS = {'experiment': '安排实验', 'analyze': '分析已有证据', 'extract': '补充证据', 'wait': '等待条件'}
DISPOSITIONS = {'screened': '已筛查', 'deferred': '暂缓', 'waived': '放弃', 'pending': '待评估',
    'excluded': '排除', 'concentrated': '集中投入'}
GOAL_COMPLETION = {'not_asserted': '未声称完成目标', 'asserted': '作者声称完成目标'}
STATES = {
    'ready': '未开始', 'idle': '待继续', 'active': '进行中', 'pause_requested': '即将暂停', 'paused': '已暂停',
    'waiting': '等待条件', 'unknown': '需要核对', 'final': '最终评估', 'budget_exhausted': '预算已用完',
    'completed': '已完成', 'error': '状态不可读',
}
EXPERIMENT_TOOLTIP = '本阶段实验数 = 封存观察中已接受的受管调用数；不等于独立实验设计数或科学覆盖。'
EXTREME_TOOLTIP = '仅在同一口径（evaluation_key）内按作者声明的指标方向取数值极值；不代表科学排序。'
CANDIDATE_LIMIT = 20
ROUND_LIMIT = 5
WINDOW = 50


# ---------------------------------------------------------------- formatting

def fmt_int(value):
    return f'{int(value):,}'


def _trim(number):
    text = f'{number:.1f}'
    return text[:-2] if text.endswith('.0') else text


def fmt_tokens(value):
    """< 1 万 原样带千分位；≥ 1 万 用“万”，保留 1 位小数。"""
    if value is None:
        return '未记录'
    value = int(value)
    return fmt_int(value) if abs(value) < 10000 else _trim(value / 10000) + ' 万'


def fmt_duration(seconds):
    if seconds is None:
        return '未记录'
    seconds = max(0, int(math.floor(float(seconds) + 0.5)))
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        if not minutes:
            return f'{rest} 秒'
        return f'{minutes} 分' + (f' {rest} 秒' if rest else '')
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f'{hours} 小时' + (f' {minutes} 分' if minutes else '')


def percent(used, limit):
    if used is None or not limit:
        return None
    return int(math.floor(used * 100 / limit + 0.5))


def fmt_metric(value):
    """Author precision, at most four decimals; never a bare inferred value."""
    if value is None:
        return '—'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return fmt_int(value) if abs(value) >= 10000 else str(value)
    if not math.isfinite(value):
        return str(value)
    text = repr(value)
    if 'e' in text or 'E' in text or len(text.partition('.')[2]) > 4:
        text = f'{value:.4f}'.rstrip('0').rstrip('.')
    return text


def first_sentence(text, limit=240):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    match = re.search(r'(?<=[.!?])\s|(?<=[。！？])(?=\S)|\n', text)
    sentence = text[:match.start()].strip() if match else text
    return sentence if len(sentence) <= limit else sentence[:limit - 1].rstrip() + '…'


def objective_title(text, fallback):
    if isinstance(text, str):
        for line in text.splitlines():
            line = re.sub(r'^\s*(?:#+|>|[-*+]\s|\d+\.\s)\s*', '', line)
            line = re.sub(r'[*_`]+', '', line).strip()
            if line:
                return line if len(line) <= 60 else line[:59].rstrip() + '…'
    return fallback


def direction_sense(direction):
    """Only an explicit author declaration selects max/min; otherwise unknown."""
    if not isinstance(direction, str):
        return None
    text = ' '.join(direction.lower().split()).removesuffix('.')
    if text in {'max', 'maximize', 'maximise', 'higher', 'larger', 'greater', 'bigger',
                'higher is better', 'higher values are better', 'larger is better',
                'greater is better', 'bigger is better'}:
        return 'max'
    if text in {'min', 'minimize', 'minimise', 'lower', 'smaller', 'less',
                'lower is better', 'lower values are better', 'smaller is better', 'less is better'}:
        return 'min'
    return None


# ---------------------------------------------------------------- safe wrapper

def failure(error, clean=None):
    message = str(error) or type(error).__name__
    if clean is not None:
        try:
            message = clean(message, 300)
        except Exception:  # noqa: BLE001 - cleaning failure must not leak the raw text
            message = '错误信息不可显示'
    else:
        message = '错误信息不可显示' if '/' in message else message[:300]
    return {'error': {'kind': type(error).__name__, 'message': message}}


def guarded(function, *args, clean=None, **options):
    try:
        return function(*args, **options)
    except Exception as error:  # noqa: BLE001 - one component, one inline error
        return failure(error, clean)


def cleaner(root):
    try:
        return public_output.cleaner(root, settings=display.settings(root))
    except Exception:  # noqa: BLE001 - unreadable settings still hide the root
        return public_output.cleaner(root, settings={})


# ---------------------------------------------------------------- sealed reads

class Reader:
    """Saved sealed records for display; scientific admission owns verification."""

    def __init__(self, root, memo=None):
        self.root = root
        self._nodes = {}
        self._observations = {}
        self._roles = {}

    def node(self, step):
        if step not in self._nodes:
            self._nodes[step] = display.node(self.root, step)
        return self._nodes[step]

    def observation(self, step):
        """Sealed observer snapshot bound to this node's exact output, if any."""
        if step not in self._observations:
            node = self.node(step)
            self._observations[step] = display.observation(self.root, step, node=node)
        return self._observations[step]

    def role(self, step):
        if step not in self._roles:
            self._roles[step] = c.read(self.root / 'jobs' / f'{step:03d}.json')['role']
        return self._roles[step]


def clean_row(row, clean):
    """Observer candidate facts with author text passed through the public cleaner."""
    value = dict(row)
    value['evaluation_identity'] = row.get('evaluation_key')
    for key, limit in (('label', 300), ('evaluation_key', 300), ('direction', 200), ('status', 60)):
        if isinstance(value.get(key), str):
            value[key] = clean(value[key], limit)
    return value


def accepted_calls(observation):
    if observation is None:
        return None
    return sum(1 for call in observation.get('managed_calls', []) if isinstance(call, dict) and call.get('accepted'))


def resolve_candidate(root, job, value, references=None, *, strict=False):
    """Author reference -> canonical id via frozen task mapping; never inferred.

    ``strict`` raises when the frozen mapping rejects the reference instead of
    displaying the author's raw text as if it were an identity.
    """
    if not isinstance(value, str):
        return None
    from . import candidate_refs
    try:
        resolved = candidate_refs.resolve_reference(root, job, value)
        if re.fullmatch(r'[HWAE]\d{3,}-I\d{3,}', resolved):
            match = re.fullmatch(r'([HWAE])(\d{3,})-I(\d{3,})', resolved)
            step, index = int(match[2]), int(match[3])
            if step in {row['step'] for row in job.get('history', {}).get('nodes', [])}:
                reader = Reader(root)
                node = reader.node(step)
                materials = sorted(node['seal']['materials'], key=lambda row: row['path'])
                if node['job']['role'] == match[1] and 1 <= index <= len(materials):
                    target = materials[index - 1]['path'].replace('inputs/parents/', 'history/nodes/', 1)
                    matches = {row['id'] for row in (reader.observation(step) or {}).get('candidates', [])
                               if row.get('implementation') == target}
                    if len(matches) == 1:
                        resolved = matches.pop()
            if strict and re.fullmatch(r'[HWAE]\d{3,}-I\d{3,}', resolved):
                raise ValueError('Implementation has no unique frozen candidate binding')
    except ValueError:
        if strict:
            raise
        resolved = value
    if references and resolved not in references:
        inverse = {ref: identity for identity, ref in references.items()}
        resolved = inverse.get(resolved, resolved)
    return resolved


# ---------------------------------------------------------------- header

def header(root, objective):
    clean = cleaner(root)
    name = root.name
    text = clean(objective, 4000) if isinstance(objective, str) else ''
    excerpt = text[:240] + ('…' if len(text) > 240 else '')
    return dict(title=objective_title(text, name), objective_excerpt=excerpt, instance_name=name)


# ---------------------------------------------------------------- mechanisms

def mechanism_labels(reader, steps):
    labels = {}
    for step in steps:
        try:
            if reader.role(step) != 'H':
                continue
            payload = reader.node(step)['payload']
        except Exception:  # noqa: BLE001 - unsealed or unreadable H keeps refs unlabeled
            continue
        plan = payload.get('search_plan') if isinstance(payload.get('search_plan'), dict) else {}
        for index, item in enumerate(plan.get('commitments') or [], 1):
            if not isinstance(item, dict) or not isinstance(item.get('label'), str):
                continue
            labels.setdefault(f'h{step:03d}.m{index}', item['label'])
            if isinstance(item.get('existing_ref'), str):
                labels.setdefault(item['existing_ref'], item['label'])
    return labels


# ---------------------------------------------------------------- role lines

def _links(node, sealed, materials=None):
    step = node['step']
    base = '/nodes/' + str(step)
    progress = dict(label='公开进度', href=base + '/progress?attempt=' + str(node.get('continuations') or 0))
    if not sealed:
        return [progress]
    files = '研究文件' + (f'（{materials} 份）' if materials is not None else '')
    return [dict(label='阶段结论', href='/results/' + str(step)), dict(label='完整原文', href=base + '/science'),
            dict(label=files, href=base + '/materials'), progress]


def _candidate_extreme(rows):
    """Within one evaluation_key only; author-declared direction decides max/min."""
    keys = list(dict.fromkeys(row.get('evaluation_identity', row.get('evaluation_key')) for row in rows))
    numeric = [row for row in rows if isinstance(row.get('metric'), (int, float)) and not isinstance(row.get('metric'), bool)
               and math.isfinite(row['metric'])]
    if not rows:
        return None
    if len(keys) > 1:
        return dict(text='多种口径，见候选表', title=EXTREME_TOOLTIP)
    if keys[0] is None or not isinstance(keys[0], str) or not keys[0].strip():
        return dict(text='口径未声明，无法比较', title=EXTREME_TOOLTIP)
    if not numeric:
        return dict(text='无数值指标', title=EXTREME_TOOLTIP)
    key = rows[0].get('evaluation_key')
    senses = {direction_sense(row.get('direction')) for row in numeric}
    sense = senses.pop() if len(senses) == 1 else None
    values = [row['metric'] for row in numeric]
    if sense == 'max':
        return dict(text='最高 ' + fmt_metric(max(values)) + '（' + key + '）', title=EXTREME_TOOLTIP, value=max(values), key=key, sense='max')
    if sense == 'min':
        return dict(text='最低 ' + fmt_metric(min(values)) + '（' + key + '）', title=EXTREME_TOOLTIP, value=min(values), key=key, sense='min')
    return dict(text='范围 ' + fmt_metric(min(values)) + '–' + fmt_metric(max(values)) + '（' + key + '；方向未声明）',
                title=EXTREME_TOOLTIP, key=key)


def role_line(reader, node, clean, *, mechanisms=None, labels=None, references=None):
    step, role = node['step'], node['role']
    state = node.get('state')
    line = dict(step=step, role=role, role_label=ROLE_LABELS.get(role, role), state=state,
                state_label=STATE_LABELS.get(state, '状态待核对'), sealed=state in SEALED,
                kind='ordinary', one_line=None, facts=[], links=_links(node, False), next_action=None)
    if not line['sealed']:
        return line
    sealed = reader.node(step)
    job, payload, seal = sealed['job'], sealed['payload'], sealed['seal']
    line['links'] = _links(node, True, len(seal.get('materials') or []))
    line['materials'] = len(seal.get('materials') or [])
    if job.get('formal_review'):
        line['kind'] = 'formal_review'
        line['role_label'] = 'A · 正式评审'
    elif job.get('purpose') == 'closeout':
        line['kind'] = 'closeout'
        line['role_label'] = 'A · 最终评估'
    summary = payload.get('summary')
    if isinstance(summary, str) and summary.strip():
        line['one_line'] = clean(summary.strip(), 600)
    else:
        sentence = first_sentence(payload.get('scientific_result'), 600)
        line['one_line'] = clean(sentence, 600) if sentence else '未记录摘要'
    facts = line['facts']
    action = payload.get('next_action') if isinstance(payload.get('next_action'), str) else None
    line['next_action'] = action
    mechanisms = mechanisms or {}
    if role == 'H':
        plan = payload.get('search_plan') if isinstance(payload.get('search_plan'), dict) else None
        commitments = [item for item in (plan or {}).get('commitments') or [] if isinstance(item, dict)]
        if plan is None:
            facts.append(dict(label='机制', value='未记录'))
        else:
            names = [clean(str(item.get('label') or '未命名'), 160) for item in commitments]
            line['mechanisms'] = [dict(label=clean(str(item.get('label') or '未命名'), 300),
                                       minimal_test=clean(str(item.get('minimal_test') or ''), 2000),
                                       existing_ref=item.get('existing_ref')) for item in commitments]
            facts.append(dict(label='机制', value=str(len(commitments)) + ' 个' + ('：' + '、'.join(names[:5]) if names else '')
                              + (f' 等（另 {len(names) - 5} 个）' if len(names) > 5 else '')))
        facts.append(dict(label='行动', value=H_ACTIONS.get(action, action) if action else '未记录'))
    elif role == 'W':
        observation = reader.observation(step)
        calls = accepted_calls(observation)
        rows = [clean_row(row, clean) for row in (observation or {}).get('candidates', []) if isinstance(row, dict)]
        line['experiments'] = calls
        line['candidate_rows'] = rows
        facts.append(dict(label='本阶段实验', value=(str(calls) + ' 次') if calls is not None else '未记录', title=EXPERIMENT_TOOLTIP))
        if observation is None:
            facts.append(dict(label='候选', value='未记录'))
        else:
            extreme = _candidate_extreme(rows)
            facts.append(dict(label='候选', value=str(len(rows)) + ' 个' + ('；' + extreme['text'] if extreme else ''),
                              title=extreme['title'] if extreme else None))
            line['extreme'] = extreme
    elif role == 'A':
        if line['kind'] == 'formal_review':
            verdict = payload.get('verdict')
            facts.append(dict(label='评审结论', value=clean(str(verdict), 200) if verdict is not None else '未记录'))
        else:
            facts.append(dict(label='决定', value=A_OUTCOMES.get(action, action) if action else '未记录'))
        reason = first_sentence(payload.get('route_reason'))
        if reason:
            facts.append(dict(label='理由', value=clean(reason, 400)))
        review = payload.get('search_review') if isinstance(payload.get('search_review'), dict) else None
        if review is not None:
            counts = {}
            for item in review.get('dispositions') or []:
                if isinstance(item, dict):
                    counts[item.get('status')] = counts.get(item.get('status'), 0) + 1
            if counts:
                facts.append(dict(label='机制处置', value='，'.join(
                    DISPOSITIONS.get(key, str(key)) + ' ' + str(value) for key, value in counts.items())))
            focus = review.get('next_focus') if isinstance(review.get('next_focus'), dict) else None
            if focus and focus.get('mechanism_ref'):
                ref = focus['mechanism_ref']
                facts.append(dict(label='下一焦点', value=clean(mechanisms.get(ref, str(ref)), 200)
                                  + (' · ' + clean(str(focus['action']), 60) if focus.get('action') else '')))
        if line['kind'] != 'formal_review' and 'candidate_recommendation' in payload:
            value = payload.get('candidate_recommendation')
            if action == 'audit':
                identity = resolve_candidate(reader.root, job, value, references)
                line['audit_target'] = identity
                label = (labels or {}).get(identity)
                facts.append(dict(label='审计对象', value=clean(label or str(value), 300) if value is not None else '未指定'))
            elif value is None:
                facts.append(dict(label='候选推荐', value='未推荐'))
            else:
                identity = resolve_candidate(reader.root, job, value, references)
                line['recommendation'] = identity
                label = (labels or {}).get(identity)
                facts.append(dict(label='候选推荐', value='★ ' + clean(label if label else str(value), 300)))
    elif action:
        facts.append(dict(label='行动', value=clean(action, 60)))
    return line


# ---------------------------------------------------------------- rounds

def _round_summary(item):
    parts = ['第 ' + str(item['index']) + ' 轮' if item['index'] else '准备', item['outcome']]
    sealed = [line for line in item['w'] if line.get('sealed')]
    if sealed:
        experiments = [line.get('experiments') for line in sealed]
        known = [value for value in experiments if value is not None]
        parts.append('W ' + (str(sum(known)) if known else '?') + ' 次实验' + ('（部分未记录）' if len(known) != len(experiments) else ''))
    if len(sealed) != len(item['w']):
        parts.append('W 进行中')
    if item['w']:
        rows = [row for line in item['w'] for row in line.get('candidate_rows', [])]
        extreme = _candidate_extreme(rows)
        if extreme:
            parts.append(extreme['text'])
    return ' · '.join(parts)


def rounds(reader, nodes, clean, *, completed=False, mechanisms=None, labels=None, references=None):
    if not nodes:
        return dict(rounds=[], omitted=0, total=0, before=0)
    first = nodes[0]['step']
    before = 0
    for step in range(1, first):
        try:
            before += reader.role(step) == 'H'
        except Exception:  # noqa: BLE001 - earlier numbering becomes unknown, not invented
            before = None
            break
    items, current = [], None
    for node in nodes:
        if node['role'] == 'H' or current is None:
            index = None
            if node['role'] == 'H':
                index = (before + 1 + sum(1 for item in items if item['index'])) if before is not None else None
            elif before:
                index = before  # Window starts inside an earlier round.
            current = dict(index=index, steps=[], h=None, w=[], a=[], others=[], outcome='进行中',
                           partial=node['role'] != 'H' and node['step'] != 1)
            items.append(current)
        line = guarded(role_line, reader, node, clean, mechanisms=mechanisms, labels=labels, references=references, clean=clean)
        if 'error' in line:
            line = dict(step=node['step'], role=node['role'], role_label=ROLE_LABELS.get(node['role'], node['role']),
                        state=node.get('state'), state_label=STATE_LABELS.get(node.get('state'), '状态待核对'),
                        sealed=False, kind='ordinary', one_line=None, facts=[], links=_links(node, False), error=line['error'])
        current['steps'].append(node['step'])
        if node['role'] == 'H' and current['h'] is None:
            current['h'] = line
        elif node['role'] == 'W':
            current['w'].append(line)
        elif node['role'] == 'A':
            current['a'].append(line)
        else:
            current['others'].append(line)
    for item in items:
        decisive = [line for line in item['a'] if line.get('sealed') and line.get('kind') != 'formal_review']
        if decisive:
            action = decisive[-1].get('next_action')
            item['outcome'] = A_OUTCOMES.get(action, action) if action else '未记录'
        elif completed and item is not items[-1]:
            item['outcome'] = '未记录'
        item['experiments'] = sum(line.get('experiments') or 0 for line in item['w'])
        item['summary'] = _round_summary(item)
    total = len(items)
    shown = items[-ROUND_LIMIT:]
    return dict(rounds=list(reversed(shown)), omitted=total - len(shown), total=total, before=before)


# ---------------------------------------------------------------- status

def status_summary(presentation, view, *, final_phase=False, exhausted=None, waiting_reason=None,
                   current_round=None, completed_line=None, elapsed=None, clean=None):
    current = presentation['current']
    if presentation['completed']:
        state = 'completed'
    elif presentation['waiting']:
        state = 'waiting'
    elif presentation['state'] in {'unknown', 'native_intent_present_unknown'}:
        state = 'unknown'
    elif view.get('paused') and presentation['active']:
        state = 'pause_requested'
    elif presentation['active']:
        state = 'active'
    elif view.get('paused'):
        state = 'paused'
    elif final_phase:
        state = 'final'
    elif exhausted:
        state = 'budget_exhausted'
    elif current is None:
        state = 'ready'
    else:
        state = 'idle'
    round_text = ('第 ' + str(current_round) + ' 轮 · ') if current_round else ''
    if state == 'completed':
        headline = completed_line or '研究已完成，可以查看结果'
    elif state == 'waiting':
        headline = 'A 等待：' + (waiting_reason or '请查看本阶段结论中的等待条件')
    elif state == 'unknown':
        headline = '上次任务结果不确定'
    elif state == 'pause_requested':
        headline = '当前阶段结束后停止'
    elif state == 'active':
        verb = ROLE_VERBS.get(current['role'], current['role'] + ' 正在工作') if current else '研究正在进行'
        headline = round_text + verb + (f'（已 {elapsed}）' if elapsed else '')
    elif state == 'paused':
        headline = '已暂停于第 ' + str(current['step']) + ' 阶段' if current else '已暂停'
    elif state == 'final':
        headline = '已进入最终评估'
    elif state == 'budget_exhausted':
        headline = '预算已用完：' + '、'.join(exhausted)
    elif state == 'ready':
        headline = '已准备，等待你开始'
    else:
        headline = '停在第 ' + str(current['step']) + ' 阶段（' + STATE_LABELS.get(current['state'], '状态待核对') + '）'
    stage = None
    if current:
        stage = '第 ' + str(current['step']) + ' 阶段 · ' + ROLE_LABELS.get(current['role'], current['role']) + ' · ' \
            + STATE_LABELS.get(current['state'], '状态待核对')
    return dict(state=state, badge=STATES[state], headline=headline, stage=stage)


def exhausted_limits(view, used_steps):
    budget = view.get('budget') or {}
    usage = view.get('usage') or {}
    reasons = []
    if budget.get('max_tokens') and (usage.get('accounted_tokens', usage.get('observed_tokens')) or 0) >= budget['max_tokens']:
        reasons.append('tokens')
    if budget.get('max_seconds') is not None and budget.get('seconds_remaining') is not None and budget['seconds_remaining'] <= 0:
        reasons.append('时间')
    if budget.get('max_steps') and used_steps is not None and used_steps >= budget['max_steps']:
        reasons.append('阶段数')
    return reasons


# ---------------------------------------------------------------- usage

def token_details(usage, context=None):
    """Shared overview and exact-attempt labels; context is never accumulated."""
    meter = native_usage.tokens(usage)
    details = []
    for key, label in [('input_tokens', '输入'), ('cached_input_tokens', '缓存输入'),
                       ('noncached_input_tokens', '非缓存输入'), ('output_tokens', '输出')]:
        value = (dict(meter, **native_usage.breakdown(meter)).get(key) if meter is not None else None)
        details.append(dict(label=label, value=fmt_int(value) if value is not None else '不可用'))
    hit = native_usage.breakdown(meter).get('cache_hit_ratio')
    details.append(dict(label='缓存命中率', value=f'{hit:.1%}' if hit is not None else '不可用'))
    telemetry = native_usage.context(context)
    label = '最近一次上报上下文'
    if isinstance(context, dict) and type(context.get('step')) is int and type(context.get('attempt')) is int:
        label += f'（阶段 {context["step"]} / attempt {context["attempt"]}）'
    if telemetry['status'] == 'last_reported':
        value = f'{fmt_int(telemetry["total_tokens"])} / {fmt_int(telemetry["model_context_window"])} · {telemetry["ratio"]:.1%}'
        stamp = telemetry.get('reported_at')
        age = time.time() - stamp if stamp is not None else None
        value += '；' + (f'上报于 {fmt_duration(age)}前' if age is not None and age >= 0 else '上报时间未知')
    else:
        value = '不可用（后端未提供完整遥测或观测异常）'
    details.append(dict(label=label, value=value))
    if usage.get('measurement_status'):
        details.append(dict(label='计量状态', value=usage['measurement_status']))
    details.append(dict(label='观测限制', value='运行中请求与平台上报延迟未计入；缓存为后端已记录值'))
    return details


def usage_stats(view, *, used_steps=None, candidates=None):
    budget = view.get('budget') or {}
    usage = view.get('usage') or {}
    closeout = view.get('closeout') or {}
    observed = usage.get('observed_tokens')
    limit = budget.get('max_tokens')
    tokens = dict(label='模型用量（tokens）', value=fmt_tokens(observed), limit=fmt_tokens(limit) if limit else '未记录',
                  raw=observed, raw_limit=limit, notes=[], details=token_details(usage, usage.get('context')))
    tokens['percent'] = percent(observed, limit)
    overrun = closeout.get('final_overrun') if isinstance(closeout.get('final_overrun'), dict) else None
    over = observed is not None and limit and observed > limit
    extra = overrun.get('shared_tokens_over_ceiling', 0) if overrun else 0
    tokens['over'] = bool(over or isinstance(extra, (int, float)) and extra > 0)
    if overrun:
        tokens['notes'].append('最终 A 受保护' + ('，超出 ' + fmt_tokens(extra) if isinstance(extra, (int, float)) and extra > 0 else ''))
    elif over:
        tokens['notes'].append('已观测用量超出上限 ' + fmt_tokens(observed - limit))
    if usage.get('unknown_steps'):
        tokens['notes'].append(str(len(usage['unknown_steps'])) + ' 个阶段用量未知（下界）')
    if usage.get('reserved_tokens'):
        tokens['notes'].append('另有预留 ' + fmt_tokens(usage['reserved_tokens']))
    consumed = budget.get('consumed_seconds')
    seconds = budget.get('max_seconds')
    duration = dict(label='研究时间', value=fmt_duration(consumed), limit=fmt_duration(seconds) if seconds is not None else '未记录',
                    raw=consumed, raw_limit=seconds, notes=[])
    duration['percent'] = percent(consumed, seconds)
    duration['over'] = bool(consumed is not None and seconds is not None and consumed > seconds)
    if budget.get('time_status') == 'unknown':
        duration['value'] = '无法核实'
        duration['notes'].append('时钟记录不足以核实已用时间与剩余额度；已保留原有记录。')
    if budget.get('waiting_seconds'):
        duration['notes'].append('其中等待 ' + fmt_duration(budget['waiting_seconds']))
    if budget.get('historical_time') not in (None, 'recorded'):
        duration['notes'].append('早期时间未完整记录')
    max_steps = budget.get('max_steps')
    steps = dict(label='研究阶段', value=str(used_steps) if used_steps is not None else '未记录',
                 limit=str(max_steps) if max_steps else '未记录', raw=used_steps, raw_limit=max_steps, notes=[])
    steps['percent'] = percent(used_steps, max_steps)
    steps['over'] = bool(used_steps is not None and max_steps and used_steps > max_steps)
    candidates = candidates or {}
    count = candidates.get('count')
    card = dict(label='历史候选记录', value=str(count) if count is not None else '未记录', limit=None, raw=count, notes=[])
    if candidates.get('unreadable'):
        card['notes'].append(str(candidates['unreadable']) + ' 个阶段的观察不可读，计数为下界')
    if candidates.get('partial'):
        card['notes'].append('最近 ' + str(WINDOW) + ' 个阶段内')
    if candidates.get('recommended'):
        card['notes'].append('A 推荐 1')
    if candidates.get('delivered'):
        card['notes'].append('已交付 1')
    return dict(tokens=tokens, time=duration, steps=steps, candidates=card)


# ---------------------------------------------------------------- overview projection

def project(root, view, *, final_step=None, objective=None, memo=None):
    """One snapshot projection. Called inside the shared snapshot cache."""
    from . import ui_mechanisms
    clean = cleaner(root)
    reader = Reader(root, memo)
    nodes = list(view.get('nodes') or [])
    steps = [node['step'] for node in nodes]
    mechanisms = guarded(mechanism_labels, reader, range(1, (steps[-1] if steps else 0) + 1))
    mechanisms = {} if 'error' in mechanisms else mechanisms
    labels, references, candidate_ids, observed_any, unreadable = {}, {}, [], False, []
    for node in nodes:
        if node['role'] != 'W' or node.get('state') not in SEALED:
            continue
        try:
            observation = reader.observation(node['step'])
        except Exception:  # noqa: BLE001 - shown on that node's role line and counted below
            unreadable.append(node['step'])
            continue
        if observation is None:
            continue
        observed_any = True
        for row in observation.get('candidates', []):
            if isinstance(row, dict) and isinstance(row.get('id'), str):
                if row['id'] not in labels:
                    candidate_ids.append(row['id'])
                labels.setdefault(row['id'], clean(row['label'], 300) if isinstance(row.get('label'), str) and row['label']
                                  else row['id'].rsplit('-', 1)[-1][:12])
    try:
        from . import candidate_refs
        records = [dict(step=node['step'], role=node['role'], observation=reader.observation(node['step']))
                   for node in nodes if node['role'] == 'W' and node.get('state') in SEALED]
        references = candidate_refs.references(records)
    except Exception:  # noqa: BLE001 - references are optional display help
        references = {}
    closeout = view.get('closeout') or {}
    completed = final_step is not None or closeout.get('status') in {'final_a_completed', 'delivery_confirmed', 'exported_unsubmitted'}
    trail = guarded(rounds, reader, nodes, clean, completed=completed, mechanisms=mechanisms, labels=labels,
                    references=references, clean=clean)
    latest_a = None
    for item in ([] if 'error' in trail else trail['rounds']):
        for line in reversed(item['a']):
            if line.get('sealed') and line.get('kind') != 'formal_review' and line.get('next_action') != 'audit':
                latest_a = latest_a or line
        if latest_a:
            break
    recommended = latest_a.get('recommendation') if latest_a else None
    confirmed = (closeout.get('last_confirmed') or {}).get('candidate_id')
    candidates = dict(count=len(candidate_ids) if observed_any or nodes else None,
                      partial=bool(nodes) and nodes[0]['step'] > 1,
                      recommended=recommended is not None, delivered=confirmed is not None)
    if unreadable:
        candidates['unreadable'] = len(unreadable)
    used_steps = closeout.get('used_steps') if isinstance(closeout.get('used_steps'), int) else (steps[-1] if steps else 0)
    materials = sum(line.get('materials') or 0 for item in ([] if 'error' in trail else trail['rounds'])
                    for line in [item['h'], *item['w'], *item['a'], *item['others']] if line)
    mechanism_trails = guarded(ui_mechanisms.project, reader,
        [node['step'] for node in nodes if node.get('state') in SEALED], clean,
        partial=bool(nodes) and nodes[0]['step'] > 1, clean=clean)
    return dict(header=guarded(header, root, objective if objective is not None else view.get('objective')),
                trail=trail, mechanisms=mechanisms, mechanism_trails=mechanism_trails, labels=labels, references=references,
                candidates=candidates, used_steps=used_steps, recommended=recommended, confirmed=confirmed,
                latest_a=latest_a, materials=materials)


# ---------------------------------------------------------------- results

UNRESOLVED_DELIVERY = {'delivery_unknown', 'delivery_confirmation_pending'}


def _delivery_state(closeout, chosen, recommended_value, *, unresolved_reference=False):
    """Control delivery records only; an open submission outranks an older confirmation."""
    closeout = closeout or {}
    if recommended_value is None:
        return 'none', '未推荐'
    actual = closeout.get('current_actual')
    if unresolved_reference or actual == 'unknown' or closeout.get('status') in UNRESOLVED_DELIVERY:
        return 'unknown', '交付状态未知'
    confirmed = (closeout.get('last_confirmed') or {}).get('candidate_id')
    if chosen is not None and confirmed == chosen:
        return 'confirmed', '已确认交付'
    return 'recommended', '已推荐未交付'


def candidate_projection(root, before_step, *, job=None, reader=None):
    """Display the subject's saved input prefix without rerunning admission."""
    from . import candidate_nominations, candidate_refs, lineage
    reader = reader or Reader(root)
    job = job if job is not None else reader.node(before_step)['job']
    cutoff = (job.get('final_report') or {}).get('source_cutoff', job.get('source_cutoff'))
    extras = sorted(int(ref['path'].split('/')[2]) for ref in job.get('direct_refs', [])
                    if cutoff is not None and int(ref['path'].split('/')[2]) > cutoff)
    history = job.get('history') or {}
    steps = [row['step'] for row in history['nodes']] if 'nodes' in history else list(range(1, before_step))
    steps = [step for step in steps if 0 < step < before_step and (cutoff is None or step <= cutoff or step in extras)]
    sources, records, mechanisms, candidates = [], [], {}, {}
    current_h = None
    for step in steps:
        node = reader.node(step)
        source, payload, seal = node['job'], node.get('result', node['payload']), node['seal']
        sources.append((source, payload, seal, None))
        if source['role'] == 'H':
            current_h = step
            for index, item in enumerate((payload.get('search_plan') or {}).get('commitments') or [], 1):
                ref = item.get('existing_ref') or f'h{step:03d}.m{index}'
                mechanisms[ref] = dict(item, status='pending')
        for item in (payload.get('search_review') or {}).get('dispositions') or []:
            if item['mechanism_ref'] in mechanisms:
                mechanisms[item['mechanism_ref']].update(status=item['status'], disposition=dict(item, source_a=step))
        observation = reader.observation(step)
        record = dict(step=step, role=source['role'], observation=observation,
            recommendation=payload.get('candidate_recommendation') if source['role'] == 'A' and payload.get('next_action') != 'audit' else None,
            view=dict(h_scope=current_h, summary=payload.get('summary', ''),
                conclusion_pages=[f'inputs/history/nodes/{step:03d}/0001.json']))
        if isinstance(payload.get('candidate_nominations'), dict):
            record['candidate_nominations'] = payload['candidate_nominations']
        if payload.get('next_action') == 'audit':
            record['audit_request'] = True
        records.append(record)
        for item in (observation or {}).get('candidates') or []:
            candidates.setdefault(item['id'], dict(observation=item, mechanism_ref=None))
        for item in (observation or {}).get('contexts') or []:
            if item.get('candidate_id') in candidates:
                candidates[item['candidate_id']].update(
                    mechanism_ref=(item.get('mechanism_binding') or {}).get('mechanism_ref') or item.get('scope', {}).get('mechanism_ref'),
                    execution_status=item.get('status'))
    indexed = lineage.candidate_view(records)
    graph = dict(mechanisms=mechanisms, candidates=candidates)
    latest_w = next((record for record in reversed(records) if record['role'] == 'W'), None)
    nominated = candidate_nominations.project([latest_w] if latest_w is not None else [], dict(mechanisms=mechanisms))
    nominated_ids = list(dict.fromkeys(row['candidate'] for row in [*nominated['representatives'], *nominated['extras']]))
    try:
        references = candidate_refs.references(records)
    except ValueError:
        references = {}
    return dict(index=indexed['candidate_index'], graph=graph, references=references,
                nominated_ids=nominated_ids,
                records=records, sources=sources, subject=job,
                recommended=(indexed.get('a_recommended') or {}).get('candidate_id'))


def candidate_rows(projection, *, recommended=None, delivered=None, audits=None, mechanisms=None, clean=None):
    """Latest W nominees and exact A choice, in neutral observation order."""
    clean = clean or (lambda text, limit=16384: text)
    def text(value, limit=300):
        return None if value is None else clean(str(value), limit)
    graph = projection['graph']
    labels = {ref: item.get('label') for ref, item in (graph.get('mechanisms') or {}).items() if isinstance(item, dict)}
    labels.update({key: value for key, value in (mechanisms or {}).items() if key not in labels})
    audits_recorded = audits is not None and audits.get('__recorded__', bool(audits))
    audits = {key: value for key, value in (audits or {}).items() if key != '__recorded__'}
    rows, groups = [], {}
    nominated_ids = set(projection.get('nominated_ids', ()))
    if recommended is not None:
        nominated_ids.add(recommended)
    nominated = [item for item in projection['index'] if item['id'] in nominated_ids]
    for order, item in enumerate(nominated, 1):
        identity = item['id']
        context = (graph.get('candidates') or {}).get(identity) or {}
        ref = context.get('mechanism_ref')
        status = item.get('status')
        execution = context.get('execution_status')
        marks = []
        if recommended and identity == recommended:
            marks.append('recommended')
        if delivered and identity == delivered:
            marks.append('delivered')
        if status == 'failed' or execution in {'known_failure', 'failed'}:
            marks.append('failed')
        audit = audits.get(identity)
        row = dict(order=order, id=identity,
                   label=text(item.get('label')) or identity.rsplit('-', 1)[-1][:12],
                   authored_label=bool(text(item.get('label'))), authored_mechanism=bool(ref in labels and labels[ref]),
                   mechanism=text(labels.get(ref, ref)) if ref else '未关联',
                   metric=item.get('metric'), metric_text=text(fmt_metric(item.get('metric')), 80),
                   evaluation_key=text(item.get('evaluation_key')) if item.get('evaluation_key') is not None else '未声明',
                   direction=text(item.get('direction'), 200),
                   step=item.get('observation_step'),
                   audit=audit or ('未审计' if audits_recorded else '未记录'),
                   status={'ok': '成功', 'failed': '失败', 'unknown': '未知'}.get(status, status or '未知'),
                   marks=marks, reference=text(projection['references'].get(identity)))
        rows.append(row)
        evaluation = item.get('evaluation_key')
        # Missing comparability never creates a numeric comparison group.
        identity_key = evaluation if isinstance(evaluation, str) and evaluation.strip() else ('unknown', order)
        groups.setdefault(identity_key, []).append(row)
    # The first CANDIDATE_LIMIT rows of every group show by default; marked
    # (author-recommended, delivered, failed) rows always stay visible.
    for group in groups.values():
        for index, row in enumerate(group):
            row['extra'] = index >= CANDIDATE_LIMIT and not row['marks']
    display_groups = [dict(key=value[0]['evaluation_key'], rows=value) for value in groups.values()]
    for group in display_groups:
        group['ambiguous_label'] = sum(other['key'] == group['key'] for other in display_groups) > 1
    return dict(total=len(rows), groups=display_groups,
                limit=CANDIDATE_LIMIT,
                recommended=recommended, delivered=delivered)


def audit_facts(root, projection, *, subject=None):
    """Saved public audit outcomes and their recorded artifact attribution."""
    root = Path(root)
    job = projection['subject']
    sources = projection['sources']
    allowed = {source['step'] for source, _, _, _ in sources}
    audit_steps = {source['candidate_audit']['step'] for source, _, _, _ in sources
        if source.get('candidate_audit') and source['candidate_audit']['step'] in allowed}
    if job.get('candidate_audit') and job['candidate_audit']['step'] in allowed:
        audit_steps.add(job['candidate_audit']['step'])
    requests = [(source, payload) for source, payload, _, _ in sources]
    if subject is not None:
        requests.append((job, subject))
    for source, payload in requests:
        if source['role'] == 'A' and payload.get('next_action') == 'audit':
            audit_steps.add(source['step'])
    models = {}
    for record in projection['records']:
        observation = record.get('observation') or {}
        for context in observation.get('contexts', []):
            model = context.get('model')
            if context.get('status') == 'known_success' and model and model.get('retention') == 'archived':
                models.setdefault(context['candidate_id'], set()).add((model['sha256'], model['size_bytes']))
        actual = observation.get('actual_submission')
        if actual and actual.get('candidate_id') is not None and (actual.get('artifact') or {}).get('retention') == 'archived':
            model = actual['artifact']
            models.setdefault(actual['candidate_id'], set()).add((model['sha256'], model['size_bytes']))
    result, recorded = {}, False
    for step in sorted(audit_steps):
        directory = root / 'native-evidence' / f'{step:03d}' / 'candidate-audit'
        path = directory / 'outcome.json'
        if not path.exists():
            continue
        value = c.read(path)
        recorded = True
        chosen, status = value.get('candidate_id'), value['result']['status']
        if chosen is None:
            continue
        binding = c.read(directory / 'binding.json') if value.get('binding_sha256') is not None else None
        artifact = binding['source']['objects']['artifact'] if binding is not None else None
        matched = artifact is not None and models.get(chosen) == {(artifact['sha256'], artifact['size_bytes'])}
        result[chosen] = ('公开审计通过' if status == 'audited' and matched else
            '公开审计已完成；候选文件归属未知' if status == 'audited' else
            '公开审计失败' if status == 'rejected' else '公开审计未完成')
    result['__recorded__'] = recorded
    return result


def result_card(reader, step, *, closeout=None, projection=None, goal_completion=None, labels=None, clean=None):
    clean = clean or cleaner(reader.root)
    sealed = reader.node(step)
    job, payload, seal = sealed['job'], sealed['payload'], sealed['seal']
    references = (projection or {}).get('references') or {}
    value = payload.get('candidate_recommendation')
    audit = payload.get('next_action') == 'audit'
    resolution_error = None
    try:
        saved = sealed.get('result') or {}
        chosen = (saved['candidate_recommendation'] if 'candidate_recommendation' in saved else
            resolve_candidate(reader.root, job, value, references, strict=True) if value is not None else None)
    except ValueError as error:
        chosen, resolution_error = None, clean(str(error), 300)
    if closeout and (closeout.get('existing_A_recommendation') or {}).get('author_step') == step:
        chosen = closeout['existing_A_recommendation'].get('candidate_id') or chosen
    index = {item['id']: item for item in (projection or {}).get('index') or []}
    candidate = None
    if chosen is not None:
        item = index.get(chosen)
        candidate = dict(id=chosen, reference=references.get(chosen) or (value if isinstance(value, str) else None),
                         label=clean(str((item or {}).get('label') or (labels or {}).get(chosen) or value), 300),
                         authored_label=bool((item or {}).get('label') or (labels or {}).get(chosen)),
                         metric=fmt_metric((item or {}).get('metric')) if item else '未记录',
                         evaluation_key=clean(str(item['evaluation_key']), 300) if item and item.get('evaluation_key') is not None else '未声明',
                         direction=clean(str(item['direction']), 200) if item and item.get('direction') else None,
                         step=(item or {}).get('observation_step'), indexed=item is not None)
    delivery, delivery_label = _delivery_state(closeout, chosen, value, unresolved_reference=resolution_error is not None)
    if audit:
        delivery, delivery_label = 'audit', '审计请求'
    explanation = payload.get('selection_explanation') if isinstance(payload.get('selection_explanation'), dict) else None
    reason = first_sentence((explanation or {}).get('criteria'), 400) or first_sentence(payload.get('route_reason'), 400)
    change = (explanation or {}).get('criteria_change') if isinstance((explanation or {}).get('criteria_change'), dict) else None
    criteria_change = None
    if change is not None:
        criteria_change = dict(status=change.get('status') or '未记录',
            label={'unchanged': '准则未变化', 'revised': '准则已修订'}.get(change.get('status'), '准则变化：' + str(change.get('status'))),
            details=[dict(label=label, text=clean(str(change[key]), 4000)) for key, label in
                     (('prior_criteria', '原准则'), ('changes', '变化'), ('evidence', '依据'), ('accepted_tradeoffs', '接受的取舍'))
                     if change.get(key)])
    limitations = [clean(str(item), 1200) for item in payload.get('limitations') or []]
    goal = goal_completion if goal_completion is not None else (closeout or {}).get('goal_completion')
    return dict(step=step, role=job['role'], kind='closeout' if job.get('purpose') == 'closeout' else
                'formal_review' if job.get('formal_review') else 'ordinary',
                next_action=payload.get('next_action'), decision=A_OUTCOMES.get(payload.get('next_action'), payload.get('next_action')),
                summary=clean(payload['summary'], 1200) if isinstance(payload.get('summary'), str) else '未记录摘要',
                delivery_state=delivery, delivery_label=delivery_label, chosen=None if audit else candidate,
                audit_target=candidate if audit else None,
                recommendation_raw=clean(value, 300) if isinstance(value, str) and not audit else None,
                resolution_error=resolution_error,
                reason=clean(reason, 400) if reason else '未记录',
                criteria_change=criteria_change,
                unmet=limitations[:3], unmet_omitted=max(0, len(limitations) - 3),
                goal_completion=GOAL_COMPLETION.get(goal, goal) if goal is not None else '未记录',
                materials=len(seal.get('materials') or []))


def candidate_file(reader, candidate, projection):
    """Exact sealed member from the candidate's existing original binding."""
    if not candidate or not candidate.get('reference') or not candidate.get('step'):
        return None
    indexed = next((row for row in projection.get('index', []) if row['id'] == candidate['id']), {})
    implementation = indexed.get('implementation')
    if isinstance(implementation, str):
        match = re.fullmatch(r'history/nodes/(\d{3,})/artifacts/(.+)', implementation)
        if match:
            owner = int(match[1])
            if owner in {row['step'] for row in projection.get('records', [])}:
                for index, member in enumerate(reader.node(owner)['seal'].get('materials') or [], 1):
                    if member['path'] == 'inputs/parents/' + f'{owner:03d}/artifacts/' + match[2]:
                        return dict(step=owner, index=index, name=display._label(member, owner), sha256=member['sha256'])
    match = re.fullmatch(r'[HWAE](\d{3,})-C(\d{3,})/(.+)', candidate['reference'])
    if not match or int(match[1]) != candidate['step']:
        return None
    step, number, artifact = int(match[1]), int(match[2]), match[3]
    observation = reader.observation(step)
    calls = (observation or {}).get('managed_calls') or []
    if not 1 <= number <= len(calls):
        return None
    name = f'experiments/{calls[number - 1]["call_id"]}/{artifact}'
    for index, material in enumerate(reader.node(step)['seal'].get('materials') or [], 1):
        try:
            label = display._label(material, step)
        except ValueError:
            continue
        if label == name:
            return dict(step=step, index=index, name=name, sha256=material.get('sha256'))
    return None


def results_list_row(reader, step, clean):
    sealed = reader.node(step)
    job, payload = sealed['job'], sealed['payload']
    summary = payload.get('summary') if isinstance(payload.get('summary'), str) and payload['summary'].strip() else \
        first_sentence(payload.get('scientific_result'), 600)
    label = ROLE_LABELS.get(job['role'], job['role'])
    if job.get('formal_review'):
        label = 'A · 正式评审'
    elif job.get('purpose') == 'closeout':
        label = 'A · 最终评估'
    return dict(step=step, role=job['role'], role_label=label, one_line=clean(summary, 600) if summary else '未记录摘要')


def elapsed_text(prepared_at, now=None):
    if type(prepared_at) not in (int, float) or prepared_at < 1e9:
        return None
    seconds = (time.time() if now is None else now) - prepared_at
    if seconds < 0:
        return None
    minutes = int(seconds // 60)
    return (str(minutes) + ' 分钟') if minutes < 60 else fmt_duration(seconds)
