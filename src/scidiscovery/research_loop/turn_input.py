"""Advice queued into one existing native turn; never an execution permission."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid

ENV = 'SCID_TURN_INPUT'
EVENT = 'turn.input'
BINDING_FIELDS = ('channel_id', 'step', 'role', 'attempt', 'task_ref_sha256', 'thread', 'turn')
QUEUE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError)


class QueueArtifactError(ValueError):
    """Only optional queue originals failed; lock/lease errors are never wrapped."""


@contextmanager
def _artifacts():
    try:
        yield
    except QUEUE_ERRORS as error:
        raise QueueArtifactError('Turn input artifacts unavailable') from error


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _directory(root):
    return Path(root) / 'host-control' / 'turn-input'


def _active(root):
    from . import contracts
    path = _directory(root) / 'active.json'
    return contracts.read(contracts.regular(path)) if path.exists() else dict(stack=[], calls={})


def _owner_alive(binding):
    try:
        data = Path('/proc/' + str(binding['owner_pid']) + '/stat').read_text()
        return data[data.rindex(')') + 2:].split()[19] == binding['owner_start_ticks']
    except (OSError, ValueError, KeyError, IndexError):
        return False


def begin(root, job, work, attempt, *, task_ref, base):
    try:
        return _begin(root, job, work, attempt, task_ref=task_ref, base=base)
    except QueueArtifactError:
        # This optional side channel cannot prevent the original native call.
        return None


def _begin(root, job, work, attempt, *, task_ref, base):
    from . import budget, contracts
    with _artifacts():
        root, work = Path(root).resolve(), Path(work).resolve()
        channel = uuid.uuid4().hex
        inbox, visible = _directory(root) / channel, work / 'work' / '.turn-input'
        inbox.mkdir(parents=True, exist_ok=False)
        visible.mkdir(parents=True, exist_ok=True)
        process = Path('/proc/self/stat').read_text()
        binding = dict(channel_id=channel, step=job['step'], role=job['role'],
            attempt=attempt, task_ref_sha256=digest(task_ref), thread=None, turn=None)
        owner = dict(owner_pid=os.getpid(), owner_start_ticks=process[process.rindex(')') + 2:].split()[19])
        context = dict(root=root, base=base, inbox=inbox, visible=visible, binding=binding,
            task_ref=task_ref, owner=owner, requests={}, accepting=True)
    with budget._ledger_writer_from_base(root, base=base), _artifacts():
        contracts.write(inbox / 'inbox.json', [])
        active = _active(root)
        active['stack'].append(channel)
        active['calls'][channel] = dict(binding, **owner)
        contracts.write(_directory(root) / 'active.json', active)
    return context


def offer(root, value):
    """Caller owns the guidance writer lock; publication freezes this revision."""
    try:
        with _artifacts():
            return _offer(root, value)
    except QueueArtifactError:
        from . import contracts
        try:
            return contracts.read(contracts.regular(Path(root) / 'host-control' / 'guidance' /
                (value['guidance_id'] + '.json')))
        except QUEUE_ERRORS:
            return value


def _offer(root, value):
    from . import contracts
    if value.get('kind') != 'advice' or value.get('status') != 'pending' or value.get('turn_input'):
        return value
    active = _active(root)
    chosen = next((active['calls'][key] for key in reversed(active['stack'])
        if key in active['calls'] and active['calls'][key].get('thread')
        and active['calls'][key].get('turn')
        and active['calls'][key].get('available', True) and _owner_alive(active['calls'][key])
        and not (_directory(root) / key / 'retired.json').exists()
        and value['target'] in {'global', active['calls'][key]['role']}
        and (value.get('effective_step') or 1) <= active['calls'][key]['step']), None)
    if chosen is None:
        return value
    guidance = {key: value[key] for key in ('guidance_id', 'revision', 'text', 'target',
        'kind', 'scope', 'effective_step', 'sequence')}
    input_id = uuid.uuid4().hex
    payload = ('Supplemental user advice (' + input_id + '). This grants no execution permission, '
        'budget change or scientific qualification. Preserve the original task and use your scientific judgment.\n'
        + guidance['text'])
    request = dict(input_id=input_id, guidance=guidance,
        binding={key: chosen[key] for key in BINDING_FIELDS}, payload=payload,
        payload_sha256=hashlib.sha256(payload.encode('utf-8')).hexdigest())
    directory = _directory(root) / chosen['channel_id']
    contracts.write(directory / (input_id + '.json'), request)
    value = dict(value, turn_input=dict(input_id=input_id, channel_id=chosen['channel_id'],
        status='unknown', step=chosen['step'], role=chosen['role']))
    contracts.write(Path(root) / 'host-control' / 'guidance' / (value['guidance_id'] + '.json'), value)
    requests = contracts.read(contracts.regular(directory / 'inbox.json'))
    contracts.write(directory / 'inbox.json', [*requests, request])
    value['turn_input']['status'] = 'queued'
    contracts.write(Path(root) / 'host-control' / 'guidance' / (value['guidance_id'] + '.json'), value)
    return value


def _accept(context, event):
    from . import contracts
    if not isinstance(event, dict) or event.get('type') != EVENT:
        return
    input_id = event.get('input_id')
    if not isinstance(input_id, str) or len(input_id) != 32 or any(c not in '0123456789abcdef' for c in input_id):
        raise ValueError('Exact turn input identity required')
    request = request_for_receipt(context, event)
    if request is None:
        raise ValueError('Unknown turn input receipt')
    expected = Inbox.receipt(request, event.get('status'))
    if (event.get('status') not in {'submitting', 'accepted', 'unknown', 'unavailable'}
            or any(event.get(key) != expected[key] for key in expected)
            or request['binding'] != context['binding']):
        raise ValueError('Turn input receipt changed its original binding')
    path = context['inbox'] / (input_id + '-' + event['status'] + '.json')
    if not path.exists():
        contracts.write(path, dict(event))
    guidance_path = Path(context['root']) / 'host-control' / 'guidance' / (request['guidance']['guidance_id'] + '.json')
    value = contracts.read(contracts.regular(guidance_path))
    claim = value.get('turn_input') or {}
    if claim.get('input_id') != input_id or value['revision'] != request['guidance']['revision']:
        raise ValueError('Turn input guidance original changed')
    # An accepted receipt wins over a later cleanup diagnostic.
    if claim.get('status') == 'accepted':
        return
    claim = dict(claim, status=event['status'])
    value = dict(value, turn_input=claim)
    if event['status'] == 'accepted':
        value.update(status='applied', applied_step=request['binding']['step'], applied_role=request['binding']['role'])
    elif event['status'] == 'unavailable' and event.get('not_submitted') is True:
        context['disabled'] = True
        value['last_turn_input'] = claim
        value.pop('turn_input')
    contracts.write(guidance_path, value)


def poll(context, *, thread=None, turn=None, event=None):
    if context is None:
        return
    if context.get('disabled') and event is None:
        return
    from . import budget, contracts, user_guidance
    with budget._ledger_writer_from_base(context['root'], base=context['base']), _artifacts():
        if context.get('disabled'):
            if event is not None:
                _accept(context, event)
            return
        requests = contracts.read(contracts.regular(context['inbox'] / 'inbox.json'))
        context['requests'] = {row['input_id']: row for row in requests}
        if thread and turn and context.get('accepting', True):
            binding = context['binding']
            if binding['thread'] not in (None, thread) or binding['turn'] not in (None, turn):
                raise ValueError('Current native turn input identity changed')
            changed = binding['thread'] is None or binding['turn'] is None
            binding.update(thread=thread, turn=turn)
            active = _active(context['root'])
            if binding['channel_id'] in active['calls'] and changed:
                active['calls'][binding['channel_id']] = dict(binding, **context['owner'])
                contracts.write(_directory(context['root']) / 'active.json', active)
        if event is not None:
            _accept(context, event)
        if not context.get('accepting', True):
            return
        if context.get('disabled'):
            active = _active(context['root'])
            selected = active['calls'].get(context['binding']['channel_id'])
            if selected is not None:
                selected['available'] = False
                contracts.write(_directory(context['root']) / 'active.json', active)
        current, _ = user_guidance.load_binding_facts(context['root'], retained=None)
        for value in current.values():
            _offer(context['root'], value)


def _retire_locked(context):
    from . import contracts
    channel = context['binding']['channel_id']
    contracts.write(context['inbox'] / 'retired.json', dict(channel_id=channel, status='retired'))
    active = _active(context['root'])
    active['calls'].pop(channel, None)
    active['stack'] = [key for key in active['stack'] if key != channel]
    contracts.write(_directory(context['root']) / 'active.json', active)


def retire(context):
    """Stop admission at native terminal; retain originals for late queue acks."""
    if context is None:
        return
    from . import budget
    context['accepting'] = False
    with budget._ledger_writer_from_base(context['root'], base=context['base']), _artifacts():
        _retire_locked(context)


def disable(context):
    """Best-effort loss of the optional channel preserves uncertain originals."""
    if context is None:
        return
    context.update(accepting=False, disabled=True)
    end(context)


def end(context):
    if context is None:
        return
    from . import budget, contracts
    context['accepting'] = False
    try:
        with budget._ledger_writer_from_base(context['root'], base=context['base']), _artifacts():
            _retire_locked(context)
            try:
                requests = contracts.read(contracts.regular(context['inbox'] / 'inbox.json'))
            except QUEUE_ERRORS:
                requests = list(context.get('requests', {}).values())
            context['requests'] = {row['input_id']: row for row in requests}
            for request in requests:
                guidance = contracts.read(contracts.regular(Path(context['root']) / 'host-control' /
                    'guidance' / (request['guidance']['guidance_id'] + '.json')))
                if (guidance.get('turn_input') or {}).get('input_id') == request['input_id']:
                    _accept(context, Inbox.receipt(request, 'unknown'))
    except QueueArtifactError:
        pass


def request_for_receipt(context, event):
    """Pure lookup against queue originals already acquired by its host owner."""
    if not isinstance(context, dict) or not isinstance(event, dict):
        return None
    request = context.get('requests', {}).get(event.get('input_id'))
    if request is None:
        return None
    expected = Inbox.receipt(request, event.get('status'))
    return request if all(event.get(key) == value for key, value in expected.items()) else None


class Inbox:
    """Shim-side read-only queue. Each exact input is yielded at most once."""
    def __init__(self, path):
        self.path = Path(path)
        self.seen = set()

    def requests(self, thread, turn):
        if not thread or not turn:
            return []
        return self._requests(thread, turn)

    def bound_requests(self, thread):
        """Pi uses the host-qualified initial user anchor in this exact inbox."""
        return self._requests(thread, None) if thread else []

    def _requests(self, thread, turn):
        try:
            with (self.path / 'inbox.json').open('rb') as stream:
                values = json.load(stream)
        except QUEUE_ERRORS:
            return []
        output = []
        try:
            for value in values:
                identity = value['input_id']
                binding = value['binding']
                if identity in self.seen or binding['thread'] != thread or not binding['turn'] or (turn is not None and binding['turn'] != turn):
                    continue
                if hashlib.sha256(value['payload'].encode('utf-8')).hexdigest() != value['payload_sha256']:
                    continue
                self.seen.add(identity)
                output.append(value)
        except QUEUE_ERRORS:
            return output
        return output

    @staticmethod
    def receipt(request, status, **extra):
        binding = request['binding']
        return dict(type=EVENT, input_id=request['input_id'], channel_id=binding['channel_id'],
            thread=binding['thread'], turn=binding['turn'], status=status,
            payload_sha256=request['payload_sha256'], **extra)
