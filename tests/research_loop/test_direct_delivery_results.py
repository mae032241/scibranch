"""Saved ordinary A delivery results need no extra FINAL or delivery call."""
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import contracts as c, delivery, native, ui_saved_status
from test_execution_authority import prepare
from test_instance_ui import session
from test_instance_science import read


def saved_scene(tmp_path, monkeypatch, *, state='confirmed', closeout=True):
    root, key = prepare(tmp_path, enabled=False)
    candidate = 'local-002-0004-retained-direct-019'
    call = '0004-retained-direct'
    filename = 'native/model_run1.pkl'
    name = f'experiments/{call}/artifacts/{filename}'
    original = root / 'project/nodes/002/work/retained-experiments' / call / 'outputs' / filename
    original.parent.mkdir(parents=True, exist_ok=True)
    content = (b'Exact saved direct delivery original.\x00' * 20000)[:463404]
    original.write_bytes(content)
    unrelated = root / 'project/nodes/002/artifacts/unrelated.txt'
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b'An unrelated sealed member.')
    artifact = dict(path=str(original), size_bytes=len(content))
    materials = [dict(path='inputs/parents/002/artifacts/unrelated.txt',
        source=str(unrelated), size_bytes=unrelated.stat().st_size),
        dict(path='inputs/parents/002/artifacts/' + name, source=str(original), size_bytes=len(content))]
    observation = dict(candidates=[], contexts=[], managed_calls=[dict(call_id=call,
        scope=dict(step=2), outputs=[dict(name=filename, path=original.relative_to(root).as_posix(),
            snapshot=original.relative_to(root).as_posix(), retention='archived', size_bytes=len(content))])])
    observation_path = root / 'host-history/observations/002.json'
    c.write(observation_path, observation)
    paths = [root / 'settings.json', original, unrelated, observation_path]
    nodes = {}

    def node(step, role, *, action=None, summary='Saved bounded research result.', **job_fields):
        job = dict(step=step, role=role, direct_refs=[],
            history=dict(nodes=[dict(step=number) for number in (1, 2)] if step >= 3 else []), **job_fields)
        payload = dict(summary=summary, scientific_result='Retain the exact observation and its scientific gaps.',
            limitations=['Missing scientific evidence remains unresolved.'])
        if action is not None:
            payload.update(next_action=action, route_reason='Display the existing author decision.')
        normalized = dict(payload)
        if role == 'A':
            payload.update(task_complete=False, candidate_recommendation='W002-C004/artifacts/' + filename)
            normalized.update(payload, candidate_recommendation=candidate)
        seal = dict(materials=materials if step == 2 else [])
        if step == 2:
            seal['observations'] = dict(path='host-history/observations/002.json')
        record = root / 'native-evidence' / f'{step:03d}'
        documents = [(root / 'jobs' / f'{step:03d}.json', job),
            (record / 'science-output.json', payload), (record / 'result.json', normalized),
            (record / 'result-identity.json', seal)]
        for path, value in documents:
            c.write(path, value)
            paths.append(path)
        nodes[step] = (job, payload, seal)

    node(1, 'H', action='experiment')
    node(2, 'W', action='analyze')
    node(3, 'A', action='deliver')
    projection = dict(index=[dict(id=candidate, label='Retained direct candidate', observation_step=2,
        evaluation_key='retained fixture', metric=1, direction='max', status='ok',
        implementation='history/nodes/002/artifacts/' + name)],
        references={candidate: 'W002-C004/artifacts/' + filename},
        graph=dict(mechanisms={}, candidates={}), nominated_ids=[candidate],
        records=[dict(step=2, role='W', observation=observation)],
        sources=[(*nodes[2], None)], subject=nodes[3][0], recommended=candidate)

    def event(number, kind, **fields):
        path = root / 'control-ledger' / f'{number:06d}.json'
        c.write(path, dict(seq=number, kind=kind, **fields))
        paths.append(path)

    if closeout:
        event(1, 'closeout_begin', mode='model', source_cutoff=3, first_step=4, authorization_step=3)
        operation = 'a' * 32
        binding = dict(candidate_id=candidate)
        confirmed = dict(status='confirmed', candidate_id=candidate, artifact_ref=artifact)
        event(2, 'action_intent', action_id='action-000002', action_kind='submit',
            operation_id=operation, decision=dict(step=3), binding=binding)
        event(3, 'action_outcome', action_id='action-000002',
            result=dict(confirmed, status='unknown' if state == 'unknown' else 'confirmed'))
        if state != 'pending':
            event(4, 'action_intent', action_id='action-000004', action_kind='confirm',
                operation_id=operation, decision=dict(step=3), binding=binding)
            event(5, 'action_outcome', action_id='action-000004',
                result=dict(confirmed, status='unknown' if state == 'unknown' else 'confirmed'))
    ui, cookie = session(root, key)
    # Candidate projection is an existing saved display input.  This regression
    # exercises result selection and the real exact-member download chain.
    monkeypatch.setattr(ui, '_candidates', lambda *args, **options: projection)
    return dict(root=root, ui=ui, cookie=cookie, node=node, event=event, paths=paths,
        original=original, content=content)


