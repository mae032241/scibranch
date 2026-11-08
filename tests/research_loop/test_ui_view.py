"""Workbench view models: plain data from sealed/control reads, no HTML, no writes."""
import json
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import instance_control as control, ui_view
from test_execution_authority import prepare
from test_history_handoff import finish


def test_numbers_durations_and_metrics_are_readable_without_rounding_science():
    assert ui_view.fmt_tokens(5846087) == '584.6 万'
    assert ui_view.fmt_tokens(5000000) == '500 万'
    assert ui_view.fmt_tokens(1234) == '1,234' and ui_view.fmt_tokens(30) == '30'
    assert ui_view.fmt_tokens(None) == '未记录'
    assert ui_view.fmt_duration(2636.5) == '43 分 57 秒'
    assert ui_view.fmt_duration(10800) == '3 小时' and ui_view.fmt_duration(3720) == '1 小时 2 分'
    assert ui_view.fmt_duration(45) == '45 秒' and ui_view.fmt_duration(None) == '未记录'
    assert ui_view.percent(5846087, 5000000) == 117 and ui_view.percent(1, None) is None
    assert ui_view.fmt_metric(0.91) == '0.91' and ui_view.fmt_metric(1.2345678901) == '1.2346'
    assert ui_view.fmt_metric(None) == '—' and ui_view.fmt_metric(3) == '3' and ui_view.fmt_metric('n/a') == 'n/a'
    assert ui_view.first_sentence('First claim. Second claim.') == 'First claim.'
    assert ui_view.first_sentence('第一句。第二句。') == '第一句。'
    assert ui_view.first_sentence(None) is None and ui_view.first_sentence('  ') is None
    assert ui_view.objective_title('\n# **Sparse** `inverse`\nbody', 'fallback') == 'Sparse inverse'
    assert ui_view.objective_title('', 'fallback') == 'fallback'
    assert len(ui_view.objective_title('x' * 80, 'f')) == 60


def test_metric_direction_comes_only_from_author_declaration():
    assert ui_view.direction_sense('higher is better') == 'max'
    assert ui_view.direction_sense('min') == 'min' and ui_view.direction_sense('lower is better') == 'min'
    assert ui_view.direction_sense(None) is None and ui_view.direction_sense('see notes') is None
    same = [dict(evaluation_key='val', metric=0.5, direction='max'), dict(evaluation_key='val', metric=0.7, direction='max')]
    assert ui_view._candidate_extreme(same)['text'] == '最高 0.7（val）'
    undeclared = [dict(evaluation_key='val', metric=0.5), dict(evaluation_key='val', metric=0.7)]
    assert ui_view._candidate_extreme(undeclared)['text'] == '范围 0.5–0.7（val；方向未声明）'
    mixed = [dict(evaluation_key='a', metric=1.0, direction='max'), dict(evaluation_key='b', metric=9.0, direction='max')]
    assert ui_view._candidate_extreme(mixed)['text'] == '多种口径，见候选表'


@pytest.mark.parametrize('label, expected', [
    ('max', 'max'), ('min', 'min'), ('MAXIMIZE', 'max'), ('minimise', 'min'),
    ('higher', 'max'), ('lower', 'min'), (' Higher  is better. ', 'max'),
    ('lower values are better', 'min'),
    ('lower is worse', None), ('higher is worse', None),
    ('lower is not better', None), ('higher is not better', None),
    ('higher or lower', None), ('max but lower is better', None),
    ('min and higher is better', None), ('maximize loss unless lower is better', None),
    ('higher recovery is better', None), ('a larger sample was observed', None),
])
def test_metric_direction_requires_an_unambiguous_declared_label(label, expected):
    assert ui_view.direction_sense(label) == expected
    rows = [dict(evaluation_key='val', metric=value, direction=label) for value in (1, 2)]
    extreme = ui_view._candidate_extreme(rows)
    assert extreme.get('sense') == expected
    if expected is None:
        assert extreme['text'].startswith('范围 ') and 'value' not in extreme


class FakeReader:
    """Sealed nodes only; reading an unsealed node's original is a failure."""

    def __init__(self, nodes):
        self.root = Path('/nonexistent-ui-view-fixture')
        self.nodes = nodes

    def node(self, step):
        if self.nodes[step] is None:
            pytest.fail('Unsealed role lines must not read originals')
        return self.nodes[step]

    def observation(self, step):
        return self.nodes[step].get('observation')

    def role(self, step):
        return self.nodes[step]['job']['role'] if self.nodes[step] else 'W'

    def candidate(self,job,value,references=None,*,strict=False):
        return ui_view.resolve_candidate(self.root,job,value,references,strict=strict,facts=dict(resolved=value))


def sealed(step, role, payload, *, observation=None, materials=0, **job):
    return dict(job=dict(step=step, role=role, **job), payload=payload,
                seal=dict(materials=[{}] * materials, raw_ref='x'), observation=observation)


def calls(count):
    return [dict(call_id=f'{index:04d}', accepted=dict(at=1)) for index in range(1, count + 1)] + [dict(call_id='rejected', accepted=None)]


