"""Escaped workbench components. Every text argument is escaped here.

Only host-authored markup wrapped in ``Safe`` (control forms built by the UI
host, or other component output) passes through unchanged. Callers never
concatenate unescaped author content.
"""
from __future__ import annotations

import html
import json
import math


class Safe(str):
    """Host-authored markup that a component may embed verbatim."""


def esc(value):
    if isinstance(value, Safe):
        return value
    return html.escape('' if value is None else str(value), quote=True)


def translatable(value):
    """Explicit natural-language slot; original text is escaped and retained."""
    text = '' if value is None else str(value)
    if not text.strip() or len(text) > 12000:
        return esc(text)
    return Safe('<span data-translation-source="' + html.escape(json.dumps(text, ensure_ascii=False), quote=True) + '">'
                + html.escape(text, quote=True) + '</span>')


def join(*parts):
    return Safe(''.join(esc(part) for part in parts if part is not None))


def attr(name, value):
    return '' if value in (None, '') else ' ' + name + '="' + esc(value) + '"'


def link(label, href, *, cls=None, title=None, focus_key=None):
    return Safe('<a href="' + esc(href) + '"' + attr('class', cls) + attr('title', title)
                + attr('data-live-focus', focus_key) + '>' + esc(label) + '</a>')


def badge(text, tone='neutral'):
    return Safe('<span class="badge tone-' + esc(tone) + '">' + esc(text) + '</span>')


def card(title, body, *, cls=None, ident=None, level=2):
    heading = '<h' + str(level) + '>' + esc(title) + '</h' + str(level) + '>' if title else ''
    return Safe('<section' + attr('class', cls) + attr('id', ident) + '>' + heading + esc(body) + '</section>')


def empty(text):
    return Safe('<p class="empty-note">' + esc(text) + '</p>')


def error_inline(error, *, what='此部分'):
    error = (error or {}).get('error', error) or {}
    return Safe('<p class="inline-error" role="status">' + esc(what) + '暂不可读：' + esc(error.get('kind', 'Error'))
                + '：' + esc(error.get('message', '')) + '</p>')


def progress(percent, *, over=False, label=None):
    if percent is None:
        return Safe('')
    # A native <progress>; inline style attributes are not allowed by the CSP.
    width = max(0, min(100, percent))
    return Safe('<progress class="meter' + (' over' if over else '') + '" max="100" value="' + str(width) + '"'
                + ' aria-label="' + esc(label or ('已用 ' + str(percent) + '%')) + '">' + str(percent) + '%</progress>')


def stat(item):
    """One metric card: value / limit, percent bar, notes. Red when over."""
    if 'error' in item:
        return Safe('<section class="stat">' + error_inline(item) + '</section>')
    over = bool(item.get('over'))
    value = '<p class="stat-value"><strong>' + esc(item['value']) + '</strong>'
    if item.get('limit') is not None:
        value += '<span class="stat-limit"> / ' + esc(item['limit']) + (' ' + esc(item['unit']) if item.get('unit') else '') + '</span>'
    elif item.get('unit'):
        value += ' ' + esc(item['unit'])
    value += '</p>'
    pct = item.get('percent')
    bar = ''
    if pct is not None:
        bar = progress(pct, over=over) + '<p class="stat-percent' + (' over' if over else '') + '">' + esc(str(pct) + '%') + '</p>'
    notes = ''.join('<p class="stat-note' + (' over' if over else '') + '">' + esc(note) + '</p>' for note in item.get('notes') or [])
    return Safe('<section class="stat' + (' over' if over else '') + '"' + attr('title', item.get('title'))
                + '><h2>' + (link(item['label'], item['href']) if item.get('href') else esc(item['label'])) + '</h2>' + value + bar + notes + '</section>')


def stats(usage):
    if 'error' in usage:
        return Safe('<div class="stats">' + error_inline(usage, what='用量与预算') + '</div>')
    return Safe('<div class="stats">' + ''.join(stat(usage[key]) for key in ('tokens', 'time', 'steps', 'candidates')) + '</div>')


