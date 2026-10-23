"""Saved UI browsing never replays scientific admission or sealed hashes."""
import json
import re
import pytest
from ui_html import element_texts

from scidiscovery.research_loop import (
    budget, contracts as c, execution, history, instance_control, instance_science,
    qualification, ui_saved_status, ui_view,
)
from scidiscovery.research_loop.instance_ui import InstanceUI
from test_instance_science import read
from test_instance_ui import session
from test_ui_view import nominated_scene
from test_execution_authority import prepare
from test_history_handoff import finish


@pytest.mark.process_e2e
def test_saved_workbench_results_and_downloads_do_not_requalify(tmp_path, monkeypatch):
    root, key = nominated_scene(tmp_path)
    ui, cookie = session(root, key)
    workspace = tmp_path / 'workbench'; workspace.mkdir()
    host = InstanceUI(workspace, secret_file=key, instance_roots=[root], workspace=True)
    host.host = ui.host
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    def forbidden(*args, **options):
        raise AssertionError('Browsing invoked scientific admission or hash validation')
    for module, names in (
        (c, ('settings', 'validate', 'file_ref')),
        (history, ('source_sequence', '_source', 'candidate_materials')),
        (instance_science, ('_sealed', 'science', 'materials', 'artifact')),
        (qualification, ('validate_inputs', 'status', 'validate_outputs')),
        (execution, ('record', 'records', 'reference')),
        (budget, ('_events', 'snapshot', 'closeout_state')),
        (instance_control, ('status', 'operations', 'operation')),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    for target in ('/', '/results', '/nodes/2/materials', '/final-results'):
        status, page, _ = ui.get(target, host=ui.host, cookie=cookie)
        assert status == 200
        assert b'AssertionError' not in page
    for step, nominees in ((4, range(1, 7)), (6, range(7, 13))):
        status, page, _ = ui.get(f'/results/{step}', host=ui.host, cookie=cookie)
        assert status == 200
        page = page.decode()
        assert 'AssertionError' not in page
        table = re.search(r'<section class="candidates.*?</section>', page, re.S).group(0)
        assert element_texts(table, 'span', cls='cand-label') == [
            f'Candidate {number}' for number in [*nominees, 37]]
        href = re.search(r'href="([^"]+)"[^>]*>下载推荐候选的研究文件：model-37.py', page).group(1)
        response = ui.get(href, host=ui.host, cookie=cookie)
        assert read(response[1]) == b'# exact original 37\n'
    projection = ui_view.candidate_projection(root, 4)
    assert len(projection['index']) == 37
    assert [row['step'] for row in projection['records']] == [1, 2, 3]
    assert 'instance-card' in host._loaded_instances()
    original = ui.get('/nodes/4/science', host=ui.host, cookie=cookie)
    assert read(original[1]) == (root / 'native-evidence/004/science-output.json').read_bytes()
    assert before == {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.process_e2e
def test_workspace_cards_do_not_construct_scientific_projection(tmp_path, monkeypatch):
    root, key = nominated_scene(tmp_path)
    workspace = tmp_path / 'workbench'; workspace.mkdir()
    host = InstanceUI(workspace, secret_file=key, instance_roots=[root], workspace=True)
    def forbidden(*args, **options):
        raise AssertionError('Instance cards loaded a full scientific projection')
    monkeypatch.setattr(ui_view, 'project', forbidden)
    monkeypatch.setattr(ui_view, 'candidate_projection', forbidden)
    cards = host._loaded_instances()
    assert 'instance-card' in cards and 'AssertionError' not in cards
    assert '第 6 阶段' in cards and 'Candidate 37' not in cards


def test_saved_final_return_stops_marking_resumed_normal_science_completed(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'A', [1], action='finish')
    finish(root, 3, 'H', [2], action='experiment')
    ui, _ = session(root, key)
    directory = root / 'control-ledger'; directory.mkdir(exist_ok=True)
    events = [dict(kind='closeout_begin', mode='final', first_step=2),
              dict(kind='final_a_return', version=2, step=2)]
    for index, event in enumerate(events, 1):
        (directory / f'{index:06d}.json').write_text(json.dumps(event))
    assert ui._final_step() == 2
    (directory / '000003.json').write_text(json.dumps(dict(kind='closeout_resume')))
    assert ui._final_step() is None
    assert ui_saved_status.final_return(root) is None


def test_saved_final_return_selects_only_the_new_active_final_cycle(tmp_path):
    events = [dict(kind='closeout_begin', mode='final', first_step=2),
              dict(kind='final_a_return', version=2, step=2), dict(kind='closeout_resume'),
              dict(kind='closeout_begin', mode='final', first_step=5)]
    assert ui_saved_status.final_return(tmp_path, events) is None
    current = dict(kind='final_a_return', version=2, step=5)
    assert ui_saved_status.final_return(tmp_path, [*events, current]) == current
    legacy = dict(kind='final_a_return', version=1, step=2)
    assert ui_saved_status.final_return(tmp_path, [legacy, dict(kind='closeout_resume')]) == legacy


@pytest.mark.process_e2e
def test_saved_audit_nodes_do_not_erase_the_prior_scientific_recommendation(tmp_path):
    from test_candidate_audit import audit_fixture
    root = audit_fixture(tmp_path, candidate_count=2)
    finish(root, 3, 'A', [1, 2], action='continue_w', recommendation='run-1')
    finish(root, 4, 'A', [1, 2, 3], action='audit', recommendation='run-2')
    finish(root, 5, 'A', [1, 2, 3, 4], action='audit', recommendation='run-2')
    assert ui_view.candidate_projection(root, 5)['recommended'] == 'run-1'


def test_saved_used_steps_retain_continuations_delivery_and_audit_intents(tmp_path):
    for name in ('native-evidence/002/continuations/001/native-intent.json',
                 'native-evidence/002/candidate-audit/intent.json',
                 'host-delivery/audit-recovery/original-operation/intent.json'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
    events = [dict(kind='action_intent', action_kind='submit'),
              dict(kind='action_outcome'), dict(kind='action_intent', action_kind='confirm')]
    assert ui_saved_status.used_steps(tmp_path, [1, 2, 5], events) == 10


@pytest.mark.parametrize('start, now', [
    (dict(at_unix=100., at_boottime=10., boot_id='before'), dict(at_unix=1000., at_boottime=1., boot_id='after')),
    (dict(at_unix=100., boot_id='before'), dict(at_unix=1000., at_boottime=20., boot_id='before')),
    (dict(at_unix=100., at_boottime=10., boot_id='before'), dict(at_unix=1000., at_boottime=9., boot_id='before')),
])
def test_saved_active_time_keeps_unverifiable_intervals_unknown(tmp_path, monkeypatch, start, now):
    root, _ = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    c.write(root / 'host-started.json', start)
    directory = root / 'control-ledger'; directory.mkdir(exist_ok=True)
    event = directory / '000001.json'
    c.write(event, dict(kind='clock', phase='advance', **start))
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(now))
    originals = {path: path.read_bytes() for path in (root / 'host-started.json', event,
        root / 'jobs/001.json', root / 'native-evidence/001/result-identity.json')}
    view = ui_saved_status.status(root)
    value = view['budget']
    assert value['consumed_seconds'] is None and value['seconds_remaining'] is None
    assert value['time_status'] == 'unknown'
    assert value['max_tokens'] == 4000 and value['max_seconds'] == 60
    assert view['nodes'][0]['state'] == 'seal_present_unverified'
    assert view['usage']['observed_tokens'] == 10
    assert '时间' not in ui_view.exhausted_limits(view, 1)
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_saved_paused_consumption_and_unstarted_time_do_not_sample_now(tmp_path, monkeypatch):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='active')
    def forbidden():
        pytest.fail('Closed or unstarted consumption must not sample the live clock')
    monkeypatch.setattr(budget, 'clock_stamp', forbidden)
    unstarted = ui_saved_status._budget(tmp_path, settings, [])
    assert unstarted['consumed_seconds'] == 0 and unstarted['seconds_remaining'] == 60
    start = dict(at_unix=100., at_boottime=10., boot_id='original')
    c.write(tmp_path / 'host-started.json', start)
    events = [dict(kind='clock', phase='advance', **start),
        dict(kind='clock', phase='waiting', at_unix=102., at_boottime=12., boot_id='original'),
        dict(kind='clock', phase='paused', at_unix=105., at_boottime=15., boot_id='original')]
    paused = ui_saved_status._budget(tmp_path, settings, events)
    assert (paused['advance_seconds'], paused['waiting_seconds'], paused['consumed_seconds']) == (2., 3., 5.)
    assert paused['seconds_remaining'] == 55 and paused['time_status'] == 'recorded'
    assert paused['paused_seconds'] is None and paused['elapsed_seconds'] is None


@pytest.mark.parametrize('now, expected', [(110., 10.), (90., None)])
def test_saved_legacy_elapsed_accepts_only_nonnegative_wall_intervals(tmp_path, monkeypatch, now, expected):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5)
    c.write(tmp_path / 'host-started.json', dict(at_unix=100.))
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(at_unix=now, at_boottime=1., boot_id='current'))
    value = ui_saved_status._budget(tmp_path, settings, [])
    assert value['consumed_seconds'] == expected
    assert value['seconds_remaining'] == (60 - expected if expected is not None else None)