def test_rounds_split_at_each_h_and_take_outcome_from_last_ordinary_a():
    candidates = [dict(id='local-a', label='alpha', evaluation_key='val', metric=0.8, direction='higher', status='ok'),
                  dict(id='local-b', label='beta', evaluation_key='val', metric=0.9, direction='higher', status='ok')]
    reader = FakeReader({
        1: sealed(1, 'E', dict(summary='Prepared evidence')),
        2: sealed(2, 'H', dict(summary='Plan', next_action='experiment', search_plan=dict(commitments=[
            dict(label=f'M{index}') for index in range(1, 7)]))),
        3: sealed(3, 'W', dict(summary='', scientific_result='Measured. More detail.'),
                  observation=dict(candidates=candidates, managed_calls=calls(4)), materials=7),
        4: sealed(4, 'A', dict(summary='Revise', next_action='revise_h', route_reason='Need a narrower question. Details.',
                               candidate_recommendation=None, search_review=dict(
                                   dispositions=[dict(mechanism_ref='h002.m1', status='screened'), dict(mechanism_ref='h002.m2', status='deferred')],
                                   next_focus=dict(mechanism_ref='h002.m2', action='improve')))),
        5: sealed(5, 'H', dict(summary='Revised', next_action='experiment', search_plan=None)),
        6: None,
    })
    nodes = [dict(step=1, role='E', state='seal_present_unverified'), dict(step=2, role='H', state='seal_present_unverified'),
             dict(step=3, role='W', state='seal_present_unverified'), dict(step=4, role='A', state='seal_present_unverified'),
             dict(step=5, role='H', state='seal_present_unverified'), dict(step=6, role='W', state='running', continuations=1)]
    mechanisms = ui_view.mechanism_labels(reader, [1, 2, 3, 4, 5, 6])
    assert mechanisms['h002.m2'] == 'M2'
    value = ui_view.rounds(reader, nodes, lambda text, limit=16384: str(text)[:limit], mechanisms=mechanisms,
                           labels={'local-a': 'alpha'})
    chronological = list(reversed(value['rounds']))
    assert [item['index'] for item in chronological] == [None, 1, 2]
    assert [item['steps'] for item in chronological] == [[1], [2, 3, 4], [5, 6]]
    assert [item['outcome'] for item in chronological] == ['进行中', '修订方向', '进行中']
    first = chronological[1]
    assert first['experiments'] == 4 and first['summary'] == '第 1 轮 · 修订方向 · W 4 次实验 · 最高 0.9（val）'
    h, w, a = first['h'], first['w'][0], first['a'][0]
    assert h['facts'][0]['value'].startswith('6 个：M1、M2、M3、M4、M5') and '另 1 个' in h['facts'][0]['value']
    assert w['one_line'] == 'Measured.' and w['facts'][0]['title'] == ui_view.EXPERIMENT_TOOLTIP
    assert w['links'][2]['label'] == '研究文件（7 份）'
    facts = {row['label']: row['value'] for row in a['facts']}
    assert facts['决定'] == '修订方向' and facts['理由'] == 'Need a narrower question.'
    assert facts['机制处置'] == '已筛查 1，暂缓 1' and facts['下一焦点'] == 'M2 · improve' and facts['候选推荐'] == '未推荐'
    assert chronological[2]['h']['facts'][0]['value'] == '未记录'
    running = chronological[2]['w'][0]
    assert running['sealed'] is False and running['one_line'] is None and running['facts'] == []
    assert running['links'] == [dict(label='公开进度', href='/nodes/6/progress?attempt=1')]


def test_formal_review_does_not_decide_round_and_closeout_is_labelled():
    reader = FakeReader({
        1: sealed(1, 'H', dict(summary='Plan', next_action='experiment')),
        2: sealed(2, 'A', dict(summary='Review', verdict='pass', next_action='finish'), formal_review=True),
        3: sealed(3, 'A', dict(summary='Final', next_action='deliver', candidate_recommendation='local-x'), purpose='closeout'),
        4: sealed(4, 'A', dict(summary='Late review', verdict='fail'), formal_review=True),
    })
    nodes = [dict(step=index, role=role, state='seal_present_unverified') for index, role in ((1, 'H'), (2, 'A'), (3, 'A'), (4, 'A'))]
    (item,) = ui_view.rounds(reader, nodes, lambda text, limit=16384: str(text), labels={'local-x': 'chosen <label>'})['rounds']
    assert item['outcome'] == '交付'
    assert [line['role_label'] for line in item['a']] == ['A · 正式评审', 'A · 最终评估', 'A · 正式评审']
    assert {row['label']: row['value'] for row in item['a'][1]['facts']}['候选推荐'] == '★ chosen <label>'


def test_status_matrix_maps_existing_presentation_without_new_control_semantics():
    def summary(state='seal_present_unverified', *, completed=False, waiting=False, active=False, paused=False, current=True, **options):
        node = dict(step=4, role='W', state=state) if current else None
        presentation = dict(current=node, state=state if current else 'ready', completed=completed, waiting=waiting, active=active)
        return ui_view.status_summary(presentation, dict(paused=paused), **options)
    assert summary(current=False)['state'] == 'ready' and summary(current=False)['badge'] == '未开始'
    active = summary('running', active=True, current_round=2, elapsed='12 分钟')
    assert active['state'] == 'active' and active['headline'] == '第 2 轮 · W 正在实验（已 12 分钟）'
    assert summary('running', active=True, paused=True)['state'] == 'pause_requested'
    assert summary(paused=True)['headline'] == '已暂停于第 4 阶段'
    assert summary(waiting=True, waiting_reason='Need data.')['headline'] == 'A 等待：Need data.'
    assert summary('unknown')['badge'] == '需要核对'
    assert summary(final_phase=True)['state'] == 'final'
    assert summary(exhausted=['tokens'])['headline'] == '预算已用完：tokens'
    assert summary(completed=True, completed_line='第 3 轮 · A 决定交付 x')['badge'] == '已完成'
    assert summary()['state'] == 'idle' and '第 4 阶段 · W · 实验 · 已有阶段结论' == summary()['stage']


@pytest.mark.parametrize('code, meaning, action', [
    ('invalid_api_key', '认证失败或凭据无效', '重新登录'),
    ('permission_denied', '账户或组织权限不足', '模型访问权限'),
    ('insufficient_quota', '服务配额、余额或计费上限不足', '增加本研究的 tokens 预算不会恢复'),
    ('model_not_found', '所选模型或服务资源不可用', '分支重做不会自动换模型'),
    ('invalid_configuration', '请求格式、参数或启动器配置不兼容', '检查宿主启动器版本'),
    ('invalid_json_schema', '请求格式、参数或启动器配置不兼容', '不能靠补填阶段结论修复'),
    ('context_length_exceeded', '原任务上下文超过模型容量', '分支重做保留原始输入'),
])
def test_permanent_failure_help_uses_existing_safe_codes_with_concrete_actions(code, meaning, action):
    node = dict(native_failure=dict(category='permanent', codes=[code], statuses=[429],
        message='private-token /private/provider https://private.invalid'))
    guidance = ui_view.native_failure_guidance(node)
    assert meaning in guidance['summary']
    assert code + '：' in '；'.join(guidance['explanations'])
    assert action in '；'.join(guidance['advice'])
    assert 'private' not in repr(guidance)
    facts = ui_view.native_facts(node, lambda text, limit=16384: str(text)[:limit])
    assert any(row['label'] == '处理建议' and action in row['value'] for row in facts)


