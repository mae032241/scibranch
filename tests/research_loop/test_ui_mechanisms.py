from ui_html import element_texts
import pytest

from scidiscovery.research_loop import public_output, ui_mechanisms as trail, ui_view


class Reader:
    def __init__(self, nodes):
        self.nodes = nodes
        self.read = []

    def node(self, step):
        self.read.append(step)
        return self.nodes[step]

    def observation(self, step):
        return self.nodes[step].get('observation')


def node(role, payload=None, observation=None, **job):
    return dict(job=dict(role=role, **job), payload=payload or {}, observation=observation)


def commitment(label='Alpha', **changes):
    return dict(label=label, minimal_test='Does the response distinguish the mechanism?', existing_ref=None, **changes)


def h(*rows):
    return node('H', dict(search_plan=dict(scope='Compare explicit mechanisms',
        commitments=list(rows), focus=dict(label=None, action='screen'))))


def w(finding, *, calls=None, candidates=None, summary='Shared W summary', **job):
    return node('W', dict(summary=summary, mechanism_findings=[dict(mechanism='Alpha', basis='observed', finding=finding)]),
        observation=dict(managed_calls=calls or [], candidates=candidates or []),
        search=dict(breadth=dict(mechanisms=[dict(mechanism_ref='h001.m1', label='Alpha', minimal_test='Prediction')])), **job)


def a(status, reason):
    return node('A', dict(search_review=dict(dispositions=[dict(mechanism_ref='h001.m1',
        status=status, reason=reason)], next_focus=None)))


def clean(text, limit=16384):
    return str(text)[:limit]


def test_continuous_w_and_a_keep_original_findings_and_deferred_reason():
    reader = Reader({1: h(commitment()), 2: w('Measured first response.'),
        3: a('screened', 'The first discriminator was observed.'),
        4: w('Measured second response; uncertainty remains.'),
        5: a('deferred', 'Await independent evidence; this is not a rejection.')})
    value = trail.project(reader, range(1, 6), clean)
    item, = value['mechanisms']
    assert [event['role'] for event in item['events']] == ['H', 'W', 'A', 'W', 'A']
    assert item['status'] == 'deferred'
    assert item['reason'] == 'Await independent evidence; this is not a rejection.'
    rendered = trail.render(value)
    assert '暂缓' in rendered and '淘汰' not in rendered
    assert 'Measured first response.' in rendered and 'Measured second response' in rendered
    assert '/nodes/2/science' in rendered and '/nodes/4/materials' in rendered
    assert 'Shared W summary' not in rendered  # breadth summaries are never copied to each branch


def test_only_exact_call_bindings_link_candidates_and_metric_groups_stay_separate(monkeypatch):
    monkeypatch.setattr(trail.generic_results, 'candidates', lambda rows: rows[0].get('observed_candidates', []))
    rows = [dict(id='first', label='One', metric=.8, evaluation_key='validation', direction='higher', status='ok'),
        dict(id='second', label='Two', metric=.2, evaluation_key='test', direction='lower', status='ok'),
        dict(id='unbound', label='Unbound', metric=999, evaluation_key='validation')]
    calls = [dict(accepted={'proof': 'bound'}, status='known_success', mechanism_binding=dict(mechanism_ref='h001.m1'),
        observed_candidates=rows[:2])]
    reader = Reader({1: h(commitment()), 2: w('Observed separate evaluations.', calls=calls, candidates=rows)})
    value = trail.project(reader, [1, 2], clean)
    event = value['mechanisms'][0]['events'][1]
    assert event['calls'] == dict(accepted=1, terminal=1)
    assert [group['key'] for group in event['metrics']] == ['validation', 'test']
    assert [group['rows'][0]['metric'] for group in event['metrics']] == ['0.8', '0.2']
    rendered = trail.render(value)
    assert 'Unbound' not in rendered and '999' not in rendered
    assert '最高' not in rendered and '最佳' not in rendered