def breadth_metrics(value):
    """Compact advice from the saved global projection, never the trail window."""
    key = 'breadth-metrics'
    summary = '探索投入提示'
    if value.get('available') and value.get('completed_cycle_count') is not None:
        summary += ' · 已评估 ' + str(value['completed_cycle_count']) + ' 周期'
        if value.get('pending_assessment'):
            summary += ' · 待 A 评估 1 项'
        if value.get('partial'):
            summary += '（保存范围）'
    body = ('<details class="breadth-metrics" data-details-key="' + key + '"><summary data-live-focus="' + key
            + '">' + esc(summary) + '</summary>')
    if 'error' in value:
        return Safe(body + error_inline(value, what='探索投入提示') + '</details>')
    if not value.get('available'):
        return Safe(body + empty('完整统计快照暂不可用；已完成独立 W→A 周期、参与比例及参考轮数不可估。') + '</details>')
    asof = value.get('source_step')
    body += '<p class="note">保存的科学历史统计' + ('截至第 ' + esc(asof) + ' 阶段' if asof else '，来源阶段未记录') + '；不以当前轨迹窗口计数。</p>'
    if value.get('partial'):
        body += '<p class="note">快照缺失或滞后，以下仅为已保存范围，不能代表当前全局统计；参考轮数不可估。</p>'
    if value.get('inherited'):
        body += '<p class="note">科学历史含准确 cut 内的继承记录；当前余额与消费仅计本分支。</p>'
    count = value.get('completed_cycle_count')
    body += '<p>已完成独立 W→A 评估周期：<strong>' + esc(count if count is not None else '不可核实') + '</strong>。</p>'
    if value.get('pending_assessment'):
        step = value.get('pending_step')
        body += '<p>待 A 评估的 W：' + (link('第 ' + str(step) + ' 阶段', '/nodes/' + str(step) + '/science') if step else '来源未记录')
        if value.get('pending_labels'):
            body += ' · ' + '、'.join(translatable(item) for item in value['pending_labels'])
        body += '；未计入已完成周期。</p>'
    else:
        body += '<p class="note">保存范围内没有待 A 评估的 W。</p>'
    if value.get('participation'):
        body += '<div class="table-scroll"><table class="compact"><thead><tr><th>机制</th><th>参与周期</th><th>参与比例</th></tr></thead><tbody>'
        for row in value['participation']:
            body += '<tr><td>' + translatable(row['label']) + '</td><td>' + esc(row['participated_cycles'] if row['participated_cycles'] is not None else '未知') + '</td><td>' + esc(row['fraction_text']) + '</td></tr>'
        body += '</tbody></table></div>'
    body += '<p class="note">每机制参与次数除以同一独立周期分母；同周期可涉及多个机制，比例重叠、合计可超过 100%。这不是资源份额或科学深度。</p>'
    if value.get('main_line') is None:
        body += '<p class="note">A 当前主线未声明。</p>'
    else:
        body += '<p>A 连续选择主线：' + translatable(value['main_line']) + ' · ' + esc(value.get('mainline_streak') if value.get('mainline_streak') is not None else '未知') + ' 次独立组合判断'
        if value.get('mainline_step'):
            body += '（' + link('A 原文', '/nodes/' + str(value['mainline_step']) + '/science') + '）'
        body += '；与 W→A 周期分别计数。</p>'
    body += '<h3>仍有效的判别工作（' + esc(value.get('active_work_count', 0)) + '）</h3>'
    for row in value.get('work') or []:
        body += '<article><p><strong>' + translatable(row['question']) + '</strong></p><p>改变判断的条件：' + translatable(row['change_criterion']) + '</p>'
        action = row.get('action')
        if action:
            body += '<p class="note">最新 A 处置：' + esc({'execute': '选择执行', 'defer': '继续暂缓', 'withdraw': '撤回'}.get(action, action)) + '。执行响应本身不表示科学问题已解决。</p>'
        for name, title in (('reason', '当前处置理由'), ('trigger', '触发条件'), ('revisit', '再议时点')):
            if row.get(name):
                body += '<p>' + title + '：' + translatable(row[name]) + '</p>'
        sources = sorted({step for step in (row.get('source_step'), row.get('update_step')) if step})
        body += links([dict(label='第 ' + str(step) + ' 阶段原文', href='/nodes/' + str(step) + '/science') for step in sources]) + '</article>'
    if not value.get('active_work_count'):
        body += empty('保存范围内没有仍有效的已声明判别工作。')
    if value.get('work_omitted'):
        body += '<p class="note">另有 ' + esc(value['work_omitted']) + ' 项未展开；' + link('按阶段查看完整原文', '/results?after=0') + '。</p>'
    advice = value.get('token_advice') or {}
    body += '<h3>按已报告 token 的参考轮数：' + (esc(advice['reference_cycles']) if advice.get('available') else '不可估') + '</h3>'
    body += '<p class="note">仅供 token 口径参考，不表示全部预算还能运行相应轮数；时间、控制动作、FINAL 和执行权限仍分别约束。</p>'
    body += '<p class="note">完整本分支周期样本：' + esc(advice.get('complete_sample_count') if advice.get('complete_sample_count') is not None else '未知')
    body += '；最低样本数：' + esc(advice.get('minimum_samples') if advice.get('minimum_samples') is not None else '未记录')
    body += '；排除样本：' + esc(advice.get('excluded_sample_count') if advice.get('excluded_sample_count') is not None else '未知') + '。</p>'
    if advice.get('scope'):
        body += '<p class="note">' + translatable(advice['scope']) + '</p>'
    if advice.get('reasons'):
        body += '<p class="note">不可估原因：' + '；'.join(translatable(item) for item in advice['reasons']) + '</p>'
    for row in advice.get('samples') or []:
        sources = ([row['w_step']] if row.get('w_step') else []) + row.get('a_steps', [])
        body += links([dict(label='样本第 ' + str(step) + ' 阶段', href='/nodes/' + str(step) + '/science') for step in sources])
    return Safe(body + '</details>')