@pytest.mark.parametrize('state,label', [
    ('confirmed', '已确认交付'), ('unknown', '交付状态未知'), ('pending', '交付状态未知'),
])
def test_direct_delivery_result_uses_exact_saved_author_and_candidate_original(tmp_path, monkeypatch, state, label):
    scene = saved_scene(tmp_path, monkeypatch, state=state)
    root, ui, cookie = scene['root'], scene['ui'], scene['cookie']
    before = {path: path.read_bytes() for path in scene['paths']}

    def forbidden(*args, **options):
        pytest.fail('Viewing a saved delivery must not execute, requalify or hash originals')

    monkeypatch.setattr(delivery, 'invoke', forbidden)
    monkeypatch.setattr(native, 'execute_native', forbidden)
    monkeypatch.setattr(c, 'validate', forbidden)
    calls = []
    load_closeout = ui_saved_status.closeout

    def counted(*args, **options):
        calls.append(1)
        return load_closeout(*args, **options)

    monkeypatch.setattr(ui_saved_status, 'closeout', counted)
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    status, body, _ = ui.get('/final-results', host=ui.host, cookie=headers['Set-Cookie'])
    page = body.decode()
    assert status == 200 and calls == [1]
    assert '最终结果 · 第 3 阶段' in page and 'A · 综合评估' in page and label in page
    assert '未声称完成目标' in page and 'Missing scientific evidence remains unresolved.' in page
    assert '查看完整原文（第 3 阶段）' in page and '/nodes/3/science' in page
    assert 'Retained direct candidate' in page and '/nodes/2/materials/2' in page
    assert ('下载交付文件：model_run1.pkl' in page) is (state == 'confirmed')
    assert '最终综合结论尚未返回' not in page and 'action="/control/' not in page
    _, original, headers = ui.get('/nodes/2/materials/2', host=ui.host, cookie=cookie)
    assert headers['Content-Type'] == 'application/octet-stream'
    assert read(original) == scene['content']
    assert not (root / 'jobs/004.json').exists()
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize('state,expected', [('confirmed', 3), ('unknown', None), ('pending', None)])
def test_snapshots_reuse_saved_result_without_completing_unconfirmed_delivery(tmp_path, monkeypatch, state, expected):
    scene = saved_scene(tmp_path, monkeypatch, state=state)
    ui = scene['ui']

    def forbidden(*args, **options):
        pytest.fail('Snapshots already own the saved result source')

    monkeypatch.setattr(ui, '_final_step', forbidden)
    monkeypatch.setattr(ui, '_result_source', forbidden)
    for snapshot in (ui._snapshot(force=True), ui._summary_snapshot(force=True)):
        assert snapshot['view']['result_step'] == 3
        assert snapshot['final'] == expected
        assert ui._presentation(snapshot, {})['completed'] is (state == 'confirmed')


@pytest.mark.parametrize('change', ['new_final_cycle', 'new_model_cycle', 'resume'])
def test_later_cycle_or_resume_does_not_revive_an_old_delivery_author(tmp_path, monkeypatch, change):
    scene = saved_scene(tmp_path, monkeypatch)
    if change == 'resume':
        scene['event'](6, 'closeout_resume')
    else:
        scene['event'](6, 'closeout_begin', mode='final' if change == 'new_final_cycle' else 'model',
            source_cutoff=3, first_step=4)
    ui = scene['ui']
    page = ui.get('/final-results', host=ui.host, cookie=scene['cookie'])[1].decode()
    assert '最终综合结论尚未返回' in page and 'Retained direct candidate' not in page
    assert ui._final_step() is None