def test_cards_keep_short_original_excerpts_above_complete_collapsed_trails():
    question = 'Can the measurement distinguish Alpha? Full question with its original limitations.'
    finding = 'The response remains uncertain. Full finding with the observed conditions.'
    reason = 'Await independent evidence. Full disposition reason and its original caveats.'
    value = trail.project(Reader({1: h(dict(label='Alpha', minimal_test=question, existing_ref=None), commitment('Beta')),
        2: w(finding), 3: a('deferred', reason)}), [1, 2, 3], clean)
    rendered = trail.render(value)
    assert '<div class="mechanism-grid">' in rendered
    assert rendered.count('<article class="mechanism-card">') == 2
    alpha = rendered.split('<article class="mechanism-card">', 1)[1].split('</article>', 1)[0]
    overview, details = alpha.split('<details', 1)
    assert element_texts(overview, 'h3') == ['Alpha']
    assert '暂缓' in element_texts(overview, 'span', cls='mechanism-status')
    assert 'Can the measurement distinguish Alpha?' in overview
    assert 'The response remains uncertain.' in overview and 'Await independent evidence.' in overview
    assert 'Full question' not in overview and 'Full finding' not in overview and 'Full disposition' not in overview
    assert question in details and finding in details and reason in details
    assert ' open' not in details.split('>', 1)[0]
    assert 'data-details-key="mechanism:h001.m1"' in details
    assert 'data-live-focus="mechanism:h001.m1"' in details
    assert details.index('role-H') < details.index('role-W') < details.index('role-A')
    assert '/nodes/3/science' in details and '/nodes/3/materials' in details
    beta = rendered.split('<article class="mechanism-card">', 2)[2].split('<details', 1)[0]
    assert '当前声明暂无 A 处置记录' in beta and '本窗口尚未记录逐机制的作者发现。' in beta


def test_empty_evidence_and_dispatch_do_not_invent_experiment_counts():
    reader = Reader({1: h(commitment()), 2: node('W', dict(summary='Unbound summary'),
        observation=dict(candidates=[], managed_calls=[]),
        search=dict(focus=dict(mechanism_ref='h001.m1', action='screen'), breadth=dict(mechanisms=[
            dict(mechanism_ref='h001.m1', label='Alpha', minimal_test='Prediction', source_h=1)])))})
    value = trail.project(reader, [1, 2], clean)
    assert value['unbound'] == [2]
    assert [row['role'] for row in value['mechanisms'][0]['events']] == ['H']
    rendered = trail.render(value)
    assert '没有明确的逐机制关联' in rendered
    assert '0 次' not in rendered and 'Unbound summary' not in rendered
    assert '尚无可读取' in trail.render(trail.project(Reader({}), [], clean))


@pytest.mark.parametrize('action', ['improve', 'repair', 'validate'])
def test_local_author_summary_is_not_a_completed_experiment(action):
    reader = Reader({1: h(commitment()), 2: node('W',
        dict(summary='Analysis of available inputs.', mechanism_findings=None),
        observation=dict(candidates=[], managed_calls=[]),
        search=dict(focus=dict(mechanism_ref='h001.m1', action=action), breadth=dict(mechanisms=[
            dict(mechanism_ref='h001.m1', label='Alpha', minimal_test='Prediction', source_h=1)])))})
    value = trail.project(reader, [1, 2], clean)
    event = value['mechanisms'][0]['events'][-1]
    assert not value['unbound'] and event['role'] == 'W'
    assert event['text'] == 'Analysis of available inputs.' and event['calls'] is None
    assert event['sources'] == [1]
    assert '已接受的受管调用' not in trail.render(value)


def test_revision_preserves_history_but_does_not_inherit_previous_a_disposition():
    revised = dict(label='Revised Alpha', minimal_test='A new discriminator.', existing_ref='h001.m1')
    value = trail.project(Reader({1: h(commitment()), 2: a('screened', 'For the earlier question.'),
        3: h(revised)}), [1, 2, 3], clean)
    item, = value['mechanisms']
    assert item['label'] == 'Revised Alpha' and item['status'] is None
    assert item['events'][1]['status'] == 'screened'
    assert item['question'] == 'A new discriminator.'


def test_window_is_bounded_and_marks_missing_history(monkeypatch):
    monkeypatch.setattr(ui_view, 'WINDOW', 2)
    reader = Reader({2: w('Only the recent observation.'), 3: a('deferred', 'Need more evidence.')})
    value = trail.project(reader, [1, 2, 3], clean)
    assert reader.read == [2, 3] and value['omitted'] == 1
    assert value['mechanisms'][0]['label'] == 'Alpha'  # frozen W declaration supplies the label
    assert '较早记录未汇入' in trail.render(value)
    assert '/results?after=0' in trail.render(value)


