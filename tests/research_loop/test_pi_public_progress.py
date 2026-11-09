"""Pi public telemetry omits placeholders without changing native originals."""
import copy

import pytest

from scidiscovery.research_loop.pi_backend import Pi


ZERO = dict(input=0, output=0, cacheRead=0, cacheWrite=0, totalTokens=0)


@pytest.fixture(params=['closed', 'configured'])
def backend(request):
    mode = request.param
    return Pi(dict(native_launcher_argv=['pi'],
        native_options=dict(resource_mode=mode, workspace='read-only', network=True,
            web='live' if mode == 'configured' else 'disabled', subagents=False),
        experiment_mode='external', model='fixture/exact-model',
        reasoning_effort='high', service_tier=None))


def usage_event(kind, usage):
    if kind == 'message_end':
        return dict(type=kind, message=dict(role='assistant', content=[], usage=usage))
    if kind == 'compaction_end':
        return dict(type=kind, result=dict(usage=usage))
    if kind == 'entry_appended':
        return dict(type=kind, entry=dict(type='usage', usage=usage))
    return dict(type=kind, usage=usage)


@pytest.mark.parametrize('kind', ['message_update', 'message_end', 'compaction_end', 'entry_appended'])
def test_zero_usage_placeholders_have_no_public_event(backend, kind):
    value = usage_event(kind, copy.deepcopy(ZERO))
    if kind == 'message_update':
        value['assistantMessageEvent'] = dict(type='thinking_delta', delta='private reasoning')
    before = copy.deepcopy(value)
    assert backend.public_events(value) == []
    assert value == before


@pytest.mark.parametrize('kind', ['message_update', 'message_end', 'compaction_end', 'entry_appended'])
@pytest.mark.parametrize('usage, expected', [
    (dict(input=10, output=2, cacheRead=7, cacheWrite=3, totalTokens=22),
        dict(input_tokens=20, cached_input_tokens=7, output_tokens=2)),
    (dict(input=0, output=1, cacheRead=0, cacheWrite=0),
        dict(input_tokens=0, cached_input_tokens=0, output_tokens=1)),
    (dict(input=0, output=0, cacheRead=7, cacheWrite=0),
        dict(input_tokens=7, cached_input_tokens=7, output_tokens=0)),
])
def test_positive_usage_keeps_native_conversion_and_cache_fields(backend, kind, usage, expected):
    value = usage_event(kind, copy.deepcopy(usage))
    before = copy.deepcopy(value)
    assert backend.public_events(value) == [dict(type='usage', usage=expected)]
    assert value == before


def test_zero_usage_keeps_lifecycle_and_assistant_text(backend):
    values = [
        dict(type='session', usage=copy.deepcopy(ZERO)),
        dict(type='message_end', message=dict(role='assistant', usage=copy.deepcopy(ZERO),
            content=[dict(type='thinking', thinking='Public thinking summary'),
                dict(type='text', text='Visible model output'),
                dict(type='toolCall', name='bash', arguments=dict(command='private command'))])),
        dict(type='agent_settled', aborted=False, usage=copy.deepcopy(ZERO)),
    ]
    before = copy.deepcopy(values)
    assert [event for value in values for event in backend.public_events(value)] == [
        dict(type='session.started'), dict(type='thinking', text='Public thinking summary'),
        dict(type='message', text='Visible model output'),
        dict(type='turn.completed')]
    assert values == before


@pytest.mark.parametrize('usage', [None, {}, [], dict(input=3),
    dict(ZERO, input=True), dict(ZERO, input=-1), dict(ZERO, output='1'),
    dict(ZERO, totalTokens=1), dict(unknownTokens=9)])
@pytest.mark.parametrize('kind', ['message_update', 'message_end'])
def test_unknown_or_malformed_usage_never_becomes_public_zero(backend, kind, usage):
    value = usage_event(kind, copy.deepcopy(usage))
    before = copy.deepcopy(value)
    assert backend.public_events(value) == []
    assert value == before