def token_monitor(rows=None, *, scope='当前阶段 · 本次助手用量', live=False):
    """A separate monitoring card; the live client preserves the same dt/dd pairs."""
    body = '<section class="token-monitor"' + (' data-token-monitor' if live else '') + '><header><h2>Token 监控</h2><p class="token-scope" data-token-scope>' + esc(scope) + '</p></header>'
    body += '<div data-token-details><dl class="token-metrics">'
    for row in rows or []:
        label = row['label']
        cls = 'token-note' if label in {'计量状态', '观测限制'} else ('token-feature' if label == '缓存命中率' or label.startswith('最近一次上报上下文') else 'token-count')
        main, separator, report = str(row['value']).partition('；') if cls != 'token-note' else (str(row['value']), '', '')
        body += '<div class="' + cls + '"><dt>' + esc(label) + '</dt><dd' + (' class="token-unavailable"' if main.startswith('不可用') else '') + '>' + esc(main)
        if separator:
            body += '<span class="token-report">' + esc(report) + '</span>'
        body += '</dd></div>'
    return Safe(body + '</dl></div></section>')


def facts(rows):
    if not rows:
        return Safe('')
    # The tooltip sits on <dd>: a display:contents row cannot be hovered.
    return Safe('<dl class="facts">' + ''.join('<div><dt>' + esc(row['label'])
        + '</dt><dd' + attr('title', row.get('title')) + '>' + esc(row['value'])
        + ('<span class="hint" aria-hidden="true"> ⓘ</span>' if row.get('title') else '')
        + '</dd></div>' for row in rows) + '</dl>')


def links(rows, *, focus_prefix=None):
    return Safe('<p class="links">' + ' · '.join(link(row['label'], row['href'],
        focus_key=focus_prefix + row['href'] if focus_prefix else None) for row in rows) + '</p>')