def test_large_detail_history_preserves_summary_and_links_with_complete_bounded_html():
    nodes = {1: h(commitment())}
    nodes.update({step: w('中间观察与限制' * 500) for step in range(2, ui_view.WINDOW + 1)})
    value = trail.project(Reader(nodes), nodes, clean)
    rendered = trail.render(value)
    assert len(rendered.encode('utf-8')) <= trail.MAX_RENDER_BYTES
    assert 'Alpha' in rendered and '当前声明暂无 A 处置记录' in rendered
    assert '因显示长度限制未展开' in rendered
    assert '/results/2' in rendered and '/results/' + str(ui_view.WINDOW) in rendered
    assert rendered.count('<article') == rendered.count('</article>')
    assert rendered.count('<details') == rendered.count('</details>')
    assert rendered.endswith('</section>')


def test_public_cleaning_and_html_escape_apply_before_render_and_keys_never_merge(tmp_path, monkeypatch):
    clean_public = public_output.cleaner(tmp_path, settings={})
    secret = str(tmp_path) + '/private-input'
    rows = [dict(id=str(index), label='<script>bad</script>', metric=.1,
        evaluation_key=secret + '/' + suffix, direction='higher', status='ok')
        for index, suffix in enumerate(('first', 'second'))]
    # Two distinct exact evaluation keys may collapse under public cleaning.
    def private_clean(text, limit=16384):
        return clean_public(text, limit).replace('/private-input/first', '').replace('/private-input/second', '')
    monkeypatch.setattr(trail.generic_results, 'candidates', lambda calls: rows)
    call = dict(accepted={}, status='known_success', mechanism_binding=dict(mechanism_ref='h001.m1'))
    value = trail.project(Reader({1: h(commitment('<b>Alpha</b>')),
        2: w('token=supersecret ' + secret, calls=[call], candidates=rows)}), [1, 2], private_clean)
    event = value['mechanisms'][0]['events'][-1]
    assert len(event['metrics']) == 2
    assert all(group['ambiguous_label'] for group in event['metrics'])
    rendered = trail.render(value)
    assert '<script>' not in rendered and '<b>Alpha</b>' not in rendered
    assert '&lt;script&gt;' in rendered and '&lt;b&gt;Alpha&lt;/b&gt;' in rendered
    assert str(tmp_path) not in rendered and 'supersecret' not in rendered
    assert '[credential]' in rendered


def sealed_fixture(tmp_path, findings):
    from scidiscovery.research_loop import controller, search
    from test_history_handoff import finish

    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare an explicit mechanism using sealed evidence.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-invoked'], max_tokens=100000,
        max_seconds=600, max_steps=6, model='fixture', reasoning_effort='high',
        experiment_mode='external', search_policy=search.DEFAULT_POLICY)
    plan = dict(scope='One mechanism comparison', commitments=[commitment()], focus=dict(label=None, action='screen'))
    finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    finish(root, 2, 'W', [1], mechanism_findings=findings,stage_intent='decision' if not isinstance(findings,list) else 'review')
    key = tmp_path / 'operator.key'
    key.write_bytes(b'mechanism-ui-fixture-secret-32bytes')
    key.chmod(0o600)
    return root, key


def test_sealed_results_and_sse_share_real_mechanism_evidence_without_writes(tmp_path):
    from test_history_handoff import finish
    from test_instance_ui import session

    root, key = sealed_fixture(tmp_path, [dict(mechanism='Alpha', basis='blocked',
        finding='An independent observation is still missing.')])
    ui, cookie = session(root, key)
    before = _originals(root)
    page = ui.get('/results', host=ui.host, cookie=cookie)[1].decode()
    assert 'id="live-mechanisms"' in page and 'data-live-url="/live"' in page
    assert 'Alpha' in page and 'An independent observation is still missing.' in page
    assert '已接受的受管调用 0' not in page
    assert _originals(root) == before
    first = ui._live_snapshot(ui._browser(cookie), '', force=True)['fragments']['live-mechanisms']
    assert 'An independent observation is still missing.' in first
    finish(root, 3, 'A', [2], search_choice=dict(dispositions=[dict(mechanism_ref='h001.m1',
        status='deferred', reason='Await the missing observation; keep the mechanism open.')], next_focus=None))
    before = _originals(root)
    updated = ui._live_snapshot(ui._browser(cookie), '', force=True)['fragments']['live-mechanisms']
    assert updated != first and '暂缓' in updated
    assert 'Await the missing observation; keep the mechanism open.' in updated
    assert 'data-details-key="mechanism:h001.m1"' in updated
    assert 'data-live-focus="mechanism:h001.m1"' in updated
    assert _originals(root) == before


