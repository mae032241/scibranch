"""Append-only time facts and operator ceilings; no scientific scheduling."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time

from . import contracts


def clock_stamp():
    return dict(at_unix=time.time(), at_boottime=time.clock_gettime(time.CLOCK_BOOTTIME),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def _duration(before, after, *, legacy_elapsed=False):
    if 'at_boottime' not in before:  # Legacy elapsed startup has no monotonic anchor.
        wall = after['at_unix'] - before['at_unix']
        if wall < 0:
            raise ValueError('Control clock moved backwards; consumption is not refundable')
        return wall
    if before['boot_id'] != after['boot_id']:
        raise ValueError('Control clock boot changed; elapsed interval cannot be verified')
    elapsed = after['at_boottime'] - before['at_boottime']
    # UTC can slew or step without a boot-clock reversal. It is a display
    # timestamp, never a second authority over advancement consumption.
    # Legacy elapsed retains its previously frozen UTC refusal rules.
    wall = after['at_unix'] - before['at_unix']
    if elapsed < 0 or legacy_elapsed and (wall < 0 or wall + .02 < elapsed):
        raise ValueError('Control clock moved backwards; consumption is not refundable')
    return elapsed


def _paused_checkpoint(root, settings, events):
    from . import qualification as q
    phase = next((event['phase'] for event in reversed(events) if event['kind'] == 'clock'), 'paused')
    if settings.get('time_budget_basis') != 'active' or phase != 'paused':
        raise ValueError('Cold clock continuation requires an active budget already paused')
    started = Path(root) / 'host-started.json'
    head = (dict(seq=events[-1]['seq'], ref=q._ref(root, Path(root) / 'control-ledger' / f'{events[-1]["seq"]:06d}.json'))
            if events else None)
    return dict(version=1, basis='active', phase='paused', head=head,
                started=q._ref(root, started) if started.exists() else None)


def _verify_reanchor(root, settings, event, previous):
    """Historical receipt relations only; later restores may advance the owner."""
    from . import instance_owner, qualification as q
    marker = event.get('reanchor')
    if (event['kind'] != 'clock' or event.get('phase') != 'paused'
            or not isinstance(marker, dict) or set(marker) != {'version', 'checkpoint', 'transfer'}
            or type(marker['version']) is not int or marker['version'] != 1):
        raise ValueError('Invalid cold clock continuation event')
    value = instance_owner.read_transition(root, marker['checkpoint'], marker['transfer'])
    checkpoint = value['checkpoint']
    if (checkpoint['instance_id'] != settings['goal_id']
            or checkpoint['settings_ref'] != q._ref(root, Path(root) / 'settings.json')
            or checkpoint['budget'] != _paused_checkpoint(root, settings, previous)):
        raise ValueError('Cold clock continuation lost its original ledger or settings')
    before = previous[-1] if previous else contracts.read(Path(root) / 'host-started.json') if checkpoint['budget']['started'] else None
    if before is None or before['boot_id'] == event['boot_id']:
        raise ValueError('Cold clock continuation must bridge an original paused boot')


def _events(root, settings, *, resources=False):
    settings = contracts.settings(root)
    events, parent, origins = [], None, {}
    identity = contracts.digest(root / 'settings.json')
    for seq, path in enumerate(sorted((root / 'control-ledger').glob('*.json')), 1):
        data = contracts.regular(path).read_bytes()
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError('Invalid control ledger event')
        if (type(event.get('seq')) is not int or path.name != f'{seq:06d}.json' or event.get('seq') != seq
                or event.get('parent_sha256') != parent or event.get('settings_sha256') != identity
                or type(event.get('at_unix')) not in (int, float) or not math.isfinite(event['at_unix'])
                or type(event.get('at_boottime')) not in (int, float)
                or not math.isfinite(event['at_boottime']) or event['at_boottime'] < 0
                or not isinstance(event.get('boot_id'), str) or not event['boot_id']
                or event.get('kind') not in {'clock', 'budget_amend', 'closeout_begin', 'closeout_resume', 'action_intent', 'action_outcome',
                    'native_usage_reserve', 'final_a_return', 'resource_grant', 'resource_reserve', 'resource_rebind', 'resource_bind', 'resource_settle', 'resource_release'}):
            raise ValueError('Control ledger identity, sequence or time changed')
        if 'reanchor' in event:
            _verify_reanchor(root, settings, event, events)
        elif events:
            _duration(events[-1], event, legacy_elapsed=settings.get('time_budget_basis', 'elapsed') == 'elapsed')
        if event['kind'] == 'budget_amend':
            for limits in (event.get('before'), event.get('after')):
                if (not isinstance(limits, dict) or set(limits) != {'max_tokens', 'max_seconds'}
                        or type(limits['max_tokens']) is not int or limits['max_tokens'] <= 0
                        or type(limits['max_seconds']) not in (int, float)
                        or not 0 < limits['max_seconds'] < float('inf')):
                    raise ValueError('Invalid audited budget ceilings')
            if not isinstance(event.get('authorization_note'), str) or not event['authorization_note'].strip():
                raise ValueError('Budget amendment has no operator decision')
            if 'before_max_steps' in event or 'after_max_steps' in event:
                if any(type(event.get(key)) is not int or event[key] <= 0
                       for key in ('before_max_steps', 'after_max_steps')):
                    raise ValueError('Invalid audited step ceilings')
                if event.get('max_steps_delta') != event['after_max_steps'] - event['before_max_steps']:
                    raise ValueError('Audited step amendment delta changed')
            if 'authorization_statement' in event and (not isinstance(event['authorization_statement'], str)
                    or not event['authorization_statement'].strip()):
                raise ValueError('Budget amendment has no original operator statement')
        if event['kind'] == 'native_usage_reserve':
            _validate_usage_reserve(root, event)
        if event['kind'] == 'final_a_return':
            _validate_final_a_event(root, settings, event, events)
        if event['kind'] == 'clock' and event.get('phase') not in {'advance', 'paused', 'waiting'}:
            raise ValueError('Unknown control clock phase')
        if event['kind'] == 'closeout_begin':
            if (type(event.get('source_cutoff')) is not int or event['source_cutoff'] < 0
                    or type(event.get('first_step')) is not int or event['first_step'] <= event['source_cutoff']
                    or type(event.get('preparation_seconds')) not in (int, float)
                    or not math.isfinite(event['preparation_seconds']) or event['preparation_seconds'] < 0
                    or not isinstance(event.get('stop_reason'), str) or event.get('mode') not in {'model', 'export_only', 'final'}):
                raise ValueError('Invalid immutable closeout boundary')
            if event.get('resume_cursor') is not None:
                _validate_cursor(root,event['resume_cursor'],event['source_cutoff']+1,events)
            if 'authorization_step' in event and (type(event['authorization_step']) is not int or event['authorization_step'] != event['source_cutoff']):
                raise ValueError('Direct delivery authorization must bind its exact scientific boundary')
        if event['kind'] == 'closeout_resume':
            _validate_resume_event(root, event, events)
        if event['kind'] == 'action_intent':
            if (event.get('action_kind') not in {'submit', 'confirm'} or not isinstance(event.get('action_id'), str)
                    or not isinstance(event.get('operation_id'), str) or not isinstance(event.get('decision'), dict)
                    or not isinstance(event.get('binding'), dict) or type(event.get('max_seconds')) not in (int, float)
                    or not 0 < event['max_seconds'] < float('inf')
                    or type(event.get('advance_seconds')) not in (int, float) or not math.isfinite(event['advance_seconds']) or event['advance_seconds'] < 0):
                raise ValueError('Invalid bounded delivery action intent')
            if _active_final(events) is not None and 'final_return' not in event:
                raise ValueError('Final delivery lost its reserved authorization')
            if 'final_return' in event:
                _validate_final_a_action(root, event, events)
        if event['kind'] == 'action_outcome' and (not isinstance(event.get('action_id'), str)
                or not isinstance(event.get('result'), dict) or event['result'].get('status') not in {'confirmed', 'failed', 'unknown', 'rejected', 'engineering_error', 'unclassified'}):
            raise ValueError('Invalid delivery action outcome')
        events.append(event)
        parent = hashlib.sha256(data).hexdigest()
        origins[seq] = dict(path=path.relative_to(root).as_posix(), sha256=parent, size_bytes=len(data))
    state = _resource_state(root, settings, events, origins)
    return (events, parent, state) if resources else (events, parent)


RESOURCE_DIMENSIONS = {'wall_time_seconds', 'max_storage_bytes'}


def _resource_dimensions(value, *, partial=False, zero=False):
    if (not isinstance(value, dict) or (not set(value).issubset(RESOURCE_DIMENSIONS) if partial
            else set(value) != RESOURCE_DIMENSIONS)
            or any(type(item) is not int or item < (0 if zero else 1) for item in value.values())):
        raise ValueError('Explicit finite execution resource dimensions required')
    return dict(value)


def execution_resource_policy(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'version', 'allowance'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Execution resources require explicit version 1 allowance')
    return dict(version=1, allowance=_resource_dimensions(value['allowance']))


def _resource_ref(root, event, origins=None):
    if origins is not None:
        return origins[event['seq']]
    from . import qualification as q
    return q._ref(root, root / 'control-ledger' / f'{event["seq"]:06d}.json')


def _resource_settings(root):
    """One cumulative owner, retained when a later node switches executor.

    A previously disabled pool starts from the first explicit revision grant;
    its immutable origin remains the baseline for all later accounting.
    """
    settings = contracts.settings(root)
    if settings.get('execution_resource_policy') is not None:
        return settings
    from . import configuration_updates
    for index, path in enumerate(sorted((Path(root) / 'host-control/configuration/revisions').glob('*.json')), 1):
        row = configuration_updates.revisions(root, through=index)[-1]
        if row['settings'].get('execution_resource_policy') is not None:
            return row['settings']
    return settings


def _resource_owner(settings):
    policy = execution_resource_policy(settings.get('execution_resource_policy'))
    selected = settings.get('execution_config')
    if policy is None or selected is None or settings.get('execution_authority_policy') is None:
        raise ValueError('Explicit frozen execution resource owner is unavailable')
    value = dict(instance=settings['goal_id'], executor=selected['executor'])
    key = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(key=key, **value)


def _resource_state(root, settings, events, origins):
    """Fold only ledger facts; never rehash every execution's large originals."""
    grants, reservations = {}, {}
    for event in events:
        kind = event['kind']
        if not kind.startswith('resource_'):
            continue
        settings = _resource_settings(root)
        owner = _resource_owner(settings)
        if event.get('owner') != owner:
            raise ValueError('Execution resource owner differs from the original instance')
        if kind == 'resource_grant':
            allowance = _resource_dimensions(event.get('allowance'))
            previous = grants.get(owner['key'])
            if (event.get('previous_grant') != (previous['ref'] if previous else None)
                    or not isinstance(event.get('authorization_note'), str) or not event['authorization_note'].strip()
                    or previous is None and allowance != settings['execution_resource_policy']['allowance']):
                raise ValueError('Execution resource grant revision changed')
            grants[owner['key']] = dict(ref=_resource_ref(root, event, origins), allowance=allowance)
            continue
        identity = event.get('reservation_id')
        if not isinstance(identity, str) or len(identity) != 64:
            raise ValueError('Execution reservation identity is unavailable')
        if kind == 'resource_reserve':
            request = _resource_dimensions(event.get('budget'))
            ceiling = _resource_dimensions(event.get('effective_allowance'))
            grant = grants.get(owner['key'])
            binding = event.get('binding')
            if (grant is None or event.get('grant_revision') != grant['ref'] or identity in reservations
                    or not isinstance(binding, dict)
                    or identity != hashlib.sha256(json.dumps(dict(owner=owner, binding=binding),
                        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    or any(ceiling[key] > grant['allowance'][key] for key in ceiling)):
                raise ValueError('Execution reservation changed its grant or original binding')
            remaining = dict(ceiling)
            for prior in reservations.values():
                if not prior['released']:
                    for key, amount in {**prior['budget'], **prior['actual']}.items():
                        remaining[key] -= amount
            if any(request[key] > remaining[key] for key in request):
                raise ValueError('Cumulative execution resources exceeded')
            reservations[identity] = dict(ref=_resource_ref(root, event, origins), owner=owner,
                binding=binding, budget=request, actual={}, bound=None, released=False, settled_observation=None)
            continue
        original = reservations.get(identity)
        if original is None or event.get('reservation') != original['ref'] or original['released']:
            raise ValueError('Execution resource event lost its original reservation')
        if kind == 'resource_rebind':
            binding=event.get('binding');proof=event.get('proof')
            if (original['bound'] is not None or original['actual'] or event.get('before')!=original['binding']
                    or not isinstance(binding,dict) or set(binding)!=set(original['binding'])
                    or any(binding.get(key)!=original['binding'].get(key) for key in ('call_id','scope','native_attempt','interface'))
                    or not isinstance(proof,dict) or set(proof)!={'seal','allocation','pending_set'}
                    or any(not isinstance(ref,dict) or set(ref)!={'path','sha256','size_bytes'} for ref in proof.values())):
                raise ValueError('Only the exact never-prepared reservation can change authority')
            original['binding']=binding
        elif kind == 'resource_bind':
            binding = event.get('accepted_binding')
            if original['bound'] is not None or not isinstance(binding, dict) or set(binding) != {'accepted', 'prepared'}:
                raise ValueError('Execution accepted resource binding changed')
            original['bound'] = binding
        elif kind == 'resource_settle':
            actual = _resource_dimensions(event.get('actual'), partial=True, zero=True)
            if original['bound'] is None or not isinstance(event.get('observation'), dict) or any(
                    key in original['actual'] and original['actual'][key] != amount for key, amount in actual.items()):
                raise ValueError('Execution resource settlement changed proven actual usage')
            original['actual'].update(actual)
            original['settled_observation'] = event['observation']
        elif kind == 'resource_release':
            if original['bound'] is None or original['actual'] or not isinstance(event.get('retirement'), dict):
                raise ValueError('Execution resources have no permanent non-submission proof')
            original['released'] = True
    return grants, reservations


def execution_grant(root, *, operation_context=None):
    """Seed exactly the operator's frozen ceiling in the existing ledger."""
    root = Path(root).resolve(); settings = _resource_settings(root)
    owner = _resource_owner(settings)
    with _ledger_writer(root,context=operation_context):
        events, parent, (grants, _) = _events(root, settings, resources=True)
        if owner['key'] not in grants:
            event = _append(root, settings, events, parent, kind='resource_grant', owner=owner,
                previous_grant=None, allowance=settings['execution_resource_policy']['allowance'],
                authorization_note='Original explicit operator execution resource configuration')
            return dict(owner=owner, ref=_resource_ref(root, event), allowance=event['allowance'])
        return dict(owner=owner, **grants[owner['key']])


@contextmanager
def _operator_writer(root):
    """Reuse this controller scope, or acquire its existing exclusive writer."""
    from .controller import _writer, require_writer
    try:
        require_writer(root)
    except RuntimeError:
        with _writer(Path(root).resolve()):
            yield
    else:
        yield


def amend_execution_grant(root, *, allowance, authorization_note, operation_id=None):
    with _operator_writer(root):
        return _amend_execution_grant_locked(root, allowance=allowance,
            authorization_note=authorization_note, operation_id=operation_id)


def _amend_execution_grant_locked(root, *, allowance, authorization_note, operation_id=None):
    """Operator ceiling amendment, never an exact-call execution decision."""
    from .controller import require_writer
    require_writer(root)
    allowance = _resource_dimensions(allowance)
    if not isinstance(authorization_note, str) or not authorization_note.strip():
        raise ValueError('Execution resource amendment requires an operator decision')
    execution_grant(root)
    root = Path(root).resolve(); settings = _resource_settings(root); owner = _resource_owner(settings)
    with _ledger_writer(root):
        events, parent, (grants, _) = _events(root, settings, resources=True)
        if operation_id is not None:
            prior = next((event for event in events if event['kind'] == 'resource_grant'
                          and event.get('operation_id') == operation_id), None)
            if prior is not None:
                if prior['allowance'] != allowance or prior['owner'] != owner:
                    raise ValueError('Execution resource amendment operation changed')
                return dict(owner=owner, ref=_resource_ref(root, prior), allowance=allowance)
        previous = grants[owner['key']]
        if previous['allowance'] == allowance and operation_id is None:
            return dict(owner=owner, **previous)
        event = _append(root, settings, events, parent, kind='resource_grant', owner=owner,
            previous_grant=previous['ref'], allowance=allowance, authorization_note=authorization_note,
            **(dict(operation_id=operation_id) if operation_id is not None else {}))
        return dict(owner=owner, ref=_resource_ref(root, event), allowance=allowance)


def reserve_execution(root, *, binding, request_ref, decision_ref, budget, grant_revision, effective_allowance, unprepared_recovery=None, operation_context=None):
    """Atomic reservation before prepare/upload; revisions never create a pool."""
    from . import qualification as q
    root = Path(root).resolve(); settings = _resource_settings(root); owner = _resource_owner(settings)
    budget = _resource_dimensions(budget); effective_allowance = _resource_dimensions(effective_allowance)
    binding = dict(binding, request=request_ref, decision=decision_ref)
    identity = hashlib.sha256(json.dumps(dict(owner=owner, binding=binding), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    q._read(root, request_ref); q._read(root, decision_ref)
    verified_rebind=None
    if unprepared_recovery is not None:
        from .execution_resume import verify_rebind
        for original in execution_accounting(root)['reservations']:
            if (original['binding']['call_id']==binding['call_id'] and original['binding']['scope']==binding['scope']
                    and original['binding']!=binding):
                verify_rebind(root,unprepared_recovery,original['binding'],binding)
                verified_rebind=(original['ref'],original['binding'])
    with _ledger_writer(root,context=operation_context):
        events, parent, (grants, reservations) = _events(root, settings, resources=True)
        grant = grants.get(owner['key'])
        if grant is None or grant_revision != grant['ref']:
            raise ValueError('Execution resource authorization revision changed')
        if identity in reservations:
            original = reservations[identity]
            if original['budget'] != budget or original['released'] or original['binding']!=binding:
                raise ValueError('Original execution reservation cannot be replaced')
            return original['ref']
        same=[(key,row) for key,row in reservations.items() if row['binding']['call_id']==binding['call_id']
              and row['binding']['scope']==binding['scope']]
        if same:
            from .execution_resume import verify_rebind
            if len(same)!=1:
                raise ValueError('Original call already owns another exact reservation')
            original_id,original=same[0]
            if original['released'] or original['budget']!=budget:
                raise ValueError('Original reservation cannot be replaced or refunded')
            if original['binding']==binding:return original['ref']
            if unprepared_recovery is None or original['bound'] is not None or original['actual']:
                raise ValueError('Accepted reservation cannot change authority')
            total={key:sum(({**item['budget'],**item['actual']}).get(key,0) for item in reservations.values() if not item['released']) for key in budget}
            if any(total[key]>min(effective_allowance[key],grant['allowance'][key]) for key in budget):
                raise ValueError('Current cumulative resource allowance cannot authorize the held reservation')
            if verified_rebind!=(original['ref'],original['binding']):
                raise ValueError('Original reservation changed after exact rebind verification')
            if original['binding']!=binding:
                _append(root,settings,events,parent,kind='resource_rebind',owner=owner,
                    reservation_id=original_id,reservation=original['ref'],before=original['binding'],
                    binding=binding,proof=unprepared_recovery)
            return original['ref']
        remaining = {key: min(effective_allowance[key], grant['allowance'][key]) for key in budget}
        for original in reservations.values():
            if not original['released']:
                for key, amount in {**original['budget'], **original['actual']}.items():
                    remaining[key] -= amount
        if any(budget[key] > remaining[key] for key in budget):
            raise ValueError('Cumulative execution resources are insufficient')
        event = _append(root, settings, events, parent, kind='resource_reserve', owner=owner,
            reservation_id=identity, binding=binding, budget=budget, grant_revision=grant_revision,
            effective_allowance={key: min(effective_allowance[key], grant['allowance'][key]) for key in budget})
        return _resource_ref(root, event)


def _reserved(state, reservation):
    _, rows = state
    matches = [(key, row) for key, row in rows.items() if row['ref'] == reservation]
    if len(matches) != 1:
        raise ValueError('Exact execution reservation is unavailable')
    return matches[0]


def bind_execution(root, *, reservation, accepted, prepared, operation_context=None):
    from . import qualification as q
    root = Path(root).resolve(); settings = contracts.settings(root)
    value = q._read(root, accepted)
    original = dict(accepted=accepted, prepared=prepared)
    with _ledger_writer(root,context=operation_context):
        events, parent, state = _events(root, settings, resources=True); identity, row = _reserved(state, reservation)
        binding = row['binding']
        if (row['released'] or value.get('call_id') != binding['call_id'] or value.get('scope') != binding['scope']
                or value.get('native_attempt') != binding['native_attempt']
                or value.get('interface') != binding['interface'] or value.get('execution_authority') != binding['authority']
                or value.get('execution_reservation') != reservation
                or value.get('prepared') != prepared):
            raise ValueError('Accepted execution does not match its original reservation')
        if row['bound'] is not None:
            if row['bound'] != original:
                raise ValueError('Original reservation already bound another preparation')
            return row['ref']
        q._check(dict(path=prepared['local_path'], sha256=prepared['sha256'], size_bytes=prepared['size_bytes']))
        q._check(value['script'])
        _append(root, settings, events, parent, kind='resource_bind', owner=row['owner'],
            reservation_id=identity, reservation=reservation, accepted_binding=original)
        return row['ref']


def settle_execution(root, *, reservation, observation, consumed):
    from . import experiments, qualification as q
    root = Path(root).resolve(); settings = contracts.settings(root)
    value = q._read(root, observation)
    experiments.verify_observation(value, lambda item: Path(item['path']))
    consumed = _resource_dimensions(consumed, partial=True, zero=True)
    with _ledger_writer(root):
        events, parent, state = _events(root, settings, resources=True); identity, row = _reserved(state, reservation)
        bound = row['bound']
        if (bound is None or row['released'] or value.get('call_id') != row['binding']['call_id']
                or value.get('scope') != row['binding']['scope']
                or q._identity(root / bound['accepted']['path']) != value.get('accepted')):
            raise ValueError('Terminal observation does not bind the reserved execution')
        if any(key in row['actual'] and row['actual'][key] != amount for key, amount in consumed.items()):
            raise ValueError('Terminal resource usage conflicts with its original observation')
        if row['settled_observation'] == observation and all(row['actual'].get(key) == amount for key, amount in consumed.items()):
            return row['ref']
        _append(root, settings, events, parent, kind='resource_settle', owner=row['owner'],
            reservation_id=identity, reservation=reservation, observation=observation, actual=consumed)
        return row['ref']


def release_execution(root, *, reservation, retirement):
    from . import experiments, qualification as q
    root = Path(root).resolve(); settings = contracts.settings(root)
    value = q._read(root, retirement)
    verified = experiments.verify_retirement(value, lambda item: Path(item['path']))
    with _ledger_writer(root):
        events, parent, state = _events(root, settings, resources=True); identity, row = _reserved(state, reservation)
        bound = row['bound']; binding = row['binding']
        if (bound is None or row['actual'] or verified.get('call_id') != binding['call_id']
                or verified.get('scope') != binding['scope'] or verified.get('authority') != binding['authority']
                or verified.get('prepared') != bound['prepared']
                or verified.get('accepted') != {**bound['accepted'], 'path': str(root / bound['accepted']['path'])}):
            raise ValueError('Permanent retirement does not bind the original reserved preparation')
        if row['released']:
            return row['ref']
        _append(root, settings, events, parent, kind='resource_release', owner=row['owner'],
            reservation_id=identity, reservation=reservation, retirement=retirement)
        return row['ref']


def execution_accounting(root):
    root = Path(root).resolve(); settings = _resource_settings(root); owner = _resource_owner(settings)
    _, _, (grants, reservations) = _events(root, settings, resources=True)
    grant = grants.get(owner['key'])
    remaining = dict(grant['allowance']) if grant else dict(settings['execution_resource_policy']['allowance'])
    for row in reservations.values():
        if not row['released']:
            for key, amount in {**row['budget'], **row['actual']}.items():
                remaining[key] -= amount
    return dict(owner=owner, grant=grant, remaining=remaining, reservations=list(reservations.values()))


def execution_reservation(root, reservation):
    root = Path(root).resolve(); settings = contracts.settings(root)
    _, _, state = _events(root, settings, resources=True)
    return _reserved(state, reservation)[1]


def authorization_revision(root):
    """Only explicit ceiling amendments revise authority; clocks never do."""
    root = Path(root).resolve(); settings = contracts.settings(root)
    events, _ = _events(root, settings)
    changes = [event for event in events if event['kind'] == 'budget_amend']
    if changes:
        return _resource_ref(root, changes[-1])
    from . import qualification as q
    return q._ref(root, root / 'settings.json')


def execution_available(root, job, limits, usage):
    """Existing shared authorization minus its existing closeout floor; no grant."""
    settings = contracts.settings(root, job)
    if job.get('purpose') == 'closeout':
        begin = closeout_state(root)['begin']
        if begin is None:
            raise ValueError('Closeout execution has no original boundary')
        reserve = closeout_floor(settings, preparation_seconds=begin['preparation_seconds'])
        if job.get('engineering_repair') and _final_a_policy(settings) is None:
            author = settings['delivery_policy']['closeout_a']
            reserve = dict(reserve, seconds=reserve['seconds'] + author['max_seconds'],
                           tokens=reserve['tokens'] + author['max_observed_tokens'])
    else:
        reserve = delivery_reserve(settings) or dict(seconds=0, tokens=0)
    return dict(seconds=max(0., limits['seconds_remaining'] - reserve['seconds']),
                tokens=max(0, limits['max_tokens'] - usage.get('accounted_tokens', usage['observed_tokens']) - reserve['tokens']))


@contextmanager
def _ledger_writer(root, *, context=None):
    from .protocol import require_current
    from . import instance_owner
    with instance_owner.lease(root):
        require_current(root)
        fd = os.open(root / 'control-ledger.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            if context is None:fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                while True:
                    context.remaining_seconds()
                    try:
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:time.sleep(min(.05,context.remaining_seconds()))
            yield
        finally:
            os.close(fd)


def _append(root, settings, events, parent, **fields):
    from .protocol import require_current
    require_current(root)
    stamp = clock_stamp()
    event = dict(seq=len(events) + 1, parent_sha256=parent,
                 settings_sha256=contracts.digest(root / 'settings.json'), **stamp, **fields)
    if 'reanchor' in event:
        _verify_reanchor(root, settings, event, events)
    elif events:
        _duration(events[-1], stamp, legacy_elapsed=settings.get('time_budget_basis', 'elapsed') == 'elapsed')
    contracts.write(root / 'control-ledger' / f'{event["seq"]:06d}.json', event)
    return event


def paused_checkpoint(root):
    """Freeze only original paused ledger identity, never a second balance."""
    root = Path(root).resolve(); settings = contracts.settings(root)
    events, _ = _events(root, settings)
    return _paused_checkpoint(root, settings, events)


def reanchor_paused_checkpoint(root, checkpoint_ref, transfer_ref):
    """Only the exclusive cold owner can bridge a proven paused active clock."""
    from . import instance_owner
    root = Path(root).resolve()
    with _ledger_writer(root):
        value = instance_owner.verify_transition(root, checkpoint_ref, transfer_ref)
        settings = contracts.settings(root); events, parent = _events(root, settings)
        marker = dict(version=1, checkpoint=checkpoint_ref, transfer=transfer_ref)
        stamp = clock_stamp()
        for event in events:
            if event.get('reanchor') == marker:
                if event['boot_id'] != stamp['boot_id']:
                    raise ValueError('A cold clock transition cannot be reused on another boot')
                return dict(status='clock_already_reanchored', seq=event['seq'])
        if value['checkpoint']['budget'] != _paused_checkpoint(root, settings, events):
            raise ValueError('Cold checkpoint does not preserve the current original budget')
        started = Path(root) / 'host-started.json'
        before = events[-1] if events else contracts.read(started) if started.exists() else None
        if before is None or before['boot_id'] == stamp['boot_id']:
            return dict(status='clock_reanchor_not_needed')
        event = _append(root, settings, events, parent, kind='clock', phase='paused',
                        reason='verified_cold_checkpoint_restore', reanchor=marker)
        return dict(status='clock_reanchored', seq=event['seq'])


def _ceiling_limits(settings, events):
    """Fold verified immutable amendments without sampling current consumption."""
    limits = dict(max_tokens=settings['max_tokens'], max_seconds=settings['max_seconds'])
    max_steps = historical_steps = settings['max_steps']
    for event in events:
        if event['kind'] == 'budget_amend':
            if event.get('before') != limits:
                raise ValueError('Budget amendment predecessor changed')
            limits = event['after']
            if 'before_max_steps' in event:
                if event['before_max_steps'] != max_steps:
                    raise ValueError('Budget step amendment predecessor changed')
                max_steps = event['after_max_steps']
                historical_steps = max(historical_steps, max_steps)
    return limits, max_steps, historical_steps


def ceilings(root):
    """Read verified operator ceilings without sampling live consumption."""
    root = Path(root)
    settings = contracts.settings(root)
    events, _ = _events(root, settings)
    limits, max_steps, _ = _ceiling_limits(settings, events)
    return dict(**limits, max_steps=max_steps)


def snapshot(root, settings=None, *, sample_stamp=None):
    original = contracts.settings(root)
    if settings is not None and any(settings.get(key) != original.get(key) for key in ('goal_id', 'objective', 'public_root', 'global_materials')):
        raise ValueError('Budget view differs from original research identity')
    settings = original
    basis = settings.get('time_budget_basis', 'elapsed')
    if basis not in {'active', 'elapsed'}:
        raise ValueError('Unknown frozen time-budget basis')
    def interval(before, after):
        return _duration(before, after, legacy_elapsed=basis == 'elapsed')
    events, _ = _events(root, settings)
    limits, max_steps, _ = _ceiling_limits(settings, events)
    started = root / 'host-started.json'
    start = contracts.read(started) if started.exists() else None
    now = (clock_stamp() if events or start is not None else None) if sample_stamp is None else sample_stamp
    if events:
        interval(events[-1], now)
    bridged = any('reanchor' in event for event in events)
    if start is not None and not bridged:
        interval(start, now)
    if basis == 'active' and start is not None and 'at_boottime' not in start:
        raise ValueError('Active clock requires its original boot anchor')
    advance = paused = unclassified = waiting = 0.
    phase, at = ('paused' if basis == 'active' else 'unclassified'), start
    for index, event in enumerate(events):
        if event['kind'] != 'clock':
            continue
        if 'reanchor' in event:
            # Its predecessor phase is verified paused by _events. The idle
            # cross-boot gap is unmeasured, never refunded advancement/waiting.
            if at is not None and index:
                paused += interval(at, events[index - 1])
            at, phase = event, 'paused'
            continue
        if at is not None:
            duration = interval(at, event)
            if phase == 'advance':
                advance += duration
            elif phase == 'waiting':
                waiting += duration
            elif phase == 'paused':
                paused += duration
            else:
                unclassified += duration
        at, phase = event, event['phase']
    if at is not None:
        if phase == 'advance':
            advance += interval(at, now)
        elif phase == 'waiting':
            waiting += interval(at, now)
        elif phase == 'paused':
            paused += interval(at, now)
        else:
            unclassified += interval(at, now)
    elapsed = None if bridged else interval(start, now) if start is not None else 0.
    if basis == 'elapsed' and start is not None:
        elapsed = _duration(dict(at_unix=start['at_unix']), now)
    consumed = advance + waiting if basis == 'active' else elapsed
    return dict(**limits, max_steps=max_steps, original_max_steps=settings['max_steps'], time_budget_basis=basis, advance_seconds=advance, waiting_seconds=waiting,
                paused_seconds=paused, unclassified_seconds=unclassified, elapsed_seconds=elapsed, consumed_seconds=consumed,
                seconds_remaining=max(0., limits['max_seconds'] - consumed), clock_phase=phase,
                ledger_events=len(events), historical_time='unknown_before_ledger' if basis == 'elapsed' else
                'recorded_active_with_unmeasured_paused_gap' if bridged else 'recorded',
                **(dict(unmeasured_paused_gap=True) if bridged else {}))


def status_snapshot(root):
    """Read closed active consumption at its recorded endpoint, without now.

    Open intervals and legacy elapsed budgets retain strict live-clock reads.
    This projection is not an admission or a clock continuation permission.
    """
    root = Path(root)
    settings = contracts.settings(root)
    if settings.get('time_budget_basis', 'elapsed') != 'active':
        return snapshot(root, settings)
    events, _ = _events(root, settings)
    phase = next((event['phase'] for event in reversed(events) if event['kind'] == 'clock'), 'paused')
    if phase != 'paused':
        return snapshot(root, settings)
    started = root / 'host-started.json'
    stamp = events[-1] if events else contracts.read(started) if started.exists() else None
    value = snapshot(root, settings, sample_stamp=stamp)
    if started.exists():
        value.update(paused_seconds=None, elapsed_seconds=None)
    return value


def transition(root, phase, reason, *, operation_context=None):
    """Caller owns the host writer lock; runner must obtain it too."""
    from .protocol import require_current
    require_current(root)
    if phase not in {'advance', 'paused', 'waiting'}:
        raise ValueError('Unknown control clock phase')
    settings = contracts.settings(root)
    if not (root / 'host-started.json').exists():
        if phase == 'advance':
            raise ValueError('Advance clock requires an original host start')
        snapshot(root, settings)
        return
    with _ledger_writer(root,context=operation_context):
        events, parent = _events(root, settings)
        # Check rollback even when the phase is unchanged.
        snapshot(root, settings)
        current = next((item['phase'] for item in reversed(events) if item['kind'] == 'clock'), None)
        if current != phase:
            _append(root, settings, events, parent, kind='clock', phase=phase, reason=reason)


def execution_blocked(root, *, ignore_operation=None, include_managed=True, ignore_recovery=None, ignore_unstarted_action=None):
    """Unknown or live originals prohibit inactive time and amendments."""
    from . import runtime, execution
    settings = contracts.settings(root)
    from . import execution_recovery
    if execution_recovery.pending(root,unknown_only=not include_managed,ignore=ignore_recovery):return True
    from . import delivery
    from . import audit_recovery
    if audit_recovery.blocked(root):
        return True
    if delivery.preparation_blocked(root):
        return True
    from . import candidate_audit
    if candidate_audit.blocked(root):
        return True
    state = closeout_state(root)
    if state['begin'] is not None and state['begin']['stop_reason'] == 'delivery_cleanup_unknown':
        return True  # A read-only resolver can still leave unknown local work.
    for action in state['actions']:
        if ignore_unstarted_action == action['intent']['action_id']:
            from . import startup_recovery
            startup_recovery.unstarted_action(root,action['intent'])
            continue
        outcome = action['outcome']
        if outcome is not None and not outcome['result']['cleanup_confirmed']:
            return True
        if outcome is None:
            paths = list((root / 'host-delivery/action-processes').glob(action['intent']['action_id'] + '-*.json'))
            final_phase = 'promote' if action['intent']['action_kind'] == 'submit' else 'confirm'
            if not any(path.name == action['intent']['action_id'] + '-' + final_phase + '.json' for path in paths):
                return True  # Popen/identity persistence window is truly unknown.
            for path in paths:
                record = contracts.read(contracts.regular(path))
                identity = record.get('identity', {})
                if not isinstance(identity, dict):
                    return True
                if (record.get('action_id') != action['intent']['action_id'] or record.get('operation_id') != action['intent']['operation_id']
                        or record.get('phase') not in {'audit', 'promote', 'confirm'}
                        or path.name != action['intent']['action_id'] + '-' + record['phase'] + '.json'
                        or identity.get('task_sha256') != action['intent']['operation_id']
                        or identity.get('boot_id') != clock_stamp()['boot_id']
                        or any(type(identity.get(key)) is not int or identity[key] <= 0 for key in ('pid', 'start_ticks', 'pgid', 'sid'))
                        or runtime.process_matches(identity, action['intent']['operation_id'])
                        or _delivery_group_live(identity)):
                    return True
    if any(op != ignore_operation for op in state['unresolved_operations']):
        return True
    from . import fork_runtime
    for step in fork_runtime.local_steps(root, include_native=True):
        try:
            record = execution.record(root, step)
            if not (record / 'native-intent.json').exists():
                continue
            binding = contracts.digest(root / 'jobs' / f'{step:03d}.json')
            if contracts.read(record / 'native-intent.json')['task_sha256'] != binding:
                return True
            outcome = contracts.read(record / 'native-outcome.json')
            if outcome.get('stop_reason') in {'native_error_unknown', 'cleanup_unknown'}:
                return True
            if not (type(outcome.get('returncode')) is int or outcome.get('native_started') is False):
                return True
            if (record / 'native-process.json').exists():
                child = contracts.read(record / 'native-process.json')
                if runtime.process_matches(child, execution.identity(root, step)):
                    return True
        except (OSError, ValueError, KeyError, TypeError):
            return True
    if include_managed:
        from . import managed_wait
        if managed_wait.pending(root):return True
    return False


def _delivery_group_live(identity):
    """Read-only recovery check under the launcher's same-group contract."""
    from . import runtime
    for path in Path('/proc').glob('[0-9]*'):
        try:
            member = runtime.process_identity(int(path.name), identity['task_sha256'])
        except OSError as error:
            if error.errno in {errno.ENOENT, errno.ESRCH}:
                continue
            return True  # An unobservable member is not cleanup proof.
        except (ValueError, IndexError, KeyError, TypeError):
            return True
        if member['state'] != 'Z' and member['sid'] == identity['sid'] and member['pgid'] == identity['pgid']:
            return True
    return False


def inactive(root, reason):
    from .protocol import require_current
    require_current(root)
    if execution_blocked(root,include_managed=False):
        transition(root, 'advance', 'unknown_or_running_original')
    else:
        from . import managed_wait
        transition(root, 'waiting' if managed_wait.pending(root) else 'paused', reason)


def effective_max_steps(root):
    """Read the audited step ceiling without sampling current time or usage."""
    root = Path(root)
    settings = contracts.settings(root)
    events, _ = _events(root, settings)
    return _ceiling_limits(settings, events)[1]


def historical_step_limit(root):
    """Read original granted or consumed steps, never a new grant.

    A lower current ceiling cannot hide original consumption or accepted work.
    Only future effects use effective_max_steps; historical validators retain
    every prepared original, including the exceptional final A, and its checks.
    """
    root = Path(root)
    settings = contracts.settings(root)
    events, _ = _events(root, settings)
    return max(_ceiling_limits(settings, events)[2],
               max((int(path.stem) for path in (root / 'jobs').glob('*.json')), default=0))


step_limit = effective_max_steps


def task_step_available(root, step):
    """Check a new task's resource count independently of its logical label."""
    from . import fork_runtime
    if not fork_runtime.is_fork(root):
        return step <= step_limit(root)
    additional = int(step not in fork_runtime.local_steps(root))
    return used_steps(root) + additional <= step_limit(root)


def _validate_usage_reserve(root, event):
    if (type(event.get('step')) is not int or event['step'] <= 0
            or type(event.get('attempt')) is not int or event['attempt'] < 0
            or type(event.get('tokens')) is not int or event['tokens'] <= 0
            or event.get('scope') != 'author_closeout_and_successor_review_delivery'
            or event.get('accounting_basis') != 'operator_contingency_not_actual_usage_upper_bound'
            or any(not isinstance(event.get(key), str) or not event[key].strip()
                   for key in ('authorization_note', 'authorization_statement', 'record'))
            or not isinstance(event.get('originals'), dict)
            or set(event['originals']) != {'job', 'intent', 'outcome', 'usage', 'observation', 'stream', 'session'}):
        raise ValueError('Invalid operator unknown-usage contingency')
    record = root / event['record']
    if (Path(event['record']).is_absolute() or record.resolve() != record
            or not record.is_relative_to(root)):
        raise ValueError('Unknown-usage contingency changed its original record')
    expected = dict(job=root / 'jobs' / f'{event["step"]:03d}.json',
                    intent=record / 'native-intent.json', outcome=record / 'native-outcome.json',
                    usage=record / 'usage.json', observation=record / 'usage-observation.json',
                    stream=record / 'native.jsonl')
    for name, ref in event['originals'].items():
        if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'size_bytes'}
                or not isinstance(ref['path'], str) or Path(ref['path']).is_absolute()):
            raise ValueError('Unknown-usage contingency has an invalid original reference')
        path = root / ref['path']
        if (not path.is_relative_to(root) or path.resolve() != path
                or type(ref['size_bytes']) is not int or ref['size_bytes'] < 0
                or not isinstance(ref['sha256'],str) or len(ref['sha256']) != 64
                or any(char not in '0123456789abcdef' for char in ref['sha256'])):
            raise ValueError('Unknown-usage contingency original changed')
        if name in expected and path != expected[name]:
            raise ValueError('Unknown-usage contingency lost its exact native record')
    intent = contracts.read(expected['intent'])
    original = contracts.host_record(contracts.workspace(root, event['step']))
    expected_record = original if event['attempt'] == 0 else original / 'continuations' / f'{event["attempt"]:03d}'
    if record != expected_record or intent.get('attempt', 0) != event['attempt']:
        raise ValueError('Unknown-usage contingency changed its exact native attempt')
    if (contracts.digest(expected['job']) != event['originals']['job']['sha256']
            or contracts.digest(expected['intent']) != event['originals']['intent']['sha256']
            or intent.get('task_sha256') != event['originals']['job']['sha256']):
        raise ValueError('Unknown-usage contingency declared native identity changed')


def require_usage_reserve_eligibility(root, event):
    """Verify original unknown-usage facts separately from the signed charge."""
    from . import qualification as q
    _validate_usage_reserve(root,event)
    for ref in event['originals'].values():
        if q._ref(root,root/ref['path']) != ref:
            raise ValueError('Unknown-usage contingency original changed')
    expected = {name:root/ref['path'] for name,ref in event['originals'].items()}
    intent = contracts.read(expected['intent'])
    outcome = contracts.read(expected['outcome'])
    observation = contracts.read(expected['observation'])
    if (outcome.get('cleanup') != 'observed_group_cleaned' or type(outcome.get('returncode')) is not int
            or outcome.get('metering', observation).get('coverage') != 'unknown'
            or contracts.read(expected['intent']).get('task_sha256') != contracts.digest(expected['job'])):
        raise ValueError('Unknown-usage contingency has no exact cleaned unknown original')
    session_path = root / event['originals']['session']['path']
    home = Path(intent.get('native_home', intent.get('session', {}).get('home', str(root / 'native-homes' / f'{event["step"]:03d}'))))
    base = root / 'native-homes' / f'{event["step"]:03d}'
    if (home.resolve() != home or home != base and (home.parent != base or not home.name.startswith('context-')
            or not home.name[8:].isdigit()) or not session_path.is_relative_to(home / 'sessions')):
        raise ValueError('Unknown-usage session is outside its original native home')
    with session_path.open() as stream:
        meta = json.loads(stream.readline())
    thread = outcome.get('thread_id')
    if (not isinstance(thread, str) or not thread or meta.get('type') != 'session_meta'
            or meta.get('payload', {}).get('id') != thread or observation.get('thread_id') != thread
            or meta.get('payload', {}).get('cwd') != str(contracts.workspace(root, event['step']))
            or observation.get('tokens') != contracts.read(expected['usage'])):
        raise ValueError('Unknown-usage contingency changed its exact session or observed counter')


def usage_accounting(root, meter):
    """Retain reported consumption and unknown coverage without estimated fees."""
    root = Path(root).resolve()
    events, _ = _events(root, contracts.settings(root))
    reserves = [event for event in events if event['kind'] == 'native_usage_reserve']
    for event in reserves:require_usage_reserve_eligibility(root,event)
    records = set()
    for event in reserves:
        if event['record'] in records:
            raise ValueError('Unknown native usage was charged more than once')
        records.add(event['record'])
    from . import startup_recovery
    reconciled = []
    for permit in (root/'host-recovery/native-startup').glob('[0-9]*.json'):
        if not permit.stem.isdigit():continue
        value = startup_recovery.reconciled_original(root,int(permit.stem))
        reconciled.append(dict(step=value['step'],attempt=0,record=str(Path(value['originals']['intent']['path']).parent.relative_to(root)),
            provider_model_tokens=0,reconciliation_sha256=value['reconciliation']['sha256']))
    # Historical reserve declarations remain immutable and validated above.
    # Missing billing coverage neither invents usage nor grants replay;
    # unresolved execution is checked through its own retained facts.
    return dict(reserved_tokens=0, accounted_tokens=meter['observed_tokens'],
        reserved_unknown_steps=[], blocking_unknown_steps=[], accounting_basis='observed_lower_bound',
        **(dict(reconciled_not_submitted_attempts=reconciled) if reconciled else {}))


def amend(root, *, max_tokens=None, max_seconds=None, max_steps=None, authorization_note, authorization_statement=None):
    from .controller import _writer
    with _writer(root):
        return _amend_locked(root, max_tokens=max_tokens, max_seconds=max_seconds, max_steps=max_steps,
                             authorization_note=authorization_note, authorization_statement=authorization_statement)


def _validate_amendment(root, *, max_tokens=None, max_seconds=None, max_steps=None, authorization_note, authorization_statement=None):
    """Operator entry only. A note records a decision; it authenticates nobody."""
    from .protocol import require_current
    require_current(root)
    if not isinstance(authorization_note, str) or not authorization_note.strip():
        raise ValueError('An explicit operator authorization note is required')
    if max_tokens is None and max_seconds is None and max_steps is None:
        raise ValueError('Specify a revised token, seconds or step ceiling')
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise ValueError('max_tokens must be a positive integer')
    if max_seconds is not None and (type(max_seconds) not in (int, float) or not 0 < max_seconds < float('inf')):
        raise ValueError('max_seconds must be positive and finite')
    if max_steps is not None and (type(max_steps) is not int or max_steps <= 0):
        raise ValueError('max_steps must be a positive integer')
    if max_steps is not None and authorization_statement is None:
        raise ValueError('A step ceiling amendment requires the original operator statement')
    if authorization_statement is not None and (not isinstance(authorization_statement, str) or not authorization_statement.strip()):
        raise ValueError('The original operator statement must be nonempty')


def _amend_locked(root, *, max_tokens=None, max_seconds=None, max_steps=None, authorization_note, authorization_statement=None, operation_id=None):
    """Original amendment policy, called only by an existing controller writer."""
    _validate_amendment(root, max_tokens=max_tokens, max_seconds=max_seconds, max_steps=max_steps,
                      authorization_note=authorization_note, authorization_statement=authorization_statement)
    if execution_blocked(root):
        raise RuntimeError('Budget amendment requires no running or unknown original execution')
    settings = contracts.settings(root)
    with _ledger_writer(root):
        events, parent = _events(root, settings)
        if operation_id is not None:
            prior = next((event for event in events if event.get('operation_id') == operation_id), None)
            if prior is not None:
                return dict(status='budget_amended', amendment_seq=prior['seq'], before=prior['before'], after=prior['after'], consumption='retained')
        value = snapshot(root, settings)
        before = {key: value[key] for key in ('max_tokens', 'max_seconds')}
        after = dict(before, **({} if max_tokens is None else dict(max_tokens=max_tokens)),
                     **({} if max_seconds is None else dict(max_seconds=max_seconds)))
        event = _append(root, settings, events, parent, kind='budget_amend', **(dict(operation_id=operation_id) if operation_id else {}), before=before,
                        after=after, authorization_note=authorization_note.strip(),
                        **(dict(before_max_steps=value['max_steps'], after_max_steps=max_steps, max_steps_delta=max_steps-value['max_steps']) if max_steps is not None else {}),
                        **(dict(authorization_statement=authorization_statement.strip()) if authorization_statement is not None else {}))
    return dict(status='budget_amended', amendment_seq=event['seq'], before=before, after=after,
                consumption='retained', authorization='operator_declaration_not_authentication',
                **(dict(before_max_steps=value['max_steps'], after_max_steps=max_steps, max_steps_delta=max_steps-value['max_steps']) if max_steps is not None else {}))

class NodeBudgetUnavailable(ValueError):
    """An unaccepted node cannot fit its remaining authorized allocation."""
    def __init__(self, reason):
        super().__init__('Node budget admission refused: ' + reason)
        self.reason = reason


def node_mode(root):
    mode = contracts.settings(root).get('node_budget_mode')
    if mode != 'soft':
        raise ValueError('Preserved hard or missing node budget mode is read-only; prepare a new soft instance')
    return mode


def node_policy(value):
    """Explicit operator ceilings for every role; there are no trial defaults."""
    if not isinstance(value, dict) or set(value) != {'H', 'W', 'A', 'E'}:
        raise ValueError('Node budget policy requires exactly H/W/A/E')
    for role, limits in value.items():
        if (not isinstance(limits, dict) or set(limits) != {'max_observed_tokens', 'max_seconds'}
                or type(limits['max_observed_tokens']) is not int or limits['max_observed_tokens'] <= 0
                or type(limits['max_seconds']) not in (int, float)
                or not 0 < limits['max_seconds'] < float('inf')):
            raise ValueError(f'Node budget {role} requires positive finite explicit ceilings')
    return {role: dict(limits) for role, limits in value.items()}


def node_limits(root, job):
    from . import configuration_updates
    settings = configuration_updates.for_new_job(root, job['step']) if 'configuration_revision' not in job else contracts.settings(root, job)
    policy = settings.get('node_budget_policy')
    if policy is None:
        if job.get('final_only') and job.get('role') == 'A':
            if job.get('purpose') == 'closeout' and settings.get('delivery_policy') is not None:
                return dict(settings['delivery_policy']['closeout_a'])
            return dict(max_observed_tokens=settings['max_tokens'], max_seconds=settings['max_seconds'])
        return None
    limits = node_policy(policy)[job['role']]
    if job.get('purpose') == 'closeout' and job['role']=='A':
        delivery = settings.get('delivery_policy')
        if delivery is None or job['role'] != 'A':
            raise ValueError('Unconfigured closeout node budget')
        limits = dict(delivery['closeout_a'])
    elif job['role'] in ('H', 'A') and settings.get('role_budget_policy') is not None and settings.get('node_budget_mode', 'hard') == 'soft':
        # A soft review/hint target only; hard mode keeps its explicit operator ceilings.
        limits['max_observed_tokens'] = min(limits['max_observed_tokens'], settings['role_budget_policy']['thinking_node_target_tokens'])
    if job['role'] == 'W' and settings.get('search_policy') is not None:
        limits['max_seconds'] = min(limits['max_seconds'], settings['search_policy']['max_w_seconds'])
        limits['max_observed_tokens'] = min(limits['max_observed_tokens'], settings['search_policy']['max_w_observed_tokens'])
    return limits


def _final_a_policy(settings):
    value = settings.get('final_a_completion_policy')
    if value is not None and (not isinstance(value, dict) or set(value) != {'version'}
            or type(value['version']) is not int or value['version'] != 1):
        raise ValueError('Frozen final A completion policy changed')
    return value


def protected_final_report(settings):
    """The frozen completion policy protects one explicitly configured final A."""
    policy = settings.get('delivery_policy')
    if (_final_a_policy(settings) is None or settings.get('node_budget_policy') is None
            or policy is not None and policy['adapter'] is not None):
        return False
    return True


def _final_a_reserve(settings, job, events):
    if settings.get('delivery_policy') is None:
        return dict(tokens=0, seconds=0., steps=0)
    preparation = 0.
    if job.get('purpose') == 'closeout':
        begin = next((row for row in reversed(events) if row['kind'] == 'closeout_begin'), None)
        if (begin is None or begin['mode'] not in {'model', 'final'} or job.get('source_cutoff') != begin['source_cutoff']
                or job['step'] < begin['first_step']):
            raise ValueError('Final A has no original model closeout boundary')
        preparation = begin['preparation_seconds']
    return closeout_floor(settings, preparation_seconds=preparation)


def stopped_report_original(root, step):
    """Bind a cleaned stopped native task; its scientific output stays unqualified."""
    job_path = Path(root) / 'jobs' / f'{step:03d}.json'
    record = contracts.host_record(contracts.workspace(root, step))
    names = ['native-intent.json', 'native-outcome.json', 'usage-observation.json'] if (record / 'native-intent.json').exists() else []
    if (record / 'usage.json').exists():
        names.append('usage.json')
    value = dict(step=step, job_sha256=contracts.digest(contracts.regular(job_path)),
                 records={name: contracts.digest(contracts.regular(record / name)) for name in names})
    _validate_stopped_report(root, value)
    return value


def _validate_stopped_report(root, value):
    if (not isinstance(value, dict) or set(value) != {'step', 'job_sha256', 'records'}
            or type(value['step']) is not int or value['step'] <= 0 or not isinstance(value['records'], dict)
            or set(value['records']) not in (set(), {'native-intent.json', 'native-outcome.json', 'usage-observation.json'},
                    {'native-intent.json', 'native-outcome.json', 'usage-observation.json', 'usage.json'})):
        raise ValueError('Final report requires the exact stopped original')
    path = Path(root) / 'jobs' / f'{value["step"]:03d}.json'
    job = contracts.read(contracts.regular(path))
    record = contracts.host_record(contracts.workspace(root, value['step']))
    if (contracts.digest(path) != value['job_sha256']
            or (record / 'result-identity.json').exists() or (record / 'continuations').exists()
            or any(contracts.digest(contracts.regular(record / name)) != proof for name, proof in value['records'].items())):
        raise ValueError('Stopped report original identity changed')
    if not value['records']:
        from . import experiment_client, qualification
        qualification.validate_inputs(root, job)
        managed = experiment_client.record_directory(root, value['step'])
        if (any(path.name not in {'science-result.schema.json', 'input-provenance.json'} for path in record.iterdir())
                or managed.exists() and any(managed.iterdir())):
            raise ValueError('Unstarted report predecessor has native or managed execution records')
        return value
    intent = contracts.read(record / 'native-intent.json')
    outcome = contracts.read(record / 'native-outcome.json')
    if (intent.get('task_sha256') != value['job_sha256'] or 'attempt' in intent
            or type(outcome.get('returncode')) is not int or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('stop_reason') not in {'closeout_protected_token_limit', 'closeout_protected_time_limit',
                'token_limit', 'wall_limit', 'node_slice_token_limit', 'node_slice_time_limit'}):
        raise ValueError('Final report cannot summarize a live or unknown-effect original')
    return value


def validate_final_report_inputs(root, job, *, events=None):
    value = job.get('final_report')
    if value is None:
        return None
    settings = contracts.settings(root, job)
    delivery = settings.get('delivery_policy')
    if events is None:
        events, _ = _events(root, settings)
    begin = _final_boundary(events, job)
    final = begin is not None
    if (not (protected_final_report(settings) or final)
            or job['role'] != 'A' or job.get('purpose') is not None or job.get('formal_review') is not None
            or not final and delivery is not None and delivery['adapter'] is not None
            or not isinstance(value, dict) or set(value) != {'source_cutoff', 'stopped_original'}
            or type(value['source_cutoff']) is not int or not 0 <= value['source_cutoff'] < job['step']):
        raise ValueError('Invalid final report input boundary')
    stopped = value['stopped_original']
    if stopped is not None:
        _validate_stopped_report(root, stopped)
    if final:
        if value['source_cutoff'] != begin['source_cutoff'] or stopped != begin.get('stopped_original'):
            raise ValueError('Final report changed its durable input boundary')
        for ref in job.get('direct_refs', []):
            number = int(Path(ref['path']).parts[2])
            if number > value['source_cutoff']:
                prior = contracts.read(Path(root) / 'jobs' / f'{number:03d}.json')
                if (prior.get('purpose') != 'closeout' or prior.get('source_cutoff') != value['source_cutoff']
                        or not (contracts.host_record(contracts.workspace(root,number))/'result-identity.json').exists()):
                    raise ValueError('Final report cannot include unaccepted scientific originals')
    elif stopped is not None:
        if stopped['step'] != value['source_cutoff'] + 1 or job['step'] != stopped['step'] + 1:
            raise ValueError('Final report can omit only its exact stopped predecessor')
    elif value['source_cutoff'] != job['step'] - 1:
        raise ValueError('Final report cannot omit unrelated scientific originals')
    if not final and any(int(Path(ref['path']).parts[2]) > value['source_cutoff'] for ref in job.get('direct_refs', [])):
        raise ValueError('Stopped pending output is not a scientific parent')
    return value


def final_a_usage_unknown(accepted, usage, *, ignore_step=None):
    if accepted is not None and accepted.get('version') == 2 and accepted.get('basis') == 'final_state':
        # The exact durable FINAL grant permits its one original completion;
        # billing uncertainty remains charged and never grants another turn.
        return False
    allowed = {ignore_step} if ignore_step is not None else set()
    if accepted is not None and accepted['selection'].get('stopped_original') is not None:
        stopped = accepted['selection']['stopped_original']
        if stopped is not None:
            allowed.add(stopped['step'])
    return any(step not in allowed for step in usage.get('blocking_unknown_steps', usage['unknown_steps']))


def _final_a_basis(root, settings, job, policy, shared, selection, reserve):
    if (job.get('role') != 'A' or job.get('formal_review') is not None
            or job.get('purpose') not in (None, 'closeout')):
        return None
    remaining_tokens = shared['max_tokens'] - selection['accounted_tokens']
    remaining_steps = selection['max_steps'] - selection['used_steps']
    blocked_role = selection['blocked_role']
    if blocked_role is not None:
        delivery = settings.get('delivery_policy')
        if not protected_final_report(settings) or job.get('purpose') is not None or delivery is not None and delivery['adapter'] is not None:
            return None
        return 'protected_final_report'
    if job.get('purpose') is None:
        return ('last_authorized_node' if remaining_steps <= reserve['steps']
                or remaining_tokens < policy['max_observed_tokens'] + reserve['tokens']
                or selection['seconds_remaining'] < policy['max_seconds'] + reserve['seconds'] else None)
    predecessor = Path(root) / 'jobs' / f'{job["step"]-1:03d}.json'
    if predecessor.exists():
        prior = contracts.read(contracts.regular(predecessor))
        if (prior['role'] == 'W' and prior.get('purpose') in (None, 'closeout') and prior.get('engineering_repair')
                and not (contracts.host_record(contracts.workspace(root, prior['step'])) / 'native-intent.json').exists()):
            return 'last_closeout_allocation'
    worker = node_policy(settings['node_budget_policy'])['W']
    next_tokens = max(2 * policy['max_observed_tokens'], worker['max_observed_tokens'] + policy['max_observed_tokens']) if settings.get('node_budget_mode', 'hard') == 'hard' else 2 * policy['max_observed_tokens']
    next_seconds = max(2 * policy['max_seconds'], worker['max_seconds'] + policy['max_seconds']) if settings.get('node_budget_mode', 'hard') == 'hard' else 2 * policy['max_seconds']
    # The frozen completion policy authorizes one original final A even when
    # shared accounting has already crossed its ceilings. Per-call time remains
    # finite; this is not a new allocation or permission for follow-up science.
    if (remaining_tokens < next_tokens + reserve['tokens']
            or selection['seconds_remaining'] < next_seconds + reserve['seconds']
            or remaining_steps < 1 + reserve['steps']):
        return 'last_closeout_allocation'
    return None


def _active_final(events):
    phase = next((row for row in reversed(events) if row['kind'] in {'closeout_begin','closeout_resume'}), None)
    return phase if phase is not None and phase['kind'] == 'closeout_begin' and phase['mode'] == 'final' else None


def _final_boundary(events, job):
    return next((row for row in reversed(events) if row['kind'] == 'closeout_begin' and row['mode'] == 'final'
        and job.get('role') == 'A' and job.get('formal_review') is None and job.get('final_only')
        and job['step'] == row['first_step']), None)


def normal_resume(root, before_step=None, *, events=None):
    if events is None:
        events, _ = _events(root, contracts.settings(root))
    return next((row for row in reversed(events) if row['kind'] == 'closeout_resume'
        and (before_step is None or row['first_step'] <= before_step)), None)


def _event_ref(root, event):
    return dict(seq=event['seq'], sha256=contracts.digest(Path(root)/'control-ledger'/f'{event["seq"]:06d}.json'))


def _validate_cursor(root, cursor, before_step, events):
    from . import fork_runtime
    fields = {'role','parents','instruction','evidence_return','correction_request','author_correction','source_refs'}
    if (not isinstance(cursor,dict) or set(cursor) != fields or cursor['role'] not in ('H','W','A','E')
            or not isinstance(cursor['instruction'],str) or not isinstance(cursor['parents'],list)
            or not isinstance(cursor['source_refs'],list)):
        raise ValueError('Final resume requires its exact pending scientific route')
    steps = []
    for ref in cursor['source_refs']:
        number = ref.get('step')
        if (set(ref) != {'step','seal_sha256'} or type(number) is not int or number <= 0
                or not isinstance(ref['seal_sha256'],str) or len(ref['seal_sha256']) != 64
                or any(char not in '0123456789abcdef' for char in ref['seal_sha256'])
                or fork_runtime.source_job(root, number).get('final_only')):
            raise ValueError('Final resume source differs from declared normal science')
        steps.append(number)
    resumed = normal_resume(root,before_step,events=events)
    if resumed is None:
        expected,first = [],1
    else:
        prior = next(row for row in events if row['seq'] == resumed['source_final']['seq'])
        expected = [ref['step'] for ref in prior['resume_cursor']['source_refs']]
        first = resumed['first_step']
    expected += [number for number in range(first,before_step)
        if (Path(root)/'jobs'/f'{number:03d}.json').exists()
        and not contracts.read(Path(root)/'jobs'/f'{number:03d}.json').get('final_only')]
    if fork_runtime.is_fork(root):
        expected = fork_runtime.science_steps(root, before_step)
    if (steps != sorted(set(steps)) or any(number not in steps for number in cursor['parents']) or steps != expected):
        raise ValueError('Final resume parents are outside its exact declared sequence')


def require_cursor_eligibility(root, cursor, before_step, events):
    """Check accepted seals at the routing boundary, never during grant replay."""
    _validate_cursor(root,cursor,before_step,events)
    if cursor['source_refs'] != _normal_source_refs(root,before_step,events):
        raise ValueError('Final resume source differs from accepted normal science')
    for ref in cursor['source_refs']:
        from . import fork_runtime
        if (Path(root)/'jobs'/f'{ref["step"]:03d}.json').exists():
            contracts.validate(contracts.workspace(root,ref['step']))
        elif not fork_runtime.is_fork(root):
            raise ValueError('Final resume scientific original is missing')
        elif contracts.digest(fork_runtime.source_record(root, ref['step'], 'result-identity.json')) != ref['seal_sha256']:
            raise ValueError('Final resume inherited scientific original changed')


def _threshold_raised(events, after_seq):
    latest = next((row for row in reversed(events) if row['kind'] == 'budget_amend' and row['seq'] > after_seq
                   and not str(row.get('operation_id', '')).startswith('configuration-')), None)
    return latest is not None and (any(latest['after'][key] > latest['before'][key] for key in ('max_tokens','max_seconds'))
        or latest.get('after_max_steps',0) > latest.get('before_max_steps',0))


def _validate_resume_event(root, event, previous):
    begin = _active_final(previous)
    if (begin is None or begin.get('resume_cursor') is None
            or event.get('source_final') != _event_ref(root,begin)
            or type(event.get('first_step')) is not int or event['first_step'] <= begin['first_step']
            or not _threshold_raised(previous,begin['seq'])):
        raise ValueError('Normal resume requires an explicit raised threshold and exact final cursor')
    _validate_cursor(root,begin['resume_cursor'],begin['source_cutoff']+1,previous)


def resume_final(root, begin, first_step):
    """Publish normal routing only; admission and quiescence are checked by run."""
    settings = contracts.settings(root)
    with _ledger_writer(root):
        events,parent = _events(root,settings)
        value = dict(kind='closeout_resume', source_final=_event_ref(root,begin), first_step=first_step)
        # Validate before publication, preserving the original final cycle.
        probe = dict(value,seq=len(events)+1)
        _validate_resume_event(root,probe,events)
        require_cursor_eligibility(root,begin['resume_cursor'],begin['source_cutoff']+1,events)
        return _append(root,settings,events,parent,**value)


def resume_cursor(root, resume, *, events=None):
    if events is None:
        events,_ = _events(root,contracts.settings(root))
    if not any(row['kind'] == 'closeout_resume' and row == resume for row in events):
        raise ValueError('Normal resume must be the exact immutable journal event')
    begin = next(row for row in events if row['seq'] == resume['source_final']['seq'])
    if resume['source_final'] != _event_ref(root,begin):
        raise ValueError('Normal resume changed its final source')
    return begin['resume_cursor']


def _normal_source_refs(root, before_step, events):
    from . import fork_runtime
    if fork_runtime.is_fork(root):
        return [dict(step=number, seal_sha256=contracts.digest(fork_runtime.source_record(root, number, 'result-identity.json')))
            for number in fork_runtime.science_steps(root, before_step)]
    resumed = normal_resume(root,before_step,events=events)
    if resumed is None:
        refs,first = [],1
    else:
        prior = next(row for row in events if row['seq'] == resumed['source_final']['seq'])
        refs = list(prior['resume_cursor']['source_refs'])
        first = resumed['first_step']
    for number in range(first,before_step):
        path = Path(root)/'jobs'/f'{number:03d}.json'
        seal = contracts.host_record(contracts.workspace(root,number))/'result-identity.json'
        if path.exists() and seal.exists() and not contracts.read(path).get('final_only'):
            refs.append(dict(step=number,seal_sha256=contracts.digest(contracts.regular(seal))))
    return refs


def scientific_steps(root, before_step, *, resume=None):
    """Only a journal-bound resume may omit preserved non-scientific originals."""
    events,_ = _events(root,contracts.settings(root))
    resumed = normal_resume(root,before_step,events=events)
    if resume is not None and resume != resumed:
        raise ValueError('Scientific history requires its exact original resume event')
    if resumed is None:
        from . import fork_runtime
        if fork_runtime.is_fork(root):
            return fork_runtime.science_steps(root, before_step)
        return list(range(1,before_step))
    resume_cursor(root,resumed,events=events)
    return [ref['step'] for ref in _normal_source_refs(root,before_step,events)]


def final_a_admission(root, job, *, final_report=None):
    """Final eligibility comes only from the persisted phase, never its balance."""
    settings = contracts.settings(root, job)
    if _final_a_policy(settings) is None:
        return None
    events, _ = _events(root, settings)
    boundary = _final_boundary(events, job)
    active = _active_final(events)
    return 'final_state' if boundary is not None and active is not None and boundary['seq'] == active['seq'] else None


def _validate_final_a_event(root, settings, event, previous):
    """Replay only the original admission facts; never sample live budget here."""
    fields = {'seq', 'parent_sha256', 'settings_sha256', 'at_unix', 'at_boottime', 'boot_id',
              'kind', 'version', 'step', 'attempt', 'basis', 'anchor', 'selection', 'reserve'}
    if (_final_a_policy(settings) is None or (event.get('version') == 1 and settings.get('node_budget_policy') is None) or set(event) != fields
            or type(event['version']) is not int or event['version'] not in (1, 2)
            or type(event['step']) is not int or event['step'] <= 0
            or type(event['attempt']) is not int or event['attempt'] != 0
            or any(row['kind'] == 'final_a_return' and (event['version'] == 1 or row['step'] == event['step']) for row in previous)):
        raise ValueError('Invalid or reused final A return authorization')
    job_path = Path(root) / 'jobs' / f'{event["step"]:03d}.json'
    job = contracts.read(contracts.regular(job_path))
    settings = contracts.settings(root, job)
    anchor, selection, reserve = event['anchor'], event['selection'], event['reserve']
    ceilings, max_steps, _ = _ceiling_limits(contracts.settings(root), previous)
    if (not isinstance(anchor, dict) or set(anchor) != {'task_sha256', 'role', 'boot_id', 'advance_seconds', 'limits', 'shared_limits'}
            or anchor['task_sha256'] != contracts.digest(job_path) or anchor['role'] != 'A'
            or anchor['boot_id'] != event['boot_id']
            or type(anchor['advance_seconds']) not in (int, float) or not math.isfinite(anchor['advance_seconds'])
            or anchor['advance_seconds'] < 0 or not isinstance(anchor['limits'], dict) or anchor['limits'] != node_limits(root, job)
            or anchor['shared_limits'] != ceilings
            or not isinstance(selection, dict) or set(selection) != {'max_steps', 'used_steps', 'accounted_tokens', 'seconds_remaining', 'blocked_role', 'stopped_original'}
            or selection['blocked_role'] not in (None, 'H', 'W', 'A', 'E')
            or type(selection['max_steps']) is not int or selection['max_steps'] != max_steps
            or type(selection['used_steps']) is not int or not _selection_accounts_job(root, job, selection['used_steps'])
            or type(selection['accounted_tokens']) is not int or selection['accounted_tokens'] < 0
            or type(selection['seconds_remaining']) not in (int, float)
            or not math.isfinite(selection['seconds_remaining']) or not 0 <= selection['seconds_remaining'] <= ceilings['max_seconds']
            or reserve != _final_a_reserve(settings, job, previous)
            or (event['version'] == 1 and (event['basis'] != _final_a_basis(root, settings, job, anchor['limits'], ceilings, selection, reserve)
                or event['basis'] not in {'last_authorized_node', 'last_closeout_allocation', 'protected_final_report'}))
            or (event['version'] == 2 and (event['basis'] != 'final_state' or _final_boundary(previous, job) is None
                or _active_final(previous) != _final_boundary(previous,job)))):
        raise ValueError('Final A return differs from its exact original admission')
    report = job.get('final_report')
    if report is not None and (not isinstance(report,dict) or set(report) != {'source_cutoff','stopped_original'}
            or type(report['source_cutoff']) is not int or not 0 <= report['source_cutoff'] < job['step']):
        raise ValueError('Final A return changed its declared report boundary')
    boundary = _final_boundary(previous, job) if event['version'] == 2 else None
    expected_stopped = boundary.get('stopped_original') if boundary is not None else report['stopped_original'] if report else None
    if (selection['stopped_original'] != expected_stopped
            or (event['version'] == 1 and (event['basis'] == 'protected_final_report') != (report is not None))):
        raise ValueError('Final A return changed its exact report input boundary')


def final_a_return(root, job, intent, *, events=None):
    """Validate one final A identity, including its bounded output-only repairs."""
    settings = contracts.settings(root, job)
    _final_a_policy(settings)
    if events is None:
        events, _ = _events(root, settings)
    marker = (intent.get('budget_slice') or {}).get('final_return')
    accepted = next((row for row in events if row['kind'] == 'final_a_return'
        and (row['seq'] == marker.get('seq') if isinstance(marker,dict) else row['step'] == job['step'])), None)
    if marker is None:
        if accepted is not None and accepted['step'] == job['step']:
            raise ValueError('Final A return reference is missing from its original intent')
        return None
    startup = intent.get('continuation_kind') == 'startup_successor'
    repair = intent.get('continuation_kind') == 'repair' and isinstance(intent.get('output_repair'),dict) and 'work_repair' not in intent
    if startup:
        from . import startup_recovery
        startup_recovery.validate_intent(root,job,intent)
    expected_anchor = dict(accepted['anchor'], final_return=marker) if accepted is not None else None
    if repair and accepted is not None:
        number=intent.get('attempt')
        limits=settings.get('resume_policy') or {}
        if (type(number) is not int or not 1 <= number <= limits.get('max_attempts',0)
                or not limits.get('output_repair',False)):
            raise ValueError('Final A return has no bounded output repair authorization')
        from . import continuation, execution
        first=execution.origin(root,job['step'])
        prior=first if number==1 else first/'continuations'/f'{number-1:03d}'
        if (intent.get('root_intent_sha256')!=contracts.digest(contracts.regular(first/'native-intent.json'))
                or intent.get('previous')!=execution.proof(prior)):
            raise ValueError('Final A return output repair changed its exact source')
        continuation.validate_repair(root,job['step'],intent,prior_record=prior,verify_task=False)
        # Validate cumulative consumption in _node_duration; retain the original
        # ceilings, task and final-return marker while advancing this call's clock.
        expected_anchor.update({key:intent['budget_slice'].get(key) for key in
            ('advance_seconds','previous_tokens','previous_seconds')})
    if (accepted is None or _final_a_policy(settings) is None or ('attempt' in intent and not (startup or repair))
            or intent.get('continuation_kind') is not None and not (startup or repair) or intent.get('step') != job['step']
            or job['role'] != 'A' or job.get('formal_review') is not None
            or intent.get('task_sha256') != accepted['anchor']['task_sha256']
            or accepted['step'] != job['step']
            or not isinstance(marker, dict) or set(marker) != {'seq', 'sha256'}
            or type(marker['seq']) is not int or marker['seq'] != accepted['seq']
            or marker['sha256'] != contracts.digest(Path(root) / 'control-ledger' / f'{accepted["seq"]:06d}.json')
            or intent['budget_slice'] != expected_anchor):
        raise ValueError('Final A return reference or original attempt changed')
    return accepted


def final_a_barrier(root, *, before_step=None):
    """Only the active FINAL cycle bars normal science; historical v1 stays frozen."""
    events,_ = _events(root,contracts.settings(root))
    active = _active_final(events)
    return next((row for row in reversed(events) if row['kind'] == 'final_a_return'
        and (row['version'] == 1 or active is not None and row['step'] == active['first_step'])
        and (before_step is None or row['step'] < before_step)), None)


def _accept_final_a(root, job, anchor, limits, *, final_report=None):
    settings = contracts.settings(root, job)
    if _final_a_policy(settings) is None:
        return anchor
    with _ledger_writer(root):
        events, parent = _events(root, settings)
        active = _active_final(events)
        accepted = next((row for row in events if row['kind'] == 'final_a_return'
            and (row['version'] == 1 or active is not None and row['step'] == active['first_step'])), None)
        if accepted is not None:
            if accepted['step'] != job['step'] or accepted['anchor']['task_sha256'] != anchor['task_sha256']:
                raise NodeBudgetUnavailable('final_a_return_consumed')
        else:
            from . import native
            meter = native.usage(root)
            selection = dict(max_steps=limits['max_steps'], used_steps=used_steps(root),
                accounted_tokens=meter.get('accounted_tokens', meter['observed_tokens']),
                seconds_remaining=limits['seconds_remaining'], blocked_role=final_report['blocked_role'] if final_report else None,
                stopped_original=(_final_boundary(events, job) or {}).get('stopped_original'))
            reserve = _final_a_reserve(settings, job, events)
            basis = 'final_state' if _final_boundary(events, job) is not None else None
            if basis is None:
                if final_report is not None:
                    raise NodeBudgetUnavailable('final_a_report_admission_unavailable')
                return anchor
            fields = dict(kind='final_a_return', version=2, step=job['step'], attempt=0,
                          basis=basis, anchor=anchor, selection=selection, reserve=reserve)
            # Publish once before the intent. A crash before intent publication
            # can only reuse this same exact acceptance, never select a new A.
            accepted = _append(root, settings, events, parent, **fields)
        marker = dict(seq=accepted['seq'], sha256=contracts.digest(Path(root) / 'control-ledger' / f'{accepted["seq"]:06d}.json'))
        return dict(accepted['anchor'], final_return=marker)


def final_a_receipt(accepted, intent, meter, shared):
    """An explicit overrun receipt, never an amendment or a refunded meter."""
    observed = None if meter is None else meter['input_tokens'] + meter['output_tokens'] + intent['budget_slice'].get('previous_tokens',0)
    accounted = shared.get('accounted_tokens', shared['observed_tokens'])
    ceiling = accepted['anchor']['shared_limits']['max_tokens']
    target = accepted['anchor']['limits']['max_observed_tokens']
    receipt = dict(final_return=intent['budget_slice']['final_return'], scope='original_final_a_only',
        final_overrun=dict(original_shared_max_tokens=ceiling, original_local_target_tokens=target,
            accounted_shared_tokens=accounted, observed_node_tokens=observed,
            shared_tokens_over_ceiling=max(0, accounted-ceiling),
            local_tokens_over_target=None if observed is None else max(0, observed-target)),
        accounting='All actual usage retained; original ceilings unchanged; no further scientific dispatch.')
    if meter is None or accepted['step'] in shared.get('unknown_steps', []):
        receipt['final_overrun']['usage_coverage'] = 'unknown'
    return receipt


def native_completion_known(outcome, settings=None):
    """Execution completion is independent of token-accounting coverage."""
    if settings is None:
        # Older callers can recognize only the original Codex evidence shape;
        # a new source string never selects its own completion protocol.
        expected_backend, terminal_source = 'codex', 'turn.completed'
    else:
        from . import backends
        backend = backends.selected(settings)
        expected_backend, terminal_source = backend.name, backend.terminal_source
    return (type(outcome.get('returncode')) is int and outcome['returncode'] == 0
            and not outcome.get('stop_reason') and outcome.get('cleanup') == 'observed_group_cleaned'
            and outcome.get('native_backend','codex') == expected_backend
            and (outcome.get('metering') or {}).get('source') == terminal_source)


def validate_final_a_receipt(root, job, intent, outcome, meter=None):
    accepted = final_a_return(root, job, intent)
    if accepted is not None and meter is None:
        record = contracts.host_record(contracts.workspace(root, job['step']))
        if intent.get('attempt') is not None:record = record/'continuations'/f'{intent["attempt"]:03d}'
        if (record / 'usage.json').exists():
            meter = contracts.read(contracts.regular(record / 'usage.json'))
    expected = final_a_receipt(accepted, intent, meter, outcome['usage']) if accepted is not None else None
    if outcome.get('final_a_completion') != expected:
        raise ValueError('Final A completion or overrun receipt changed')
    if accepted is not None and outcome.get('returncode') == 0 and not outcome.get('stop_reason'):
        from .native_usage import tokens
        observation = outcome.get('metering') or {}
        if (not native_completion_known(outcome, contracts.settings(root, job))
                or meter is not None and (tokens(meter) != meter or observation.get('tokens') != meter)
                or observation.get('coverage') == 'terminal' and meter is None):
            raise ValueError('Final A completion lacks a known terminal execution or cleanup')
    return accepted


def _validate_final_a_action(root, event, previous):
    marker = event['final_return']
    accepted = next((row for row in previous if row['kind'] == 'final_a_return' and row['seq'] == marker.get('seq')), None)
    actions = [row for row in previous if row['kind'] == 'action_intent' and row.get('final_return') == marker]
    decision_step = (event.get('decision') or {}).get('step')
    if type(decision_step) is not int or decision_step <= 0:
        raise ValueError('Final delivery requires its exact decision job')
    from . import configuration_updates
    policy = configuration_updates.for_step(root, decision_step).get('delivery_policy')
    if (accepted is None or not isinstance(marker, dict) or set(marker) != {'seq', 'sha256'}
            or type(marker['seq']) is not int or marker['seq'] != accepted['seq']
            or marker['sha256'] != contracts.digest(Path(root) / 'control-ledger' / f'{accepted["seq"]:06d}.json')
            or accepted['version'] == 2 and (_active_final(previous) is None or _active_final(previous)['first_step'] != accepted['step'])
            or accepted['version'] == 1 and accepted['reserve']['steps'] < 2 or policy is None
            or event['max_seconds'] != policy['submit_seconds' if event['action_kind'] == 'submit' else 'confirm_seconds']
            or event['decision'].get('step') != accepted['step']
            or event['binding'].get('decision') != event['decision']
            or event['binding'].get('operation_id') != event['operation_id']
            or event['action_kind'] != ('submit' if not actions else 'confirm')):
        raise ValueError('Final delivery exceeds its exact bounded operation')
    # Authorization replay checks the immutable declared object references,
    # never execution results, scientific qualification or current admission.
    decision = event['decision']
    if (set(decision) != {'step','job_sha256','raw_sha256','seal_sha256'}
            or decision['job_sha256'] != accepted['anchor']['task_sha256']
            or any(not isinstance(decision[key],str) or len(decision[key]) != 64
                or any(char not in '0123456789abcdef' for char in decision[key])
                for key in ('job_sha256','raw_sha256','seal_sha256'))
            or actions and any(event[key] != actions[0][key] for key in ('operation_id','decision','binding'))):
        raise ValueError('Final delivery changed its authorized object references')


def final_a_delivery(root, decision, binding):
    """Read the reserved delivery scope without traversing scientific results."""
    accepted = final_a_barrier(root)
    if accepted is None or decision.get('step') != accepted['step']:
        return None
    step = accepted['step']
    job = contracts.read(contracts.regular(Path(root)/'jobs'/f'{step:03d}.json'))
    intent = contracts.read(contracts.regular(contracts.host_record(contracts.workspace(root,step))/'native-intent.json'))
    final_a_return(root,job,intent)
    if (decision.get('job_sha256') != accepted['anchor']['task_sha256'] or binding.get('decision') != decision):
        raise ValueError('Final delivery changed its declared object scope')
    return intent['budget_slice']['final_return']


def require_final_a_delivery_eligibility(root, decision, binding):
    """Verify the sealed choice before accepting a new host delivery action."""
    marker = final_a_delivery(root,decision,binding)
    if marker is None:return None
    step = decision['step']
    job = contracts.read(contracts.regular(Path(root)/'jobs'/f'{step:03d}.json'))
    result = contracts.validate(contracts.workspace(root,step))
    from . import delivery
    if binding != delivery.binding(root,job,result):
        raise ValueError('Final delivery must retain its exact original binding')
    return marker


def _attempt_consumption(root, job, paths):
    """Derive cumulative consumption from exact completed native facts."""
    from . import native
    tokens, seconds = 0, 0.
    policy = node_limits(root, job)
    for number, path in enumerate(paths):
        intent = contracts.read(contracts.regular(path / 'native-intent.json'))
        anchor = intent.get('budget_slice', {})
        original = contracts.read(contracts.regular(path / 'native-outcome.json'))
        from . import startup_recovery
        startup_original = not (path/'usage.json').exists() and number == 0 and startup_recovery.path(root,job['step']).exists()
        if startup_original:
            startup_recovery.reconciled_original(root,job['step'])
            duration = original['budget_slice']['advance_seconds']
            if duration < seconds:raise ValueError('Reconciled startup time cannot be refunded')
            seconds = duration
            continue
        meter = contracts.read(contracts.regular(path / 'usage.json')) if (path/'usage.json').exists() else None
        if meter is None and not native_completion_known(dict(original,stop_reason=None), contracts.settings(root, job)):
            raise ValueError('Missing billing has no known original completion')
        # An absent meter contributes no reported tokens to the lower bound;
        # retain its absence and unknown coverage, never publish a zero meter.
        reported = meter or dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
        startup = intent.get('continuation_kind') == 'startup_successor'
        if startup:
            from . import startup_recovery
            startup_recovery.validate_intent(root,job,intent)
        report = original.get('budget_slice', {})
        if (meter is not None and native._usage_event(dict(type='turn.completed', usage=meter)) != meter
                or intent.get('task_sha256') != contracts.digest(root / 'jobs' / f'{job["step"]:03d}.json')
                or anchor.get('limits') != policy or anchor.get('role') != job['role']
                or not startup and (anchor.get('previous_tokens', 0) != tokens or anchor.get('previous_seconds', 0.) != seconds)
                or bool(report.get('usage_observed')) != (meter is not None) or report.get('limits') != policy
                or report.get('role') != job['role'] or report.get('dispatches') != number + 1
                or any(report.get(key) != reported[key] for key in ('input_tokens', 'cached_input_tokens', 'output_tokens'))):
            raise ValueError('Original node usage report or identity changed')
        tokens += reported['input_tokens'] + reported['output_tokens']
        duration = report.get('advance_seconds')
        if (type(duration) not in (int, float) or not math.isfinite(duration) or duration < seconds
                or report.get('observed_tokens') != tokens):
            raise ValueError('Original cumulative node consumption changed')
        seconds = duration
    return tokens, seconds

def _node_duration(root, job, intent, limits):
    anchor = intent.get('budget_slice')
    final = final_a_return(root, job, intent) if (anchor or {}).get('final_return') is not None else None
    if (not isinstance(anchor, dict) or anchor.get('task_sha256') != contracts.digest(root / 'jobs' / f'{job["step"]:03d}.json')
            or anchor.get('role') != job['role'] or anchor.get('limits') != node_limits(root, job)
            or final is None and anchor.get('shared_limits') != {key: limits[key] for key in ('max_tokens', 'max_seconds')}
            or anchor.get('boot_id') != clock_stamp()['boot_id']
            or type(anchor.get('advance_seconds')) not in (int, float)
            or not math.isfinite(anchor['advance_seconds']) or anchor['advance_seconds'] < 0):
        raise ValueError('Accepted node budget identity or boot changed')
    prior = anchor.get('previous_seconds', 0.)
    prior_tokens = anchor.get('previous_tokens', 0)
    if type(prior_tokens) is not int or prior_tokens < 0:
        raise ValueError('Invalid original node token consumption')
    if intent.get('attempt') is not None and intent.get('continuation_kind') != 'startup_successor':
        from . import execution
        number = intent['attempt']
        paths = execution.records(root, job['step'])
        if type(number) is not int or not 0 < number < len(paths):
            raise ValueError('Continuation has no exact original execution')
        original = _attempt_consumption(root, job, paths[:number])
        if (prior_tokens, prior) != original:
            raise ValueError('Continuation cannot refund original consumption')
    elif prior_tokens or prior:
        raise ValueError('Initial node cannot invent previous consumption')
    if type(prior) not in (int, float) or not math.isfinite(prior) or prior < 0:
        raise ValueError('Invalid original node consumption')
    duration = limits['advance_seconds'] - anchor['advance_seconds'] + prior
    if duration < 0:
        raise ValueError('Node consumption cannot move backwards')
    return duration


def node_admission(root, job, *, ignore_step=None, intent=None, final_report=None, normal_resume_check=False):
    if final_report is not None and (not isinstance(final_report, dict) or set(final_report) != {'blocked_role'}
            or final_report['blocked_role'] not in ('H', 'W', 'A', 'E')):
        raise ValueError('Final report requires its exact blocked role')
    policy = node_limits(root, job)
    barrier = final_a_barrier(root)
    if not normal_resume_check and barrier is not None and barrier['step'] != job['step']:
        return 'final_a_return_consumed'
    begin = closeout_state(root)['begin']
    if not normal_resume_check and begin is not None and begin['mode'] == 'final' and (job.get('role') != 'A' or job['step'] != begin['first_step']):
        return 'final_a_return_consumed'
    if job.get('final_only') and (begin is None or begin['mode'] != 'final' or job['step'] != begin['first_step']):
        return 'final_cycle_closed'
    from . import native
    meter = native.usage(root)
    if meter.get('reserved_unknown_steps') and job['role'] in {'H', 'W'}:
        if intent is None or intent.get('continuation_kind') != 'author_closeout':
            return 'operator_closeout_only'
        from . import author_closeout
        author_closeout.validate_intent(root, job, intent)
    if policy is None:
        return None
    limits, meter = snapshot(root), native.usage(root)
    accepted = final_a_return(root, job, intent) if intent is not None else None
    report = validate_final_report_inputs(root, job) if job.get('final_report') is not None else None
    if final_a_admission(root, job) is not None:
        unknown_scope = dict(version=2, basis='final_state', step=job['step'])
    elif report is not None:
        unknown_scope = dict(basis='protected_final_report', selection=dict(stopped_original=report['stopped_original']))
    else:
        unknown_scope = accepted
        if intent is None and begin is not None and begin['mode'] == 'final':
            unknown_scope = dict(selection=dict(stopped_original=begin.get('stopped_original')))
    if final_a_usage_unknown(unknown_scope, meter, ignore_step=ignore_step):
        return 'usage_unknown'
    if intent is not None:
        if accepted is not None:
            _node_duration(root, job, intent, limits)
            return None
    if intent is None and final_a_admission(root, job, final_report=final_report) is not None:
        return None
    if contracts.settings(root, job).get('resume_policy') is not None:
        additional = 0 if (root / 'jobs' / f'{job["step"]:03d}.json').exists() else 1
        if used_steps(root) + additional > step_limit(root):
            return 'step_limit'
    duration = _node_duration(root, job, intent, limits) if intent is not None else 0.
    soft = node_mode(root) == 'soft'
    if (limits['seconds_remaining'] <= 0 if soft else
            limits['seconds_remaining'] < max(0., policy['max_seconds'] - duration)):
        return 'node_budget_unavailable_seconds'
    actual_only = _final_a_policy(contracts.settings(root, job)) is not None
    needed_tokens = 0 if actual_only else max(0, policy['max_observed_tokens'] - (intent.get('budget_slice', {}).get('previous_tokens', 0) if intent else 0))
    if final_report is None and (limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) <= 0 if soft or actual_only else
            limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) < needed_tokens):
        return 'node_budget_unavailable_tokens'
    if job.get('purpose') == 'closeout':
        settings = contracts.settings(root, job)
        delivery = settings['delivery_policy']
        begin = closeout_state(root)['begin']
        if begin is None or begin['mode'] not in {'model', 'final'} or begin['source_cutoff'] != job['source_cutoff']:
            raise ValueError('Closeout node has no exact model boundary')
        external = delivery['adapter'] is not None
        floor = closeout_floor(settings, preparation_seconds=begin['preparation_seconds'])
        if job.get('engineering_repair') and _final_a_policy(settings) is None:
            floor=dict(tokens=floor['tokens']+delivery['closeout_a']['max_observed_tokens'],seconds=floor['seconds']+delivery['closeout_a']['max_seconds'],steps=floor['steps']+1)
        additional = 0 if (root / 'jobs' / f'{job["step"]:03d}.json').exists() else 1
        seconds_unavailable = (limits['seconds_remaining'] <= floor['seconds'] if soft else
            limits['seconds_remaining'] < max(0., policy['max_seconds'] - duration) + floor['seconds'])
        tokens_unavailable = (limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) <= floor['tokens'] if soft or actual_only else
            limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) < needed_tokens + floor['tokens'])
        if (seconds_unavailable or tokens_unavailable
                or step_limit(root) - used_steps(root) < additional + floor['steps']):
            return 'closeout_budget_unavailable'
        return None
    return exploration_admission(root, job, ignore_step=ignore_step, intent=intent)