@pytest.mark.parametrize('now, expected', [
    (dict(at_unix=112., at_boottime=20., boot_id='original'), 12.),
    (dict(at_unix=109., at_boottime=20., boot_id='original'), None),
    (dict(at_unix=112., at_boottime=1., boot_id='new'), None),
])
def test_saved_anchored_elapsed_preserves_its_original_clock_rules(tmp_path, monkeypatch, now, expected):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='elapsed')
    c.write(tmp_path / 'host-started.json', dict(at_unix=100., at_boottime=10., boot_id='original'))
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(now))
    value = ui_saved_status._budget(tmp_path, settings, [])
    assert value['consumed_seconds'] == expected
    assert value['seconds_remaining'] == (60 - expected if expected is not None else None)


def test_saved_time_does_not_erase_clock_records_without_a_start(tmp_path, monkeypatch):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='active')
    stamp = dict(at_unix=100., at_boottime=10., boot_id='original')
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    value = ui_saved_status._budget(tmp_path, settings, [dict(kind='clock', phase='advance', **stamp)])
    assert value['consumed_seconds'] is None and value['seconds_remaining'] is None


def test_saved_paused_reanchor_preserves_closed_active_consumption(tmp_path, monkeypatch):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='active')
    start = dict(at_unix=100., at_boottime=10., boot_id='original')
    c.write(tmp_path / 'host-started.json', start)
    events = [dict(kind='clock', phase='advance', **start),
        dict(kind='clock', phase='paused', at_unix=105., at_boottime=15., boot_id='original'),
        dict(kind='clock', phase='paused', at_unix=1000., at_boottime=1., boot_id='new', reanchor={'version': 1}),
        dict(kind='clock', phase='advance', at_unix=1002., at_boottime=3., boot_id='new'),
        dict(kind='clock', phase='paused', at_unix=1005., at_boottime=6., boot_id='new')]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: pytest.fail('Paused active consumption uses only recorded intervals'))
    value = ui_saved_status._budget(tmp_path, settings, events)
    assert value['consumed_seconds'] == 8 and value['seconds_remaining'] == 52
    assert value['paused_seconds'] is None and value['elapsed_seconds'] is None


