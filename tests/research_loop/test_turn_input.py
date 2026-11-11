"""Current-turn advice never starts a new native call or changes its identity."""
from contextlib import contextmanager, nullcontext

import pytest

from scidiscovery.research_loop import backends, budget, contracts, turn_input, user_guidance
from scidiscovery.research_loop.native_app_server import Transport, metadata_json, selected_line


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, '_ledger_writer', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(budget, '_ledger_writer_from_base', lambda *args, **kwargs: nullcontext())
    context = turn_input.begin(tmp_path, dict(step=4, role='W'), tmp_path / 'workspace', 0,
        task_ref=dict(path='jobs/004.json', sha256='a' * 64), base={})
    turn_input.poll(context, thread='thread', turn='turn')
    return tmp_path, context


def _request(context):
    return contracts.read(contracts.regular(context['inbox'] / 'inbox.json'))[-1]


def test_role_and_future_step_remain_pending(queue):
    root, context = queue
    other = user_guidance.submit(root, 'For H', target='H')
    future = user_guidance.submit(root, 'Later W', target='W', effective_step=5)
    assert 'turn_input' not in other and 'turn_input' not in future
    assert contracts.read(contracts.regular(context['inbox'] / 'inbox.json')) == []
    advice = user_guidance.submit(root, 'Current W', target='W')
    assert advice['turn_input']['status'] == 'queued'
    with pytest.raises(ValueError, match='exact running native turn'):
        user_guidance.edit(root, advice['guidance_id'], 'Replacement', expected_revision=1)


def test_accepted_queue_is_consumed_once_and_has_no_scientific_verdict(queue):
    root, context = queue
    advice = user_guidance.submit(root, 'Compare alternatives', target='global')
    request = _request(context)
    inbox = turn_input.Inbox(context['inbox'])
    assert inbox.requests('another-thread', 'turn') == []
    assert inbox.requests('thread', 'turn') == [request]
    assert inbox.requests('thread', 'turn') == []
    turn_input.poll(context, thread='thread', turn='turn', event=turn_input.Inbox.receipt(request, 'accepted'))
    turn_input.end(context)
    value = user_guidance.list_guidance(root)[0]
    assert value['guidance_id'] == advice['guidance_id']
    assert value['turn_input']['status'] == 'accepted'
    assert value['status'] == 'applied'
    assert user_guidance.select_binding(5, 'W', None, current={value['guidance_id']: value}, revocations={}) == []


def test_unknown_is_frozen_and_not_resent_at_new_stage(queue):
    root, context = queue
    user_guidance.submit(root, 'Uncertain delivery', target='W')
    turn_input.end(context)
    value = user_guidance.list_guidance(root)[0]
    assert value['turn_input']['status'] == 'unknown'
    assert user_guidance.select_binding(5, 'W', None, current={value['guidance_id']: value}, revocations={}) == []
    with pytest.raises(ValueError, match='exact running native turn'):
        user_guidance.revoke(root, value['guidance_id'], expected_revision=1)


def test_terminal_retirement_stops_admission_but_reconciles_late_ack(queue):
    root, context = queue
    first = user_guidance.submit(root, 'Queued before terminal', target='W')
    request = _request(context)
    turn_input.retire(context)
    late = user_guidance.submit(root, 'After terminal', target='W')
    assert 'turn_input' not in late
    turn_input.poll(context, thread='thread', turn='turn',
        event=turn_input.Inbox.receipt(request, 'accepted'))
    rows = {row['guidance_id']: row for row in user_guidance.list_guidance(root)}
    assert rows[first['guidance_id']]['turn_input']['status'] == 'accepted'
    assert 'turn_input' not in rows[late['guidance_id']]
    selected = user_guidance.select_binding(5, 'W', None, current=rows, revocations={})
    assert [row['guidance_id'] for row in selected] == [late['guidance_id']]
    assert len(contracts.read(contracts.regular(context['inbox'] / 'inbox.json'))) == 1


def test_optional_queue_io_failure_disables_channel_without_stopping_call(queue, monkeypatch):
    root, context = queue
    user_guidance.submit(root, 'Pending when queue becomes unavailable', target='W')
    turn_input.poll(context, thread='thread', turn='turn')
    read = contracts.read
    def unavailable(path):
        if path.name == 'inbox.json':
            raise OSError('optional queue unavailable')
        return read(path)
    monkeypatch.setattr(contracts, 'read', unavailable)
    adapter = backends.Protocol(dict(native_launcher_argv=['fixture']))
    adapter.poll_turn_input(context, thread='thread', turn='turn')
    assert context['accepting'] is False
    assert user_guidance.list_guidance(root)[0]['turn_input']['status'] == 'unknown'