def accept_node(root, job, *, final_report=None):
    from .protocol import require_current
    require_current(root,job)
    policy = node_limits(root, job)
    if policy is None:
        return None
    accepted = final_a_barrier(root)
    if accepted is not None and accepted['step'] == job['step']:
        if accepted['anchor']['task_sha256'] != contracts.digest(Path(root) / 'jobs' / f'{job["step"]:03d}.json'):
            raise ValueError('Final A return cannot bind a replacement task')
        marker = dict(seq=accepted['seq'], sha256=contracts.digest(Path(root) / 'control-ledger' / f'{accepted["seq"]:06d}.json'))
        return dict(accepted['anchor'], final_return=marker)
    if job.get('final_report') is not None and final_report is None and final_a_admission(root, job) is None:
        raise NodeBudgetUnavailable('final_a_report_requires_control_admission')
    reason = node_admission(root, job, final_report=final_report)
    if reason:
        raise NodeBudgetUnavailable(reason)
    limits, stamp = snapshot(root), clock_stamp()
    anchor = dict(task_sha256=contracts.digest(root / 'jobs' / f'{job["step"]:03d}.json'), role=job['role'],
                boot_id=stamp['boot_id'], advance_seconds=limits['advance_seconds'], limits=policy,
                shared_limits={key: limits[key] for key in ('max_tokens', 'max_seconds')})
    return _accept_final_a(root, job, anchor, limits, final_report=final_report)


