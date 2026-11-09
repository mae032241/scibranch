"""Read-only workbench view models: plain data, no HTML, no writes.

Every projection reuses existing sealed/control readers. A failing projection
returns ``{"error": {...}}`` for its own component and never fails the page.
Control supplies no scientific ranking or default choice: candidate rows keep
first-observation order and only author/control records mark A or delivery.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
import threading
import time

from . import contracts as c, instance_display as display
from . import native_usage, public_output, ui_semantics

ROLE_LABELS = {'H': 'H · 假设', 'W': 'W · 实验', 'A': 'A · 综合评估', 'E': 'E · 证据'}
ROLE_VERBS = {'H': 'H 正在提出假设', 'W': 'W 正在实验', 'A': 'A 正在综合评估', 'E': 'E 正在整理证据'}
STATE_LABELS = {'prepared': '等待开始', 'running': '正在运行', 'waiting': '等待外部工作',
    'automatic_recovery_waiting': '等待自动接续', 'automatic_recovery_stopped': '自动接续已停止',
    'automatic_recovery_paused': '自动接续已暂停',
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
    'external_wait': '等待原实验', 'unsealed': '结论待封存', 'not_submitted': '提交意图待核对',
    'service_wait': '等待服务恢复',
    'service_error': '服务错误待处理',
}
EXPERIMENT_TOOLTIP = '本阶段实验数 = 封存观察中已接受的受管调用数；不等于独立实验设计数或科学覆盖。'
EXTREME_TOOLTIP = '全部已观察数值，含失败记录；仅在同一口径（evaluation_key）内按作者声明的指标方向取数值极值；不代表科学排序。'
CANDIDATE_LIMIT = 20
ROUND_LIMIT = 5
WINDOW = 50


_memo_lock = threading.RLock()


def memo_lock(memo):
    """Serialize UI cache transactions, including legacy plain-dict callers."""
    return getattr(memo, 'lock', _memo_lock)


class DisplayMemo(dict):
    """Display-only records and compact history facts; never admission evidence."""

    def __init__(self, *, lock=None):
        super().__init__()
        self.lock = lock if lock is not None else threading.RLock()
        self.summaries = {}

    def clear(self):
        with self.lock:
            super().clear()
            self.summaries.clear()


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
        self._members = {}
        self._candidate_facts = {}
        self.memo = memo if memo is not None else {}

    @staticmethod
    def _fingerprint(paths):
        return tuple(display.file_identity(path) for path in paths)

    def node(self, step):
        with memo_lock(self.memo):
            return self._node(step)

    def _node(self, step):
        key = (str(self.root), step)
        if step not in self._nodes:
            saved = self.memo.get(key)
            if saved is not None:
                try:
                    if self._fingerprint(saved['paths']) == saved['fingerprint']:
                        self._nodes[step] = copy.deepcopy(saved['node'])
                        self._observations[step] = copy.deepcopy(saved['observation'])
                        self.memo.pop(key)
                        self.memo[key] = saved
                        return self._nodes[step]
                except (OSError, ValueError):
                    pass
                self.memo.pop(key, None)
            # Use the same saved original/fork resolver as the display reader.
            root, record, seal, raw = display._saved(self.root, step)
            if isinstance(record, str):
                paths = [root / 'settings.json', root / f'jobs/{step:03d}.json',
                    root / record / 'result-identity.json', root / record / 'result.json', raw]
                if seal.get('observations'):
                    paths.append(root / seal['observations']['path'])
            else:
                from . import originals
                paths = [root / 'settings.json', root / 'host-runtime/fork/manifest.json', raw,
                    originals.source_record(record, 'result-identity.json'),
                    originals.source_record(record, 'result.json'), originals.resolve_ref(record, dict(path=f'jobs/{step:03d}.json'))]
                if seal.get('observations'):
                    paths.append(originals.resolve_ref(record, seal['observations']))
            try:
                before = self._fingerprint(paths)
            except OSError:
                before = None
            node = display.node(self.root, step)
            observation = display.observation(self.root, step, node=node)
            navigation=node.get('navigation_file')
            if navigation is not None:
                paths.append(navigation)
                # This optional accepted view is acquired by display.node once.
                # Its appearance/change invalidates the same shared cache.
                observed=node.get('navigation_fingerprint')
                before=(*before,observed) if before is not None and observed is not None else None
            try:
                fingerprint = self._fingerprint(paths)
                if fingerprint != before or node['seal'] != seal:
                    fingerprint = None
            except OSError:
                # Uncacheable evidence remains readable without retaining errors.
                fingerprint = None
            if fingerprint is not None:
                while len(self.memo) >= 256:
                    self.memo.pop(next(iter(self.memo)))
                self.memo[key] = dict(paths=paths, fingerprint=fingerprint, node=copy.deepcopy(node),
                    observation=copy.deepcopy(observation))
            self._nodes[step], self._observations[step] = node, observation
        if key in self.memo:
            # Keep frequently rendered window nodes while summaries cover the
            # full history; local Reader hits also count as recent use.
            saved = self.memo.pop(key)
            self.memo[key] = saved
        return self._nodes[step]

    def summary(self, step):
        """Compact all-history facts survive eviction of the 256 large nodes.

        Every reuse checks the same exact saved-source metadata as ``node``.
        Missing or changed evidence is reread, never treated as an empty stage.
        """
        with memo_lock(self.memo):
            summaries = getattr(self.memo, 'summaries', None)
            key = (str(self.root), step)
            saved = summaries.get(key) if summaries is not None else None
            if saved is not None:
                try:
                    if (self._fingerprint(saved['paths']) == saved['fingerprint']
                            and all(self._fingerprint(source['paths']) == source['fingerprint']
                                for source in saved.get('dependencies', []))):
                        return copy.deepcopy(saved['value'])
                except (OSError, ValueError):
                    pass
                summaries.pop(key, None)
            node = self.node(step)
            observation = self.observation(step)
            value = dict(role=node['job']['role'], kind=ui_semantics.kind(node['job']), observed=observation is not None,
                candidates=[dict(id=row['id'], label=row.get('label'))
                    for row in (observation or {}).get('candidates', [])
                    if isinstance(row, dict) and isinstance(row.get('id'), str)])
            payload = node['payload']
            dependencies = []
            if (ui_semantics.assessment(node['job']) and payload.get('next_action') != 'audit'):
                try:
                    if node.get('candidate_reference_unresolved'):
                        raise ValueError('Inherited candidate input identity is unavailable')
                    value['recommendation'] = self.candidate(node['job'],payload.get('candidate_recommendation'),dependencies=dependencies)
                except (OSError, ValueError, KeyError, TypeError):
                    # A later author choice with an unreadable source still
                    # supersedes earlier A advice; never revive the old choice.
                    value.update(recommendation=None, recommendation_gap=True)
                    dependencies.append(None)
            original = self.memo.get(key)
            if summaries is not None and original is not None and None not in dependencies:
                summaries[key] = dict(paths=original['paths'], fingerprint=original['fingerprint'],
                    dependencies=dependencies, value=copy.deepcopy(value))
            return value

    def observation(self, step):
        """Sealed observer snapshot bound to this node's exact output, if any."""
        self.node(step)
        return self._observations[step]

    def member(self, prefix, step, *, dependencies=None):
        """One exact saved membership from the subject's frozen cut, no acceptance."""
        from . import accepted_prefix
        key=(json.dumps(prefix,sort_keys=True),step)
        if key not in self._members:
            value=accepted_prefix.member(self.root,prefix,step)
            paths=[self.root/'host-history/accepted-members'/accepted_prefix._view_key(prefix)/'logical'/f'{step:03d}.json',
                accepted_prefix._member_path(self.root,prefix,value['origin_identity'],step)]
            self._members[key]=dict(value=value,paths=paths,fingerprint=self._fingerprint(paths))
        saved=self._members[key]
        if dependencies is not None:
            dependencies.append(dict(paths=saved['paths'],fingerprint=saved['fingerprint']))
        return saved['value']

    def candidate_facts(self,job,value,*,dependencies=None):
        """Acquire one requested candidate's frozen facts, never a live prefix."""
        from . import candidate_refs
        if not isinstance(value,str):return dict(resolved=None)
        key=(job.get('step'),value,json.dumps(dict(history=job.get('history'),
            worker_view_version=job.get('worker_view_version'),fork_source=job.get('fork_source')),sort_keys=True))
        if key not in self._candidate_facts:
            used=[];facts=dict(resolved=value)
            try:
                resolved=candidate_refs.resolve_reference(self.root,job,value)
                facts['resolved']=resolved
                match=re.fullmatch(r'([HWAE])(\d{3,})-I(\d{3,})',resolved) if isinstance(resolved,str) else None
                if match:
                    step=int(match[2]);prefix=job.get('history',{}).get('prefix')
                    member=self.member(prefix,step,dependencies=used) if isinstance(prefix,dict) else None
                    if member is not None or step in {row['step'] for row in job.get('history',{}).get('nodes',[])}:
                        node=self.node(step)
                        if member is not None and node.get('origin_identity')!=member['origin_identity']:
                            raise ValueError('Implementation source differs from its frozen branch member')
                        saved=self.memo.get((str(self.root),step))
                        used.append(dict(paths=saved['paths'],fingerprint=saved['fingerprint']) if saved else None)
                        facts['implementation']=dict(node=node,observation=self.observation(step))
            except (ValueError,FileNotFoundError) as error:
                facts['error']=ValueError('Candidate is outside its frozen scientific input') if isinstance(error,FileNotFoundError) else error
                used.append(None)
            self._candidate_facts[key]=(facts,used)
        facts,used=self._candidate_facts[key]
        if dependencies is not None:dependencies.extend(used)
        return facts

    def candidate(self,job,value,references=None,*,strict=False,dependencies=None):
        facts=self.candidate_facts(job,value,dependencies=dependencies)
        if dependencies is not None and facts.get('error') is not None:raise facts['error']
        return resolve_candidate(self.root,job,value,references,strict=strict,facts=facts)

    def role(self, step):
        if step not in self._roles:
            try:
                self._roles[step] = self.summary(step)['role']
            except (OSError, ValueError):
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