def test_unknown_permanent_failure_points_to_host_diagnostics_without_echoing_raw_text():
    guidance = ui_view.native_failure_guidance(dict(native_failure=dict(category='permanent',
        codes=['private-token /private/provider'], statuses=[])))
    assert '具体原因尚未识别' in guidance['summary']
    assert '宿主机核对本次原生诊断' in guidance['advice'][0]
    assert 'private' not in repr(guidance)
    assert ui_view.native_failure_guidance(dict(native_failure=dict(category='temporary', codes=['rate_limit_exceeded']))) is None


@pytest.mark.parametrize('paused', [False, True])
def test_stopped_permanent_failure_is_actionable_even_when_paused(paused):
    node = dict(step=4, role='W', state='automatic_recovery_stopped', returncode=1,
        terminal_observed=False, cleanup='observed_group_cleaned',
        automatic_recovery=dict(status='stopped'),
        native_failure=dict(category='permanent', codes=['insufficient_quota'], statuses=[429]))
    presentation = dict(current=node, state=node['state'], active=False, waiting=False, completed=False)
    value = ui_view.status_summary(presentation, dict(paused=paused))
    assert value['state'] == 'service_error' and value['badge'] == '服务错误待处理'
    assert '配额、余额或计费上限不足' in value['headline']
    assert ('研究已暂停' in value['headline']) is paused
    assert ui_view.native_failure_fork_available(node)
    for state in ('running', 'unknown', 'waiting', 'automatic_recovery_waiting', 'seal_present_unverified'):
        assert not ui_view.native_failure_fork_available(dict(node, state=state))
    assert not ui_view.native_failure_fork_available(dict(node, cleanup='unknown'))
    assert not ui_view.native_failure_fork_available(dict(node, returncode=None))


@pytest.mark.parametrize('category, reason, expected', [
    ('unknown', 'automatic_recovery_unknown_failure', '原生错误原因未识别'),
    ('temporary', 'automatic_recovery_retry_limit', '自动接续次数或等待额度已用尽'),
    ('incomplete', 'automatic_recovery_continue_limit', '自动接续次数或等待额度已用尽'),
])
def test_confirmed_stopped_unclassified_or_limited_failure_has_help_without_claiming_permanence(category, reason, expected):
    node = dict(step=4, role='W', state='automatic_recovery_stopped', returncode=1,
        cleanup='observed_group_cleaned', native_failure=dict(category=category, codes=[], statuses=[]),
        automatic_recovery=dict(status='stopped', reason=reason))
    help_value = ui_view.native_failure_guidance(node)
    assert expected in help_value['summary'] and '永久' not in repr(help_value)
    assert '宿主' in help_value['advice'][0] and ui_view.native_failure_fork_available(node)
    value = ui_view.status_summary(dict(current=node, state=node['state'], active=False,
        waiting=False, completed=False), dict(paused=False))
    assert value['state'] == 'service_error' and value['badge'] == '接续停止待处理'
    assert expected in value['headline']
    for state in ('running', 'unknown', 'waiting', 'automatic_recovery_waiting', 'automatic_recovery_paused'):
        assert ui_view.native_failure_guidance(dict(node, state=state)) is None
        assert not ui_view.native_failure_fork_available(dict(node, state=state))
    assert ui_view.native_failure_guidance(dict(node, cleanup='unknown')) is None


def test_usage_cards_mark_overrun_unknown_usage_and_waiting():
    view = dict(budget=dict(max_tokens=5000000, max_seconds=10800.0, consumed_seconds=2636.5, waiting_seconds=600, max_steps=80),
                usage=dict(observed_tokens=5846087, unknown_steps=[3, 4]),
                closeout=dict(final_overrun=dict(shared_tokens_over_ceiling=846087)))
    value = ui_view.usage_stats(view, used_steps=11, candidates=dict(count=32, recommended=True, delivered=True))
    tokens = value['tokens']
    assert (tokens['value'], tokens['limit'], tokens['percent'], tokens['over']) == ('584.6 万', '500 万', 117, True)
    assert tokens['notes'] == ['最终 A 受保护，超出 84.6 万', '2 个阶段用量未知（下界）']
    assert (value['time']['value'], value['time']['limit'], value['time']['notes']) == ('43 分 57 秒', '3 小时', ['其中等待 10 分'])
    assert (value['steps']['value'], value['steps']['limit']) == ('11', '80')
    assert value['candidates']['label'] == '历史候选记录'
    assert value['candidates']['value'] == '32' and value['candidates']['notes'] == ['A 推荐 1', '已交付 1']
    missing = ui_view.usage_stats({})
    assert missing['tokens']['value'] == '未记录' and missing['time']['limit'] == '未记录' and missing['candidates']['value'] == '未记录'


def test_final_coverage_displays_only_a_authored_rows_and_exact_scientific_sources():
    job = dict(role='A',final_coverage_mechanisms=[dict(mechanism_ref='h001.m1', label='Alpha'),
        dict(mechanism_ref='h001.m2', label='Waived alternative')])
    payload = dict(final_coverage=[dict(mechanism_ref='h001.m1', status='screened_pending',
        reason='Evidence remains preliminary.', science_refs=[dict(source_step=2, pointer='/scientific_result')],
        unanswered_questions=['Does the response persist?']), dict(mechanism_ref='h001.m2', status='unexplored',
        reason='Required input is unavailable.', science_refs=[dict(source_step=1, pointer='/search_plan')],
        unanswered_questions=[])])
    clean = lambda value, limit=16384: str(value)[:limit]
    rows = ui_view.final_coverage(job, payload, clean)
    assert [row['status'] for row in rows] == ['screened_pending', 'unexplored']
    assert rows[0]['science_refs'] == [dict(source_step=2, pointer='/scientific_result')]
    assert rows[1]['label'] == 'Waived alternative' and rows[1]['unanswered_questions'] == []
    assert ui_view.final_coverage(job, dict(candidates=[dict(status='ok')]), clean) is None
    assert ui_view.final_coverage(dict(job, formal_review=True), payload, clean) is None
    reader = FakeReader({3: sealed(3, 'A', dict(payload, summary='Final', next_action='finish'), **{key:value for key,value in job.items() if key!='role'})})
    assert ui_view.result_card(reader, 3, clean=clean)['final_coverage'] == rows


