"""Bounded, read-only mechanism trails from verified sealed originals.

This is a navigation projection, not search-state reconstruction or a scientific
verdict. Only explicit findings and observed bindings associate W evidence.
"""
from __future__ import annotations

from . import contracts as c, generic_results, search as scientific_search, ui_components as ui, ui_view

MAX_RENDER_BYTES = 64 * 1024


def _ref(row):
    return (row.get('mechanism_binding') or {}).get('mechanism_ref') or (row.get('scope') or {}).get('mechanism_ref')


def _metrics(candidates, clean):
    groups = {}
    for index, row in enumerate(candidates):
        key = row.get('evaluation_key')
        identity = key if isinstance(key, str) and key.strip() else ('unknown', index)
        group = groups.setdefault(identity, dict(key=clean(key, 300) if key is not None else '未声明', rows=[]))
        group['rows'].append(dict(label=clean(row.get('label') or '未命名候选', 300),
            authored_label=bool(row.get('label')), metric=clean(ui_view.fmt_metric(row.get('metric')), 80),
            direction=clean(row.get('direction') or '未声明', 200),
            status=clean(row.get('status') or '未记录', 100)))
    # Different exact keys can have the same cleaned label; keep groups separate.
    result = list(groups.values())
    for group in result:
        group['ambiguous_label'] = sum(other['key'] == group['key'] for other in result) > 1
        group['omitted'] = max(0, len(group['rows']) - ui_view.CANDIDATE_LIMIT)
        group['rows'] = group['rows'][:ui_view.CANDIDATE_LIMIT]
    return result