def resolve_candidate(root, job, value, references=None, *, strict=False, facts):
    """Pure candidate preview from the UI owner's already acquired frozen facts."""
    if not isinstance(value,str):return None
    if facts.get('error') is not None:
        if strict:raise facts['error']
        resolved=value
    else:resolved=facts['resolved']
    match=re.fullmatch(r'([HWAE])(\d{3,})-I(\d{3,})',resolved) if isinstance(resolved,str) else None
    if match:
        binding=facts.get('implementation')
        if binding is not None:
            node=binding['node'];index=int(match[3])
            materials=sorted(node['seal']['materials'],key=lambda row:row['path'])
            if node['job']['role']==match[1] and 1<=index<=len(materials):
                target=materials[index-1]['path'].replace('inputs/parents/','history/nodes/',1)
                matches={row['id'] for row in (binding.get('observation') or {}).get('candidates',[])
                    if row.get('implementation')==target}
                if len(matches)==1:resolved=matches.pop()
        if strict and re.fullmatch(r'[HWAE]\d{3,}-I\d{3,}',resolved):
            raise ValueError('Implementation has no unique frozen candidate binding')
    if references and resolved not in references:
        inverse={ref:identity for identity,ref in references.items()}
        resolved=inverse.get(resolved,resolved)
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
                state_label=native_state_label(node), sealed=state in SEALED,
                kind=node.get('kind','ordinary'), one_line=None, facts=[], links=_links(node, False), next_action=None)
    line['role_label']=node.get('role_label') or line['role_label']
    line['facts'].extend(reflection_facts(node.get('reflection')))
    line['facts'].extend(worker_continuation_facts(node.get('worker_continuation')))
    if not line['sealed']:
        line['facts'].extend(native_facts(node, clean))
        recovery = node.get('automatic_recovery')
        if isinstance(recovery, dict):
            line['automatic_recovery'] = recovery
            line['facts'].extend(recovery_facts(recovery, clean))
        return line
    sealed = reader.node(step)
    job, payload, seal = sealed['job'], sealed['payload'], sealed['seal']
    line['links'] = _links(node, True, len(seal.get('materials') or []))
    line['materials'] = len(seal.get('materials') or [])
    line['kind'] = ui_semantics.kind(job)
    line['role_label'] = ui_semantics.role_label(job) or line['role_label']
    if 'reflection' not in node:
        line['facts'].extend(reflection_facts(ui_semantics.reflection(job)))
    summary = payload.get('summary')
    if isinstance(summary, str) and summary.strip():
        line['one_line'] = clean(summary.strip(), 600)
    else:
        sentence = first_sentence(payload.get('scientific_result'), 600)
        line['one_line'] = clean(sentence, 600) if sentence else '未记录摘要'
    facts = line['facts']
    action = payload.get('next_action') if isinstance(payload.get('next_action'), str) else None
    line['next_action'] = action
    line['action_label'] = ui_semantics.action(job, action, A_OUTCOMES)
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
        facts.append(dict(label='行动', value=(line['action_label'] if line['kind']=='consultation' else H_ACTIONS.get(action,action)) if action else '未记录'))
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
        reflection=ui_semantics.reflection(job)
        if reflection is not None:
            facts.append(dict(label='诊断回程' if ui_semantics.diagnostic_advice(job) else '反思结论',
                value=reflection_response(payload,line['action_label'],diagnostic=ui_semantics.diagnostic_advice(job))))
        if line['kind'] == 'formal_review':
            verdict = payload.get('verdict')
            facts.append(dict(label='评审结论', value=clean(str(verdict), 200) if verdict is not None else '未记录'))
        else:
            facts.append(dict(label='咨询答复' if line['kind']=='consultation' else
                '建议回程' if ui_semantics.diagnostic_advice(job) else '决定', value=line['action_label'] if action else '未记录'))
        reason = first_sentence(payload.get('route_reason'))
        if reason:
            facts.append(dict(label='理由', value=clean(reason, 400)))
        review = payload.get('search_review') if ui_semantics.assessment(job) and isinstance(payload.get('search_review'), dict) else None
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
        if ui_semantics.assessment(job) and 'candidate_recommendation' in payload:
            value = payload.get('candidate_recommendation')
            if action == 'audit':
                identity = reader.candidate(job,value,references)
                line['audit_target'] = identity
                label = (labels or {}).get(identity)
                facts.append(dict(label='审计对象', value=clean(label or str(value), 300) if value is not None else '未指定'))
            elif value is None:
                facts.append(dict(label='候选推荐', value='未推荐'))
            else:
                identity = reader.candidate(job,value,references)
                line['recommendation'] = identity
                label = (labels or {}).get(identity)
                facts.append(dict(label='候选推荐', value='★ ' + clean(label if label else str(value), 300)))
    elif role == 'E' and line['kind'] == 'consultation' and ui_semantics.advisory_scope(job):
        facts.append(dict(label='证据答复', value=line['action_label']))
    elif action:
        facts.append(dict(label='行动', value=clean(action, 60)))
    return line