def stop_node(root, job, intent, limits, meter):
    policy = node_limits(root, job)
    if (intent.get('budget_slice') or {}).get('final_return') is not None:
        final_a_return(root, job, intent)
        duration = _node_duration(root, job, intent, limits)
        return 'node_slice_time_limit' if duration >= policy['max_seconds'] else None
    if policy is None:
        return None
    duration = _node_duration(root, job, intent, limits)
    if node_mode(root) == 'soft':
        return None
    if duration >= policy['max_seconds']:
        return 'node_slice_time_limit'
    if meter['input_tokens'] + meter['output_tokens'] + intent['budget_slice'].get('previous_tokens', 0) >= policy['max_observed_tokens']:
        return 'node_slice_token_limit'
    return None


def node_report(root, job, intent, meter):
    duration = _node_duration(root, job, intent, snapshot(root))
    return dict(role=job['role'], advance_seconds=duration, input_tokens=meter['input_tokens'],
                cached_input_tokens=meter['cached_input_tokens'], output_tokens=meter['output_tokens'],
                observed_tokens=meter['input_tokens'] + meter['output_tokens'] + intent['budget_slice'].get('previous_tokens', 0), dispatches=1 + intent.get('attempt', 0),
                limits=intent['budget_slice']['limits'])


def accept_continuation(root, job, paths, *, output_repair=None):
    from .protocol import require_current
    require_current(root,job)
    final=final_a_barrier(root)
    if final is not None:
        from . import continuation
        if (output_repair is None or final['step']!=job['step'] or
                output_repair!=continuation.output_request(root,job['step'],paths[-1])):
            raise NodeBudgetUnavailable('final_a_return_consumed')
    from . import native
    tokens, seconds = _attempt_consumption(root, job, paths)
    policy = node_limits(root, job)
    limits, meter = snapshot(root), native.usage(root)
    if final is not None:
        original=contracts.read(contracts.regular(paths[0]/'native-intent.json'))
        final_a_return(root,job,original)
        if seconds >= policy['max_seconds']:
            raise NodeBudgetUnavailable('worker_resume_node_exhausted')
        return dict(original['budget_slice'],advance_seconds=limits['advance_seconds'],
            previous_tokens=tokens,previous_seconds=seconds)
    if (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0
            or node_mode(root)=='hard' and (tokens >= policy['max_observed_tokens'] or seconds >= policy['max_seconds'])):
        raise NodeBudgetUnavailable('worker_resume_node_exhausted')
    settings = contracts.settings(root, job)
    soft = node_mode(root) == 'soft'
    reserve = (closeout_floor(settings, preparation_seconds=closeout_state(root)['begin']['preparation_seconds'])
        if job.get('purpose') == 'closeout' else delivery_reserve(settings)) or dict(tokens=0, seconds=0, steps=0)
    if job.get('purpose') == 'closeout' and job.get('engineering_repair') and _final_a_policy(settings) is None:
        author = settings['delivery_policy']['closeout_a']
        reserve = dict(tokens=reserve['tokens'] + author['max_observed_tokens'],
                       seconds=reserve['seconds'] + author['max_seconds'], steps=reserve['steps'] + 1)
    actual_only = _final_a_policy(settings) is not None
    needed_tokens = 0 if soft or actual_only else max(0, policy['max_observed_tokens'] - tokens)
    needed_seconds = 0. if soft else max(0., policy['max_seconds'] - seconds)
    if (meter.get('blocking_unknown_steps', meter['unknown_steps']) or limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) < needed_tokens + reserve['tokens']
            or limits['seconds_remaining'] < needed_seconds + reserve['seconds']
            or (soft or actual_only) and limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) <= reserve['tokens']
            or soft and limits['seconds_remaining'] <= reserve['seconds']
            or step_limit(root) - used_steps(root) < 1 + reserve['steps']):
        raise NodeBudgetUnavailable('worker_resume_budget_unavailable')
    return dict(task_sha256=contracts.digest(root / 'jobs' / f'{job["step"]:03d}.json'), role=job['role'],
                boot_id=clock_stamp()['boot_id'], advance_seconds=limits['advance_seconds'], limits=policy,
                shared_limits={key: limits[key] for key in ('max_tokens', 'max_seconds')},
                previous_tokens=tokens, previous_seconds=seconds)


