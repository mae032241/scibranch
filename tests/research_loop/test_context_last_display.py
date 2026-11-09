"""UI-observed context history never becomes current control telemetry."""
import copy
from pathlib import Path
import threading

from scidiscovery.research_loop import instance_ui, ui_components, ui_view


def viewer(root='/explicit-instance'):
    host = instance_ui.InstanceUI.__new__(instance_ui.InstanceUI)
    host.root = Path(root)
    host.cache_lock = threading.RLock()
    host.context_display = None
    return host


def sample(*, step=2, attempt=0, tokens=30, **timestamps):
    return dict(status='last_reported', total_tokens=tokens, model_context_window=100,
                current_window=True, step=step, attempt=attempt, **timestamps)


def test_valid_then_unavailable_keeps_only_display_history(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    host = viewer()
    original = sample(observed_at=140, thread_id='private-session')
    host._context_display_value(original, 1)
    current = dict(status='unavailable', step=2, attempt=0)
    untouched = copy.deepcopy(current)
    shown = host._context_display_value(current, 2)
    assert shown['status'] == 'historical'
    assert shown['sample']['total_tokens'] == 30
    assert 'thread_id' not in shown['sample']
    rows = ui_view.token_details({}, current, context_display=shown)
    context = next(row for row in rows if '历史快照' in row['label'])
    assert '最近一次本地观测上下文' in context['label']
    assert '30 / 100 · 30.0%' in context['value']
    assert '本地观测时间 1970-01-01 00:02:20 UTC' in context['value']
    assert '不代表当前窗口或当前占用' in context['value']
    assert '当前上下文状态' in str(rows) and '上报时间未知' not in str(rows)
    assert current == untouched and original['status'] == 'last_reported'
    assert 'token-feature' in ui_components.token_monitor(rows)


def test_never_valid_and_missing_scope_do_not_invent_history():
    host = viewer()
    current = dict(status='unavailable', step=2, attempt=0)
    shown = host._context_display_value(current, 1)
    assert shown == dict(status='unavailable')
    rows = ui_view.token_details({}, current, context_display=shown)
    assert '尚未收到有效上下文快照' in str(rows)
    assert '后端未提供完整遥测或观测异常' not in str(rows)
    host._context_display_value(sample(), 2)
    assert host._context_display_value(dict(status='unavailable'), 3) == dict(status='unavailable')
    assert viewer('/other-instance')._context_display_value(current, 4) == dict(status='unavailable')


def test_new_attempt_and_step_clear_history_without_old_reader_eviction():
    host = viewer()
    host._context_display_value(sample(), 1)
    assert host._context_display_value(dict(status='unavailable', step=2, attempt=1), 2,
                                       producer_scope=True)['status'] == 'unavailable'
    host._context_display_value(sample(attempt=1, tokens=50), 3)
    host._context_display_value(sample(attempt=0, tokens=99), 4)
    assert host._context_display_value(dict(status='unavailable', step=2, attempt=1), 5)['sample']['total_tokens'] == 50
    assert host._context_display_value(dict(status='unavailable', step=3, attempt=0), 6,
                                       producer_scope=True)['status'] == 'unavailable'


def test_nonexistent_progress_attempt_cannot_evict_real_attempt_history(monkeypatch):
    host = viewer()
    unavailable = dict(status='unavailable')
    contexts = iter([sample(tokens=30), unavailable, sample(tokens=50), unavailable])
    reads = []
    def read(root, step, **options):
        reads.append((step, options['attempt']))
        return dict(state='running', stream_state='ready', context=next(contexts), usage={})
    monkeypatch.setattr(instance_ui.progress, 'read', read)
    monkeypatch.setattr(instance_ui.saved_status, 'status',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('unexpected status read')))
    assert host._progress_value(2, 0, None)['context_display']['status'] == 'current'
    retained = copy.deepcopy(host.context_display)
    invalid = host._progress_value(2, 999, None)
    assert invalid['context'] == unavailable
    assert invalid['context_display'] == dict(status='unavailable')
    assert host.context_display == retained
    host._progress_value(2, 0, None)
    shown = host._progress_value(2, 0, None)
    assert shown['context'] == unavailable
    assert shown['context_display']['sample']['total_tokens'] == 50
    assert host.context_display['scope'] == ('/explicit-instance', 2, 0)
    assert reads == [(2, 0), (2, 999), (2, 0), (2, 0)]


def test_out_of_order_reads_and_source_samples_cannot_replace_newer_fact():
    host = viewer()
    host._context_display_value(sample(tokens=60, observed_at=140), 20)
    late = host._context_display_value(sample(tokens=20, observed_at=120), 10)
    assert late['status'] == 'historical' and late['sample']['total_tokens'] == 60
    lagged = host._context_display_value(sample(tokens=25, observed_at=125), 30)
    assert lagged['sample']['total_tokens'] == 60
    assert host._context_display_value(dict(status='unavailable', step=2, attempt=0), 40)['sample']['total_tokens'] == 60


def test_progress_api_retains_unavailable_and_adds_display_only_history(monkeypatch):
    host = viewer()
    current = dict(status='unavailable')
    values = iter([sample(reported_at=130), current])
    reads = []
    def read(root, step, **options):
        reads.append((root, step, options))
        return dict(state='running', stream_state='ready', context=next(values),
                    context_windows=dict(coverage='live', windows_started=len(reads)), usage={})
    monkeypatch.setattr(instance_ui.progress, 'read', read)
    fresh = host._progress_value(2, 0, None)
    stale = host._progress_value(2, 0, None)
    assert fresh['context_display']['status'] == 'current'
    assert stale['context'] == current == dict(status='unavailable')
    assert stale['context_windows']['windows_started'] == 2
    assert stale['context_display']['status'] == 'historical'
    assert '不代表当前窗口或当前占用' in str(stale['token_details'])
    assert len(reads) == 2


def test_shared_overview_snapshot_retains_history_without_changing_source_context(monkeypatch):
    host = viewer()
    host.status_memo = host.view_memo = {}
    host.live_cache = instance_ui.live.SnapshotCache()
    host._latest_step = lambda: 2
    current = dict(status='unavailable', step=2, attempt=0)
    values = iter([sample(observed_at=140), current])
    def status(*args, **kwargs):
        return dict(nodes=[dict(step=2, state='running')], usage=dict(context=next(values)))
    monkeypatch.setattr(instance_ui.saved_status, 'status', status)
    monkeypatch.setattr(instance_ui.saved_status, 'operations', lambda *args, **kwargs: [])
    monkeypatch.setattr(ui_view, 'project', lambda *args, **kwargs: {})
    monkeypatch.setattr(ui_view, 'cleaner', lambda root: lambda value, limit=100: value)
    assert host._snapshot(force=True)['context_display']['status'] == 'current'
    stale = host._snapshot(force=True)
    assert stale['view']['usage']['context'] == current
    assert stale['context_display']['status'] == 'historical'
    rows = ui_view.usage_stats(stale['view'], context_display=stale['context_display'])['tokens']['details']
    assert '历史快照／等待更新' in str(rows)


def test_fresh_reported_and_observed_labels_keep_their_time_source(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    observed = str(ui_view.token_details({}, sample(observed_at=140)))
    reported = str(ui_view.token_details({}, sample(reported_at=130)))
    assert '最近一次本地观测上下文' in observed and '本地观测于 10 秒前' in observed
    assert '上报时间未知' not in observed
    assert '最近一次上报上下文' in reported and '上报于 20 秒前' in reported
