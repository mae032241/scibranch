"""Workbench components escape author text and render empty/error states inline."""
from scidiscovery.research_loop import ui_components as ui, ui_view
from ui_html import element_texts


XSS = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def test_every_component_escapes_author_text_and_only_safe_markup_passes():
    assert ui.esc(XSS) == '&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=alert(2)&gt;'
    assert ui.esc(ui.Safe('<b>host</b>')) == '<b>host</b>' and ui.esc(None) == ''
    assert ui.link(XSS, '/x?a="b"') == '<a href="/x?a=&quot;b&quot;">' + ui.esc(XSS) + '</a>'
    line = dict(step=2, role='W', role_label='W · 实验', state='seal_present_unverified', state_label='已有阶段结论',
                sealed=True, one_line=XSS, facts=[dict(label='候选', value=XSS, title=XSS)],
                links=[dict(label=XSS, href='/results/2')], candidate_rows=[dict(id='local-1', label=XSS, metric=1.5,
                evaluation_key=XSS, status='ok')], mechanisms=[dict(label=XSS, minimal_test=XSS)])
    status = dict(state='completed', badge='已完成', headline=XSS, stage=XSS)
    rows = ui_view.candidate_rows(dict(index=[dict(id='local-1', label=XSS, metric=0.5, evaluation_key=XSS,
                                                   direction=XSS, observation_step=2, status='ok')],
                                       nominated_ids=['local-1'], graph={}, references={'local-1': XSS}))
    card = dict(step=3, kind='closeout', decision=XSS, delivery_state='recommended', delivery_label='已推荐未交付',
                chosen=dict(label=XSS, metric='0.5', evaluation_key=XSS, direction=XSS, step=2, reference=XSS),
                summary=XSS, reason=XSS, criteria_change=dict(status='revised', label='准则已修订',
                details=[dict(label='原准则', text=XSS)]), goal_completion=XSS, unmet=[XSS], unmet_omitted=0)
    rendered = [ui.role_line(line, expanded=True), ui.hero({}, status, attention=[dict(text=XSS, href='/x', action=XSS)], notices=[XSS]),
                ui.candidate_table(rows), ui.result_card(card),
                ui.understanding([XSS]), ui.activity(dict(step=1, role_label=XSS, elapsed=XSS, label=XSS, messages=[XSS], href='/p')),
                ui.instance_card(dict(heading=XSS, path=XSS, retry='/instances', error=dict(kind=XSS, message=XSS))),
                ui.trail(dict(rounds=[dict(index=1, summary=XSS, steps=[2], h=None, w=[line], a=[], others=[])], omitted=0))]
    for markup in rendered:
        assert '<script>' not in markup and '<img' not in markup and 'onerror=alert' not in markup.replace('onerror=alert(2)&gt;', '')
        assert '&lt;script&gt;' in markup


def test_empty_and_error_states_render_inline_without_failing_the_page():
    failure = ui_view.failure(ValueError('Ledger malformed'))
    assert ui.stats(failure).startswith('<div class="stats"><p class="inline-error"')
    assert 'ValueError' in ui.stats(failure) and 'Ledger malformed' in ui.trail(failure)
    assert '还没有开始研究' in ui.trail(dict(rounds=[], omitted=0))
    assert '未记录提名或 A 选中候选' in ui.candidate_table(dict(total=0, groups=[], limit=20))
    assert ui.activity(None) == '' and 'ValueError' in ui.activity(failure)
    assert 'ValueError' in ui.result_card(failure) and 'ValueError' in ui.candidate_table(failure)
    card = ui.instance_card(dict(heading='实例 1 · x', path='/p', retry='/instances', error=dict(kind='OSError', message='busy')))
    assert '无法读取此实例：OSError：busy' in card and 'href="/instances"' in card and '重试' in card
    assert '状态暂不可用' not in card


def test_stat_cards_mark_overrun_red_and_percent_bar_is_bounded():
    usage = ui_view.usage_stats(dict(budget=dict(max_tokens=5000000), usage=dict(observed_tokens=5846087)))
    html = ui.stat(usage['tokens'])
    assert 'class="stat over"' in html and '<strong>584.6 万</strong>' in html and ' / 500 万</span>' in html
    assert 'value="100"' in html and 'meter over' in html and '117%' in html and '已观测用量超出上限 84.6 万' in html
    assert 'class="stat"' in ui.stat(ui_view.usage_stats({})['time'])


def test_candidate_table_is_grouped_neutral_and_keeps_every_row_reachable():
    index = [dict(id=f'local-{number:02d}', label=f'c{number}', evaluation_key='test' if number in (2, 24) else 'val',
                  metric=None if number == 3 else number / 10, status='failed' if number == 4 else 'ok', observation_step=2)
             for number in range(1, 26)]
    rows = ui_view.candidate_rows(dict(index=index, nominated_ids=[row['id'] for row in index], graph={}, references={}), recommended='local-07', delivered='local-07')
    html = ui.candidate_table(rows)
    assert html.count('<tr data-order=') + html.count('<tr class="extra" data-order=') == 25
    assert html.count('class="extra"') == 3 and '显示全部 25 条（另 3 条）' in html
    assert html.index('口径：val') < html.index('口径：test')
    assert html.count('仅按此口径数值排序，不代表科学排序。') == 2 and html.count('data-sort-note hidden') == 2
    assert 'mark-recommended' in html and 'mark-delivered' in html and 'mark-failed' in html
    assert '最佳' not in html and 'best' not in html.lower()
    order = [int(value) for value in __import__('re').findall(r'data-order="(\d+)"', html)]
    val, test = order[:23], order[23:]
    assert val == sorted(val) and test == sorted(test)