def test_optional_queue_begin_failure_does_not_prevent_native_launch(queue, monkeypatch):
    root, _ = queue
    write = contracts.write
    def unavailable(path, value):
        if path.name == 'inbox.json':
            raise OSError('optional queue unavailable')
        return write(path, value)
    monkeypatch.setattr(contracts, 'write', unavailable)
    assert turn_input.begin(root, dict(step=5, role='H'), root / 'other', 0,
        task_ref={}, base={}) is None


@pytest.mark.parametrize('operation', ['begin', 'poll', 'retire', 'end'])
@pytest.mark.parametrize('phase', ['entry', 'exit'])
def test_core_writer_identity_errors_are_not_optional_queue_failures(queue, monkeypatch, operation, phase):
    root, context = queue
    @contextmanager
    def core_writer(*args, **kwargs):
        if phase == 'entry':
            raise ValueError('core current-base or lease identity changed')
        yield
        raise ValueError('core current-base or lease identity changed')
    monkeypatch.setattr(budget, '_ledger_writer_from_base', core_writer)
    adapter = backends.Protocol(dict(native_launcher_argv=['fixture']))
    actions = dict(
        begin=lambda: turn_input.begin(root, dict(step=5, role='H'), root / 'other', 0,
            task_ref={}, base={}),
        poll=lambda: adapter.poll_turn_input(context, thread='thread', turn='turn'),
        retire=lambda: adapter.retire_turn_input(context),
        end=lambda: adapter.end_turn_input(context))
    with pytest.raises(ValueError, match='core current-base or lease identity changed'):
        actions[operation]()


def test_proven_rejection_releases_only_future_binding(queue):
    root, context = queue
    user_guidance.submit(root, 'Unsupported native queue', target='W')
    request = _request(context)
    turn_input.poll(context, thread='thread', turn='turn',
        event=turn_input.Inbox.receipt(request, 'unavailable', not_submitted=True))
    value = user_guidance.list_guidance(root)[0]
    assert 'turn_input' not in value
    assert value['last_turn_input']['status'] == 'unavailable'
    assert len(contracts.read(contracts.regular(context['inbox'] / 'inbox.json'))) == 1
    assert user_guidance.select_binding(5, 'W', None, current={value['guidance_id']: value}, revocations={})


def test_receipt_cannot_change_original_turn(queue):
    root, context = queue
    user_guidance.submit(root, 'Exact input', target='W')
    event = turn_input.Inbox.receipt(_request(context), 'accepted')
    event['turn'] = 'different-turn'
    with pytest.raises(turn_input.QueueArtifactError):
        turn_input.poll(context, event=event)


def test_codex_steer_receipt_does_not_change_lifecycle(queue):
    root, context = queue
    user_guidance.submit(root, 'Existing turn advice', target='W')
    request = _request(context)
    events, sent = [], []
    transport = Transport({}, emit=events.append, send=sent.append)
    transport.stage, transport.thread, transport.turn = 4, 'thread', 'turn'
    transport.queue_input(request)
    assert sent[0]['method'] == 'turn/steer'
    assert sent[0]['params']['expectedTurnId'] == 'turn'
    response = ('{"id":"input-' + request['input_id'] + '","result":{"turnId":"turn","opaque":{"tool":"secret"}}}').encode()
    assert selected_line(response)
    projected = metadata_json(response)
    assert projected['result'] == dict(turnId='turn')
    transport.receive(projected, '')
    assert events[-1]['status'] == 'accepted'
    assert transport.stage == 4 and transport.turn == 'turn'
    assert not transport.finished and not transport.completed
    assert len(sent) == 1


def test_codex_uncertain_write_is_not_repeated(queue):
    root, context = queue
    user_guidance.submit(root, 'One-shot input', target='W')
    request = _request(context)
    events = []
    def broken(_request):
        raise BrokenPipeError()
    transport = Transport({}, emit=events.append, send=broken)
    transport.stage, transport.thread, transport.turn = 4, 'thread', 'turn'
    inbox = turn_input.Inbox(context['inbox'])
    for item in inbox.requests('thread', 'turn'):
        transport.queue_input(item)
    assert [event['status'] for event in events] == ['submitting', 'unknown']
    assert inbox.requests('thread', 'turn') == []