@pytest.mark.parametrize('action,expected', [('finish', 3), ('continue_w', None), ('deliver', None)])
def test_ordinary_terminal_selection_still_requires_finish(tmp_path, monkeypatch, action, expected):
    scene = saved_scene(tmp_path, monkeypatch, closeout=False)
    scene['node'](3, 'A', action=action)
    assert scene['ui']._final_step() == expected


@pytest.mark.parametrize('mode', ['model', 'final'])
def test_active_pending_cycle_does_not_reuse_an_earlier_ordinary_finish(tmp_path, monkeypatch, mode):
    scene = saved_scene(tmp_path, monkeypatch, closeout=False)
    scene['node'](3, 'A', action='finish')
    scene['event'](1, 'closeout_begin', mode=mode, source_cutoff=3, first_step=4)
    ui = scene['ui']
    assert ui._final_step() is None
    page = ui.get('/final-results', host=ui.host, cookie=scene['cookie'])[1].decode()
    assert '最终综合结论尚未返回' in page
    assert ui._snapshot(force=True)['final'] is None
    assert ui._summary_snapshot(force=True)['final'] is None


@pytest.mark.parametrize('final_fields', [
    dict(purpose='closeout'), dict(final_report=dict(source_cutoff=2)), dict(final_only=True),
])
def test_resumed_final_finish_does_not_become_an_ordinary_result(tmp_path, monkeypatch, final_fields):
    scene = saved_scene(tmp_path, monkeypatch, closeout=False)
    scene['node'](3, 'A', action='finish', **final_fields)
    scene['event'](1, 'closeout_begin', mode='final', source_cutoff=2, first_step=3)
    scene['event'](2, 'final_a_return', version=2, step=3)
    scene['event'](3, 'closeout_resume')
    ui = scene['ui']
    assert ui._final_step() is None
    page = ui.get('/final-results', host=ui.host, cookie=scene['cookie'])[1].decode()
    assert '最终综合结论尚未返回' in page
    for snapshot in (ui._snapshot(force=True), ui._summary_snapshot(force=True)):
        assert snapshot['final'] is None
        assert snapshot['conclusion']['ordinary'] is False


def test_export_only_keeps_the_existing_ordinary_finish_result(tmp_path, monkeypatch):
    scene = saved_scene(tmp_path, monkeypatch, closeout=False)
    scene['node'](3, 'A', action='finish')
    scene['event'](1, 'closeout_begin', mode='export_only', source_cutoff=3, first_step=4)
    ui = scene['ui']
    assert ui._final_step() == 3
    page = ui.get('/final-results', host=ui.host, cookie=scene['cookie'])[1].decode()
    assert '最终结果 · 第 3 阶段' in page and '最终综合结论尚未返回' not in page


def test_active_final_result_precedes_a_later_ordinary_finish(tmp_path, monkeypatch):
    scene = saved_scene(tmp_path, monkeypatch)
    scene['event'](6, 'closeout_begin', mode='final', source_cutoff=3, first_step=4)
    scene['node'](4, 'A', action='finish', purpose='closeout', summary='Bounded FINAL result.')
    scene['event'](7, 'final_a_return', version=2, step=4)
    scene['node'](5, 'A', action='finish', summary='Later ordinary finish.')
    ui = scene['ui']
    assert ui._final_step() == 4
    page = ui.get('/final-results', host=ui.host, cookie=scene['cookie'])[1].decode()
    assert '最终结果 · 第 4 阶段' in page and 'Bounded FINAL result.' in page
    assert 'Later ordinary finish.' not in page


@pytest.mark.parametrize('change', ['model', 'resume'])
def test_historical_final_cannot_override_the_current_cycle(tmp_path, monkeypatch, change):
    scene = saved_scene(tmp_path, monkeypatch)
    scene['event'](6, 'closeout_begin', mode='final', source_cutoff=3, first_step=4)
    scene['node'](4, 'A', action='finish', purpose='closeout', summary='Historical FINAL result.')
    scene['event'](7, 'final_a_return', version=1, step=4)
    scene['node'](5, 'A', action='deliver', summary='Current direct author.')
    if change == 'model':
        scene['event'](8, 'closeout_begin', mode='model', source_cutoff=5, first_step=6, authorization_step=5)
    else:
        scene['event'](8, 'closeout_resume')
    ui = scene['ui']
    assert ui._final_step() == (5 if change == 'model' else None)
    page = ui.get('/final-results', host=ui.host, cookie=scene['cookie'])[1].decode()
    assert 'Historical FINAL result.' not in page
    assert ('Current direct author.' in page) is (change == 'model')