def closeout_state(root):
    """Only immutable control facts; no second balance or mutable mode file."""
    events, _ = _events(root, contracts.settings(root))
    begin, resumed, actions, operations = None, None, {}, {}
    for event in events:
        if event['kind'] == 'closeout_begin':
            if begin is not None and (begin['mode'] != 'model' or event['mode'] != 'final'
                    or event['source_cutoff'] != begin['source_cutoff'] or event['first_step'] < begin['first_step']):
                raise ValueError('Closeout may only advance once from model to final')
            begin = event
        elif event['kind'] == 'closeout_resume':
            begin, resumed = None, event
        elif event['kind'] == 'action_intent':
            if begin is None or event['action_id'] in actions:
                raise ValueError('Delivery intent requires a unique closeout action')
            op = event['operation_id']
            expected = dict(decision=event['decision'], binding=event['binding'])
            if op in operations:
                if event['action_kind'] != 'confirm' or expected != operations[op]:
                    raise ValueError('Delivery may only confirm the exact accepted operation')
            elif event['action_kind'] != 'submit':
                raise ValueError('Confirmation has no original submit identity')
            else:
                operations[op] = expected
            actions[event['action_id']] = dict(intent=event, outcome=None)
        elif event['kind'] == 'action_outcome':
            action = actions.get(event['action_id'])
            if action is None or action['outcome'] is not None:
                raise ValueError('Delivery outcome requires its original intent')
            _validate_action_result(action['intent'], event['result'])
            action['outcome'] = event
    rows = list(actions.values())
    unresolved = []
    for op in operations:
        sequence = [row for row in rows if row['intent']['operation_id'] == op]
        confirmed = max((row['intent']['seq'] for row in sequence if row['outcome'] is not None
                         and row['outcome']['result']['status'] == 'confirmed'), default=0)
        if any(row['intent']['seq'] > confirmed and (row['outcome'] is None
               or row['outcome']['result']['status'] == 'unknown') for row in sequence):
            unresolved.append(op)
    return dict(begin=begin, resume=resumed, actions=rows, unresolved_operations=unresolved)