def project(reader, steps, clean, *, partial=False):
    """Read at most WINDOW sealed stages, sharing the page's verified Reader."""
    steps = sorted(set(steps))
    shown = steps[-ui_view.WINDOW:]
    mechanisms, unbound, unresolved, errors = {}, [], [], []

    def mechanism(ref):
        return mechanisms.setdefault(ref, dict(ref=clean(ref, 160), label=clean(ref, 160),
            question=None, status=None, reason=None, declared_in_window=False, authored_label=False, events=[]))

    for step in shown:
        try:
            node = reader.node(step)
            job, payload = node['job'], node['payload']
            role = job['role']
            observation = reader.observation(step) if role == 'W' else None
        except Exception as error:  # noqa: BLE001 - retain the rest of the bounded trail
            errors.append(dict(step=step, **ui_view.failure(error, clean)))
            continue
        authored_refs=set()
        if scientific_search.mechanism_producer(job):
            try:plan = scientific_search._choice(job,payload)
            except scientific_search.UnresolvedSearch:plan = None
            if plan is None and payload.get('search_plan') is not None:
                unresolved.append(dict(step=step, field=role+' 机制声明'))
            for index, row in enumerate((plan or {}).get('commitments') or [], 1):
                ref = row.get('existing_ref') or f'{role.lower()}{step:03d}.m{index}'
                authored_refs.add(ref)
                item = mechanism(ref)
                label, question = clean(row['label'], 300), clean(row['minimal_test'], 2000)
                item.update(label=label, authored_label=True, question=question, status=None, reason=None,
                    source_step=step,source_role=role,declared_in_window=True)
                item['events'].append(dict(step=step, role=role, kind='declaration', label=label, text=question))
        if role == 'W':
            search = job.get('search') or {}
            declared = list((search.get('breadth') or {}).get('mechanisms') or [])
            calls = (observation or {}).get('managed_calls') or []
            state=(observation or {}).get('research_state') or {}
            live_rows=list((state.get('mechanisms') or {}).values()) if isinstance(state.get('mechanisms'),dict) else []
            snapshots=[]
            for call in calls:
                binding=(call.get('mechanism_binding') or {}).get('research_binding')
                if isinstance(binding,dict):snapshots.append(binding)
            # These are already sealed owner facts, never the current live pointer.
            # Call versions stay distinct from the declaration at W sealing.
            versions=set();live_refs=set()
            for row in [*snapshots,*live_rows] if job.get('research_strategy_version')==3 else []:
                ref=row.get('mechanism_ref');revision=row.get('revision')
                if (not isinstance(ref,str) or type(revision) is not int or revision<1
                        or row.get('source_role')!='W'):continue
                label=clean(row.get('label') or row.get('question') or ref,300)
                question=clean(row.get('question') or '',2000)
                key=(ref,revision)
                if key not in versions:
                    versions.add(key)
                    item=mechanism(ref)
                    item.update(label=label,authored_label=True,question=question,declared_in_window=True)
                    item['events'].append(dict(step=step,role='W',kind='declaration',label=label,text=question,
                        revision=revision,source_step=row.get('source_step'),source_role=row.get('source_role'),
                        declaration_scope='封存时接受的 W 声明' if row in live_rows else '原调用准确绑定'))
                if not any(item['mechanism_ref']==ref for item in declared):
                    declared.append(dict(mechanism_ref=ref,label=label,minimal_test=question,
                        source_step=row.get('source_step'),source_role=row.get('source_role'),research_binding=row))
                live_refs.add(ref)
            for row in declared:
                item = mechanism(row['mechanism_ref'])
                if item['question'] is None:
                    item.update(label=clean(row['label'], 300), authored_label=True, question=clean(row.get('minimal_test') or '', 2000))
            findings = {}
            declared_findings = c.scientific_field(job, payload, 'mechanism_findings')
            authored_findings=False
            if (declared_findings is None and job.get('research_strategy_version')==3
                    and isinstance(payload.get('mechanism_findings'),list)):
                # Explicit authored notes are a UI preview, never a contract gate.
                declared_findings=payload['mechanism_findings'];authored_findings=True
            if declared_findings is None and payload.get('mechanism_findings') is not None:
                unresolved.append(dict(step=step, field='W 逐机制发现'))
            for row in declared_findings or []:
                if not isinstance(row,dict) or any(not isinstance(row.get(key),str) or not row[key].strip() for key in ('mechanism','basis','finding')):
                    if dict(step=step,field='W 逐机制发现') not in unresolved:unresolved.append(dict(step=step,field='W 逐机制发现'))
                    continue
                matches = list({item['mechanism_ref'] for item in declared
                    if row['mechanism']==item['mechanism_ref'] or (not authored_findings and (not item.get('research_binding')
                        or (item.get('research_binding') or {}).get('revision')==0) and row['mechanism']==item['label'])})
                if len(matches) == 1:
                    findings.setdefault(matches[0], []).append(dict(basis=clean(row['basis'], 80),
                        text=clean(row['finding'], 2400),authored=authored_findings))
            contexts = (observation or {}).get('contexts') or []
            # Candidate identities are matched to their exact observed call, not
            # to labels, scientific nominations, dispatch focus, or metric values.
            bindings = {}
            for call in calls:
                ref = _ref(call)
                if ref:
                    for row in generic_results.candidates([call]):
                        bindings.setdefault(row['id'], set()).add(ref)
            for row in contexts:
                ref = _ref(row)
                if ref and row.get('candidate_id'):
                    bindings.setdefault(row['candidate_id'], set()).add(ref)
            candidates = {}
            for row in (observation or {}).get('candidates') or []:
                refs = bindings.get(row['id'], set())
                if len(refs) == 1:
                    candidates.setdefault(next(iter(refs)), []).append(row)
            refs = live_refs | set(findings) | set(candidates) | {ref for row in calls if (ref := _ref(row))}
            # A local W's declared summary belongs only to its one exact focus.
            # All bound tasks may carry breadth metadata, including local W.
            # A screen dispatch alone supplies no completed mechanism evidence.
            focus = search.get('focus') or {}
            local = focus.get('mechanism_ref') if focus.get('action') in {'improve', 'repair', 'validate'} else None
            if local:
                refs.add(local)
            if not refs and not authored_refs:
                unbound.append(step)
            for ref in sorted(refs):
                bound_calls = [row for row in calls if _ref(row) == ref and row.get('accepted')]
                counts = None
                if bound_calls:
                    counts = dict(accepted=len(bound_calls), terminal=sum(row.get('status') in
                        {'known_success', 'known_failed'} for row in bound_calls))
                bindings = [row for row in declared if row['mechanism_ref'] == ref]
                bindings.extend(row.get('mechanism_binding') or {} for row in calls if _ref(row) == ref)
                authors={(row.get('source_step'),row.get('source_role')) for row in bindings
                    if type(row.get('source_step')) is int and row['source_step']>0}
                mechanism(ref)['events'].append(dict(step=step, role='W', findings=findings.get(ref, []),
                    text=clean(payload['summary'], 1200) if local == ref and payload.get('summary') else None,
                    calls=counts, sources=sorted({source for source,_ in authors}),
                    source_authors=[dict(source_step=source,source_role=author if author in {'H','W'} else None)
                        for source,author in sorted(authors,key=lambda row:(row[0],str(row[1])))],
                    metrics=_metrics(candidates.get(ref, []), clean)))
        # A advice remains in its exact scientific original. It cannot become
        # an adopted mechanism disposition merely through this UI projection.
    return dict(mechanisms=list(mechanisms.values()), unbound=unbound, unresolved=unresolved, errors=errors,
        omitted=len(steps) - len(shown), partial=partial,
        first=shown[0] if shown else None, last=shown[-1] if shown else None)