def role_line(line, *, expanded=False):
    if not line:
        return Safe('')
    head = ('<p class="role-head"><span class="role role-' + esc(line['role']) + '">' + esc(line['role_label'])
            + '</span> <span class="step">第 ' + esc(line['step']) + ' 阶段</span>'
            + ('' if line.get('sealed') else ' · <span class="state">' + esc(line['state_label']) + '</span>') + '</p>')
    body = head
    if line.get('error'):
        body += error_inline(line, what='本阶段摘要')
    if line.get('one_line'):
        body += '<p class="one-line">' + translatable(line['one_line']) + '</p>'
    body += facts(line.get('facts'))
    if expanded and line.get('mechanisms'):
        body += '<h3>机制承诺（' + esc(len(line['mechanisms'])) + '）</h3><ol class="mechanisms">' + ''.join(
            '<li><strong>' + translatable(item['label']) + '</strong>' + ('<p>' + translatable(item['minimal_test']) + '</p>' if item.get('minimal_test') else '')
            + '</li>' for item in line['mechanisms']) + '</ol>'
    if expanded and line.get('candidate_rows') is not None:
        rows = line['candidate_rows']
        body += '<h3>本阶段候选（' + esc(len(rows)) + '）</h3>'
        if rows:
            body += '<div class="table-scroll"><table class="compact"><thead><tr><th scope="col">标签</th><th scope="col">指标</th><th scope="col">口径</th><th scope="col">状态</th></tr></thead><tbody>'
            from .ui_view import fmt_metric
            for row in rows:
                body += ('<tr><td>' + (translatable(row['label']) if row.get('label') else esc(str(row.get('id', ''))[-12:])) + '</td><td class="num">' + esc(fmt_metric(row.get('metric')))
                         + '</td><td>' + esc(row.get('evaluation_key') if row.get('evaluation_key') is not None else '未声明')
                         + '</td><td>' + esc({'ok': '成功', 'failed': '失败', 'unknown': '未知'}.get(row.get('status'), row.get('status') or '未知')) + '</td></tr>')
            body += '</tbody></table></div><p class="note">按首次观察顺序列出；不代表科学排序。</p>'
        else:
            body += empty('本阶段封存观察中没有候选。')
    body += links(line.get('links') or [], focus_prefix='stage:' + str(line['step']) + ':')
    return Safe('<article class="role-line">' + body + '</article>')


def timeline_round(item, *, open_=False):
    title = item['summary']
    lines = [item['h'], *item['w'], *item['a'], *item['others']]
    lines = sorted((line for line in lines if line), key=lambda line: line['step'])
    body = ''.join(role_line(line) for line in lines)
    steps = item.get('steps') or []
    meta = ('阶段 ' + str(steps[0]) + ('–' + str(steps[-1]) if len(steps) > 1 else '')) if steps else ''
    key = str(item.get('index')) + '-' + str(steps[0] if steps else '')
    return Safe('<details class="round" data-round="' + esc(key) + '" data-details-key="round:' + esc(key) + '"'
                + (' open' if open_ else '') + '><summary data-live-focus="round:' + esc(key) + '"><span class="round-title">' + esc(title)
                + '</span> <span class="round-meta">' + esc(meta) + '</span></summary>' + body + '</details>')


def trail(value, *, older_href='/results', open_current=True):
    if 'error' in value:
        return Safe('<section class="trail"><h2>研究轨迹</h2>' + error_inline(value, what='研究轨迹') + '</section>')
    body = '<section class="trail"><h2>研究轨迹</h2>'
    if not value['rounds']:
        body += empty('还没有开始研究。开始后，这里会按轮次显示 H/W/A 的进展。')
    for index, item in enumerate(value['rounds']):
        body += timeline_round(item, open_=open_current and index == 0)
    if value.get('omitted'):
        body += '<p class="note">另有 ' + esc(value['omitted']) + ' 轮较早的研究未展开；' + link('按阶段查看全部', older_href) + '。</p>'
    return Safe(body + '</section>')


def understanding(items):
    return Safe('<details class="understanding"><summary>如何理解这些结果</summary><ul>'
                + ''.join('<li>' + esc(item) + '</li>' for item in items) + '</ul></details>')


