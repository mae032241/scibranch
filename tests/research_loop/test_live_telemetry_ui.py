"""Live telemetry presentation preserves provenance and original records."""
import copy
import json

import pytest

from scidiscovery.research_loop import progress, public_output, ui_view
from test_public_progress import line, message, transport


@pytest.mark.parametrize('timestamps,label,timing', [
    (dict(observed_at=140), '最近一次本地观测上下文', '本地观测于 10 秒前'),
    (dict(reported_at=130), '最近一次上报上下文', '上报于 20 秒前'),
    (dict(reported_at=130, observed_at=140), '最近一次上报上下文',
     '上报于 20 秒前；本地观测于 10 秒前'),
    ({}, '最近一次上报上下文', '上报时间未知'),
    (dict(reported_at=160), '最近一次上报上下文', '上报时间未知'),
    (dict(observed_at=160), '最近一次本地观测上下文', '本地观测时间未知'),
    (dict(reported_at=160, observed_at=140), '最近一次上报上下文',
     '上报时间未知；本地观测于 10 秒前'),
    (dict(reported_at=130, observed_at=160), '最近一次上报上下文',
     '上报于 20 秒前；本地观测时间未知'),
    (dict(reported_at='invalid', observed_at=140), '最近一次本地观测上下文', '本地观测于 10 秒前'),
    (dict(reported_at='invalid', observed_at=float('nan')), '最近一次上报上下文', '上报时间未知'),
    (dict(reported_at=True, observed_at=-1), '最近一次上报上下文', '上报时间未知'),
])
def test_context_time_labels_follow_the_available_source(monkeypatch, timestamps, label, timing):
    monkeypatch.setattr(ui_view.time, 'time', lambda: 150)
    context = dict(status='last_reported', total_tokens=30, model_context_window=100,
                   step=2, attempt=1, **timestamps)
    usage = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    values = {row['label']: row['value'] for row in ui_view.token_details(usage, context)}
    assert values[f'{label}（阶段 2 / attempt 1）'] == f'30 / 100 · 30.0%；{timing}'
    assert values['输入'] == '100' and values['非缓存输入'] == '20'
    if set(timestamps) == {'observed_at'}:
        assert '上报时间未知' not in str(values)
        assert '上报于' not in str(values)


def clean(text, limit=16384):
    return str(text)[:limit]


@pytest.mark.parametrize('meter', [
    dict(input_tokens=0, cached_input_tokens=0, output_tokens=0),
    dict(input_tokens=3, cached_input_tokens=0, output_tokens=0),
    dict(input_tokens=0, cached_input_tokens=3, output_tokens=0),
    dict(input_tokens=0, cached_input_tokens=0, output_tokens=3),
])
def test_usage_projection_keeps_valid_counters_without_mutating_the_input(meter):
    event = dict(type='usage', usage=meter, private='omitted')
    original = copy.deepcopy(event)
    assert public_output.event(event, clean) == dict(type='usage', usage=meter)
    assert event == original


@pytest.mark.parametrize('meter', [
    dict(input_tokens=0, cached_input_tokens=0),
    dict(input_tokens=None, cached_input_tokens=0, output_tokens=0),
    dict(input_tokens=False, cached_input_tokens=0, output_tokens=0),
    dict(input_tokens=0, cached_input_tokens=-1, output_tokens=0),
])
def test_invalid_or_unknown_usage_keeps_the_existing_projection_rejection(meter):
    event = dict(type='usage', usage=meter)
    original = copy.deepcopy(event)
    assert public_output.event(event, clean) is None
    assert event == original


def test_historical_zero_usage_events_are_filtered_without_changing_known_zero_meter(tmp_path):
    root, _, log = transport(tmp_path)
    zero = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    positive = dict(input_tokens=5, cached_input_tokens=3, output_tokens=1)
    cache_only = dict(input_tokens=2, cached_input_tokens=2, output_tokens=0)
    raw = b''.join([
        line(dict(type='thread.started', thread_id='native-session')),
        line(dict(type='turn.completed', usage=zero)),
        line(message('message after zero usage')),
        line(dict(type='item.completed', item=dict(type='mcp_tool_call', tool='experiment'))),
        line(dict(type='turn.completed', usage=positive)),
        line(dict(type='turn.completed', usage=cache_only)),
    ])
    log.write_bytes(raw)
    meter_path = log.parent / 'usage.json'
    meter_bytes = json.dumps(zero).encode()
    meter_path.write_bytes(meter_bytes)

    value = progress.read(root, 1)
    assert value['usage']['coverage'] == 'observed'
    assert {key: value['usage'][key] for key in zero} == zero
    assert value['usage']['noncached_input_tokens'] == 0
    assert all(event['type'] in {'message', 'thinking'} for event in value['events'])
    assert [event['text'] for event in value['events'] if event['type'] == 'message'] == ['message after zero usage']
    assert len(value['events']) == 1
    assert value['bytes_read'] == len(raw)
    resumed = progress.read(root, 1, cursor=value['cursor'])
    assert resumed['events'] == [] and resumed['bytes_read'] == 0
    assert meter_path.read_bytes() == meter_bytes and log.read_bytes() == raw