def _event(event):
    step, role = event['step'], event['role']
    body = '<h4 class="mechanism-event-head"><span class="role role-' + role + '">' + role + '</span> ' + ui.link('第 ' + str(step) + ' 阶段 · ' + ui_view.ROLE_LABELS[role], '/results/' + str(step)) + '</h4>'
    declaration=event.get('kind')=='declaration'
    if declaration:
        body += '<p><strong>' + ui.translatable(event['label']) + '</strong></p>'
        if 'revision' in event:
            body+='<p class="note">'+ui.esc(event['declaration_scope'])+' · revision '+str(event['revision'])+'</p>'
            if type(event.get('source_step')) is int and event['source_step']>0:
                body+='<p>'+ui.link('声明来源 '+str(event.get('source_role') or '')+' · 第 '+str(event['source_step'])+' 阶段','/results/'+str(event['source_step']))+'</p>'
    if role == 'A':
        body += ui.badge(ui_view.DISPOSITIONS.get(event['status'], event['status']))
    if event.get('text'):
        body += '<p>' + ui.translatable(event['text']) + '</p>'
    if role == 'W' and not declaration:
        if event['source_authors']:
            body += '<p class="note">关联机制声明：' + '、'.join(ui.link(
                (source['source_role']+' · ' if source['source_role'] else '')+'第 '+str(source['source_step'])+' 阶段',
                '/results/'+str(source['source_step'])) for source in event['source_authors']) + '</p>'
        if any(row.get('authored') for row in event['findings']):
            body+='<p class="note">以下为 W 原文作者声明；按准确机制身份展示，不改变科学状态或资格。</p>'
        for row in event['findings']:
            body += '<p><strong>' + ui.esc({'observed': '观察发现', 'blocked': '受阻原因'}.get(row['basis'], row['basis'])) + '：</strong>' + ui.translatable(row['text']) + '</p>'
        counts = event['calls']
        if counts:
            body += '<p class="note">已接受的受管调用 ' + str(counts['accepted']) + ' 次；其中已知终态 ' + str(counts['terminal']) + ' 次。调用数不等于独立实验设计数或科学覆盖。</p>'
        if not event['findings'] and not event.get('text'):
            body += ui.empty('本阶段没有逐机制的作者发现；请查看阶段原文。')
        for index, group in enumerate(event['metrics'], 1):
            body += '<h5>评估口径：' + ui.esc(group['key']) + ('（独立分组 ' + str(index) + '）' if group['ambiguous_label'] else '') + '</h5>'
            body += '<div class="table-scroll"><table class="compact"><thead><tr><th>候选</th><th>指标</th><th>作者声明方向</th><th>状态</th></tr></thead><tbody>'
            for row in group['rows']:
                body += '<tr>' + ''.join('<td>' + (ui.translatable(row[key]) if key == 'label' and row.get('authored_label', True) else ui.esc(row[key])) + '</td>' for key in ('label', 'metric', 'direction', 'status')) + '</tr>'
            body += '</tbody></table></div>'
            if group['omitted']:
                body += '<p class="note">另有 ' + str(group['omitted']) + ' 个候选，请查看本阶段原文。</p>'
    body += ui.links([dict(label='完整原文', href='/nodes/' + str(step) + '/science'),
        dict(label='研究文件', href='/nodes/' + str(step) + '/materials')])
    return '<li class="mechanism-event">' + body + '</li>'


def _summary(item, *, compact=False):
    status = ui_view.DISPOSITIONS.get(item['status'], item['status']) if item['status'] else '科学判断见阶段原文'
    body = '<article class="mechanism-card"><header class="mechanism-card-head"><h3>' + (ui.translatable(ui_view.first_sentence(item['label'], 80) if compact else item['label']) if item.get('authored_label', True) else ui.esc(item['label'])) + '</h3><span class="badge mechanism-status">' + ui.esc(status) + '</span></header>'
    if not compact:
        body += '<dl class="mechanism-overview"><div><dt>可证伪问题</dt><dd>' + (ui.translatable(ui_view.first_sentence(item['question'], 96)) if item['question'] else '本窗口未记录可证伪问题。') + '</dd></div>'
        latest_w = next((event for event in reversed(item['events']) if event['role'] == 'W' and event.get('kind')!='declaration'), None)
        finding = None
        if latest_w:
            texts = [row['text'] for row in latest_w['findings']] or [latest_w.get('text')]
            finding = ui_view.first_sentence(texts[0], 120)
        body += '<div><dt>最近 W 记录' + (' · ' + ui.link('第 ' + str(latest_w['step']) + ' 阶段', '/results/' + str(latest_w['step'])) if latest_w else '') + '</dt><dd>' + (ui.translatable(finding) if finding else '本窗口尚未记录逐机制的作者发现。') + '</dd></div>'
        body += '</dl>'
    return body