def saved_breadth(*, partial=False, advice_status='available', reasons=None):
    return dict(portfolio_metrics=dict(source_step=5, partial=partial,
        mechanisms=[dict(mechanism_ref='a', label='Alpha'), dict(mechanism_ref='b', label='Beta')],
        portfolio=dict(completed_cycle_count=2, pending_assessment=True,
            pending_w=dict(source_step=6, mechanism_refs=['b']), main_line='a', mainline_streak=2,
            mainline_source=dict(source_step=5, pointer='/portfolio_review/main_line'),
            participation=[dict(mechanism_ref='a', participated_cycles=2, participation_fraction=1),
                dict(mechanism_ref='b', participated_cycles=1, participation_fraction=.5)]),
        discriminating_work=[dict(work_ref='a5.d1', source_a=5, mechanism_refs=['a', 'b'],
            question='Which mechanism explains the response?', change_criterion='An independent observation differs.',
            status='deferred', updates=[dict(source_a=5, action='defer', reason='The input is missing.',
                trigger='The input arrives.', revisit='Before closing.')])],
        token_advice=dict(status=advice_status, reference_cycles=3.5, minimum_samples=2, complete_sample_count=2,
            excluded_sample_count=1, reasons=reasons or [], scope='Complete local W/A reported tokens only.',
            samples=[dict(w_step=2, a_steps=[3]), dict(w_step=4, a_steps=[5])])))


def test_saved_breadth_keeps_global_denominator_pending_streak_and_work_separate():
    clean = lambda value, limit=16384: str(value)[:limit]
    value = ui_view.breadth_metrics(saved_breadth(), clean)
    assert value['completed_cycle_count'] == 2
    assert [row['fraction_text'] for row in value['participation']] == ['100%', '50%']
    assert value['pending_step'] == 6 and value['pending_labels'] == ['Beta']
    assert value['main_line'] == 'Alpha' and value['mainline_streak'] == 2 and value['mainline_step'] == 5
    assert value['work'][0]['trigger'] == 'The input arrives.' and value['active_work_count'] == 1
    assert value['token_advice']['available'] and value['token_advice']['reference_cycles'] == '3.5'


@pytest.mark.parametrize('partial,status,reasons', [(True, 'available', []),
    (False, 'unavailable', ['unknown_usage']), (False, 'unavailable', ['insufficient_complete_samples'])])
def test_saved_breadth_partial_unknown_and_insufficient_samples_never_show_numeric_estimate(partial, status, reasons):
    clean = lambda value, limit=16384: str(value)[:limit]
    value = ui_view.breadth_metrics(saved_breadth(partial=partial, advice_status=status, reasons=reasons), clean)
    assert not value['token_advice']['available'] and value['token_advice']['reference_cycles'] is None
    assert value['token_advice']['reasons'] == reasons
    assert not ui_view.breadth_metrics(dict(nodes=[dict(step=99)]), clean)['available']


def test_saved_breadth_zero_denominator_unknown_fraction_and_fork_history_are_explicit():
    view = saved_breadth()
    view['fork'] = dict(inherited_costs={})
    portfolio = view['portfolio_metrics']['portfolio']
    portfolio['completed_cycle_count'] = 0
    assert all(row['fraction_text'] == '无完整周期样本' for row in ui_view.breadth_metrics(view, lambda value, limit: str(value))['participation'])
    portfolio['completed_cycle_count'] = 2
    portfolio['participation'][0]['participation_fraction'] = float('nan')
    value = ui_view.breadth_metrics(view, lambda value, limit: str(value))
    assert value['inherited'] and value['participation'][0]['fraction_text'] == '不可核实'


@pytest.mark.parametrize('complete_samples,unknown', [(0, []), (1, []), (2, [6])])
def test_saved_breadth_does_not_publish_available_label_with_insufficient_samples_or_unknown_usage(complete_samples, unknown):
    view = saved_breadth()
    view['portfolio_metrics']['token_advice']['complete_sample_count'] = complete_samples
    view['usage'] = dict(unknown_steps=unknown)
    value = ui_view.breadth_metrics(view, lambda text, limit=16384: str(text)[:limit])
    assert not value['token_advice']['available'] and value['token_advice']['reference_cycles'] is None


def test_unverifiable_time_is_explicit_without_claiming_exhaustion():
    view = dict(budget=dict(max_tokens=100, max_seconds=60, max_steps=5,
        consumed_seconds=None, seconds_remaining=None, time_status='unknown'),
        usage=dict(observed_tokens=2))
    duration = ui_view.usage_stats(view)['time']
    assert duration['value'] == '无法核实'
    assert duration['limit'] == '1 分'
    assert duration['raw'] is None and duration['percent'] is None and not duration['over']
    assert duration['notes'] == ['时钟记录不足以核实已用时间与剩余额度；已保留原有记录。']
    assert ui_view.exhausted_limits(view, 1) == []


def test_token_monitoring_keeps_cache_subset_and_context_independent(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    usage = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5, observed_tokens=105,
                 context=dict(status='last_reported', total_tokens=30, model_context_window=100, reported_at=140))
    token_card = ui_view.usage_stats(dict(usage=usage))['tokens']
    values = {row['label']: row['value'] for row in token_card['details']}
    assert token_card['raw'] == 105
    assert values['输入'] == '100' and values['缓存输入'] == '80'
    assert values['非缓存输入'] == '20' and values['输出'] == '5'
    assert values['缓存命中率'] == '80.0%'
    assert values['最近一次上报上下文'] == '30 / 100 · 30.0%；上报于 10 秒前'
    empty = ui_view.token_details(dict(input_tokens=0, cached_input_tokens=0, output_tokens=5))
    assert {row['label']: row['value'] for row in empty}['缓存命中率'] == '不可用'
    assert '不可用' in {row['label']: row['value'] for row in empty}['最近一次上报上下文']