def reflection_facts(value):
    """Display only the frozen automatic opportunity, never a stall verdict."""
    if not isinstance(value, dict):
        return []
    reasons = []
    for reason in value.get('due_reasons') or []:
        if value.get('policy_version') == 4 and reason == 'changed_authored_question_or_original_receipt':
            reasons.append('作者问题或原调用收据的输入边界变化；提供控制侧检查机会')
        elif value.get('policy_version') == 4 and reason == 'operational_no_new_experiment':
            reasons.append('运行检查机会：没有新的实验输入；不判定科学停滞')
        elif reason == 'consecutive_calls':
            reasons.append('连续原调用投入达到冻结阈值')
        elif reason == 'consecutive_rounds':
            reasons.append('已声明轮次达到冻结阈值')
        elif isinstance(reason, str) and reason.startswith('budget_checkpoint:'):
            reasons.append('预算检查点 ' + format(float(reason.split(':', 1)[1]), '.0%'))
    facts = [dict(label='反思范围', value='W 的研究问题、线索与证据；触发事实不判断科学进展或停滞')]
    if value.get('advisory_scope_version') == 1:
        facts.append(dict(label='诊断职责', value='有证据的局部锁定、忽视反证、目标偏移或严重推理缺陷；建议交回 W，不认可或确认当前方向'))
    if value.get('policy_version') == 4:
        facts.append(dict(label='实验性监督策略', value='policy 4；控制侧检查机会'))
        facts.append(dict(label='冻结 W 来源', value='W #' + str(value['source_w'])))
    facts.append(dict(label='触发原因', value='；'.join(reasons) if reasons else '冻结请求未记录可显示原因'))
    fraction = value.get('budget_fraction')
    facts.append(dict(label='请求时预算消费', value=format(fraction, '.1%') if fraction is not None else '未记录'))
    if 'investment_cutoff' in value:
        facts.append(dict(label='冻结原调用截止数', value=str(value['investment_cutoff'])))
    return facts


def reflection_response(payload, action_label, *, diagnostic=False):
    if diagnostic:
        return '诊断建议已返回 W；continue_existing 仅表示回程，不认可或确认当前方向' if payload.get('supervision_result') == 'continue_existing' else action_label or '未记录'
    if payload.get('supervision_result') == 'continue_existing':
        return '继续现有研究（continue_existing）'
    return action_label or '未记录'


def worker_continuation_facts(value):
    """Outcome observation only; a resume request never establishes a resume."""
    if not isinstance(value, dict):
        return []
    labels = {'same_session': '已观察到原会话接续',
        'new_context': '已使用新上下文接续；原会话未恢复',
        'resume_unconfirmed': '已请求原会话接续；实际恢复尚未证实'}
    label = labels.get(value.get('mode'))
    if label is None:
        return []
    facts = [dict(label='W 接续方式', value=label)]
    if value.get('fallback_reason'):
        labels = {'unsupported_backend': '后端未提供原会话恢复能力', 'profile_changed': '冻结执行配置已变化',
            'backend_changed': '执行后端已变化', 'context_replaced': '原接续已明确替换上下文',
            'session_unavailable': '原会话不可用', 'shared_home_unavailable': '原会话所需的共享运行环境不可用',
            'context_rotation_required': '需要轮换上下文', 'inherited_source': '原会话来自继承来源'}
        reason = value['fallback_reason'].removeprefix('consultation_resume_')
        if reason in labels:
            facts.append(dict(label='接续回退原因', value=labels[reason]))
    return facts


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
    from . import originals
    for source in originals.source_sequence(reader.root, first):
        try:
            summary=reader.summary(source.step) if hasattr(reader,'summary') else dict(role=reader.role(source.step))
            before += summary['role']=='H' and summary.get('kind')!='consultation'
        except Exception:  # noqa: BLE001 - earlier numbering becomes unknown, not invented
            before = None
            break
    items, current = [], None
    for node in nodes:
        line = guarded(role_line, reader, node, clean, mechanisms=mechanisms, labels=labels, references=references, clean=clean)
        if 'error' in line:
            line = dict(step=node['step'], role=node['role'], role_label=ROLE_LABELS.get(node['role'], node['role']),
                        state=node.get('state'), state_label=STATE_LABELS.get(node.get('state'), '状态待核对'),
                        sealed=False, kind='ordinary', one_line=None, facts=[], links=_links(node, False), error=line['error'])
        if (node['role'] == 'H' and line['kind'] != 'consultation') or current is None:
            index = None
            if node['role'] == 'H' and line['kind']!='consultation':
                index = (before + 1 + sum(1 for item in items if item['index'])) if before is not None else None
            elif before:
                index = before  # Window starts inside an earlier round.
            current = dict(index=index, steps=[], h=None, w=[], a=[], others=[], outcome='进行中',
                           partial=node['role'] != 'H' and node['step'] != 1)
            items.append(current)
        current['steps'].append(node['step'])
        if line['kind'] == 'consultation':
            current['others'].append(line)
        elif node['role'] == 'H' and current['h'] is None:
            current['h'] = line
        elif node['role'] == 'W':
            current['w'].append(line)
        elif node['role'] == 'A':
            current['a'].append(line)
        else:
            current['others'].append(line)
    for item in items:
        decisive = [line for line in item['a'] if line.get('sealed') and line.get('kind') not in {'formal_review', 'consultation'}]
        if decisive:
            action = decisive[-1].get('next_action')
            item['outcome'] = decisive[-1].get('action_label', A_OUTCOMES.get(action, action)) if action else '未记录'
        elif completed and item is not items[-1]:
            item['outcome'] = '未记录'
        item['experiments'] = sum(line.get('experiments') or 0 for line in item['w'])
        item['summary'] = _round_summary(item)
    total = len(items)
    shown = items[-ROUND_LIMIT:]
    return dict(rounds=list(reversed(shown)), omitted=total - len(shown), total=total, before=before)


# ---------------------------------------------------------------- status

def native_completed(node):
    """Display the collector's known terminal endpoint; this grants no action."""
    return bool(node and node.get('state') == 'native_outcome_unsealed'
        and type(node.get('returncode')) is int and node['returncode'] == 0
        and node.get('terminal_observed') is True and not node.get('stop_reason')
        and not node.get('native_failure') and node.get('cleanup') == 'observed_group_cleaned')


def native_state_label(node):
    return '原生已完成，待收集封存' if native_completed(node) else STATE_LABELS.get(node.get('state'), '状态待核对')