def delivery_reserve(settings):
    if _final_a_policy(settings) is not None:
        # The durable FINAL grant owns its one completion and finite delivery
        # windows independently of shared tokens, time and steps. Ordinary work
        # uses reported shared usage without estimating missing billing.
        return dict(tokens=0, seconds=0., steps=0)
    policy = settings.get('delivery_policy')
    if policy is None:
        return None
    external = policy['adapter'] is not None
    authors = 1 if settings.get('node_budget_mode', 'hard') == 'soft' else 3
    return dict(tokens=authors * policy['closeout_a']['max_observed_tokens'] + policy['inflight_tokens'],
                seconds=authors * policy['closeout_a']['max_seconds'] + policy['cleanup_seconds']
                        + (policy['submit_seconds'] + policy['confirm_seconds'] if external else 0),
                steps=authors + (2 if external else 0))


def closeout_floor(settings, *, preparation_seconds=0.):
    """After exploration: retain execution allowance, not another whole A target."""
    if _final_a_policy(settings) is not None:
        return dict(tokens=0, seconds=0., steps=0)
    policy = settings['delivery_policy']
    external = policy['adapter'] is not None
    return dict(tokens=policy['inflight_tokens'],
                seconds=max(0., policy['cleanup_seconds'] - preparation_seconds)
                    + (policy['submit_seconds'] + policy['confirm_seconds'] if external else 0),
                steps=2 if external else 0)