def test_candidate_rows_keep_observation_order_groups_marks_and_missing_values():
    index = [dict(id=f'local-{number:02d}', label=None if number == 3 else f'c{number}',
                  evaluation_key=None if number == 4 else ('val' if number % 2 else 'test'),
                  metric=None if number == 5 else 30 - number, direction='higher' if number % 2 else None,
                  status='failed' if number == 6 else 'ok', observation_step=2 + number // 10)
             for number in range(1, 26)]
    graph = dict(mechanisms={'h001.m1': dict(label='Mechanism one')},
                 candidates={'local-02': dict(mechanism_ref='h001.m1', execution_status='known_success')})
    value = ui_view.candidate_rows(dict(index=index, nominated_ids=[row['id'] for row in index], graph=graph, references={'local-01': 'W002-C001/model.pkl'}),
                                   recommended='local-07', delivered='local-07', audits={'local-01': '公开审计通过'})
    rows = [row for group in value['groups'] for row in group['rows']]
    assert value['total'] == 25 and len(rows) == 25
    assert [group['key'] for group in value['groups']] == ['val', 'test', '未声明']
    assert all(earlier['order'] < later['order'] for group in value['groups'] for earlier, later in zip(group['rows'], group['rows'][1:]))
    by_id = {row['id']: row for row in rows}
    assert by_id['local-03']['label'] == '03' and by_id['local-05']['metric_text'] == '—'
    assert by_id['local-02']['mechanism'] == 'Mechanism one' and by_id['local-01']['mechanism'] == '未关联'
    assert by_id['local-07']['marks'] == ['recommended', 'delivered'] and by_id['local-06']['marks'] == ['failed']
    assert by_id['local-01']['audit'] == '公开审计通过' and by_id['local-08']['audit'] == '未审计'
    assert by_id['local-01']['reference'] == 'W002-C001/model.pkl'
    assert not any(row['extra'] for row in rows)  # Every group is within its default 20 rows.
    single = ui_view.candidate_rows(dict(index=[dict(row, evaluation_key='val') for row in index], nominated_ids=[row['id'] for row in index], graph=graph, references={}),
                                    recommended='local-25')
    flags = [row['extra'] for row in single['groups'][0]['rows']]
    assert flags == [False] * 20 + [True] * 4 + [False]  # A marked row is never hidden by default.
    assert not any('best' in json.dumps(row) or '最佳' in json.dumps(row, ensure_ascii=False) for row in rows)


def nominated_scene(tmp_path):
    """Sealed historical pool, two W shortlists, and an A choice outside both."""
    from scidiscovery.research_loop import controller, search
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Compare the retained mechanisms.')
    observer = tmp_path / 'observer.py'
    observer.write_text('''import json,sys
r=json.load(sys.stdin)
rows=[dict(id=f'model-{i}',label=f'Candidate {i}',implementation=f'history/nodes/002/artifacts/model-{i}.py',evaluation_key='val',metric=i/100,direction='max',status='ok',evidence=['project/global/objective.md']) for i in range(1,38)] if r['step']==2 else []
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Presentation fixture only'))))
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-invoked'], max_tokens=100000, max_seconds=600,
        max_steps=8, model='fixture', reasoning_effort='high', experiment_mode='external',
        search_policy=search.DEFAULT_POLICY, history_observer=dict(adapter=str(observer), config={}))
    labels = ('P', 'Q', 'R', 'S')
    plan = dict(scope='Compare four mechanisms', commitments=[dict(label=label, minimal_test='Bounded comparison',
        existing_ref=None) for label in labels], focus=dict(label=None, action='screen'))
    finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    findings = [dict(mechanism=label, basis='blocked', finding='No managed experiment in this UI fixture.') for label in labels]
    finish(root, 2, 'W', [1], mechanism_findings=findings,
        files={f'model-{i}.py': f'# exact original {i}\n' for i in range(1, 38)})
    def nominations(start):
        return dict(representatives=[dict(mechanism_ref=f'h001.m{i+1}', candidate=f'model-{start+i}',
            reason='Retain the declared mechanism representative.') for i in range(4)],
            extras=[dict(candidate=f'model-{start+i}', reason='Retain an explicit alternative.') for i in (4, 5)], gaps=[])
    finish(root, 3, 'W', [2], mechanism_findings=findings, candidate_nominations=nominations(1))
    finish(root, 4, 'A', [3], recommendation='model-37')
    finish(root, 5, 'W', [4], mechanism_findings=findings, candidate_nominations=nominations(7))
    finish(root, 6, 'A', [5], recommendation='model-37')
    key = tmp_path / 'operator.key'; key.write_bytes(b'fixture-nomination-key-32-bytes---'); key.chmod(0o600)
    return root, key


@pytest.mark.process_e2e
def test_comparison_uses_latest_frozen_nominations_and_exact_a_choice_without_shrinking_pool(tmp_path):
    root, _ = nominated_scene(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    older = ui_view.candidate_projection(root, 4)
    assert len(older['index']) == 37
    assert older['nominated_ids'] == [f'model-{i}' for i in range(1, 7)]
    assert ui_view.candidate_rows(older)['total'] == 6
    assert ui_view.candidate_rows(older, recommended='model-1')['total'] == 6
    selected = ui_view.candidate_rows(older, recommended='model-37', delivered='model-36')
    assert [row['id'] for row in selected['groups'][0]['rows']] == [f'model-{i}' for i in (1, 2, 3, 4, 5, 6, 37)]
    assert selected['groups'][0]['rows'][-1]['marks'] == ['recommended']
    assert ui_view.candidate_projection(root, 6)['nominated_ids'] == [f'model-{i}' for i in range(7, 13)]
    job = dict(ui_view.Reader(root).node(6)['job'], final_report=dict(source_cutoff=3), direct_refs=[])
    frozen = ui_view.candidate_projection(root, 6, job=job)
    assert frozen['nominated_ids'] == older['nominated_ids']
    assert [record['step'] for record in frozen['records']] == [1, 2, 3]
    direct = ui_view.candidate_projection(root, 6, job=dict(job, direct_refs=[dict(path='inputs/parents/005/conclusion.json')]))
    assert [record['step'] for record in direct['records']] == [1, 2, 3, 5]
    assert before == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('declaration', [None, dict(representatives=[], extras=[], gaps=[dict(mechanism_ref='h001.m1', reason='No usable model.')])])
def test_latest_w_without_nominees_never_falls_back_to_older_w_or_pool_but_keeps_a_choice(monkeypatch, declaration):
    previous = dict(candidate_nominations=dict(
        representatives=[dict(mechanism_ref='h001.m1', candidate='older-nominee', reason='An earlier W nomination.')],
        extras=[], gaps=[]))
    record = {}
    if declaration is not None:
        record['candidate_nominations'] = declaration
    class Saved:
        def node(self, step):
            payload = previous if step == 1 else record
            return dict(job=dict(step=step, role='W'), payload=payload, seal={})
        def observation(self, step):
            return dict(candidates=[dict(id=identity, label=identity, evaluation_key='val', implementation=None,
                metric=1, direction='higher', status='ok') for identity in ('older-nominee', 'historical')],
                current_candidate=None, actual_submission=None)
    projection = ui_view.candidate_projection(Path('/unused'), 3, job={}, reader=Saved())
    assert projection['nominated_ids'] == [] and len(projection['index']) == 2
    assert ui_view.candidate_rows(projection)['total'] == 0
    assert ui_view.candidate_rows(projection, delivered='historical')['total'] == 0
    chosen = ui_view.candidate_rows(projection, recommended='historical')
    assert chosen['total'] == 1 and chosen['groups'][0]['rows'][0]['marks'] == ['recommended']
    assert chosen['groups'][0]['rows'][0]['id'] == 'historical'


def test_guarded_component_error_is_typed_and_hides_private_paths(tmp_path):
    root, _ = prepare(tmp_path, enabled=False)
    clean = ui_view.cleaner(root)
    def broken():
        raise ValueError('Ledger at ' + str(root) + '/control-ledger is malformed')
    value = ui_view.guarded(broken, clean=clean)
    assert value['error']['kind'] == 'ValueError'
    assert str(root) not in value['error']['message'] and '[private]' in value['error']['message']
    assert ui_view.failure(OSError('/secret/path'))['error']['message'] == '错误信息不可显示'


def test_projection_and_result_card_read_sealed_fixture_without_writes(tmp_path):
    root, _ = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='experiment', summary='First <script>plan</script>')
    finish(root, 2, 'A', [1], action='finish', summary='Final judgment', limitations=['L1', 'L2', 'L3', 'L4'])
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    view = control.status(root, after=0, limit=50, live_feedback=False)
    value = ui_view.project(root, view, final_step=None)
    assert value['header']['title'] == 'Assess one exact declared implementation.'
    (item,) = value['trail']['rounds']
    assert item['index'] == 1 and item['outcome'] == '请求提交前最终评估' and item['h']['one_line'] == 'First <script>plan</script>'
    assert value['candidates'] == dict(count=0, partial=False, recommended=False, delivered=False)
    again = ui_view.project(root, view, final_step=None, memo={})
    assert again['trail'] == value['trail']
    assert value['used_steps'] == 2
    card = ui_view.result_card(ui_view.Reader(root), 2, closeout=view.get('closeout'))
    assert card['delivery_state'] == 'none' and card['delivery_label'] == '未推荐' and card['chosen'] is None
    assert card['unmet'] == ['L1', 'L2', 'L3'] and card['unmet_omitted'] == 1 and card['criteria_change'] is None
    assert card['goal_completion'] == '未记录' and card['decision'] == '请求提交前最终评估'
    row = ui_view.results_list_row(ui_view.Reader(root), 1, ui_view.cleaner(root))
    assert row == dict(step=1, role='H', role_label='H · 假设', one_line='First <script>plan</script>')
    assert before == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}


SAMPLE = os.environ.get('SCID_UI_SAMPLE_S1')


@pytest.mark.skipif(not SAMPLE, reason='Optional retained real sample; set SCID_UI_SAMPLE_S1 to a read-only copy')
def test_retained_real_sample_projection_matches_acceptance_facts():
    root = Path(SAMPLE)
    before = {path: path.stat().st_mtime_ns for path in root.rglob('*') if path.is_file()}
    view = control.status(root, after=0, limit=50, live_feedback=False)
    value = ui_view.project(root, view, final_step=9)
    chronological = list(reversed(value['trail']['rounds']))
    assert [item['outcome'] for item in chronological] == ['修订方向', '修订方向', '交付']
    assert [item['experiments'] for item in chronological] == [5, 3, 13]
    usage = ui_view.usage_stats(view, used_steps=value['used_steps'], candidates=value['candidates'])
    assert (usage['tokens']['value'], usage['tokens']['limit'], usage['tokens']['percent']) == ('584.6 万', '500 万', 117)
    assert usage['tokens']['over'] and usage['tokens']['notes'][0].startswith('最终 A 受保护')
    assert usage['time']['value'].startswith('43 分') and usage['time']['limit'] == '3 小时'
    projection = ui_view.candidate_projection(root, 9)
    rows = ui_view.candidate_rows(projection, recommended=value['confirmed'], delivered=value['confirmed'])
    assert len(projection['index']) == 32
    assert rows['total'] == len(set(projection['nominated_ids']) | {value['confirmed']})
    reader = ui_view.Reader(root)
    card = ui_view.result_card(reader, 9, closeout=view['closeout'], projection=projection)
    assert card['chosen']['label'] == 'convex_strong' and card['delivery_label'] == '已确认交付'
    assert card['criteria_change']['status'] == 'revised' and len(card['unmet']) == 3
    member = ui_view.candidate_file(reader, card['chosen'], projection)
    assert member['reference'] == view['closeout']['last_confirmed']['artifact_ref']
    assert before == {path: path.stat().st_mtime_ns for path in root.rglob('*') if path.is_file()}


def test_delivery_state_uses_control_records_and_open_submissions_outrank_old_confirmations():
    confirmed = dict(last_confirmed=dict(candidate_id='local-a'), current_actual=dict(candidate_id='local-a'), status='delivery_confirmed')
    assert ui_view._delivery_state(confirmed, 'local-a', 'ref') == ('confirmed', '已确认交付')
    assert ui_view._delivery_state(confirmed, 'local-b', 'ref') == ('recommended', '已推荐未交付')
    assert ui_view._delivery_state(confirmed, None, None) == ('none', '未推荐')
    pending = dict(confirmed, current_actual='unknown', status='delivery_unknown')
    assert ui_view._delivery_state(pending, 'local-a', 'ref') == ('unknown', '交付状态未知')
    assert ui_view._delivery_state(dict(status='delivery_confirmation_pending'), 'local-a', 'ref')[0] == 'unknown'
    assert ui_view._delivery_state(None, 'local-a', 'ref', unresolved_reference=True)[0] == 'unknown'


def test_reader_displays_saved_content_without_rechecking_seal_hashes(tmp_path):
    root, _ = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='experiment', summary='Plan')
    memo = {}
    first = ui_view.Reader(root, memo).node(1)
    assert ui_view.Reader(root, memo).node(1) == first
    original = root / 'native-evidence/001/science-output.json'
    original.chmod(0o600); original.write_bytes(original.read_bytes() + b' ')
    assert ui_view.Reader(root, memo).node(1) == first


@pytest.mark.parametrize('dependency', ['result.json', 'stage-end.json', 'settings.json'])
def test_saved_reader_does_not_replay_canonical_seal_dependencies(tmp_path, dependency):
    root, _ = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='experiment')
    memo = {}
    first = ui_view.Reader(root, memo).node(1)
    assert ui_view.Reader(root, memo).node(1) == first
    path = root / ('settings.json' if dependency == 'settings.json' else 'native-evidence/001/' + dependency)
    if path.exists():
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path.write_text('{}\n')
    assert ui_view.Reader(root, memo).node(1) == first


def test_evaluation_identity_survives_cleaning_truncation_and_missing_values():
    clean = lambda value, limit=300: '[private]' if value.startswith('/secret/') else value[:limit]
    keys = ['/secret/alpha', '/secret/beta', 'x' * 310 + 'a', 'x' * 310 + 'b', None, '未声明']
    original = [dict(id=str(index), evaluation_key=key, metric=index, direction='max', observation_step=2)
                for index, key in enumerate(keys)]
    value = ui_view.candidate_rows(dict(index=original, nominated_ids=[row['id'] for row in original], graph={}, references={}), clean=clean)
    assert len(value['groups']) == 6
    assert [group['key'] for group in value['groups'][:2]] == ['[private]', '[private]']
    assert all(group['ambiguous_label'] for group in value['groups'])
    assert '/secret/' not in json.dumps(value)
    rows = [ui_view.clean_row(row, clean) for row in original[:2]]
    extreme = ui_view._candidate_extreme(rows)
    assert extreme['text'] == '多种口径，见候选表' and 'value' not in extreme
    missing = ui_view._candidate_extreme([dict(evaluation_key=None, metric=1, direction='max'),
                                          dict(evaluation_key=None, metric=2, direction='max')])
    assert missing['text'] == '口径未声明，无法比较' and 'value' not in missing
    unknown = ui_view.candidate_rows(dict(index=[dict(id=str(number), metric=number, evaluation_key=None)
        for number in (1, 2)], nominated_ids=['1', '2'], graph={}, references={}))
    assert len(unknown['groups']) == 2 and all(len(group['rows']) == 1 for group in unknown['groups'])


def test_protected_final_with_zero_excess_is_not_an_overrun():
    view = dict(budget=dict(max_tokens=1000), usage=dict(observed_tokens=500),
                closeout=dict(final_overrun=dict(shared_tokens_over_ceiling=0, shared_seconds_over_ceiling=0)))
    tokens = ui_view.usage_stats(view)['tokens']
    assert tokens['over'] is False and tokens['notes'] == ['最终 A 受保护']
    view['closeout']['final_overrun']['shared_tokens_over_ceiling'] = 25
    assert ui_view.usage_stats(view)['tokens']['over'] is True


def test_audit_request_is_a_target_without_recommendation_or_delivery(tmp_path, monkeypatch):
    reader = FakeReader({1: sealed(1, 'A', dict(summary='Audit this evidence', next_action='audit',
        candidate_recommendation='candidate-p'))})
    node = dict(step=1, role='A', state='seal_present_unverified')
    line = ui_view.role_line(reader, node, lambda value, limit=300: value, labels={'candidate-p': 'P'})
    assert line['audit_target'] == 'candidate-p' and 'recommendation' not in line
    assert dict(label='审计对象', value='P') in line['facts']
    assert not any('★' in row['value'] for row in line['facts'])
    card = ui_view.result_card(reader, 1, closeout=dict(last_confirmed=dict(candidate_id='candidate-p')),
                              clean=lambda value, limit=300: value)
    assert card['chosen'] is None and card['audit_target']['id'] == 'candidate-p'
    assert card['recommendation_raw'] is None and card['delivery_state'] == 'audit'
    monkeypatch.setattr(ui_view, 'Reader', lambda root, memo=None: reader)
    value = ui_view.project(reader.root, dict(nodes=[node], closeout={}))
    assert value['recommended'] is None and value['candidates']['recommended'] is False
    assert 'A 推荐 1' not in ui_view.usage_stats({}, candidates=value['candidates'])['candidates']['notes']


def test_audit_preserves_prior_scientific_recommendation_in_overview(tmp_path, monkeypatch):
    reader = FakeReader({
        1: sealed(1, 'A', dict(summary='Recommend P', next_action='continue_w', candidate_recommendation='P')),
        2: sealed(2, 'A', dict(summary='Audit Q', next_action='audit', candidate_recommendation='Q')),
    })
    monkeypatch.setattr(ui_view, 'Reader', lambda root, memo=None: reader)
    nodes = [dict(step=step, role='A', state='seal_present_unverified') for step in (1, 2)]
    value = ui_view.project(reader.root, dict(nodes=nodes))
    assert value['recommended'] == 'P' and value['latest_a']['step'] == 1
    assert value['candidates']['recommended'] is True
    assert value['trail']['rounds'][0]['a'][1]['audit_target'] == 'Q'
    rows = ui_view.candidate_rows(dict(index=[dict(id=identity, evaluation_key='val') for identity in ('P', 'Q')],
                                      nominated_ids=['P', 'Q'], graph={}, references={}), recommended=value['recommended'])
    assert [row['marks'] for row in rows['groups'][0]['rows']] == [['recommended'], []]


def test_implementation_reference_uses_exact_candidate_binding_and_file(tmp_path, monkeypatch):
    from scidiscovery.research_loop import controller, history
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Compare exact retained objects.')
    observer = tmp_path / 'observer.py'
    observer.write_text('''import json,sys
r=json.load(sys.stdin)
rows=[dict(id=label,label=label,implementation='history/nodes/002/artifacts/'+label+'/model.py',evaluation_key='val',metric=metric,direction='max',status='ok',evidence=['project/nodes/002/artifacts/'+label+'/model.py']) for label,metric in [('P',.8),('Q',.6)]] if r['step']==2 else []
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Fixture only'))))
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-invoked'], max_tokens=100000, max_seconds=300,
        max_steps=8, model='fixture', reasoning_effort='high', search_policy=None,
        history_observer=dict(adapter=str(observer), config={}))
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], files={'P/model.py': 'same bytes\n', 'Q/model.py': 'same bytes\n'})
    finish(root, 3, 'A', [2], action='wait', recommendation='W002-I001')
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    projection = ui_view.candidate_projection(root, 3)
    reader = ui_view.Reader(root)
    card = ui_view.result_card(reader, 3, projection=projection)
    if card['chosen'] is None:
        diagnostic = dict(resolution_error=card['resolution_error'],
            index=projection['index'], references=projection['references'], subject_history=projection['subject'].get('history'),
            saved_recommendation=(reader.node(3).get('result') or {}).get('candidate_recommendation'),
            w_observation=reader.observation(2),
            w_materials=[row['path'] for row in reader.node(2)['seal']['materials']],
            candidate_materials=history.candidate_materials(root, projection['subject']))
        log_dir = os.environ.get('SCID_TEST_LOG_DIR')
        if log_dir:
            destination = Path(log_dir) / 'ui-candidate-binding.json'
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise AssertionError(json.dumps(diagnostic, ensure_ascii=False, indent=2))
    assert card['chosen']['id'] == 'P' and card['chosen']['indexed']
    assert card['chosen']['label'] == 'P' and card['chosen']['metric'] == '0.8'
    assert projection['nominated_ids'] == []
    rows = ui_view.candidate_rows(projection, recommended=card['chosen']['id'])['groups'][0]['rows']
    assert [row['id'] for row in rows] == ['P'] and rows[0]['marks'] == ['recommended']
    member = ui_view.candidate_file(reader, card['chosen'], projection)
    assert member['name'] == 'P/model.py'
    assert reader.node(2)['seal']['materials'][member['index'] - 1]['path'].endswith('/P/model.py')
    assert reader.candidate(reader.node(3)['job'],'W002-I002',strict=True) == 'Q'
    with pytest.raises(ValueError, match='no unique frozen candidate binding'):
        reader.candidate(reader.node(3)['job'],'W002-I099',strict=True)
    earlier=dict(reader.node(3)['job'],history=reader.node(2)['job']['history'])
    with pytest.raises(ValueError):
        reader.candidate(earlier,'W002-I002',strict=True)  # Existing future W cannot enlarge this original cut.
    assert before == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    # Re-observation can repeat an identical binding; equal bytes at P/Q still
    # remain different originals because their material identities differ.
    import copy
    facts=copy.deepcopy(reader.candidate_facts(reader.node(3)['job'],'W002-I002'))
    facts['implementation']['observation']['candidates']*=2
    assert ui_view.candidate_file(reader, card['chosen'], projection)['name'] == 'P/model.py'
    assert ui_view.resolve_candidate(root,reader.node(3)['job'],'W002-I002',strict=True,facts=facts)=='Q'