def native_failure_guidance(node):
    """Static help from the already-produced safe failure fields; no native reads."""
    failure = (node or {}).get('native_failure') or {}
    if failure.get('category') != 'permanent':
        if (not node or node.get('state') != 'automatic_recovery_stopped'
                or not native_failure_fork_available(node)):
            return None
        recovery = node.get('automatic_recovery') or {}
        if failure.get('category') == 'unknown':
            return dict(summary='原生错误原因未识别，自动接续已停止',
                explanations=['原生进程已结束并完成清理；错误类别尚未识别，这不表示外部调用结果已核实。'],
                advice=['请维护者在宿主机查看本次原生诊断并核对原调用状态；可先暂停研究，或进入分支预览核对是否可以重做当前任务。'],
                next_step='请先核对宿主诊断与原调用')
        if recovery.get('reason') in {'automatic_recovery_retry_limit', 'automatic_recovery_continue_limit',
                'automatic_recovery_wait_limit', 'automatic_recovery_retry_after_limit'}:
            summary = '自动接续次数或等待额度已用尽'
        else:
            summary = '自动接续已停止，原任务的接续条件尚未满足'
        return dict(summary=summary,
            explanations=['临时服务故障或原生中断的自动接续已停止；这不是科学结论失败。'],
            advice=['请维护者核对本次宿主诊断、已用恢复次数和原调用状态；可先暂停研究，或进入分支预览核对是否可以重做当前任务。已用预算保留。'],
            next_step='请先核对接续限制与原调用')
    codes = set(failure.get('codes') or [])
    statuses = set(failure.get('statuses') or [])
    groups = [
        ({'authentication_error', 'unauthorized', 'invalid_api_key', 'authentication_failed'},
         {401}, '认证失败或凭据无效',
         '在运行工作台的宿主机重新登录原生服务或修复凭据，并确认原账户可访问所选模型；不要在公开材料中填写密钥。'),
        ({'permission_denied', 'permission_error', 'forbidden'}, {403}, '账户或组织权限不足',
         '检查原服务账户、组织和模型访问权限，恢复所需权限后再处理原任务。'),
        ({'insufficient_quota', 'quota_exceeded', 'billing_hard_limit_reached', 'usage_limit_reached',
          'credit_balance_exhausted', 'organization_usage_limit_exceeded', 'usage_limit_exceeded'},
         set(), '服务配额、余额或计费上限不足',
         '检查服务账户的套餐、余额、账单和组织用量上限；增加本研究的 tokens 预算不会恢复服务账户额度。'),
        ({'model_not_found', 'unsupported_model', 'invalid_model', 'resource_not_found'},
         {404}, '所选模型或服务资源不可用',
         '核对原任务的模型名称、服务可用性和账户权限；需要改变冻结模型时，请用明确的新配置创建研究，分支重做不会自动换模型。'),
        ({'invalid_request_error', 'invalid_json_schema', 'bad_request', 'invalid_configuration',
          'configuration_error', 'unsupported_parameter', 'invalid_parameter', 'executor_version_incompatible'},
         {400, 422}, '请求格式、参数或启动器配置不兼容',
         '检查宿主启动器版本及原请求配置；服务请求被拒绝不能靠补填阶段结论修复。需要改变冻结参数时，请用明确的新配置创建研究。'),
        ({'context_length_exceeded'}, set(), '原任务上下文超过模型容量',
         '核对输入规模与模型上下文限制；分支重做保留原始输入，需要缩减输入或改变模型时，请用明确的新配置创建研究。'),
    ]
    matched = [(known & codes, label, advice) for known, _, label, advice in groups if known & codes]
    if not matched:
        matched = [(set(), label, advice) for _, known_statuses, label, advice in groups if known_statuses & statuses]
    if not matched:
        return dict(summary='永久服务错误：具体原因尚未识别',
            explanations=['已记录永久服务错误，但没有可显示的已知错误码含义。'],
            advice=['请维护者在宿主机核对本次原生诊断，确认原因和原调用状态后再决定下一步；不要盲目重试。'])
    return dict(summary='永久服务错误：' + '；'.join(label for _, label, _ in matched),
        explanations=[('、'.join(sorted(known)) + '：' if known else '') + label for known, label, _ in matched],
        advice=[advice for _, _, advice in matched])


def native_failure_fork_available(node):
    """A terminal display candidate for preview; the fork service owns admission."""
    category = ((node or {}).get('native_failure') or {}).get('category')
    return bool(node and category in {'permanent', 'temporary', 'incomplete', 'unknown'}
        and node.get('state') in {'native_outcome_unsealed', 'native_not_submitted',
            'automatic_recovery_stopped', 'automatic_recovery_paused'}
        and (category == 'permanent' or node.get('state') == 'automatic_recovery_stopped')
        and type(node.get('returncode')) is int and node.get('cleanup') == 'observed_group_cleaned')


def native_facts(node, clean):
    facts = []
    if type(node.get('returncode')) is int:
        facts.append(dict(label='原生退出码', value=str(node['returncode'])))
    failure = node.get('native_failure') or {}
    if failure:
        category = {'permanent': '永久服务错误，先修复配置或账户条件',
            'temporary': '临时服务错误，按原接续策略处理',
            'incomplete': '原生工作中断，按原续接条件处理', 'unknown': '原因未分类，须核对原任务'}
        facts.append(dict(label='原生错误', value=category.get(failure.get('category'), category['unknown'])))
        if failure.get('codes'):
            facts.append(dict(label='服务错误码', value=clean('、'.join(failure['codes']), 500)))
        if failure.get('statuses'):
            facts.append(dict(label='HTTP 状态', value='、'.join(str(value) for value in failure['statuses'])))
        guidance = native_failure_guidance(node)
        if guidance:
            facts.append(dict(label='错误含义', value='；'.join(guidance['explanations'])))
            facts.extend(dict(label='处理建议', value=advice) for advice in guidance['advice'])
    if node.get('stop_reason'):
        facts.append(dict(label='停止代码', value=clean(str(node['stop_reason']), 300)))
    return facts


def recovery_facts(recovery, clean):
    """Present saved recovery limits without implying permission to replay."""
    facts = [dict(label='自动接续范围', value='仅原任务和已核对的原 session；未知外部调用只核对，不重提')]
    for key, label in (('reason', '接续原因'), ('next_attempt_at', '下次仲裁（UTC）'),
                       ('limitation', '接续限制')):
        if recovery.get(key) is not None:
            facts.append(dict(label=label, value=clean(str(recovery[key]), 500)))
    facts.append(dict(label='已用恢复次数', value='retry ' + str(recovery.get('retry_count', 0))
        + '；continue ' + str(recovery.get('continue_count', 0))))
    if recovery.get('service_wait_seconds') is not None:
        facts.append(dict(label='累计服务等待', value=fmt_duration(recovery['service_wait_seconds'])))
    return facts


