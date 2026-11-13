from copy import deepcopy

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
    return {**dict(label=label, minimal_test='Does the response distinguish the mechanism?', existing_ref=None), **changes}


def h(*rows):
    return node('H', dict(search_plan=dict(scope='Compare explicit mechanisms',
        commitments=list(rows), focus=dict(index=None, action='screen'))))


def w(finding, *, calls=None, candidates=None, summary='Shared W summary', **job):
    return node('W', dict(summary=summary, mechanism_findings=[dict(mechanism='Alpha', basis='observed', finding=finding)]),
        observation=dict(managed_calls=calls or [], candidates=candidates or []),
        search=dict(breadth=dict(mechanisms=[dict(mechanism_ref='h001.m1', label='Alpha', minimal_test='Prediction')])), **job)


def a(status, reason):
    return node('A', dict(search_review=dict(dispositions=[dict(mechanism_ref='h001.m1',
        status=status, reason=reason)], next_focus=None)))


def clean(text, limit=16384):
    return str(text)[:limit]


def test_continuous_w_keeps_findings_without_adopting_a_advice():
    reader = Reader({1: h(commitment()), 2: w('Measured first response.'),
        3: a('screened', 'The first discriminator was observed.'),
        4: w('Measured second response; uncertainty remains.'),
        5: a('deferred', 'Await independent evidence; this is not a rejection.')})
    value = trail.project(reader, range(1, 6), clean)
    item, = value['mechanisms']
    assert [event['role'] for event in item['events']] == ['H', 'W', 'W']
    assert item['status'] is None and item['reason'] is None
    rendered = trail.render(value)
    assert '暂缓' not in rendered and '淘汰' not in rendered
    assert 'Await independent evidence; this is not a rejection.' not in rendered
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
    assert element_texts(overview, 'span', cls='mechanism-status')
    assert value['mechanisms'][0]['status'] is None and value['mechanisms'][0]['reason'] is None
    assert 'Can the measurement distinguish Alpha?' in overview
    assert 'The response remains uncertain.' in overview and 'Await independent evidence.' not in rendered
    assert 'Full question' not in overview and 'Full finding' not in overview and 'Full disposition' not in overview
    assert question in details and finding in details and reason not in rendered
    assert ' open' not in details.split('>', 1)[0]
    assert 'data-details-key="mechanism:h001.m1"' in details
    assert 'data-live-focus="mechanism:h001.m1"' in details
    assert details.index('role-H') < details.index('role-W') and 'role-A' not in details
    assert '/nodes/1/science' in details and '/nodes/2/materials' in details
    beta = rendered.split('<article class="mechanism-card">', 2)[2].split('<details', 1)[0]
    assert '本窗口尚未记录逐机制的作者发现。' in beta


def test_empty_evidence_and_dispatch_do_not_invent_experiment_counts():
    reader = Reader({1: h(commitment()), 2: node('W', dict(summary='Unbound summary'),
        observation=dict(candidates=[], managed_calls=[]),
        search=dict(focus=dict(mechanism_ref='h001.m1', action='screen'), breadth=dict(mechanisms=[
            dict(mechanism_ref='h001.m1', label='Alpha', minimal_test='Prediction', source_step=1, source_role='H')])))})
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
            dict(mechanism_ref='h001.m1', label='Alpha', minimal_test='Prediction', source_step=1, source_role='H')])))})
    value = trail.project(reader, [1, 2], clean)
    event = value['mechanisms'][0]['events'][-1]
    assert not value['unbound'] and event['role'] == 'W'
    assert event['text'] == 'Analysis of available inputs.' and event['calls'] is None
    assert event['sources'] == [1]
    assert '已接受的受管调用' not in trail.render(value)


def test_revision_preserves_h_history_without_adopting_previous_a_advice():
    revised = dict(label='Revised Alpha', minimal_test='A new discriminator.', existing_ref='h001.m1')
    value = trail.project(Reader({1: h(commitment()), 2: a('screened', 'For the earlier question.'),
        3: h(revised)}), [1, 2, 3], clean)
    item, = value['mechanisms']
    assert item['label'] == 'Revised Alpha' and item['status'] is None
    assert [event['role'] for event in item['events']] == ['H', 'H']
    assert [event['label'] for event in item['events']] == ['Alpha', 'Revised Alpha']
    assert item['events'][0]['text']=='Does the response distinguish the mechanism?'
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
    assert 'Alpha' in rendered and value['mechanisms'][0]['status'] is None
    assert '因显示长度限制未展开' in rendered
    assert '/results/2' in rendered and '/results/' + str(ui_view.WINDOW) in rendered
    assert rendered.count('<article') == rendered.count('</article>')
    assert rendered.count('<details') == rendered.count('</details>')
    assert rendered.endswith('</details>')


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