@pytest.mark.process_e2e
def test_audit_facts_display_saved_receipts_and_candidate_artifact_scope(tmp_path):
    from scidiscovery.research_loop import candidate_audit, contracts as c
    from test_candidate_audit import audit_fixture, request
    root = audit_fixture(tmp_path)
    job, payload = request(root)
    assert candidate_audit.execute(root, job, payload) is None
    following = c.build_job(root, 4, 'A', [1, 2, 3])
    projection = ui_view.candidate_projection(root, 4, job=following)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    facts = ui_view.audit_facts(root, projection)
    assert facts['run-1'] == '公开审计已完成；候选文件归属未知' and facts['__recorded__']
    assert before == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    # Adapter completion does not qualify a different or unbound artifact.
    unknown = dict(projection, records=[])
    assert ui_view.audit_facts(root, unknown)['run-1'] == '公开审计已完成；候选文件归属未知'
    artifact = c.read(candidate_audit._directory(root, 3) / 'binding.json')['source']['objects']['artifact']
    matched = dict(projection, records=[dict(observation=dict(contexts=[dict(status='known_success',
        candidate_id='run-1', model=dict(artifact, retention='archived'))]))])
    assert ui_view.audit_facts(root, matched)['run-1'] == '公开审计通过'
    path = candidate_audit._directory(root, 3) / 'adapter-result.json'
    value = c.read(path); value['status'] = 'rejected'; c.write(path, value)
    assert ui_view.audit_facts(root, projection) == facts