@pytest.mark.parametrize('raw_findings', ['Scientific narrative instead of a table', {'finding': 'Partial explanation'}])
def test_sealed_unparsed_w_findings_keep_h_and_later_a_visible(tmp_path, raw_findings):
    from test_history_handoff import finish

    root, _ = sealed_fixture(tmp_path, raw_findings)
    finish(root, 3, 'A', [2], search_choice=dict(dispositions=[dict(mechanism_ref='h001.m1',
        status='deferred', reason='Structured W findings remain unknown; retain the question.')], next_focus=None))
    before = _originals(root)
    reader = ui_view.Reader(root)
    receipt = reader.node(2)['seal']['control_outcome']['worker_delivery']
    assert receipt['status'] == 'partial' and receipt['reason'] == 'worker_decision'
    assert reader.node(2)['payload']['mechanism_findings'] == raw_findings
    value = trail.project(reader, [1, 2, 3], ui_view.cleaner(root))
    assert not value['errors']
    item, = value['mechanisms']
    assert [event['role'] for event in item['events']] == ['H', 'A']
    assert value['unresolved'] == [dict(step=2, field='W 逐机制发现')]
    rendered = trail.render(value)
    assert 'Alpha' in rendered and '暂缓' in rendered and '可证伪问题' in rendered
    assert '尚未解析为结构化信息' in rendered and '/results/2' in rendered
    assert _originals(root) == before


@pytest.mark.parametrize('role,field', [('H', 'search_plan'), ('A', 'search_review')])
def test_unparsed_h_or_a_fields_preserve_other_stage_records(role, field):
    reader = Reader({1: h(commitment()), 2: node(role, {field: 'Narrative in an auxiliary field'}),
        3: a('deferred', 'Keep this mechanism open.')})
    value = trail.project(reader, [1, 2, 3], clean)
    assert not value['errors'] and len(value['unresolved']) == 1
    assert [event['role'] for event in value['mechanisms'][0]['events']] == ['H', 'A']
    assert value['mechanisms'][0]['reason'] == 'Keep this mechanism open.'
    assert '尚未解析为结构化信息' in trail.render(value)


def _originals(root):
    paths=[root/'settings.json',root/'settings-identity.json',root/'host-history/overview.json']
    for step in (1,2,3):
        paths.extend([root/f'jobs/{step:03d}.json',root/f'host-history/nodes/{step:03d}.json'])
        paths.extend(root/f'native-evidence/{step:03d}'/name for name in
            ('science-output.json','native-intent.json','native-outcome.json','result-identity.json','work-evidence.json'))
    return {path:path.read_bytes() for path in paths if path.is_file()}


@pytest.mark.parametrize('role,field,value',[
    ('H','search_plan',{'commitments':[{}]}),
    ('H','search_plan',{'commitments':[1]}),
    ('A','search_review',{'dispositions':[{}]}),
    ('A','search_review',{'dispositions':[1]}),
    ('W','mechanism_findings',[{}]),
    ('W','mechanism_findings',[1]),
])
def test_sparse_optional_scientific_rows_preserve_other_mechanism_records(role,field,value):
    reader=Reader({1:h(commitment()),2:node(role,{field:value}),3:a('deferred','Keep the precise unresolved route visible.')})
    projected=trail.project(reader,[1,2,3],clean)
    assert not projected['errors'] and projected['unresolved']
    item,=projected['mechanisms']
    assert item['label']=='Alpha' and item['status']=='deferred'
    assert [event['role'] for event in item['events']]==['H','A']
    assert '尚未解析为结构化信息' in trail.render(projected)