def status_summary(presentation, view, *, final_phase=False, exhausted=None, waiting_reason=None,
                   current_round=None, completed_line=None, elapsed=None, clean=None):
    current = presentation['current']
    recovery = (current or {}).get('automatic_recovery')
    failure_help = native_failure_guidance(current)
    if presentation['completed']:
        state = 'completed'
    elif (failure_help and not presentation['active'] and not presentation['waiting']
            and presentation['state'] in {'native_outcome_unsealed', 'native_not_submitted',
                'automatic_recovery_stopped', 'automatic_recovery_paused'}):
        state = 'service_error'
    elif view.get('paused') and recovery and recovery.get('status') == 'paused':
        state = 'paused'
    elif recovery and recovery.get('status') == 'waiting' and not view.get('paused'):
        state = 'service_wait'
    elif presentation['waiting']:
        state = 'waiting'
    elif presentation['state'] in {'unknown', 'native_intent_present_unknown'}:
        state = 'unknown'
    elif presentation['state'] in {'waiting', 'native_outcome_unsealed', 'native_not_submitted'}:
        state = {'waiting': 'external_wait', 'native_outcome_unsealed': 'unsealed', 'native_not_submitted': 'not_submitted'}[presentation['state']]
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
    elif state == 'service_error':
        headline = failure_help['summary'] + '；' + failure_help.get('next_step', '请先处理服务条件')
        if view.get('paused'):
            headline = '研究已暂停；' + headline
    elif state == 'service_wait':
        headline = '等待服务恢复：' + str(recovery.get('reason') or '有界退避后重新核对原任务')
        if recovery.get('next_attempt_at') is not None:
            headline += '；下次仲裁（UTC）' + str(recovery['next_attempt_at'])
        if clean is not None:
            headline = clean(headline, 900)
    elif state == 'waiting':
        headline = 'A 等待：' + (waiting_reason or '请查看本阶段结论中的等待条件')
    elif state in {'external_wait', 'unsealed', 'not_submitted'}:
        headline = {'external_wait': '等待原外部执行结果', 'unsealed': '原生工作已结束，阶段结论尚未封存',
            'not_submitted': '原任务尚未提交，需先核对原意图'}[state]
        if state == 'unsealed' and native_completed(current):
            headline = native_state_label(current)
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
        stage = '第 ' + str(current['step']) + ' 阶段 · ' + (current.get('role_label') or ROLE_LABELS.get(current['role'], current['role'])) + ' · ' \
            + native_state_label(current)
    badge = ('接续停止待处理' if state == 'service_error'
        and ((current or {}).get('native_failure') or {}).get('category') != 'permanent' else STATES[state])
    return dict(state=state, badge=badge, headline=headline, stage=stage)


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
        observed=telemetry.get('observed_at')
        observed_age=time.time()-observed if observed is not None else None
        if observed_age is not None and observed_age>=0:
            value+='；本地观测于 '+fmt_duration(observed_age)+'前'
        if telemetry.get('current_window') is False:value+='；非当前窗口，保留的历史上报'
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
    tokens = dict(label='模型用量（tokens）', value=fmt_tokens(observed),
                  limit='不限' if 'max_tokens' in budget and limit is None else fmt_tokens(limit) if limit else '未记录',
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
    duration = dict(label='研究时间', value=fmt_duration(consumed),
                    limit='不限' if 'max_seconds' in budget and seconds is None else fmt_duration(seconds) if seconds is not None else '未记录',
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
    steps = dict(label='控制动作', value=str(used_steps) if used_steps is not None else '未记录',
                 limit=str(max_steps) if max_steps else '未记录', raw=used_steps, raw_limit=max_steps, notes=['包括科学节点、续接、审计和受控动作'])
    steps['percent'] = percent(used_steps, max_steps)
    steps['over'] = bool(used_steps is not None and max_steps and used_steps > max_steps)
    if view.get('fork'):
        tokens['label'] = '本分支新增模型用量（tokens）'
        duration['label'] = '本分支新增研究时间'
        steps['label'] = '本分支新增控制动作'
        inherited = view['fork'].get('inherited_costs', {})
        for item, key, formatter in ((tokens, 'tokens', fmt_tokens), (duration, 'active_seconds', fmt_duration),
                                      (steps, 'control_actions', str)):
            cost = inherited.get(key, {})
            if cost.get('status') == 'known':
                message = formatter(cost.get('value'))
            else:
                message = '未知'
                if cost.get('known_lower_bound') is not None:
                    message += '（已知下界 ' + formatter(cost['known_lower_bound']) + '）'
            item['notes'].append('继承历史成本：' + message)
    candidates = candidates or {}
    count = candidates.get('count')
    card = dict(label='历史候选记录', value=str(count) if count is not None else '未记录', limit=None, raw=count, notes=[])
    if candidates.get('unreadable'):
        card['notes'].append(str(candidates['unreadable']) + ' 个阶段的观察不可读，计数为下界')
    card['href'] = '/candidates'
    if candidates.get('partial'):
        card['notes'].append('可读已封存观察的下界')
    if candidates.get('recommendation_gap'):
        card['notes'].append('当前 A 推荐的来源不可读，推荐暂不可核对')
    if candidates.get('recommended'):
        card['notes'].append('A 推荐 1')
    if candidates.get('delivered'):
        card['notes'].append('已交付 1')
    return dict(tokens=tokens, time=duration, steps=steps, candidates=card)


def final_coverage(job, payload, clean):
    """Display A's exact coverage declaration; observations supply no verdict."""
    rows = payload.get('final_coverage')
    if not isinstance(rows, list) or not ui_semantics.assessment(job):
        return None
    labels = {row['mechanism_ref']: row.get('label') for row in job.get('final_coverage_mechanisms') or []
              if isinstance(row, dict) and isinstance(row.get('mechanism_ref'), str)}
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref = row.get('mechanism_ref')
        sources = [dict(source_step=item['source_step'], pointer=clean(item.get('pointer') or '', 300))
                   for item in row.get('science_refs') or []
                   if isinstance(item, dict) and type(item.get('source_step')) is int and item['source_step'] > 0]
        result.append(dict(mechanism_ref=clean(ref or '', 160),
            label=clean(labels.get(ref) or ref or '未声明机制', 300), authored_label=bool(labels.get(ref)),
            status=clean(row.get('status') or '未声明', 100), reason=clean(row.get('reason') or '', 4000),
            science_refs=sources, unanswered_questions=[clean(str(item), 2000)
                for item in row['unanswered_questions']] if isinstance(row.get('unanswered_questions'), list) else None))
    return result


def breadth_metrics(view, clean):
    """Format the saved global projection without replaying scientific history."""
    saved = view.get('portfolio_metrics') or {}
    partial = bool(saved.get('partial', True))
    portfolio = saved.get('portfolio')
    labels = {row['mechanism_ref']: row.get('label') for row in saved.get('mechanisms') or []
              if isinstance(row, dict) and isinstance(row.get('mechanism_ref'), str)}
    def label(ref):
        return clean(labels.get(ref) or ref or '未声明', 300)
    def count(value):
        return value if type(value) is int and value >= 0 else None
    def source(value):
        return value if type(value) is int and value > 0 else None
    result = dict(available=isinstance(portfolio, dict), partial=partial,
        source_step=source(saved.get('source_step')), inherited=bool(view.get('fork')))
    if not result['available']:
        return result
    denominator = count(portfolio.get('completed_cycle_count'))
    participation = []
    for row in portfolio.get('participation') or []:
        fraction = row.get('participation_fraction')
        valid = type(fraction) in (int, float) and math.isfinite(fraction) and 0 <= fraction <= 1
        participation.append(dict(mechanism_ref=clean(row.get('mechanism_ref') or '', 160),
            label=label(row.get('mechanism_ref')), participated_cycles=count(row.get('participated_cycles')),
            fraction_text=_trim(fraction * 100) + '%' if denominator and valid else '无完整周期样本' if denominator == 0 else '不可核实'))
    pending = portfolio.get('pending_w') or {}
    pending_step = source(pending.get('source_step'))
    mainline_source = portfolio.get('mainline_source')
    mainline_source = mainline_source if isinstance(mainline_source, dict) else {}
    result.update(completed_cycle_count=denominator, participation=participation,
        pending_assessment=bool(portfolio.get('pending_assessment')), pending_step=pending_step,
        pending_labels=[label(ref) for ref in pending.get('mechanism_refs') or []],
        main_line=label(portfolio.get('main_line')) if portfolio.get('main_line') is not None else None,
        mainline_streak=count(portfolio.get('mainline_streak')),
        mainline_step=source(mainline_source.get('source_step')))
    active = [row for row in saved.get('discriminating_work') or [] if row.get('status') != 'withdrawn']
    work = []
    for row in active[:8]:
        update = (row.get('updates') or [{}])[-1]
        deferral = update.get('deferral') or {}
        work.append(dict(question=clean(row.get('question') or '', 2000),
            change_criterion=clean(row.get('change_criterion') or '', 2000),
            source_step=source(row.get('source_a')), update_step=source(update.get('source_a')),
            status=clean(row.get('status') or '', 100), action=clean(update.get('action') or '', 100),
            reason=clean(update.get('reason') or deferral.get('reason') or '', 2000),
            trigger=clean(update.get('trigger') or deferral.get('trigger') or '', 1000),
            revisit=clean(update.get('revisit') or deferral.get('revisit') or '', 1000)))
    active_count = count(saved.get('active_work_count'))
    active_count = max(len(active), active_count) if active_count is not None else len(active)
    result.update(work=work, active_work_count=active_count, work_omitted=max(0, active_count - len(work)))
    advice = saved.get('token_advice') or {}
    value = advice.get('reference_cycles')
    sample_count, minimum = count(advice.get('complete_sample_count')), count(advice.get('minimum_samples'))
    available = (not partial and advice.get('status') == 'available' and type(value) in (int, float)
        and math.isfinite(value) and value >= 0 and minimum is not None and minimum > 0
        and sample_count is not None and sample_count >= minimum and not (view.get('usage') or {}).get('unknown_steps'))
    result['token_advice'] = dict(available=available, reference_cycles=_trim(value) if available else None,
        complete_sample_count=count(advice.get('complete_sample_count')), excluded_sample_count=count(advice.get('excluded_sample_count')),
        minimum_samples=count(advice.get('minimum_samples')), scope=clean(advice.get('scope') or '', 2000),
        reasons=[clean(str(item), 300) for item in advice.get('reasons') or []],
        samples=[dict(w_step=source(row.get('w_step')), a_steps=[step for step in row.get('a_steps') or [] if source(step)])
            for row in (advice.get('samples') or [])[:8]])
    return result


# ---------------------------------------------------------------- overview projection

def project(root, view, *, final_step=None, objective=None, memo=None):
    """One snapshot projection. Called inside the shared snapshot cache."""
    from . import ui_mechanisms
    clean = cleaner(root)
    reader = Reader(root, memo)
    nodes = list(view.get('nodes') or [])
    steps = [node['step'] for node in nodes]
    labels, references, candidate_ids, observed_any, unreadable = {}, {}, [], False, []
    historical_recommendation = []
    recommendation_gap = None
    from . import ui_saved_status
    count_steps = ui_saved_status.visible_steps(root)
    for step in count_steps:
        try:
            if hasattr(reader, 'summary'):
                summary = reader.summary(step)
            else:
                observation = reader.observation(step)
                summary = dict(observed=observation is not None,
                    candidates=(observation or {}).get('candidates', []))
        except Exception:
            # Unsealed current nodes do not constitute missing sealed evidence.
            if (root / 'native-evidence' / f'{step:03d}' / 'result-identity.json').exists():
                unreadable.append(step)
            continue
        if 'recommendation' in summary:
            historical_recommendation[:] = [summary['recommendation']]
            recommendation_gap = dict(step=step) if summary.get('recommendation_gap') else None
        if not summary['observed']:
            continue
        observed_any = True
        for row in summary['candidates']:
            if isinstance(row, dict) and isinstance(row.get('id'), str):
                if row['id'] not in labels:
                    candidate_ids.append(row['id'])
                labels.setdefault(row['id'], clean(row['label'], 300) if isinstance(row.get('label'), str) and row['label']
                                  else row['id'].rsplit('-', 1)[-1][:12])
    # Build all-history facts before the rendered window so its recent large
    # nodes stay resident when the history exceeds the 256-node cache.
    mechanisms = guarded(mechanism_labels, reader, steps)
    mechanisms = {} if 'error' in mechanisms else mechanisms
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
            if line.get('sealed') and line.get('kind') not in {'formal_review', 'consultation'} and line.get('next_action') != 'audit':
                latest_a = latest_a or line
        if latest_a:
            break
    recommended = (historical_recommendation[0] if historical_recommendation
                   else latest_a.get('recommendation') if latest_a else None)
    confirmed = (closeout.get('last_confirmed') or {}).get('candidate_id')
    candidates = dict(count=len(candidate_ids) if observed_any or nodes else None,
                      partial=bool(unreadable),
                      recommended=recommended is not None, delivered=confirmed is not None)
    if unreadable:
        candidates['unreadable'] = len(unreadable)
    if recommendation_gap:
        candidates['recommendation_gap'] = recommendation_gap
    used_steps = view.get('used_steps', closeout.get('used_steps') if isinstance(closeout.get('used_steps'), int) else (steps[-1] if steps else 0))
    materials = sum(line.get('materials') or 0 for item in ([] if 'error' in trail else trail['rounds'])
                    for line in [item['h'], *item['w'], *item['a'], *item['others']] if line)
    mechanism_trails = guarded(ui_mechanisms.project, reader,
        [node['step'] for node in nodes if node.get('state') in SEALED], clean,
        partial=bool(nodes) and nodes[0]['step'] > 1, clean=clean)
    memory_node=next((getattr(reader,'_nodes',{}).get(node['step'],{}) for node in reversed(nodes)
        if node.get('state') in SEALED),{})
    return dict(candidate_memory=candidate_memory(memory_node,clean,loaded_nodes=getattr(reader,'_nodes',{}).values()),
                header=guarded(header, root, objective if objective is not None else view.get('objective')),
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


def candidate_steps(job,before_step):
    """The exact saved source cut shared by acquisition and projection."""
    cutoff = (job.get('final_report') or {}).get('source_cutoff', job.get('source_cutoff'))
    extras = sorted(int(ref['path'].split('/')[2]) for ref in job.get('direct_refs', [])
                    if cutoff is not None and int(ref['path'].split('/')[2]) > cutoff)
    history = job.get('history') or {}
    steps = [row['step'] for row in history['nodes']] if 'nodes' in history else list(range(1, before_step))
    steps = [step for step in steps if 0 < step < before_step and (cutoff is None or step <= cutoff or step in extras)]
    return steps


def candidate_projection(root, before_step, *, job=None, reader=None, history_only=False):
    """Display the subject's saved input prefix without rerunning admission."""
    from . import candidate_nominations, candidate_refs, lineage, worker_research
    reader = reader or Reader(root)
    job = job if job is not None else reader.node(before_step)['job']
    steps=candidate_steps(job,before_step)
    sources, records, mechanisms, candidates = [], [], {}, {}
    current_h = None
    memory_node={}
    for step in steps:
        node = reader.node(step)
        memory_node=node
        source, payload, seal = node['job'], node.get('result', node['payload']), node['seal']
        sources.append((source, payload, seal, None))
        if source['role'] == 'H':
            current_h = step
            for index, item in enumerate((payload.get('search_plan') or {}).get('commitments') or [], 1):
                ref = item.get('existing_ref') or f'h{step:03d}.m{index}'
                mechanisms[ref] = dict(item, status='pending')
        for item in ((payload.get('search_review') or {}).get('dispositions') or []) if ui_semantics.assessment(source) else []:
            if item['mechanism_ref'] in mechanisms:
                mechanisms[item['mechanism_ref']].update(status=item['status'], disposition=dict(item, source_a=step))
        observation = reader.observation(step)
        record = dict(step=step, role=source['role'], observation=observation,
            recommendation=payload.get('candidate_recommendation') if ui_semantics.assessment(source) and payload.get('next_action') != 'audit' else None,
            purpose=source.get('purpose'), formal_review=source.get('formal_review'),
            assessment_author=ui_semantics.assessment(source),
            view=dict(h_scope=current_h, summary=payload.get('summary', ''),
                conclusion_pages=[f'inputs/history/nodes/{step:03d}/0001.json']))
        if 'candidate_nominations' in payload:
            record['candidate_nominations'] = payload['candidate_nominations']
        if ui_semantics.assessment(source) and payload.get('next_action') == 'audit':
            record['audit_request'] = True
        records.append(record)
        for item in (observation or {}).get('candidates') or []:
            candidates.setdefault(item['id'], dict(observation=item, mechanism_ref=None))
        for item in (observation or {}).get('contexts') or []:
            if item.get('candidate_id') in candidates:
                candidates[item['candidate_id']].update(
                    mechanism_ref=(item.get('mechanism_binding') or {}).get('mechanism_ref') or item.get('scope', {}).get('mechanism_ref'),
                    execution_status=item.get('status'))
    unresolved = []
    # Keep malformed optional advice for its original-pointer warning. Unknown
    # identities cannot become nominees, including in incomplete display reads.
    known = set()
    projected = []
    for record in records:
        record = dict(record)
        known.update(item['id'] for item in (record.get('observation') or {}).get('candidates', []))
        if history_only and record['recommendation'] is not None and (not isinstance(record['recommendation'], str) or record['recommendation'] not in known):
            unresolved.append(dict(step=record['step'], field='recommendation', count=1))
            record['recommendation'] = None
        declaration = record.get('candidate_nominations')
        if isinstance(declaration,dict):
            declaration = copy.deepcopy(declaration)
            omitted = 0
            for kind in ('representatives', 'extras'):
                for row in declaration.get(kind,[]) if isinstance(declaration.get(kind),list) else []:
                    if isinstance(row,dict) and row.get('candidate') is not None and (not isinstance(row['candidate'],str) or row['candidate'] not in known):
                        row['candidate'] = None
                        omitted += 1
            if omitted:
                unresolved.append(dict(step=record['step'], field='nominations', count=omitted))
            record['candidate_nominations'] = declaration
        projected.append(record)
    records = projected
    indexed = lineage.candidate_view(records)
    from . import selection
    indexed.update(selection.recommendations([record for record in records
        if record['role']!='A' or record.get('assessment_author')]))
    graph = dict(mechanisms=mechanisms, candidates=candidates)
    latest_w = next((record for record in reversed(records) if record['role'] == 'W'), None)
    nominated = candidate_nominations.project([latest_w] if latest_w is not None else [], dict(mechanisms=mechanisms))
    nominated_ids = list(dict.fromkeys(row['candidate'] for row in [*nominated['representatives'], *nominated['extras']]))
    try:
        references = candidate_refs.references(records)
    except ValueError:
        references = {}
    return dict(index=indexed['candidate_index'], graph=graph, references=references,
                candidate_memory=candidate_memory(memory_node,
                    lambda value,limit:worker_research.public_text(value,limit) or '',
                    loaded_nodes=getattr(reader,'_nodes',{}).values()),
                nominated_ids=nominated_ids,
                nomination_diagnostics=nominated['diagnostics'],
                records=records, sources=sources, subject=job,
                recommended=(indexed.get('a_recommended') or {}).get('candidate_id'),
                unresolved=unresolved)


def candidate_memory(node, clean, *, loaded_nodes=()):
    """Bounded W-selected navigation from one already loaded accepted view."""
    memory=(node.get('research') or {}).get('candidate_memory')
    if not isinstance(memory,dict):return dict(coverage='unknown',candidates=[],clues=[])
    source=memory.get('source') or {}
    step=source.get('source_step')
    if type(step) is not int or step<1 or source.get('source_role')!='W':
        return dict(coverage='unknown',candidates=[],clues=[])
    materials={}
    for loaded in loaded_nodes:
        owner=loaded['job'].get('step')
        for index,row in enumerate(loaded.get('seal',{}).get('materials') or [],1):
            materials[row.get('path')]=f'/nodes/{owner}/materials/{index}'
    def link(reference):
        if isinstance(reference,dict) and set(reference)=={'path'}:reference=reference['path']
        if not isinstance(reference,str):return None
        if reference in materials:return materials[reference]
        match=re.fullmatch(r'inputs/history/nodes/(?:[A-Za-z0-9._-]+/)?([0-9]{3,})/(?:science|scientific-original)\.json',reference)
        if match:return '/nodes/'+str(int(match[1]))+'/science'
        match=re.fullmatch(r'inputs/parents/([0-9]{3,})/(?:conclusion\.json|artifacts/.+)',reference)
        if match:return '/nodes/'+str(int(match[1]))+('/science' if reference.endswith('/conclusion.json') else '/materials')
        return None
    def evidence_rows(items):
        result=[]
        for item in items[:8]:
            if not isinstance(item,dict) or item.get('relation') not in {'supports','opposes','unknown'}:continue
            result.append(dict(relation=item['relation'],note=clean(str(item.get('note') or ''),400),
                href=link(item.get('ref'))))
        return result
    def source_pointer(row,field):
        source=row.get('source') if isinstance(row.get('source'),dict) else {}
        pointer=source.get('pointer') if source.get('source_step')==step and source.get('source_role')=='W' else None
        # Accepted producer pointers retain the exact original array index.
        return pointer if isinstance(pointer,str) and re.fullmatch('/research_memory/'+field+r'/[0-9]+',pointer) else None
    rows=[]
    originals=memory.get('candidates') if isinstance(memory.get('candidates'),list) else []
    for row in originals[:16]:
        if not isinstance(row,dict):continue
        items=row.get('evidence') if isinstance(row.get('evidence'),list) else []
        rows.append(dict(candidate=clean(str(row.get('candidate') or '未声明'),300),
            data=clean(str(row.get('data') or ''),400),evaluation=clean(str(row.get('evaluation') or ''),400),
            failure_condition=clean(str(row.get('failure_condition') or ''),400),
            open_question=clean(str(row.get('open_question') or ''),400),
            limitations=[clean(str(value),240) for value in (row.get('limitations') or [])[:6]],
            evidence=evidence_rows(items),evidence_omitted=max(0,len(items)-8)))
    clues=[]
    original_clues=memory.get('clues') if isinstance(memory.get('clues'),list) else []
    for row in original_clues[:16]:
        if not isinstance(row,dict):continue
        items=row.get('evidence') if isinstance(row.get('evidence'),list) else []
        clue={key:clean(row[key],600) for key in ('observed_fact','applicability','limited_negative','open_question')
            if isinstance(row.get(key),str)}
        clue.update(evidence=evidence_rows(items),evidence_omitted=max(0,len(items)-8),
            source_pointer=source_pointer(row,'clues'))
        clues.append(clue)
    return dict(coverage='accepted',source_step=step,question=clean(str(memory.get('question') or ''),1200),
        candidates=rows,clues=clues,clues_omitted=max(0,len(original_clues)-16),omitted=max(0,len(originals)-16),
        diagnostic_count=len(memory.get('diagnostics') or [])+len(memory.get('update_diagnostics') or []))


def candidate_rows(projection, *, recommended=None, delivered=None, audits=None, mechanisms=None, clean=None, all_candidates=False):
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
    nominated = [item for item in projection['index'] if all_candidates or item['id'] in nominated_ids]
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
        identity_key = ((evaluation, ref) if all_candidates else evaluation) if isinstance(evaluation, str) and evaluation.strip() else ('unknown', order)
        groups.setdefault(identity_key, []).append(row)
    # The first CANDIDATE_LIMIT rows of every group show by default; marked
    # (author-recommended, delivered, failed) rows always stay visible.
    for group in groups.values():
        for index, row in enumerate(group):
            row['extra'] = index >= CANDIDATE_LIMIT and not row['marks']
    display_groups = [dict(key=value[0]['evaluation_key'], rows=value,
        **(dict(mechanism=value[0]['mechanism']) if all_candidates else {})) for value in groups.values()]
    for group in display_groups:
        group['ambiguous_label'] = sum(other['key'] == group['key'] for other in display_groups) > 1
    return dict(total=len(rows), groups=display_groups,
                limit=CANDIDATE_LIMIT,
                nomination_diagnostics=copy.deepcopy(projection.get('nomination_diagnostics',[])),
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
        if ui_semantics.assessment(source) and payload.get('next_action') == 'audit':
            audit_steps.add(source['step'])
    from . import candidate_audit
    models=candidate_audit.candidate_artifacts(root,projection['records'])
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
        binding = c.read(directory / 'binding.json') if value.get('binding_ref') is not None else None
        artifact = binding['source']['objects']['artifact'] if binding is not None else None
        matched = artifact is not None and models.get(chosen) == {(artifact['path'], artifact['size_bytes'])}
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
    value = payload.get('candidate_recommendation') if ui_semantics.assessment(job) else None
    audit = ui_semantics.assessment(job) and payload.get('next_action') == 'audit'
    resolution_error = None
    try:
        if sealed.get('candidate_reference_unresolved'):
            raise ValueError('Inherited candidate input identity is unavailable')
        saved = sealed.get('result') or {}
        chosen = (saved['candidate_recommendation'] if ui_semantics.assessment(job) and 'candidate_recommendation' in saved else
            reader.candidate(job,value,references,strict=True) if value is not None else None)
        if isinstance(chosen, str) and re.fullmatch(r'[HWAE]\d{3,}-I\d{3,}', chosen):
            # Saved normalized output may retain an implementation reference.
            # Resolve it through the same exact frozen material/observation
            # binding as the authored reference before indexing the candidate.
            chosen = reader.candidate(job,chosen,references,strict=True)
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
    if ui_semantics.kind(job)=='consultation':
        chosen, candidate = None, None
        delivery, delivery_label = 'consultation', ('证据咨询答复返回 W；由 W 判断后续研究'
            if job.get('role') == 'E' and ui_semantics.advisory_scope(job)
            else '研究咨询；不产生正式候选推荐')
    elif ui_semantics.diagnostic_advice(job) and not audit:
        chosen, candidate = None, None
        delivery, delivery_label = 'consultation', '诊断建议返回 W；不构成方向认可或交付判断'
    if audit:
        delivery, delivery_label = 'audit', '审计请求'
    explanation = (payload.get('selection_explanation') if ui_semantics.assessment(job)
        and isinstance(payload.get('selection_explanation'), dict) else None)
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
    reflection=ui_semantics.reflection(job)
    reflection_rows=reflection_facts(reflection)
    if reflection is not None:
        reflection_rows.append(dict(label='诊断回程' if ui_semantics.diagnostic_advice(job) else '反思结论',value=reflection_response(payload,
            ui_semantics.action(job,payload.get('next_action'),A_OUTCOMES),diagnostic=ui_semantics.diagnostic_advice(job))))
    return dict(step=step, role=job['role'], kind=ui_semantics.kind(job),
                reflection_facts=reflection_rows,
                assessment_label=('自动诊断建议（返回 W）' if ui_semantics.diagnostic_advice(job) else '自动研究反思') if reflection is not None else
                    '研究诊断建议（返回 W）' if ui_semantics.diagnostic_advice(job) else None,
                next_action=payload.get('next_action'), decision=ui_semantics.action(job,payload.get('next_action'),A_OUTCOMES),
                summary=clean(payload['summary'], 1200) if isinstance(payload.get('summary'), str) else '未记录摘要',
                delivery_state=delivery, delivery_label=delivery_label, chosen=None if audit else candidate,
                audit_target=candidate if audit else None,
                recommendation_raw=clean(value, 300) if isinstance(value, str) and not audit else None,
                resolution_error=resolution_error,
                reason=clean(reason, 400) if reason else '未记录',
                criteria_change=criteria_change,
                final_coverage=final_coverage(job, payload, clean),
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
                        return dict(step=owner, index=index, name=display._label(member, owner), reference=dict(path=member['source'],size_bytes=member['size_bytes']))
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
            return dict(step=step, index=index, name=name, reference=dict(path=material['source'],size_bytes=material['size_bytes']))
    return None


def results_list_row(reader, step, clean):
    sealed = reader.node(step)
    job, payload = sealed['job'], sealed['payload']
    summary = payload.get('summary') if isinstance(payload.get('summary'), str) and payload['summary'].strip() else \
        first_sentence(payload.get('scientific_result'), 600)
    label = ui_semantics.role_label(job) or ROLE_LABELS.get(job['role'],job['role'])
    return dict(step=step, role=job['role'], role_label=label, one_line=clean(summary, 600) if summary else '未记录摘要')


def elapsed_text(prepared_at, now=None):
    if type(prepared_at) not in (int, float) or prepared_at < 1e9:
        return None
    seconds = (time.time() if now is None else now) - prepared_at
    if seconds < 0:
        return None
    minutes = int(seconds // 60)
    return (str(minutes) + ' 分钟') if minutes < 60 else fmt_duration(seconds)