def used_steps(root):
    from . import execution
    from . import fork_runtime
    prepared = fork_runtime.local_steps(root)
    continuations = sum(1 for step in prepared for record,_ in execution.accepted_intents(root,step)
        if record != execution.origin(root,step))
    from . import candidate_audit
    from . import audit_recovery
    audits = sum(1 for _ in candidate_audit.intents(root)) + sum(1 for _ in audit_recovery.intents(root))
    return fork_runtime.node_count(root, prepared) + continuations + len(closeout_state(root)['actions']) + audits


def _selection_accounts_job(root, job, selected_used):
    from . import fork_runtime
    if not fork_runtime.is_fork(root):
        return job['step'] <= selected_used
    # The FINAL original was prepared before acceptance. Its logical label is
    # unrelated to consumption; later events may add more local originals.
    prepared = [number for number in fork_runtime.local_steps(root) if number <= job['step']]
    return job['step'] in prepared and len(prepared) <= selected_used


def exploration_admission(root, job, *, ignore_step=None, intent=None):
    from . import configuration_updates
    settings = configuration_updates.for_new_job(root, job['step']) if 'configuration_revision' not in job else contracts.settings(root, job)
    reserve = delivery_reserve(settings)
    if reserve is None or job.get('purpose') == 'closeout':
        return None
    from . import native
    limits, meter = snapshot(root), native.usage(root)
    node = node_limits(root, job)
    if any(step != ignore_step for step in meter.get('blocking_unknown_steps', meter['unknown_steps'])):
        return 'usage_unknown'
    soft = node_mode(root) == 'soft'
    actual_only = _final_a_policy(settings) is not None
    needed_tokens = 0 if soft or actual_only or node is None else max(0, node['max_observed_tokens'] - (intent.get('budget_slice', {}).get('previous_tokens', 0) if intent else 0))
    if (limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) <= reserve['tokens'] if soft or actual_only else
            limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) < needed_tokens + reserve['tokens']):
        return 'closeout_reserve_tokens'
    duration = _node_duration(root, job, intent, limits) if intent is not None and node is not None else 0.
    needed_seconds = max(0., node['max_seconds'] - duration) if node is not None else 0.
    if (limits['seconds_remaining'] <= reserve['seconds'] if soft else
            limits['seconds_remaining'] < needed_seconds + reserve['seconds']):
        return 'closeout_reserve_seconds'
    additional = 0 if (root / 'jobs' / f'{job["step"]:03d}.json').exists() else 1
    if step_limit(root) - used_steps(root) < additional + reserve['steps']:
        return 'closeout_reserve_steps'
    return None