def hero(header, status, *, primary=None, attention=(), notices=(), stage_note=None):
    """Header with status badge, headline and one primary action slot."""
    tone = {'completed': 'ok', 'active': 'active', 'waiting': 'warn', 'unknown': 'warn', 'budget_exhausted': 'bad',
            'final': 'warn', 'error': 'bad', 'service_error': 'bad', 'pause_requested': 'warn', 'paused': 'neutral'}.get(status.get('state'), 'neutral')
    body = '<section class="hero overview-head"><div class="head-main">'
    if header and 'error' in header:
        body += error_inline(header, what='研究标题')
    body += '<p class="head-status">' + badge(status['badge'], tone) + ' <span class="headline">' + esc(status['headline']) + '</span></p>'
    if status.get('stage'):
        body += '<p class="note stage-line">当前：' + esc(status['stage']) + '</p>'
    if stage_note:
        body += esc(stage_note)
    for notice in notices:
        body += '<p class="note">' + esc(notice) + '</p>'
    body += '</div><div class="head-action">' + (esc(primary) if primary else '') + '</div></section>'
    if attention:
        body += '<section class="attention" aria-labelledby="attention-title"><h2 id="attention-title">需要你处理</h2><ul>'
        for item in attention:
            body += '<li><p>' + esc(item['text']) + '</p>'
            if item.get('href'):
                body += link(item.get('action', '查看'), item['href'])
            if item.get('form'):
                body += esc(item['form'])
            body += '</li>'
        body += '</ul></section>'
    return Safe(body)


def activity(value):
    """Real-time progress of the active stage; public messages only."""
    if not value:
        return Safe('')
    if 'error' in value:
        return Safe('<section class="activity"><h2>实时进度</h2>' + error_inline(value, what='实时进度') + '</section>')
    body = '<section class="activity"><h2>实时进度</h2><p><strong>' + esc(value['role_label']) + '</strong> · 第 ' + esc(value['step']) + ' 阶段'
    if value.get('elapsed'):
        body += ' · 已用 ' + esc(value['elapsed'])
    body += '</p><p class="note">' + esc(value.get('label') or '') + '</p>'
    messages = value.get('messages') or []
    if messages:
        body += '<ul class="messages">' + ''.join('<li>' + esc(text) + '</li>' for text in messages) + '</ul>'
    else:
        body += empty('暂无公开消息。')
    body += link('查看完整公开进度', value['href'])
    return Safe(body + '</section>')


def instance_card(row):
    """One workspace instance card; failures show the exact reason and a retry."""
    path_key = 'instance-path:' + str(row['path'])
    path_details = ('<details class="review-full-path" data-details-key="' + esc(path_key)
                    + '"><summary data-live-focus="' + esc(path_key) + '">查看实例完整目录</summary><code>'
                    + esc(row['path']) + '</code></details>')
    if row.get('error'):
        return Safe('<article class="instance-card failed"><h3>' + esc(row['heading']) + '</h3><p class="inline-error">无法读取此实例：'
                    + esc(row['error']['kind']) + '：' + esc(row['error']['message']) + '</p>'
                    + path_details
                    + link('重试', row['retry'], cls='secondary-link') + '</article>')
    tone = {'completed': 'ok', 'active': 'active', 'waiting': 'warn', 'unknown': 'warn', 'budget_exhausted': 'bad',
            'error': 'bad', 'service_error': 'bad'}.get(row['state'], 'neutral')
    tokens = row['tokens']
    return Safe('<article class="instance-card"><h3>' + esc(row['title']) + '</h3><p class="note">' + esc(row['heading']) + '</p>'
                + '<p class="head-status">' + badge(row['badge'], tone) + ' <span class="headline">' + esc(row['headline']) + '</span></p>'
                + ('<p class="note">当前：' + esc(row['stage']) + '</p>' if row.get('stage') else '')
                + progress(tokens.get('percent'), over=tokens.get('over'))
                + '<p class="note' + (' over' if tokens.get('over') else '') + '">模型用量 ' + esc(tokens['text']) + '</p>'
                + path_details
                + link('打开', row['href'], cls='primary') + '</article>')


