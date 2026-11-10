"""UI-observed context history never becomes current control telemetry."""
import copy
from pathlib import Path
import threading

import pytest

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
    values = {row['label']: row['value'] for row in rows}
    assert values['上下文'] == '30 / 100 · 30.0%'
    assert values['更新时间'] == '1970-01-01 00:02:20 UTC'
    assert current == untouched and original['status'] == 'last_reported'
    assert 'token-feature' in ui_components.token_monitor(rows)


def test_valid_time_survives_timestampless_current_then_unavailable(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    host = viewer()
    original = sample(observed_at=140)
    current = sample(tokens=50)
    unavailable = dict(status='unavailable', step=2, attempt=0)
    originals = copy.deepcopy((original, current, unavailable))
    host._context_display_value(original, 1)
    shown = host._context_display_value(current, 2)
    assert shown == dict(status='current', last_updated_at=140)
    assert 'observed_at' not in host.context_display['sample']
    assert 'reported_at' not in host.context_display['sample']
    for context, shown in ((current, shown),
                           (unavailable, host._context_display_value(unavailable, 3))):
        values = {row['label']: row['value'] for row in
                  ui_view.token_details({}, context, context_display=shown)}
        assert values['上下文'] == '50 / 100 · 50.0%'
        assert values['更新时间'] == '1970-01-01 00:02:20 UTC'
    assert (original, current, unavailable) == originals
    shown['sample']['total_tokens'] = 99
    assert host.context_display['sample']['total_tokens'] == 50


@pytest.mark.parametrize('invalid', [160, float('inf'), float('-inf'), float('nan'), 'invalid', True, -1])
def test_retained_time_uses_valid_reported_fallback(monkeypatch, invalid):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    host = viewer()
    host._context_display_value(sample(observed_at=140), 1)
    current = sample(tokens=50, observed_at=invalid, reported_at=145)
    shown = host._context_display_value(current, 2)
    assert shown == dict(status='current', last_updated_at=145)
    assert host.context_display['sample']['total_tokens'] == 50
    values = {row['label']: row['value'] for row in
              ui_view.token_details({}, current, context_display=shown)}
    assert values['更新时间'] == '1970-01-01 00:02:25 UTC'


def test_invalid_times_never_erase_or_manufacture_last_valid_time(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    host = viewer()
    current = sample(observed_at=160, reported_at=float('inf'))
    shown = host._context_display_value(current, 1)
    assert shown == dict(status='current', last_updated_at=None)
    # A timestamp rejected at observation does not become history as time passes.
    monkeypatch.setattr(ui_view.time, 'time', lambda: 200)
    unavailable = dict(status='unavailable', step=2, attempt=0)
    historical = host._context_display_value(unavailable, 2)
    values = {row['label']: row['value'] for row in
              ui_view.token_details({}, unavailable, context_display=historical)}
    assert values['更新时间'] == '未知'
    host._context_display_value(sample(observed_at=140), 3)
    shown = host._context_display_value(sample(tokens=50, observed_at=float('nan')), 4)
    assert shown == dict(status='current', last_updated_at=140)
    assert host.context_display['sample']['total_tokens'] == 50


def test_timestampless_sample_preserves_source_order_watermark(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    host = viewer()
    host._context_display_value(sample(observed_at=140), 20)
    host._context_display_value(sample(tokens=50), 30)
    older_source = host._context_display_value(sample(tokens=20, observed_at=130), 40)
    assert older_source['status'] == 'historical'
    assert older_source['sample']['total_tokens'] == 50
    assert older_source['last_updated_at'] == 140
    older_read = host._context_display_value(sample(tokens=60, observed_at=145), 25)
    assert older_read['sample']['total_tokens'] == 50
    assert older_read['last_updated_at'] == 140
    assert host._context_display_value(sample(tokens=70, observed_at=145), 50) == dict(
        status='current', last_updated_at=145)


def test_scope_advance_clears_retained_time(monkeypatch):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    host = viewer()
    host._context_display_value(sample(observed_at=140), 1)
    current = sample(attempt=1, tokens=50)
    shown = host._context_display_value(current, 2)
    assert shown == dict(status='current', last_updated_at=None)
    host._context_display_value(sample(observed_at=145), 3)
    assert host.context_display['scope'] == ('/explicit-instance', 2, 1)
    values = {row['label']: row['value'] for row in
              ui_view.token_details({}, current, context_display=shown)}
    assert values['更新时间'] == '未知'
    assert host._context_display_value(dict(status='unavailable', step=3, attempt=0), 4,
                                       producer_scope=True) == dict(status='unavailable')
    assert host.context_display['last_updated_at'] is None
    other = viewer('/other-instance')
    assert other._context_display_value(current, 5)['last_updated_at'] is None


def test_never_valid_and_missing_scope_do_not_invent_history():
    host = viewer()
    current = dict(status='unavailable', step=2, attempt=0)
    shown = host._context_display_value(current, 1)
    assert shown == dict(status='unavailable')
    rows = ui_view.token_details({}, current, context_display=shown)
    values = {row['label']: row['value'] for row in rows}
    assert values['上下文'] == '不可用'
    assert values['更新时间'] == '未知'
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
    assert dict((row['label'], row['value']) for row in stale['token_details'])['更新时间'] == '1970-01-01 00:02:10 UTC'
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
    assert dict((row['label'], row['value']) for row in rows)['更新时间'] == '1970-01-01 00:02:20 UTC'


def test_context_rows_keep_fixed_shape_and_time_independent_values(monkeypatch):
    current = sample(observed_at=140, reported_at=130)
    missing = dict(status='unavailable', step=2, attempt=0)
    historical = dict(status='historical', sample=current, current_status='out_of_order')
    snapshots = []
    for now in (150, 500):
        monkeypatch.setattr(ui_view.time, 'time', lambda: now)
        for context, display, value, updated in (
            (current, None, '30 / 100 · 30.0%', '1970-01-01 00:02:20 UTC'),
            (missing, historical, '30 / 100 · 30.0%', '1970-01-01 00:02:20 UTC'),
            (missing, None, '不可用', '未知'),
            (dict(current, current_window=False), None, '30 / 100 · 30.0%', '1970-01-01 00:02:20 UTC'),
        ):
            rows = ui_view.token_details({}, context, context_display=display)
            assert rows[5:7] == [dict(label='上下文', value=value), dict(label='更新时间', value=updated)]
            assert [row['label'] for row in rows] == ['输入', '缓存输入', '非缓存输入', '输出', '缓存命中率', '上下文', '更新时间', '观测限制']
            assert rows[-1]['value'] == '运行中请求与平台上报延迟未计入；缓存为后端已记录值'
            assert '<div class="token-feature"><dt>上下文</dt>' in ui_components.token_monitor(rows)
            assert '<div class="token-note"><dt>更新时间</dt>' in ui_components.token_monitor(rows)
            snapshots.append(rows)
    assert snapshots[:4] == snapshots[4:]