def begin_closeout(root, cutoff, first_step, reason, *, export_only=False, delivery_sources=None, preparation_seconds=0., authorization_step=None, final_only=False, stopped_original=None, resume_cursor=None):
    from .protocol import require_current
    require_current(root)
    settings = contracts.settings(root)
    with _ledger_writer(root):
        events, parent = _events(root, settings)
        existing = closeout_state(root)['begin']
        if existing is not None:
            if not final_only or existing['mode'] != 'model':
                return existing
            cutoff = existing['source_cutoff']
        if resume_cursor is not None:
            require_cursor_eligibility(root,resume_cursor,cutoff+1,events)
        return _append(root, settings, events, parent, kind='closeout_begin', source_cutoff=cutoff,
                       first_step=first_step, stop_reason=reason, mode='final' if final_only else 'export_only' if export_only else 'model',
                       delivery_sources=delivery_sources, preparation_seconds=preparation_seconds,
                       **(dict(authorization_step=authorization_step) if authorization_step is not None else {}),
                       **(dict(stopped_original=stopped_original,resume_cursor=resume_cursor) if final_only else {}))


def accept_action(root, kind, operation_id, decision, binding, max_seconds):
    from .protocol import require_current
    require_current(root)
    from . import configuration_updates
    settings = configuration_updates.for_step(root, decision['step'])
    begin = closeout_state(root)['begin']
    if (settings.get('delivery_policy') is None or settings['delivery_policy']['adapter'] is None
            or begin is None or begin['mode'] not in {'model', 'final'}):
        raise ValueError('External delivery requires its frozen model closeout authorization')
    if kind not in {'submit', 'confirm'} or execution_blocked(root, ignore_operation=operation_id if kind == 'confirm' else None):
        raise RuntimeError('Delivery action is blocked by an original unresolved execution')
    from . import native
    meter = native.usage(root)
    limits = snapshot(root)
    final_return = require_final_a_delivery_eligibility(root, decision, binding)
    barrier = final_a_barrier(root)
    if barrier is not None and final_return is None:
        raise NodeBudgetUnavailable('final_a_return_consumed')
    # The final exception covers shared billing, not unresolved execution:
    # the original-effect gate above and the sealed-choice gate still apply.
    if final_return is None and (meter.get('blocking_unknown_steps', meter['unknown_steps'])
            or meter.get('accounted_tokens', meter['observed_tokens']) >= limits['max_tokens']):
        raise NodeBudgetUnavailable('closeout_budget_unavailable')
    if final_return is None and (limits['seconds_remaining'] < max_seconds or used_steps(root) >= step_limit(root)):
        raise NodeBudgetUnavailable('closeout_budget_unavailable')
    with _ledger_writer(root):
        events, parent = _events(root, settings)
        action_id = f'action-{len(events) + 1:06d}'
        if final_return is not None:
            actions = [row for row in events if row['kind'] == 'action_intent' and row.get('final_return') == final_return]
            if (barrier['version'] == 1 and barrier['reserve']['steps'] < 2
                    or kind != ('submit' if not actions else 'confirm')):
                raise NodeBudgetUnavailable('final_a_delivery_reserve_exhausted')
            confirmed_ids = {row['action_id'] for row in actions if row['action_kind'] == 'confirm'}
            if any(row['kind'] == 'action_outcome' and row['action_id'] in confirmed_ids
                   and row['result']['status'] == 'confirmed' for row in events):
                raise NodeBudgetUnavailable('final_a_delivery_already_confirmed')
            if max_seconds != settings['delivery_policy']['submit_seconds' if kind == 'submit' else 'confirm_seconds']:
                raise ValueError('Final delivery requires its frozen per-call window')
            _validate_final_a_action(root, dict(final_return=final_return, action_kind=kind,
                operation_id=operation_id, decision=decision, binding=binding, max_seconds=max_seconds), events)
        return _append(root, settings, events, parent, kind='action_intent', action_id=action_id,
                       action_kind=kind, operation_id=operation_id, decision=decision, binding=binding,
                       max_seconds=max_seconds, advance_seconds=limits['advance_seconds'],
                       **(dict(final_return=final_return) if final_return is not None else {}))