def _marks(row):
    labels = {'recommended': ('★', 'A 推荐（作者记录）'), 'delivered': ('✓', '已确认交付（控制记录）'), 'failed': ('✕', '执行失败')}
    return ''.join('<span class="mark mark-' + key + '" title="' + esc(labels[key][1]) + '">' + labels[key][0]
                   + '<span class="sr">' + esc(labels[key][1]) + '</span></span>' for key in row['marks'] if key in labels)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def candidate_memory(value):
    """Author-selected evidence notes, separate from nominations and A advice."""
    body='<section id="candidate-memory"><h2>W 选择的候选证据记忆</h2>'
    body+='<p class="note">这是 W 选择的科学主张与证据导航；与提名、A 推荐、候选资格分别记录，不排名，也不判定科学真伪。</p>'
    if value.get('coverage')!='accepted':
        return Safe(body+empty('未提供已接受的候选证据记忆；缺失不表示没有候选或证据。')+'</section>')
    step=value['source_step']
    body+='<p>'+link('W 第 '+str(step)+' 阶段准确原文','/nodes/'+str(step)+'/science')+' · '+link('该阶段现有材料','/nodes/'+str(step)+'/materials')+'</p>'
    if value.get('question'):body+='<p>'+translatable(value['question'])+'</p>'
    if not value['candidates']:body+=empty('W 本次未选择可展示的候选记忆条目。')
    for row in value['candidates']:
        article='<article><h3>'+esc(row['candidate'])+'</h3><dl>'
        for key,label in (('data','数据'),('evaluation','评价口径'),('failure_condition','失败条件'),('open_question','开放问题')):
            article+='<dt>'+label+'</dt><dd>'+translatable(row[key] or '未声明')+'</dd>'
        article+='</dl><h4>证据关系</h4><ul>'
        for evidence in row['evidence']:
            article+='<li><strong>'+{'supports':'支持','opposes':'反对','unknown':'未知'}[evidence['relation']]+'</strong>：'+translatable(evidence['note'])
            article+=' · '+link('准确证据原件 / 已有材料',evidence['href']) if evidence.get('href') else ' · '+link('原文中的准确证据引用','/nodes/'+str(step)+'/science')
            article+='</li>'
        article+='</ul><h4>限制</h4><ul>'+''.join('<li>'+translatable(text)+'</li>' for text in row['limitations'])+'</ul>'
        if row.get('evidence_omitted'):article+='<p class="note">另有 '+str(row['evidence_omitted'])+' 条证据关系，见准确原文。</p>'
        article+='</article>'
        if len((body+article).encode('utf-8'))>60*1024:
            body+='<p class="note">其余记忆超出显示预算，请查看上述准确原文。</p>';break
        body+=article
    if value.get('omitted'):body+='<p class="note">另有 '+str(value['omitted'])+' 个记忆条目未展开。</p>'
    if value.get('diagnostic_count'):body+='<p class="note">部分可选记忆条目无法形成准确导航；请核对 W 原文中的声明与限制。</p>'
    return Safe(body+'</section>')