def test_sealed_reader_memo_avoids_repeat_reads_and_invalidates_replaced_original(tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_display as display
    root, _ = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze', summary='Original finding')
    memo, reads = {}, []
    original = display.node
    def counted(*args, **kwargs):
        reads.append(args[1]); return original(*args, **kwargs)
    monkeypatch.setattr(display, 'node', counted)
    first = ui_view.Reader(root, memo).node(1)
    first['payload']['summary'] = 'Mutated display copy'
    assert ui_view.Reader(root, memo).node(1)['payload']['summary'] == 'Original finding'
    assert reads == [1]
    path = root / 'native-evidence/001/science-output.json'
    value = json.loads(path.read_bytes()); value['summary'] = 'Replaced original display'
    path.chmod(0o600); path.write_text(json.dumps(value))
    assert ui_view.Reader(root, memo).node(1)['payload']['summary'] == 'Replaced original display'
    assert reads == [1, 1]
    path.write_text('{')
    with pytest.raises(ValueError):
        ui_view.Reader(root, memo).node(1)
    path.write_text(json.dumps(value))
    assert ui_view.Reader(root, memo).node(1)['payload']['summary'] == 'Replaced original display'
    assert len(memo) <= 256


@pytest.mark.parametrize('saved, state', [('waiting', 'external_wait'), ('native_outcome_unsealed', 'unsealed'),
    ('native_not_submitted', 'not_submitted')])
def test_saved_native_stops_have_distinct_non_idle_display_states(saved, state):
    current = dict(step=2, role='W', state=saved)
    presentation = dict(current=current, state=saved, completed=False, waiting=False, active=False)
    summary = ui_view.status_summary(presentation, dict(paused=False))
    assert summary['state'] == state and summary['state'] not in {'idle', 'ready', 'active'}