def test_saved_live_time_checks_the_latest_nonclock_event(tmp_path, monkeypatch):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='active')
    start = dict(at_unix=100., at_boottime=10., boot_id='original')
    c.write(tmp_path / 'host-started.json', start)
    events = [dict(kind='clock', phase='advance', **start),
        dict(kind='budget_amend', after=dict(max_tokens=200, max_seconds=60),
             at_unix=110., at_boottime=20., boot_id='original')]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(at_unix=105., at_boottime=15., boot_id='original'))
    value = ui_saved_status._budget(tmp_path, settings, events)
    assert value['consumed_seconds'] is None and value['seconds_remaining'] is None
    assert value['max_tokens'] == 200


def test_saved_active_time_requires_its_original_start_anchor(tmp_path, monkeypatch):
    settings = dict(max_tokens=100, max_seconds=60, max_steps=5, time_budget_basis='active')
    c.write(tmp_path / 'host-started.json', dict(at_unix=100.))
    events = [dict(kind='clock', phase='advance', at_unix=100., at_boottime=10., boot_id='original')]
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(at_unix=105., at_boottime=15., boot_id='original'))
    value = ui_saved_status._budget(tmp_path, settings, events)
    assert value['consumed_seconds'] is None and value['seconds_remaining'] is None


def test_saved_status_memo_caches_only_terminal_attempts_and_rechecks_changes(tmp_path, monkeypatch):
    root, _ = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    memo, reads = {}, []
    original = c.read
    def counted(path, *args, **kwargs):
        if path.name == 'usage.json': reads.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(c, 'read', counted)
    first = ui_saved_status.status(root, memo=memo)
    ui_saved_status.status(root, memo=memo)
    assert len(reads) == 1
    usage = root / 'native-evidence/001/usage.json'
    usage.chmod(0o600); c.write(usage, dict(input_tokens=10, cached_input_tokens=2, output_tokens=4))
    updated = ui_saved_status.status(root, memo=memo)
    assert len(reads) == 2 and updated['usage']['observed_tokens'] == 14
    assert first['usage']['observed_tokens'] == 10
    c.build_job(root, 2, 'A', [1])
    record = root / 'native-evidence/002'; record.mkdir(exist_ok=True)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/002.json')))
    c.write(record / 'usage.json', dict(input_tokens=1, output_tokens=1))
    ui_saved_status.status(root, memo=memo)
    c.write(record / 'usage.json', dict(input_tokens=3, output_tokens=2))
    assert ui_saved_status.status(root, memo=memo)['usage']['observed_tokens'] == 19
    assert len(memo) <= 1024