def candidate_table(value, *, open_=True, ident='candidates'):
    """Observation order, grouped by evaluation_key; client sort is in-group only."""
    if 'error' in value:
        return Safe('<section class="candidates" id="' + esc(ident) + '"><h2>候选比较</h2>' + error_inline(value, what='候选表') + '</section>')
    total = value['total']
    description = ('全部已封存观察中的候选，含失败与未推荐记录；按首次观察顺序分页，并按评估口径与机制分组。'
        if value.get('all_candidates') else '仅显示本阶段输入范围内最近 W 的提名候选及 A 选中的候选。默认按首次观察顺序排列，并按评估口径（evaluation_key）分组；不同口径的数值不可直接比较。')
    body = ('<section class="candidates" id="' + esc(ident) + '"><h2>候选比较（' + esc(total) + '）</h2>'
            '<p class="note">' + description +
            '★ 为 A 的作者推荐，✓ 为控制记录的已确认交付，✕ 为执行失败；界面不提供科学排序或默认选择。</p>')
    diagnostics=value.get('nomination_diagnostics',[])
    if diagnostics:
        body+='<p class="note" role="status">部分可选提名无法生成预览；完整候选池仍可查看。请核对科学原文：</p><ul>'
        for item in diagnostics:
            body+='<li>'+link('第 '+str(item['source_step'])+' 阶段原文','/nodes/'+str(item['source_step'])+'/science')+' <code>'+esc(item['pointer'])+'</code></li>'
        body+='</ul>'
    if not total:
        return Safe(body + empty('尚无可显示的提名或 A 选中候选；请核对上述原文。' if diagnostics else '未记录提名或 A 选中候选。') + '</section>')
    extra = sum(1 for group in value['groups'] for row in group['rows'] if row['extra'])
    toggle = ''
    if extra:
        body += '<input type="checkbox" class="toggle-all" id="' + esc(ident) + '-all">'
        toggle = ('<label class="toggle-label secondary-button" for="' + esc(ident) + '-all">显示全部 ' + esc(total)
                  + ' 条（另 ' + esc(extra) + ' 条）</label>')
    body += '<div class="candidate-groups">'
    for number, group in enumerate(value['groups'], 1):
        numeric = sum(1 for row in group['rows'] if _number(row['metric']))
        body += ('<div class="candidate-group" data-group="' + esc(number) + '"><div class="group-head"><h3>口径：' + esc(group['key'])
                 + (' · 机制：' + esc(group['mechanism']) if group.get('mechanism') else '')
                 + '<span class="count">（' + esc(len(group['rows'])) + '）</span></h3>'
                 + ('<button type="button" class="secondary-button sort-button" data-sort-group="' + esc(number) + '">在此口径内按指标排序</button>'
                    '<p class="sort-note" data-sort-note hidden>仅按此口径数值排序，不代表科学排序。</p>' if numeric > 1 else '')
                 + ('<p class="note">显示名称相同的评估口径或不同机制保留独立分组，数值不可合并比较。</p>' if group.get('ambiguous_label') else '') + '</div>'
                 '<div class="table-scroll"><table class="candidate-table"><thead><tr>'
                 '<th scope="col">标记</th><th scope="col">标签</th><th scope="col">机制</th><th scope="col">指标</th>'
                 '<th scope="col">方向</th><th scope="col">来源阶段</th><th scope="col">审计</th><th scope="col">状态</th></tr></thead><tbody>')
        for row in group['rows']:
            cls = ' class="extra"' if row['extra'] else ''
            metric = row['metric'] if _number(row['metric']) else ''
            body += ('<tr' + cls + ' data-order="' + esc(row['order']) + '" data-metric="' + esc(metric) + '">'
                     '<td class="marks">' + _marks(row) + '</td>'
                     '<td><span class="cand-label">' + (translatable(row['label']) if row.get('authored_label', True) else esc(row['label'])) + '</span>'
                     + ('<span class="cand-ref">' + esc(row['reference']) + '</span>' if row.get('reference') else '') + '</td>'
                     '<td>' + (translatable(row['mechanism']) if row.get('authored_mechanism', True) else esc(row['mechanism'])) + '</td><td class="num">' + esc(row['metric_text']) + '</td>'
                     '<td>' + esc(row['direction'] or '') + '</td>'
                     '<td>' + (link('第 ' + str(row['step']) + ' 阶段', '/results/' + str(row['step'])) if isinstance(row['step'], int) else '—') + '</td>'
                     '<td>' + esc(row['audit']) + '</td><td>' + esc(row['status']) + '</td></tr>')
        body += '</tbody></table></div></div>'
    body += '</div>' + toggle
    return Safe(body + '</section>')


