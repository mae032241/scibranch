"""Current-run Pi queue mechanics, using supplied facts and no native/model call."""
import copy
import hashlib
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import pi_backend, pi_observation, pi_transport, turn_input
from test_pi_authorship import compact_pi_facts
from test_pi_backend_v2 import adapter


def request():
    payload = 'Supplemental user advice (exact input).\nKeep the original contract.'
    return dict(input_id='a' * 32, payload=payload,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        binding=dict(channel_id='b' * 32, thread='sdk-session', turn='native-user'))


class Inbox:
    receipt = staticmethod(turn_input.Inbox.receipt)

    def __init__(self, value): self.value = value
    def bound_requests(self, thread):
        assert thread == 'sdk-session'
        value, self.value = self.value, None
        return [value] if value is not None else []


def binding():
    value = pi_transport.Binding('fixture', 'exact-model', 'high', '/session', 'Original task')
    value.session_id = 'sdk-session'
    value.prompt_sent = value.accepted = True
    return value


def test_one_steer_in_current_run_and_queue_ack_is_not_consumption():
    value, events = request(), []
    queue = pi_transport.TurnInputs(Inbox(value), events.append)
    original = binding()
    commands = list(queue.commands(original))
    assert commands == [dict(id='1', type='steer', message=value['payload'])]
    assert list(queue.commands(original)) == []
    assert events[0]['status'] == 'submitting'
    assert queue.response(dict(id='1', command='steer', success=True,
        data=dict(disposition='queued')), original)
    assert events[-1]['status'] == 'accepted'
    assert events[-1]['turn'] == 'native-user'
    assert queue.accepted == [events[-1]]
    assert original.prompt == 'Original task' and original.accepted and not original.pending
    assert not any(row.get('type') in {'prompt', 'follow_up', 'abort', 'new_session'} for row in commands)


@pytest.mark.parametrize('response', [None, dict(command='steer', success=False),
    dict(command='steer', success=True, data=dict(disposition='handled'))])
def test_missing_or_nonqueue_ack_is_unknown_and_never_retried(response):
    value, events = request(), []
    queue = pi_transport.TurnInputs(Inbox(value), events.append)
    original = binding()
    list(queue.commands(original))
    if response is not None: queue.response(dict(response, id='1'), original)
    queue.finish()
    assert events[-1]['status'] == 'unknown' and queue.accepted == []
    assert list(queue.commands(original)) == []


def test_settled_run_never_submits_and_registered_late_queue_can_remain_idle():
    value, events = request(), []
    original = binding()
    original.settled = dict(type='agent_settled', aborted=False)
    queue = pi_transport.TurnInputs(Inbox(value), events.append)
    assert list(queue.commands(original)) == [] and events == []
    original.turn_input_payloads.add(value['payload'])
    original.event(dict(type='queue_update', steering=[value['payload']], followUp=[]))
    original._state(dict(isStreaming=False, isCompacting=False, pendingMessageCount=1,
        sessionFile='/session', sessionId='sdk-session'), bound=False)
    with pytest.raises(pi_transport.TransportFault):
        original.event(dict(type='queue_update', steering=['unregistered'], followUp=[]))
    with pytest.raises(pi_transport.TransportFault):
        original.event(dict(type='queue_update', steering=[], followUp=[value['payload']]))
    with pytest.raises(pi_transport.TransportFault): original.event(dict(type='turn.input'))


def test_queue_updates_require_registered_text_even_after_settlement():
    engine = pi_backend.PiEvents()
    engine.turn_input_payloads.add(request()['payload'])
    engine.settled = True
    assert engine.event(dict(type='queue_update', steering=[request()['payload']], followUp=[])) == dict(activity=True)
    assert not engine.accounting_only
    assert engine.event(dict(type='queue_update', steering=['arbitrary work'], followUp=[]))['failed']


def observer(receipt):
    meter = SimpleNamespace(digest='f' * 64, resumed=False, profile={}, thread='sdk-session',
        baseline_offset=0, original_count=0, original_leaf=None, seen={'native-user', 'advice', 'final'})
    value = pi_observation.Observation(meter, dict(metadata='/private/metadata', nonce='c' * 64))
    value.available = value.bound = value.terminal_seen = True
    value.sequence = value.acknowledged = 1
    value.turn = 'native-user'; value.prompt_hash = 'd' * 64; value.leaf = 'final'
    value.entries = {
        'native-user': dict(type='message', parent=None, role='user', prompt_hash=value.prompt_hash),
        'advice': dict(type='message', parent='native-user', role='user', prompt_hash=request()['payload_sha256']),
        'final': dict(type='message', parent='advice', role='assistant', status='completed', profile={},
            final=dict(sha256='e' * 64, size_bytes=2))}
    value.responses = {'final': 'completed'}
    value.turn_inputs[request()['input_id']] = receipt
    return value


def test_supplemental_user_requires_exact_accepted_receipt_without_rebinding_turn():
    receipt = turn_input.Inbox.receipt(request(), 'accepted')
    value = observer(receipt)
    result = value.controlled_facts()
    assert result['user'] == dict(id='native-user', prompt_sha256='d' * 64)
    assert result['supplemental_users'] == [dict(id='advice', receipt=receipt)]
    assert value.windows_started == 0 and value.turn == 'native-user'
    for changes in (dict(status='unknown'), dict(payload_sha256='0' * 64), dict(turn='other')):
        with pytest.raises(ValueError, match='unreceipted'):
            observer(dict(receipt, **changes)).controlled_facts()


def test_retained_receipts_are_registered_only_against_host_acquired_original(monkeypatch):
    backend = adapter()
    backend._meter = SimpleNamespace(observation=SimpleNamespace(turn_inputs={}))
    monkeypatch.setattr(pi_backend.Backend, 'poll_turn_input', lambda *args, **kwargs: None)
    value = request()
    receipt = turn_input.Inbox.receipt(value, 'accepted')
    context = dict(requests={value['input_id']: value})
    backend.poll_turn_input(context, event=dict(receipt, payload_sha256='0' * 64))
    assert backend._meter.observation.turn_inputs == {}
    backend.poll_turn_input(context, event=receipt)
    assert backend._meter.observation.turn_inputs == {value['input_id']: receipt}


def test_retained_supplemental_proof_binds_same_call_queue_receipt():
    outcome, intent, _, work = compact_pi_facts()
    proof = outcome['native_identity']['controlled']
    receipt = turn_input.Inbox.receipt(request(), 'accepted')
    proof['transport']['turn_inputs'] = [receipt]
    proof['persisted']['supplemental_users'] = [dict(id='advice', receipt=copy.deepcopy(receipt))]
    proof['persisted']['entry_count'] += 1
    pi_backend._participant(outcome, intent, work)
    proof['persisted']['supplemental_users'][0]['receipt']['input_id'] = '0' * 32
    with pytest.raises(ValueError): pi_backend._participant(outcome, intent, work)


def test_launch_projects_only_current_readonly_queue_mapping(tmp_path, monkeypatch):
    backend = adapter()
    visible, source = tmp_path / 'work/.turn-input', tmp_path / 'private/exact-inbox'
    backend._turn_input = dict(visible=visible, inbox=source)
    monkeypatch.setattr(backend, 'wrap_launch', lambda args, facts: args)
    facts = dict(resource_policy=pi_transport.resource_policy(), readonly=False, mounts=dict(mappings={}))
    env = {}
    backend.prepare_launch(['original'], tmp_path, 0, env, facts, None)
    assert facts['mounts']['mappings'] == {str(visible): source}
    assert env[turn_input.ENV] == str(visible)