@pytest.mark.parametrize('role,field', [('H', 'search_plan'), ('A', 'search_review')])
def test_unparsed_h_or_a_fields_preserve_other_stage_records(role, field):
    reader = Reader({1: h(commitment()), 2: node(role, {field: 'Narrative in an auxiliary field'}),
        3: a('deferred', 'Keep this mechanism open.')})
    value = trail.project(reader, [1, 2, 3], clean)
    assert not value['errors'] and len(value['unresolved']) == (1 if role=='H' else 0)
    assert [event['role'] for event in value['mechanisms'][0]['events']] == ['H']
    assert value['mechanisms'][0]['reason'] is None
    assert ('尚未解析为结构化信息' in trail.render(value)) is (role=='H')


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
    assert not projected['errors'] and bool(projected['unresolved']) is (role!='A')
    item,=projected['mechanisms']
    assert item['label']=='Alpha' and item['status'] is None and item['reason'] is None
    assert [event['role'] for event in item['events']]==['H']
    assert ('尚未解析为结构化信息' in trail.render(projected)) is (role!='A')


def test_w_sealed_declarations_keep_true_producer_and_next_w_evidence_sources():
    declared=node('W',dict(summary='New competing explanation; not yet executed.',
        search_plan=dict(scope='Retain two authored explanations',commitments=[
            commitment('Revised Alpha',existing_ref='h001.m1'),commitment('Beta')],
            focus=dict(index=2,action='screen'))),observation=dict(managed_calls=[],candidates=[]))
    following=node('W',dict(summary='Bounded analysis of the newly frozen mechanism.',mechanism_findings=[]),
        observation=dict(managed_calls=[],candidates=[]),search=dict(
            focus=dict(mechanism_ref='w003.m2',action='improve'),breadth=dict(mechanisms=[dict(
                mechanism_ref='w003.m2',label='Beta',minimal_test='The retained competing explanation',
                source_step=3,source_role='W')])))
    value=trail.project(Reader({1:h(commitment()),2:a('deferred','Earlier evidence is incomplete.'),
        3:declared,4:following}),[1,2,3,4],clean)
    assert not value['errors'] and not value['unresolved'] and not value['unbound']
    alpha,beta=value['mechanisms']
    assert alpha['ref']=='h001.m1' and alpha['label']=='Revised Alpha'
    assert alpha['status'] is None and alpha['reason'] is None
    assert alpha['source_step']==3 and alpha['source_role']=='W'
    assert [row['role'] for row in alpha['events']]==['H','W']
    assert beta['ref']=='w003.m2' and beta['source_role']=='W'
    assert beta['events'][0]['kind']=='declaration' and beta['events'][0]['step']==3
    assert beta['events'][1]['source_authors']==[dict(source_step=3,source_role='W')]
    assert beta['events'][1]['calls'] is None
    rendered=trail.render(value)
    assert '关联机制声明' in rendered and 'W · 第 3 阶段' in rendered
    assert '关联 H 声明' not in rendered and '/nodes/3/science' in rendered
    assert '已接受的受管调用' not in rendered


@pytest.mark.parametrize('review',[
    dict(dispositions=[dict(mechanism_ref='h001.m1',status='waived',reason='Consultation advice only.')],next_focus=None),
    'Unstructured consultation evidence',
])
def test_a_consultation_never_adopts_advice_or_adds_unknown_review(review):
    consultation=node('A',dict(summary='Optional evidence consultation',search_review=review),
        purpose='worker_consultation')
    value=trail.project(Reader({1:h(commitment()),2:a('deferred','The formal evidence judgment remains open.'),
        3:consultation}),[1,2,3],clean)
    assert not value['errors'] and not value['unresolved']
    item,=value['mechanisms']
    assert item['status'] is None and item['reason'] is None
    assert [event['step'] for event in item['events']]==[1]
    assert 'Consultation advice only.' not in trail.render(value)


@pytest.mark.parametrize('status',['pending','screened','deferred','waived'])
def test_a_advice_remains_in_exact_original_without_changing_w_source_or_preview(status):
    declared=node('W',dict(search_plan=dict(commitments=[commitment('W explanation')],
        focus=dict(index=1,action='improve'))),observation=dict(managed_calls=[],candidates=[]))
    evidence=node('W',dict(summary='A bounded W observation.',mechanism_findings=[dict(
        mechanism='w001.m1',basis='observed',finding='Only the retained authored observation.')]),
        observation=dict(managed_calls=[],candidates=[]),search=dict(breadth=dict(mechanisms=[dict(
            mechanism_ref='w001.m1',label='W explanation',minimal_test='A discriminating question.',
            source_step=1,source_role='W')])))
    advice=node('A',dict(search_review=dict(dispositions=[dict(mechanism_ref='w001.m1',
        status=status,reason='High-level advice; no adopted mechanism disposition.')],next_focus=None)))
    reader=Reader({1:declared,2:evidence,3:advice})
    originals=deepcopy(reader.nodes)
    before=trail.project(reader,[1,2],clean)
    after=trail.project(reader,[1,2,3],clean)
    assert after['mechanisms']==before['mechanisms'] and not after['unresolved']
    item,=after['mechanisms']
    assert (item['ref'],item['source_step'],item['source_role'])==('w001.m1',1,'W')
    assert item['events'][1]['source_authors']==[dict(source_step=1,source_role='W')]
    assert trail.render(after)==trail.render(before)
    assert reader.nodes==originals and reader.node(3)['payload']==advice['payload']