def test_live_disclosure_and_focus_keys_follow_exact_component_identity():
    line = dict(step=2, role='W', role_label='W · 实验', sealed=True,
                links=[dict(label='阶段结论', href='/results/2')])
    item = dict(index=1, summary='Round', steps=[1, 2], h=None, w=[line], a=[], others=[])
    before = ui.timeline_round(item)
    after = ui.timeline_round(dict(item, summary='Changed', steps=[1, 2, 3]))
    for markup in (before, after):
        assert 'data-details-key="round:1-1"' in markup
        assert 'data-live-focus="round:1-1"' in markup
        assert 'data-live-focus="stage:2:/results/2"' in markup
    row = dict(heading='Instance', path='/exact/instance', retry='/instances', error=dict(kind='OSError', message='busy'))
    failed = ui.instance_card(row)
    loaded = ui.instance_card(dict(row, error=None, title='Task', state='active', badge='进行中', headline='Working',
        tokens=dict(text='10 / 100', percent=10), href='/instances/1/'))
    for markup in (failed, loaded):
        assert 'data-details-key="instance-path:/exact/instance"' in markup
        assert 'data-live-focus="instance-path:/exact/instance"' in markup
    assert 'instance-path:/different/instance' in ui.instance_card(dict(row, path='/different/instance'))


def test_audit_result_identifies_the_target_without_claiming_a_recommendation():
    card = dict(step=3, kind='ordinary', decision='audit', delivery_state='audit', delivery_label='审计请求',
                chosen=None, audit_target=dict(label='Audit candidate', metric='0.5', evaluation_key='val', step=2),
                summary='Audit finding', reason='Check the retained measurement', criteria_change=None,
                goal_completion='未声明', unmet=[], unmet_omitted=0)
    markup = ui.result_card(card)
    assert element_texts(markup, 'h2', cls='chosen') == ['审计对象：Audit candidate']
    assert '审计理由' in element_texts(markup, 'h3')
    assert '推荐' not in markup and 'href="/results/2"' in markup
    missing = ui.result_card(dict(card, audit_target=None))
    assert '审计对象未能对应已观察候选' in missing and '推荐' not in missing


def test_final_coverage_is_collapsed_authored_escaped_and_links_scientific_originals():
    card = dict(step=7, kind='closeout', decision='finish', delivery_state='none', delivery_label='未推荐',
        chosen=None, summary='Final', reason='Evidence', criteria_change=None, goal_completion='未声明',
        unmet=['Missing input'], unmet_omitted=0, final_coverage=[dict(label=XSS, authored_label=True,
            status='screened_pending', reason=XSS, science_refs=[dict(source_step=2, pointer='/scientific_result')],
            unanswered_questions=[XSS]), dict(label='Untested', status='unexplored', reason='No input',
            science_refs=[dict(source_step=1, pointer='/search_plan')], unanswered_questions=[])])
    markup = ui.result_card(card)
    coverage = markup.split('<details class="final-coverage"', 1)[1]
    assert ' open' not in coverage.split('>', 1)[0]
    assert 'data-details-key="final-coverage:7"' in markup and 'data-live-focus="final-coverage:7"' in markup
    assert '已筛查待再议' in markup and '从未探索' in markup and 'A 声明无未回答问题' in markup
    assert '/nodes/2/science' in markup and '/nodes/1/science' in markup and '/scientific_result' in markup
    assert '<script>' not in markup and '<img' not in markup and '&lt;script&gt;' in markup
    assert 'data-translation-source=' in coverage


def test_breadth_metrics_preserve_overlapping_participation_and_token_only_advice():
    from test_ui_view import saved_breadth

    value = ui_view.breadth_metrics(saved_breadth(), lambda text, limit=16384: str(text)[:limit])
    markup = ui.breadth_metrics(value)
    assert ' open' not in markup.split('>', 1)[0]
    assert 'data-details-key="breadth-metrics"' in markup and 'data-live-focus="breadth-metrics"' in markup
    assert '100%' in markup and '50%' in markup and '合计可超过 100%' in markup
    assert '待 A 评估的 W' in markup and '/nodes/6/science' in markup and '未计入已完成周期' in markup
    assert '2 次独立组合判断' in markup and '仍有效的判别工作（1）' in markup
    assert 'The input arrives.' in markup and 'Before closing.' in markup
    assert '按已报告 token 的参考轮数：3.5' in markup and '不表示全部预算还能运行相应轮数' in markup
    assert '最低样本数：2' in markup and '/nodes/3/science' in markup


def test_breadth_metrics_missing_or_stale_snapshots_do_not_claim_current_global_estimate():
    from test_ui_view import saved_breadth

    clean = lambda text, limit=16384: str(text)[:limit]
    assert '参考轮数不可估' in ui.breadth_metrics(ui_view.breadth_metrics({}, clean))
    value = ui_view.breadth_metrics(dict(saved_breadth(partial=True), fork={"cut": 3}), clean)
    markup = ui.breadth_metrics(value)
    assert '不能代表当前全局统计' in markup and '参考轮数：不可估' in markup
    assert '当前余额与消费仅计本分支' in markup and '参考轮数：3.5' not in markup
    value['participation'][0]['label'] = XSS
    value['work'][0]['question'] = XSS
    markup = ui.breadth_metrics(value)
    assert '<script>' not in markup and '<img' not in markup and '&lt;script&gt;' in markup