def action_outcome(root, intent, result):
    from .protocol import require_current
    require_current(root)
    settings = contracts.settings(root)
    _validate_action_result(intent, result)
    with _ledger_writer(root):
        events, parent = _events(root, settings)
        return _append(root, settings, events, parent, kind='action_outcome', action_id=intent['action_id'], result=result)


def exploration_stop(root, job, limits):
    if node_mode(root) == 'soft':
        return None
    settings = contracts.settings(root, job)
    reserve = delivery_reserve(settings)
    delivery = settings.get('delivery_policy')
    if _final_a_policy(settings) is not None:
        return None
    if reserve is None or job.get('purpose') == 'closeout':
        return None
    from . import native
    if limits['seconds_remaining'] <= reserve['seconds']:
        return 'closeout_protected_time_limit'
    meter = native.usage(root)
    if limits['max_tokens'] - meter.get('accounted_tokens', meter['observed_tokens']) <= reserve['tokens']:
        return 'closeout_protected_token_limit'
    return None


def _validate_action_result(intent, result):
    binding = intent['binding']
    if (set(result) - {'failure_phase','diagnostic'} != {'status', 'operation_id', 'candidate_id', 'artifact_sha256', 'note', 'cleanup_confirmed'}
            or 'failure_phase' in result and (result['status'] not in {'failed', 'rejected', 'engineering_error', 'unclassified', 'unknown'} or result['failure_phase'] not in {'audit', 'promote', 'confirm'})
            or 'diagnostic' in result and (not isinstance(result['diagnostic'],str) or len(result['diagnostic'].encode())>4096)
            or result['status'] not in {'confirmed', 'failed', 'unknown', 'rejected', 'engineering_error', 'unclassified'}
            or type(result['cleanup_confirmed']) is not bool
            or not result['cleanup_confirmed'] and result['status'] != 'unknown'
            or result['operation_id'] != intent['operation_id']
            or result['candidate_id'] != binding['candidate_id']
            or not isinstance(result['note'], str) or len(result['note'].encode()) > 4096
            or result['artifact_sha256'] not in {None, binding['source']['objects']['artifact']['sha256']}
            or result['status'] == 'confirmed' and result['artifact_sha256'] != binding['source']['objects']['artifact']['sha256']):
        raise ValueError('Delivery result differs from its exact accepted candidate and artifact')


def action_process(root, intent, phase, identity):
    from .protocol import require_current
    require_current(root)
    if phase not in {'audit', 'promote', 'confirm'} or identity['task_sha256'] != intent['operation_id']:
        raise ValueError('Delivery process differs from its exact action phase')
    path = root / 'host-delivery/action-processes' / (intent['action_id'] + '-' + phase + '.json')
    if path.exists() or path.is_symlink():
        raise ValueError('Delivery action phase cannot launch twice')
    contracts.write(path, dict(action_id=intent['action_id'], operation_id=intent['operation_id'], phase=phase, identity=identity))


def write_node_hint(root, job, intent, limits, meter, *, observed, author_limits=None):
    """Optional replaceable advice; never read as a grant, meter or evidence."""
    from .protocol import require_current
    require_current(root,job)
    mode=node_mode(root)
    if mode=='soft':publish_review(root,job,intent,limits,meter,observed=observed)
    targets=node_limits(root,job)
    duration=_node_duration(root,job,intent,limits) if targets else None
    if targets is None and job['role']=='W' and job.get('search'):
        from . import search
        search.stop_reason(root,job,intent,limits,meter)
        targets=dict(max_observed_tokens=job['search']['limits']['max_w_observed_tokens'],max_seconds=job['search']['limits']['max_w_seconds'])
        duration=limits['advance_seconds']-intent['search_slice']['advance_seconds']
    prior=intent.get('budget_slice',{}).get('previous_tokens',0)
    known=prior+meter['input_tokens']+meter['output_tokens']
    consumed=known if observed else None
    node_targets=targets
    node_tokens=max(0,targets['max_observed_tokens']-known) if targets else None
    node_seconds=max(0.,targets['max_seconds']-duration) if targets else None
    author=intent.get('continuation_kind')=='author_closeout'
    final_return=final_a_return(root,job,intent) if (intent.get('budget_slice') or {}).get('final_return') is not None else None
    boundary='node_slice' if targets else 'shared_only'
    if author:
        # The caller already verified this exact permit before native launch.
        # A reused W job's original cap is not this author attempt's allowance.
        targets=author_limits
        boundary='author_permit' if targets is not None else 'unavailable'
        duration=(duration-intent['budget_slice'].get('previous_seconds',0.)) if duration is not None else None
        consumed=meter['input_tokens']+meter['output_tokens'] if observed else None
    settings = contracts.settings(root, job)
    closeout = job.get('purpose') == 'closeout'
    reserve = (closeout_floor(settings, preparation_seconds=closeout_state(root)['begin']['preparation_seconds'])
        if closeout else delivery_reserve(settings)) or dict(tokens=0,seconds=0,steps=0)
    if final_return is not None:
        reserve=final_return['reserve']
        boundary='final_a_return'
    from . import native
    shared=native.usage(root)
    remaining=limits['max_tokens']-shared.get('accounted_tokens', shared['observed_tokens'])
    available_tokens=max(0,remaining-reserve['tokens'])
    available_seconds=max(0.,limits['seconds_remaining']-reserve['seconds'])
    if mode=='hard' and node_tokens is not None and final_return is None:
        available_tokens=min(available_tokens,node_tokens)
        available_seconds=min(available_seconds,node_seconds)
    if author:
        if targets is None or duration is None:
            available_tokens=available_seconds=None
        else:
            available_seconds=min(available_seconds,max(0.,targets['max_seconds']-duration))
    state=('reached' if targets and ((consumed is not None and consumed>=targets['max_observed_tokens']) or duration is not None and duration>=targets['max_seconds'])
           else 'near' if targets and ((consumed is not None and consumed>=.8*targets['max_observed_tokens']) or duration is not None and duration>=.8*targets['max_seconds']) else 'within')
    if mode=='hard' and node_targets:
        if node_tokens==0 or node_seconds==0:state='reached'
        elif state=='within' and (node_tokens<=.2*node_targets['max_observed_tokens'] or node_seconds<=.2*node_targets['max_seconds']):state='near'
    hint=dict(scope='advice_only_not_authorization_or_scientific_evidence',updated_at_unix=time.time(),mode=mode,
        target_state=state,targets=targets,active_seconds=duration,observed_tokens_lower_bound=consumed,
        metering='observed_lower_bound' if observed else 'inflight_unknown',
        input_accounting='input_includes_cached_input_once; observed_total=input+output',
        inflight_usage_unknown=True,reporting_delay_unmeasured=True,per_request_admission='unavailable',
        final_response_allowance='not_guaranteed',current_work_boundary=boundary,
        target_kind='unavailable' if targets is None else 'soft_target',
        unknown_usage=not observed or bool(shared.get('unknown_steps')),
        shared_remaining_basis='configured_ceiling_minus_reported_usage',
        shared_observed_tokens_remaining_upper_bound=max(0,remaining),shared_active_seconds_remaining=limits['seconds_remaining'],
        reserve_state='reached' if remaining<=reserve['tokens'] or limits['seconds_remaining']<=reserve['seconds'] else 'within',
        reserve_scope='external_closeout_floor' if closeout else 'minimal_closeout_reserve',
        current_work_observed_tokens_remaining_upper_bound=available_tokens,
        current_work_active_seconds_available=available_seconds,
        instruction='Near/reached limits, targets or reserve: finish bounded actions and return your scientific JSON. Unknown usage is not zero; upper bounds are not grants or request/billing caps. Shared hard limits still apply.')
    if author:
        hint['instruction'] = ('The author token target is soft: finish this original scientific response at the target. '
            'The verified author permit finite time limit remains enforced. No new experiment is authorized; all actual usage is retained.')
    if final_return is not None:
        hint.update(target_kind='final_a_token_target', final_response_allowance='original_final_a_token_overrun_authorized',
            current_work_observed_tokens_remaining_upper_bound=None,
            current_work_active_seconds_available=node_seconds,
            instruction='Complete this same final scientific return and explain completed work, unfinished work and any evidence gap. Token targets and reference ceilings do not terminate this original return; all actual usage is retained. The original finite call duration, resources and transport boundaries remain enforced; shared time and step exhaustion do not revoke this completion. No further scientific turn is authorized.')
    data=(json.dumps(hint,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    if len(data)>4096:raise ValueError('Budget advice exceeds byte bound')
    directory=contracts.workspace(root,job['step'])/'work'
    fd=None;temporary=None
    try:
        if directory.resolve()!=directory or directory.is_symlink():return
        destination=directory/'budget-status.json'
        if destination.is_symlink() or destination.exists() and not destination.is_file():
            return
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        identity=os.fstat(fd)
        temporary='.budget-hint-'+os.urandom(12).hex()+'.tmp'
        item=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
        with os.fdopen(item,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o400)
        current=os.stat(directory,follow_symlinks=False)
        if (current.st_dev,current.st_ino)!=(identity.st_dev,identity.st_ino):return
        os.replace(temporary,'budget-status.json',src_dir_fd=fd,dst_dir_fd=fd);temporary=None
    except OSError:
        pass  # Advice unavailable cannot weaken or replace the independent guards.
    finally:
        if fd is not None:
            if temporary is not None:
                try:os.unlink(temporary,dir_fd=fd)
                except OSError:pass
            os.close(fd)


def _review_directory(root,job):
    return Path(root)/'host-runtime/experiment-control'/contracts.digest(Path(root)/'jobs'/f'{job["step"]:03d}.json')


def review_configuration(root,job):
    """Host-owned projection, shared by every implementation of this stage."""
    from .protocol import require_current
    require_current(root,job)
    from . import execution,experiments
    settings=contracts.settings(Path(root), job)
    if settings.get('managed_admission_version')!=experiments.ADMISSION_VERSION or job.get('managed_admission_version')!=experiments.ADMISSION_VERSION:
        raise experiments.UnsupportedExecution('Preserved review contract is read-only; prepare a new instance')
    if node_mode(root)!='soft':return dict(version=experiments.ADMISSION_VERSION,mode='hard')
    directory=_review_directory(root,job);directory.mkdir(parents=True,exist_ok=True)
    if directory.resolve()!=directory:raise ValueError('Host review directory changed')
    fd=os.open(directory/'accept.lock',os.O_RDONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o444);os.close(fd)
    record=execution.record(root,job['step'])
    intent=contracts.read(contracts.regular(record/'native-intent.json'))
    meter=contracts.read(record/'usage.json') if (record/'usage.json').exists() else dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
    publish_review(root,job,intent,snapshot(Path(root)),meter,observed=(record/'usage.json').exists())
    return dict(version=experiments.ADMISSION_VERSION,mode='soft',directory=str(directory),
        stage_anchor=contracts.digest(Path(root)/'jobs'/f'{job["step"]:03d}.json'))


def publish_review(root,job,intent,limits,meter,*,observed):
    """Derive replaceable facts from original anchors, never an additional budget."""
    from .protocol import require_current
    require_current(root,job)
    from . import execution,experiments,native,search
    from .protocol import stage_scope
    root=Path(root);settings=contracts.settings(root, job)
    if job.get('managed_admission_version')!=experiments.ADMISSION_VERSION or settings.get('managed_admission_version')!=experiments.ADMISSION_VERSION:
        raise experiments.UnsupportedExecution('Preserved review contract is read-only; prepare a new instance')
    if node_mode(root)!='soft':return
    directory=_review_directory(root,job);directory.mkdir(parents=True,exist_ok=True)
    if directory.resolve()!=directory:raise ValueError('Host review directory changed')
    fd=os.open(directory/'accept.lock',os.O_RDONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o444)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        stamp=clock_stamp()
        limits=snapshot(root,sample_stamp=stamp)
        record=execution.record(root,job['step'])
        intent=contracts.read(contracts.regular(record/'native-intent.json'))
        observed=(record/'usage.json').exists()
        meter=contracts.read(contracts.regular(record/'usage.json')) if observed else dict(input_tokens=0,output_tokens=0)
        task=contracts.digest(root/'jobs'/f'{job["step"]:03d}.json')
        if intent.get('task_sha256')!=task:raise ValueError('Review publication has no exact native task')
        targets=node_limits(root,job);duration=None
        if targets is not None:duration=_node_duration(root,job,intent,limits)
        elif job['role']=='W' and job.get('search'):
            search.stop_reason(root,job,intent,limits,meter)
            targets=dict(max_observed_tokens=job['search']['limits']['max_w_observed_tokens'],max_seconds=job['search']['limits']['max_w_seconds'])
            duration=limits['advance_seconds']-intent['search_slice']['advance_seconds']
        prior=intent.get('budget_slice',{}).get('previous_tokens',0)
        consumed=prior+meter['input_tokens']+meter['output_tokens'] if observed else prior
        reserve=delivery_reserve(settings) or dict(tokens=0,seconds=0,steps=0)
        shared=native.usage(root)
        # The original grant remains the ceiling for this accepted stage.
        grant=intent.get('budget_slice',{}).get('shared_limits',dict(max_tokens=settings['max_tokens'],max_seconds=settings['max_seconds']))
        available_tokens=min(limits['max_tokens'],grant['max_tokens'])-shared.get('accounted_tokens', shared['observed_tokens'])-reserve['tokens']
        available_seconds=min(limits['seconds_remaining'],max(0.,grant['max_seconds']-limits['consumed_seconds']))-reserve['seconds']
        # Soft stage targets advise the Worker; only the original shared window
        # bounds admission and optional managed execution time reservations.
        remaining=available_seconds
        reason=('shared_usage_unknown' if any(step!=job['step'] for step in shared.get('blocking_unknown_steps', shared['unknown_steps'])) else
            'reserve_reached' if available_seconds<=0 or available_tokens<=0 else
            'target_time_reached' if targets and duration>=targets['max_seconds'] else
            'target_tokens_reached' if targets and consumed>=targets['max_observed_tokens'] else None)
        value=dict(version=experiments.ADMISSION_VERSION,stage_anchor=task,
            scope=stage_scope(root,job),
            native_attempt=dict(number=len(execution.records(root,job['step']))-1,execution_key=execution.identity(root,job['step'])),
            boot_id=stamp['boot_id'],observed_at=stamp['at_unix'],deadline_boottime=stamp['at_boottime']+max(0.,remaining),
            valid_until_boottime=stamp['at_boottime']+10,pending=bool(reason),reason=reason,
            observed_tokens_lower_bound=consumed,inflight_usage_unknown=True,targets=targets)
        path=directory/'review.json'
        if path.exists():
            old=contracts.read(contracts.regular(path))
            if old.get('stage_anchor')!=task or old.get('scope')!=value['scope'] or old.get('boot_id')!=stamp['boot_id']:
                raise ValueError('Host review projection binding changed')
        contracts.write(path,value);path.chmod(0o400)
    finally:os.close(fd)