def render(value):
    """Escaped complete markup, with a display-only UTF-8 budget for SSE."""
    body = '<section class="mechanism-trails"><h2>机制探索过程</h2>'
    if 'error' in value:
        return ui.Safe(body + ui.error_inline(value, what='机制探索过程') + '</section>')
    body += '<p class="note">仅封存阶段：按 H/W 声明和 W 的明确机制关联串联；A 判断保留在阶段原文中。不同评估口径分别保留，候选顺序不代表科学排序。每个机制展开后可查看中间过程与原文。</p>'
    if value['omitted'] or value['partial']:
        body += '<p class="note">仅汇集当前窗口内至多 ' + str(ui_view.WINDOW) + ' 个已封存阶段；较早记录未汇入，机制记录可能不完整。' + ui.link('按阶段查看更早原文', '/results?after=0') + '</p>'
    if not value['mechanisms']:
        body += ui.empty('尚无可读取的机制声明或明确绑定的机制证据。')
    tail = ''
    if value['unbound']:
        tail += '<p class="note">以下 W 阶段没有明确的逐机制关联，未归入任一机制：' + '、'.join(ui.link('第 ' + str(step) + ' 阶段', '/results/' + str(step)) for step in value['unbound']) + '。</p>'
    for row in value['unresolved']:
        tail += '<p class="note">' + ui.link('第 ' + str(row['step']) + ' 阶段', '/results/' + str(row['step'])) + '的 ' + ui.esc(row['field']) + '尚未解析为结构化信息；机制关联或处置未知，请查看阶段原文。</p>'
    for error in value['errors']:
        tail += ui.link('第 ' + str(error['step']) + ' 阶段', '/results/' + str(error['step'])) + ui.error_inline(error, what='机制记录')
    # Reserve summaries before spending bytes on details. Oversized histories
    # retain mechanism labels/status and navigate to the exact stage originals.
    size = lambda text: len(text.encode('utf-8'))
    summaries = [_summary(item) for item in value['mechanisms']]
    compact = size(body + tail + ''.join(summaries)) > MAX_RENDER_BYTES // 2
    if compact:
        summaries = [_summary(item, compact=True) for item in value['mechanisms']]
        body += '<p class="note">记录较长，机制摘要已缩短；完整问题与发现请查看阶段原文。</p>'
    body += '<div class="mechanism-grid">'
    closing = '</div>' + tail + '</section>'
    reserve = size(closing) + sum(size(summary + '</article>') for summary in summaries)
    for index, (item, summary) in enumerate(zip(value['mechanisms'], summaries)):
        reserve -= size(summary + '</article>')
        key = 'mechanism:' + item['ref']
        opening = '<details class="mechanism-details" data-details-key="' + ui.esc(key) + '"><summary data-live-focus="' + ui.esc(key) + '">查看完整过程与原文 <span class="mechanism-record-count">' + str(len(item['events'])) + ' 条记录</span></summary>'
        fallback = '<p class="note">详细过程因显示长度限制未展开；请查看对应阶段原文：' + '、'.join(ui.link('第 ' + str(step) + ' 阶段', '/results/' + str(step)) for step in sorted({event['step'] for event in item['events']})) + '。</p>'
        history_note = '' if item['declared_in_window'] else '<p class="note">本窗口未包含该机制的 H/W 声明原文；声明信息仅在任务保留时显示，请从相关阶段原文核对声明版本。</p>'
        details = opening + history_note + '<ol class="mechanism-timeline">' + ''.join(_event(event) for event in item['events']) + '</ol></details>'
        if size(body + summary + details + '</article>') + reserve > MAX_RENDER_BYTES:
            details = opening + fallback + '</details>'
        article = summary + details + '</article>'
        if size(body + article + closing) > MAX_RENDER_BYTES - 1024:
            # Pathological numbers of declarations still get an explicit limit
            # notice and originals, never a cut HTML string or a hidden verdict.
            body += '<p class="note">另有 ' + str(len(summaries) - index) + ' 个机制因显示长度限制未列出；' + ui.link('查看全部阶段原文', '/results?after=0') + '。</p>'
            break
        body += article
    return ui.Safe(body + closing)
