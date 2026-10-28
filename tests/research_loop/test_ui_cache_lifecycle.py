"""Bounded display-cache lifecycle tests; no native runner or real instance."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading

import pytest

from scidiscovery.research_loop import budget, contracts as c, ui_errors, ui_mechanisms, ui_saved_status, ui_view
from scidiscovery.research_loop.instance_ui import InstanceUI


def cache_host(root):
    host = InstanceUI.__new__(InstanceUI)
    host.root = root
    host.cache_lock = threading.RLock()
    for name in ('view_memo', 'status_memo', 'candidate_cache', 'history_pages'):
        setattr(host, name, ui_view.DisplayMemo(lock=host.cache_lock))
    return host


def synthetic_saved(monkeypatch, root, count):
    """At most 259 in-memory stage records and five synthetic stat paths each."""
    versions = {step: 1 for step in range(1, count + 1)}
    nodes = {step: dict(job=dict(role='W'), payload=dict(summary=f'Stage {step}'),
        seal=dict(materials=[])) for step in versions}
    observations = {step: dict(candidates=[dict(id=f'candidate-{step}', label=f'Candidate {step}')])
        for step in versions}
    reads = []

    def saved(root, step):
        if versions[step] is None:
            raise FileNotFoundError('Missing fixture source')
        return root, f'native-evidence/{step:03d}', nodes[step]['seal'], root / f'raw-{step}.json'

    def fingerprint(paths):
        step = int(paths[1].stem)
        if versions[step] is None:
            raise FileNotFoundError('Missing fixture source')
        return tuple((str(path), versions[step]) for path in paths)

    def node(root, step):
        reads.append(step)
        return copy.deepcopy(nodes[step])

    monkeypatch.setattr(ui_view.display, '_saved', saved)
    monkeypatch.setattr(ui_view.display, 'node', node)
    monkeypatch.setattr(ui_view.display, 'observation', lambda root, step, **options: copy.deepcopy(observations[step]))
    monkeypatch.setattr(ui_view.Reader, '_fingerprint', staticmethod(fingerprint))
    return versions, nodes, observations, reads


def test_overview_past_node_capacity_reuses_history_and_retains_old_a_choice(tmp_path, monkeypatch):
    versions, nodes, observations, reads = synthetic_saved(monkeypatch, tmp_path, 259)
    visible = list(range(1, 258))
    nodes[1].update(job=dict(role='A'), payload=dict(next_action='continue_w', candidate_recommendation='candidate-2'))
    observations[1] = None
    monkeypatch.setattr(ui_saved_status, 'visible_steps', lambda root: list(visible))
    monkeypatch.setattr(ui_view, 'cleaner', lambda root: lambda text, limit=16384: str(text)[:limit])
    monkeypatch.setattr(ui_view, 'resolve_candidate', lambda root, job, value, references=None, **options: value)
    monkeypatch.setattr(ui_view, 'rounds', lambda *args, **options: dict(rounds=[]))
    monkeypatch.setattr(ui_mechanisms, 'project', lambda *args, **options: dict(mechanisms=[]))
    memo = ui_view.DisplayMemo()

    def project():
        shown = [dict(step=step, role='W', state='seal_present_unverified') for step in visible[-50:]]
        return ui_view.project(tmp_path, dict(nodes=shown), memo=memo)

    first = project()
    assert first['candidates']['count'] == 256 and first['recommended'] == 'candidate-2'
    assert len(reads) == 257 and len(memo) <= 256
    assert len(memo.summaries) == 257
    reads.clear()
    assert project()['candidates'] == first['candidates']
    assert reads == []
    # Old role numbering also uses compact facts after large-node eviction.
    assert ui_view.Reader(tmp_path, memo).role(1) == 'A' and reads == []

    visible.append(258)
    appended = project()
    assert appended['candidates']['count'] == 257 and appended['recommended'] == 'candidate-2'
    assert reads == [258]
    reads.clear()
    versions[2] += 1
    observations[2]['candidates'].append(dict(id='replacement-candidate', label='Replacement'))
    replaced = project()
    assert replaced['candidates']['count'] == 258 and reads == [2]
    reads.clear()
    assert project()['candidates'] == replaced['candidates'] and reads == []

    # A new explicit absence of a recommendation supersedes the old choice.
    visible.append(259)
    nodes[259].update(job=dict(role='A'), payload=dict(next_action='continue_w', candidate_recommendation=None))
    observations[259] = None
    assert project()['recommended'] is None
    # A removed old source is a visible gap, never a cached complete count.
    seal = tmp_path / 'native-evidence/002/result-identity.json'
    seal.parent.mkdir(parents=True)
    seal.write_text('{}')
    versions[2] = None
    missing = project()['candidates']
    assert missing['partial'] and missing['unreadable'] == 1 and missing['count'] == 256
    assert (str(tmp_path), 2) not in memo.summaries
    memo.clear()
    assert not memo and not memo.summaries


@pytest.mark.parametrize('changed', ['observation', 'materials'])
def test_a_implementation_choice_tracks_its_exact_source(tmp_path, monkeypatch, changed):
    """Three memory records; only the referenced W metadata invalidates A."""
    versions, nodes, observations, reads = synthetic_saved(monkeypatch, tmp_path, 3)
    nodes[2]['seal']['materials'] = [dict(path='inputs/parents/002/artifacts/model.py')]
    observations[2] = dict(candidates=[dict(id='original-candidate', label='Original',
        implementation='history/nodes/002/artifacts/model.py')])
    nodes[3].update(job=dict(role='A', worker_view_version=1, history=dict(nodes=[dict(step=2)])),
        payload=dict(next_action='continue_w', candidate_recommendation='W002-I001'))
    observations[3] = None
    monkeypatch.setattr(ui_saved_status, 'visible_steps', lambda root: [2, 3])
    monkeypatch.setattr(ui_view, 'cleaner', lambda root: lambda text, limit=16384: str(text)[:limit])
    monkeypatch.setattr(ui_view, 'rounds', lambda *args, **options: dict(rounds=[]))
    monkeypatch.setattr(ui_mechanisms, 'project', lambda *args, **options: dict(mechanisms=[]))
    memo = ui_view.DisplayMemo()
    def project():
        return ui_view.project(tmp_path, dict(nodes=[dict(step=3, role='A', state='seal_present_unverified')]), memo=memo)

    assert project()['recommended'] == 'original-candidate'
    assert len(memo.summaries[(str(tmp_path), 3)]['dependencies']) == 1
    reads.clear()
    assert project()['recommended'] == 'original-candidate' and reads == []
    if changed == 'observation':
        observations[2]['candidates'][0]['id'] = 'replacement-candidate'
        expected = 'replacement-candidate'
    else:
        # I001 now names the newly sorted first material, which has no observed
        # candidate binding. Keep the raw author reference, never the old ID.
        nodes[2]['seal']['materials'].append(dict(path='inputs/parents/002/artifacts/first.py'))
        expected = 'W002-I001'
    versions[2] += 1
    assert project()['recommended'] == expected and reads == [2]
    reads.clear()
    assert project()['recommended'] == expected and reads == []
    versions[2] = None
    assert project()['recommended'] is None
    assert (str(tmp_path), 3) not in memo.summaries
    versions[2] = 9
    assert project()['recommended'] == expected


@pytest.mark.parametrize('failure', ['missing', 'value_error', 'json_error'])
def test_later_a_missing_choice_source_never_revives_earlier_recommendation(tmp_path, monkeypatch, failure):
    versions, nodes, observations, _ = synthetic_saved(monkeypatch, tmp_path, 3)
    nodes[1].update(job=dict(role='A'),
        payload=dict(next_action='continue_w', candidate_recommendation='earlier-candidate'))
    observations[1] = None
    nodes[2]['seal']['materials'] = [dict(path='inputs/parents/002/artifacts/model.py')]
    observations[2] = dict(candidates=[dict(id='later-candidate', label='Later',
        implementation='history/nodes/002/artifacts/model.py')])
    nodes[3].update(job=dict(role='A', worker_view_version=1, history=dict(nodes=[dict(step=2)])),
        payload=dict(next_action='continue_w', candidate_recommendation='W002-I001'))
    observations[3] = None
    monkeypatch.setattr(ui_saved_status, 'visible_steps', lambda root: [1, 2, 3])
    monkeypatch.setattr(ui_view, 'cleaner', lambda root: lambda text, limit=16384: str(text)[:limit])
    monkeypatch.setattr(ui_view, 'rounds', lambda *args, **options: dict(rounds=[]))
    monkeypatch.setattr(ui_mechanisms, 'project', lambda *args, **options: dict(mechanisms=[]))
    memo = ui_view.DisplayMemo()
    def project():
        return ui_view.project(tmp_path, dict(nodes=[dict(step=3, role='A', state='seal_present_unverified')]), memo=memo)

    assert project()['recommended'] == 'later-candidate'
    original_node = ui_view.display.node
    if failure == 'missing':
        versions[2] = None
    else:
        versions[2] += 1
        def unreadable(root, step):
            if step == 2:
                if failure == 'json_error':
                    raise json.JSONDecodeError('Unreadable saved source', '{', 1)
                raise ValueError('Invalid saved source payload')
            return original_node(root, step)
        monkeypatch.setattr(ui_view.display, 'node', unreadable)
    missing = project()
    assert missing['recommended'] is None
    assert missing['candidates']['recommendation_gap'] == dict(step=3)
    card = ui_view.usage_stats({}, candidates=missing['candidates'])['candidates']
    assert '当前 A 推荐的来源不可读，推荐暂不可核对' in card['notes']
    assert (str(tmp_path), 3) not in memo.summaries
    if failure != 'missing':
        # Existing non-summary display callers keep tolerant raw references.
        assert ui_view.resolve_candidate(tmp_path, nodes[3]['job'], 'W002-I001') == 'W002-I001'
    monkeypatch.setattr(ui_view.display, 'node', original_node)
    versions[2] = 9
    observations[2]['candidates'][0]['id'] = 'restored-candidate'
    restored = project()
    assert restored['recommended'] == 'restored-candidate'
    assert 'recommendation_gap' not in restored['candidates']
    # Audit requests do not replace the earlier ordinary A's choice.
    versions[2] = None
    versions[3] += 1
    nodes[3]['payload']['next_action'] = 'audit'
    audit = project()
    assert audit['recommended'] == 'earlier-candidate'
    assert 'recommendation_gap' not in audit['candidates']
    # An explicit later None replaces the old choice without a source gap.
    versions[3] += 1
    nodes[3]['payload'].update(next_action='continue_w', candidate_recommendation=None)
    absent = project()
    assert absent['recommended'] is None and 'recommendation_gap' not in absent['candidates']


def assert_clear_waits(host, load, entered, release):
    """A clear cannot interleave between cache check, load and publication."""
    started = threading.Event()
    def clear():
        started.set()
        host._clear_display_caches()
    with ThreadPoolExecutor(max_workers=2) as pool:
        loading = pool.submit(load)
        clearing = None
        try:
            assert entered.wait(2)
            acquired = host.cache_lock.acquire(blocking=False)
            if acquired:
                host.cache_lock.release()
            assert not acquired, 'Cache load must own the shared transaction lock'
            clearing = pool.submit(clear)
            assert started.wait(2)
            assert not clearing.done()
        finally:
            release.set()
        value = loading.result(timeout=2)
        if clearing is not None:
            clearing.result(timeout=2)
    assert all(not getattr(host, name) for name in ('view_memo', 'status_memo', 'candidate_cache', 'history_pages'))
    assert not host.view_memo.summaries
    return value


@pytest.mark.parametrize('kind', ['sealed', 'status', 'candidate'])
def test_cache_clear_serializes_with_read_and_eviction(tmp_path, monkeypatch, kind):
    host = cache_host(tmp_path)
    entered, release = threading.Event(), threading.Event()
    if kind == 'sealed':
        synthetic_saved(monkeypatch, tmp_path, 1)
        original = ui_view.display.node
        def blocked(root, step):
            entered.set()
            assert release.wait(2)
            return original(root, step)
        monkeypatch.setattr(ui_view.display, 'node', blocked)
        host.view_memo.update({('filler', step): {} for step in range(256)})
        load = lambda: ui_view.Reader(tmp_path, host.view_memo).summary(1)
    elif kind == 'status':
        path = tmp_path / 'usage.json'
        path.write_text('{"input_tokens": 7}')
        original = c.read
        def blocked(path):
            entered.set()
            assert release.wait(2)
            return original(path)
        monkeypatch.setattr(c, 'read', blocked)
        host.status_memo.update({str(step): ((), {}) for step in range(1024)})
        load = lambda: ui_saved_status._saved_read(path, host.status_memo)
    else:
        def blocked(*args, **options):
            entered.set()
            assert release.wait(2)
            return dict(index=[dict(id='candidate-1')])
        monkeypatch.setattr(ui_view, 'candidate_projection', blocked)
        host.candidate_cache.update({str(step): {} for step in range(8)})
        load = lambda: host._candidates(1, job=dict(history=dict(nodes=[])))
    assert assert_clear_waits(host, load, entered, release)


def test_saved_record_changed_during_read_is_not_cached(tmp_path, monkeypatch):
    path = tmp_path / 'usage.json'
    path.write_text('{"input_tokens": 1}')
    memo = ui_view.DisplayMemo()
    original = c.read
    def replacing(path):
        value = original(path)
        path.write_text('{"input_tokens": 222}')
        return value
    monkeypatch.setattr(c, 'read', replacing)
    assert ui_saved_status._saved_read(path, memo) == dict(input_tokens=1)
    assert not memo
    monkeypatch.setattr(c, 'read', original)
    assert ui_saved_status._saved_read(path, memo) == dict(input_tokens=222)
    path.unlink()
    assert ui_saved_status._saved_read(path, memo) is None and not memo


def test_history_page_keeps_frozen_prefix_and_clear_expires_it(tmp_path, monkeypatch):
    host = cache_host(tmp_path)
    projection = dict(index=[dict(id=f'candidate-{step}') for step in range(1, 27)], unresolved=[])
    host.history_pages['frozen'] = dict(through=257, missing=[2], projection=projection)
    host._navigation = lambda: ''
    host._page = lambda title, body, **options: body.encode()
    def forbidden(*args, **options):
        pytest.fail('Frozen pagination must not reread or expand the source prefix')
    host._sealed_steps = forbidden
    monkeypatch.setattr(ui_view, 'cleaner', lambda root: lambda text, limit=16384: text)
    displayed = []
    def rows(value, **options):
        displayed.extend(row['id'] for row in value['index'])
        return {}
    monkeypatch.setattr(ui_view, 'candidate_rows', rows)
    monkeypatch.setattr('scidiscovery.research_loop.instance_ui.ui.candidate_table', lambda value: '')
    status, body, _ = host._candidate_history(through=257, prefix='frozen', after=20)
    assert status == 200 and displayed == [f'candidate-{step}' for step in range(21, 27)]
    assert '已观察 26 个候选' in body.decode() and '缺口阶段：2' in body.decode()
    assert len(host.history_pages['frozen']['projection']['index']) == 26
    with pytest.raises(ui_errors.InputError):
        host._candidate_history(through=258, prefix='frozen', after=20)
    host._clear_display_caches()
    with pytest.raises(ui_errors.InputError):
        host._candidate_history(through=257, prefix='frozen', after=20)


@pytest.mark.parametrize('basis, now, expected', [
    ('active', dict(at_unix=112., at_boottime=1., boot_id='new'), 6.),
    ('active', dict(at_unix=90., at_boottime=1., boot_id='new'), 6.),
    ('active', dict(at_unix=105., at_boottime=1., boot_id='new'), 6.),
    ('active', dict(at_unix=90., at_boottime=20., boot_id='original'), 10.),
    ('elapsed', dict(at_unix=112., at_boottime=1., boot_id='new'), 12.),
    ('elapsed', dict(at_unix=112., at_boottime=20., boot_id='original'), 12.),
    ('active', dict(at_unix=112., at_boottime=14., boot_id='original'), None),
    ('elapsed', dict(at_unix=90., at_boottime=1., boot_id='new'), None),
    ('elapsed', dict(at_unix=109., at_boottime=20., boot_id='original'), None),
])
def test_saved_budget_uses_control_clock_intervals(tmp_path, monkeypatch, basis, now, expected):
    """One start file and two in-memory clock facts; no admission fixture."""
    settings = dict(max_tokens=100, max_seconds=1000, max_steps=5, time_budget_basis=basis)
    start = dict(at_unix=100., at_boottime=10., boot_id='original')
    c.write(tmp_path / 'host-started.json', start)
    events = [dict(kind='clock', phase='advance', **start),
        dict(kind='clock', phase='waiting', at_unix=105., at_boottime=15., boot_id='original')]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(now))
    original, calls = budget._duration, []
    def counted(before, after, **options):
        calls.append(options)
        return original(before, after, **options)
    monkeypatch.setattr(budget, '_duration', counted)
    value = ui_saved_status._budget(tmp_path, settings, events)
    assert calls and all(set(options) == {'legacy_elapsed', 'exclude_downtime'}
        and options['legacy_elapsed'] == (basis == 'elapsed') for options in calls)
    assert sum(options['exclude_downtime'] is True for options in calls) == (3 if basis == 'active' else 0)
    if expected is None:
        assert value['time_status'] == 'unknown'
        assert value['consumed_seconds'] is None and value['seconds_remaining'] is None
        with pytest.raises(ValueError):
            budget._snapshot(tmp_path, settings, events, sample_stamp=now)
    else:
        authority = budget._snapshot(tmp_path, settings, events, sample_stamp=now)
        assert value['time_status'] == 'recorded' and value['consumed_seconds'] == expected
        assert value['advance_seconds'] == 5.
        assert value['waiting_seconds'] == (expected - 5. if now['boot_id'] == 'new' else 5.)
        assert value['seconds_remaining'] == 1000. - expected
        for key in ('consumed_seconds', 'seconds_remaining', 'advance_seconds', 'waiting_seconds'):
            assert value[key] == authority[key]


@pytest.mark.parametrize('change, missing', [
    ({}, 'at_unix'), ({}, 'at_boottime'), ({}, 'boot_id'),
    (dict(at_unix=float('nan')), None), (dict(at_boottime=float('inf')), None),
    (dict(at_boottime=True), None), (dict(at_boottime=-1), None), (dict(boot_id=''), None),
])
def test_saved_budget_keeps_unreadable_clock_values_unknown(tmp_path, monkeypatch, change, missing):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='active')
    start = dict(at_unix=100., at_boottime=10., boot_id='original')
    c.write(tmp_path / 'host-started.json', start)
    now = dict(at_unix=112., at_boottime=1., boot_id='new')
    now.update(change)
    if missing is not None:
        now.pop(missing)
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(now))
    value = ui_saved_status._budget(tmp_path, settings, [dict(kind='clock', phase='advance', **start)])
    assert value['time_status'] == 'unknown'
    assert value['consumed_seconds'] is None and value['seconds_remaining'] is None