def result_card(card, *, files=None, full_href=None):
    if 'error' in card:
        return Safe('<section class="result-card">' + error_inline(card, what='结论卡片') + '</section>')
    tone = {'confirmed': 'ok', 'recommended': 'active', 'none': 'neutral', 'unknown': 'warn', 'audit': 'neutral', 'consultation':'neutral'}[card['delivery_state']]
    kind = {'closeout': '最终评估', 'formal_review': '正式评审', 'ordinary': '综合评估','consultation':'W 研究咨询','delivery_review':'提交前最终评估'}.get(card['kind'], '综合评估')
    body = ('<section class="result-card hero"><p class="eyebrow">第 ' + esc(card['step']) + ' 阶段 · A · ' + esc(kind)
            + ' · 决定：' + esc(card['decision'] or '未记录') + '</p>'
            '<p class="head-status">' + badge(card['delivery_label'], tone) + '</p>')
    audit = card['delivery_state'] == 'audit'
    chosen = card.get('audit_target') if audit else card.get('chosen')
    if chosen:
        body += ('<h2 class="chosen">' + ('审计对象：' if audit else '') + (translatable(chosen['label']) if chosen.get('authored_label', True) else esc(chosen['label'])) + '</h2><dl class="facts">'
                 '<div><dt>指标</dt><dd>' + esc(chosen['metric']) + '（口径：' + esc(chosen['evaluation_key']) + '）</dd></div>'
                 + ('<div><dt>方向</dt><dd>' + esc(chosen['direction']) + '</dd></div>' if chosen.get('direction') else '')
                 + '<div><dt>来源阶段</dt><dd>' + (link('第 ' + str(chosen['step']) + ' 阶段', '/results/' + str(chosen['step']))
                                                  if isinstance(chosen.get('step'), int) else '未记录') + '</dd></div>'
                 + ('<div><dt>作者引用</dt><dd><code>' + esc(chosen['reference']) + '</code></dd></div>' if chosen.get('reference') else '')
                 + '</dl>')
    else:
        body += '<h2 class="chosen">' + ('审计对象未能对应已观察候选' if audit else '本阶段未推荐候选方案' if card['delivery_state'] == 'none' else '咨询不产生正式候选推荐' if card['delivery_state']=='consultation' else '候选推荐未能对应已观察候选') + '</h2>'
        if card.get('recommendation_raw'):
            body += '<p>作者引用：<code>' + esc(card['recommendation_raw']) + '</code></p>'
        if card.get('resolution_error'):
            body += '<p class="inline-error">引用未能按冻结映射解析：' + esc(card['resolution_error']) + '</p>'
    body += '<p class="one-line">' + translatable(card['summary']) + '</p>'
    body += '<h3>' + ('审计理由' if audit else '选择理由') + '</h3><p>' + translatable(card['reason']) + '</p>'
    change = card.get('criteria_change')
    if change is None:
        body += '<p class="note">准则变化：未记录</p>'
    elif change['details'] and change['status'] == 'revised':
        body += '<details class="criteria"><summary>' + esc(change['label']) + ' · 查看原因</summary>' + ''.join(
            '<h4>' + esc(row['label']) + '</h4><div class="finding">' + translatable(row['text']) + '</div>' for row in change['details']) + '</details>'
    else:
        body += '<p class="note">' + esc(change['label']) + '</p>'
    body += '<h3>未达成的目标与局限</h3><p>目标完成声明：<strong>' + esc(card['goal_completion']) + '</strong></p>'
    if card['unmet']:
        body += '<ol class="limits">' + ''.join('<li>' + translatable(item) + '</li>' for item in card['unmet']) + '</ol>'
        if card['unmet_omitted']:
            body += '<p class="note">另有 ' + esc(card['unmet_omitted']) + ' 条局限，见完整原文。</p>'
    else:
        body += empty('原文未单列局限。')
    coverage = card.get('final_coverage')
    if coverage is not None:
        key = 'final-coverage:' + str(card['step'])
        body += ('<details class="final-coverage" data-details-key="' + esc(key) + '"><summary data-live-focus="' + esc(key)
                 + '">逐机制覆盖（A 作者声明，' + esc(len(coverage)) + ' 项）</summary>'
                 '<p class="note">覆盖状态由最终 A 根据原件声明；候选、调用数量与成功状态不代表科学深度。资源与输入限制见本卡局限及完整原文。</p>')
        statuses = {'deep': '已深入', 'screened_pending': '已筛查待再议', 'ruled_out': '已排除', 'unexplored': '从未探索'}
        for row in coverage:
            body += '<article><h4>' + (translatable(row['label']) if row.get('authored_label') else esc(row['label'])) + '</h4>'
            body += '<p>' + badge(statuses.get(row['status'], row['status'])) + '</p><p>' + translatable(row['reason']) + '</p>'
            refs = row.get('science_refs') or []
            if refs:
                body += '<p class="note">依据：' + '、'.join(link('第 ' + str(ref['source_step']) + ' 阶段',
                    '/nodes/' + str(ref['source_step']) + '/science') + ' <code>' + esc(ref['pointer']) + '</code>' for ref in refs) + '</p>'
            else:
                body += empty('未记录准确证据引用；请核对完整原文。')
            questions = row.get('unanswered_questions')
            body += '<h5>仍未回答的问题</h5>'
            if questions is None:
                body += empty('原文未明确声明；请核对完整原文。')
            elif questions:
                body += '<ul>' + ''.join('<li>' + translatable(item) + '</li>' for item in questions) + '</ul>'
            else:
                body += '<p class="note">A 声明无未回答问题。</p>'
            body += '</article>'
        body += '</details>'
    if files:
        body += '<div class="result-actions">' + esc(files) + '</div>'
    return Safe(body + '</section>')
