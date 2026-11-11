"""Append-only time facts and operator ceilings; no scientific scheduling."""
from __future__ import annotations

from scidiscovery import execution_security as security
from contextlib import contextmanager
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import time

from . import accepted_budget, budget_limits, contracts, history, originals, delivery_review


def supervision_fraction(*,initial_tokens,initial_seconds,observed_tokens,active_seconds):
    """Consume supplied initial ceilings and observations; unknown is not zero."""
    ratios=[]
    for ceiling,consumed in ((initial_tokens,observed_tokens),(initial_seconds,active_seconds)):
        if ceiling is None or consumed is None:continue
        if (type(ceiling) not in (int,float) or not 0<ceiling<float('inf')
                or type(consumed) not in (int,float) or not 0<=consumed<float('inf')):
            raise ValueError('Direction supervision budget observations must be finite and nonnegative')
        ratios.append(consumed/ceiling)
    return max(ratios) if ratios else None


def supervision_budget_facts(basis, observed, limits):
    """Raw original shared dimensions and coverage; no backend-specific reads."""
    return dict(tokens=observed.get('accounted_tokens',observed['observed_tokens']),
        seconds=limits['consumed_seconds'],
        limits=dict(tokens=basis['max_tokens'],seconds=basis['max_seconds']),
        complete=dict(tokens=not (observed.get('unknown_step_count') or observed.get('blocking_unknown_count')
            or observed.get('unknown_steps') or observed.get('blocking_unknown_steps')),
            seconds=limits['consumed_seconds'] is not None and not limits.get('unmeasured_shutdown_tail')))


def clock_stamp():
    return dict(at_unix=time.time(), at_boottime=time.clock_gettime(time.CLOCK_BOOTTIME),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def waiting_is_free(settings):
    """Consume the frozen policy; an absent marker retains historical charges."""
    if 'active_waiting_policy' in settings and settings['active_waiting_policy'] != 'excluded':
        raise ValueError('Unknown frozen active waiting policy')
    return settings.get('time_budget_basis', 'elapsed') == 'active' and settings.get('active_waiting_policy') == 'excluded'


def consumed_seconds(settings, durations, elapsed):
    """Project recorded durations without refunding old waits or guessing unknowns."""
    free_waiting = waiting_is_free(settings)
    if settings.get('time_budget_basis', 'elapsed') != 'active':
        return elapsed
    advance = durations['advance']
    if free_waiting:
        return advance
    waiting = durations['waiting']
    return advance + waiting if advance is not None and waiting is not None else None


def _duration(before, after, *, legacy_elapsed=False, exclude_downtime=False):
    if 'at_boottime' not in before:  # Legacy elapsed startup has no monotonic anchor.
        wall = after['at_unix'] - before['at_unix']
        if wall < 0:
            raise ValueError('Control clock moved backwards; consumption is not refundable')
        return wall
    if before['boot_id'] != after['boot_id']:
        if exclude_downtime:
            # Only the new boot's uptime is known. The previous boot's tail
            # after its last recorded observation cannot be reconstructed.
            return after['at_boottime']
        # Persisted Unix timestamps survive a host restart. Keep previously
        # measured intervals and charge the open cross-boot gap without ever
        # refunding consumption if the wall clock was corrected backwards.
        return max(0., after['at_unix'] - before['at_unix'])
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
    phase = (events.state['clock']['phase'] if isinstance(events, accepted_budget.AcceptedEvents) else
        next((event['phase'] for event in reversed(events) if event['kind'] == 'clock'), 'paused'))
    if settings.get('time_budget_basis') != 'active' or phase != 'paused':
        raise ValueError('Cold clock continuation requires an active budget already paused')
    started = Path(root) / 'host-started.json'
    seq = events.state['seq'] if isinstance(events, accepted_budget.AcceptedEvents) else events[-1]['seq'] if events else 0
    head = (dict(seq=seq, ref=q._ref(root, Path(root) / 'control-ledger' / f'{seq:06d}.json'))
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
    before = ((previous.state['last_stamp'] or previous.state['clock']['start']) if isinstance(previous, accepted_budget.AcceptedEvents) else
        previous[-1] if previous else contracts.read(Path(root) / 'host-started.json') if checkpoint['budget']['started'] else None)
    if before is None or before['boot_id'] == event['boot_id']:
        raise ValueError('Cold clock continuation must bridge an original paused boot')


def _events(root, *, resources=False):
    state = accepted_budget.read(root)
    return _events_from_state(state, resources=resources)


def _events_from_state(state, *, resources=False):
    """Project one accepted observation without loading another head or base."""
    events = accepted_budget.AcceptedEvents(state)
    return (events, state.parent, accepted_budget.resource_view(state)) if resources else (events, state.parent)


def audit_events(root, *, resources=False):
    """Explicit full control-log audit; ordinary control consumes its head."""
    return _read_events(Path(root), contracts.settings(root), resources=resources)


def _read_events(root, settings, *, resources=False):
    # The caller owns this immutable original-settings observation.
    events, parent, origins = [], None, {}
    identity = contracts.record_ref(root / 'settings.json')
    for seq, path in enumerate(sorted((root / 'control-ledger').glob('*.json')), 1):
        data = contracts.regular(path).read_bytes()
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError('Invalid control ledger event')
        if (type(event.get('seq')) is not int or path.name != f'{seq:06d}.json' or event.get('seq') != seq
                or event.get('parent_ref') != parent or event.get('settings_ref') != identity
                or type(event.get('at_unix')) not in (int, float) or not math.isfinite(event['at_unix'])
                or type(event.get('at_boottime')) not in (int, float)
                or not math.isfinite(event['at_boottime']) or event['at_boottime'] < 0
                or not isinstance(event.get('boot_id'), str) or not event['boot_id']
                or event.get('kind') not in accepted_budget.EVENT_KINDS):
            raise ValueError('Control ledger identity, sequence or time changed')
        if 'reanchor' in event:
            _verify_reanchor(root, settings, event, events)
        elif events:
            _duration(events[-1], event, legacy_elapsed=settings.get('time_budget_basis', 'elapsed') == 'elapsed')
        if 'uptime_accounting' in event and (event['uptime_accounting'] is not True
                or settings.get('time_budget_basis', 'elapsed') != 'active'):
            raise ValueError('Invalid uptime accounting marker')
        if 'observed_advance_seconds' in event and (event.get('uptime_accounting') is not True
                or type(event['observed_advance_seconds']) not in (int, float)
                or not math.isfinite(event['observed_advance_seconds']) or event['observed_advance_seconds'] < 0):
            raise ValueError('Invalid recorded advancement observation')
        if event['kind'] == 'budget_amend':
            for limits in (event.get('before'), event.get('after')):
                if (not isinstance(limits, dict) or set(limits) != {'max_tokens', 'max_seconds'}
                        or limits['max_tokens'] is not None and (
                            type(limits['max_tokens']) is not int or limits['max_tokens'] <= 0)
                        or limits['max_seconds'] is not None and (
                            type(limits['max_seconds']) not in (int, float)
                            or not 0 < limits['max_seconds'] < float('inf'))):
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
            elif (_direct_delivery_begin(events) or {}).get('authorization_step') is not None:
                from . import configuration_updates
                begin = _direct_delivery_begin(events)
                actions, confirmed = _direct_delivery_action_facts(events, begin)
                _validate_direct_delivery_action(event, begin=begin, actions=actions, confirmed=confirmed,
                    settings=configuration_updates.for_step(root, event['decision']['step']))
        if event['kind'] == 'action_outcome' and (not isinstance(event.get('action_id'), str)
                or not isinstance(event.get('result'), dict) or event['result'].get('status') not in {'confirmed', 'failed', 'unknown', 'rejected', 'engineering_error', 'unclassified'}):
            raise ValueError('Invalid delivery action outcome')
        events.append(event)
        parent = contracts.record_ref(path)
        origins[seq] = dict(path=path.relative_to(root).as_posix(), size_bytes=len(data))
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
    return _resource_origin(root, state=accepted_budget.read(root))['settings']


def _resource_origin(root, *, state):
    """Produce the pool's original policy and reference from one accepted head."""
    resource = state['resource']
    if resource is not None:
        reference = resource['settings_ref']
        saved = (state.base if reference == dict(path='settings.json')
            else contracts.read(Path(root)/reference['path']))
        return dict(settings=saved.get('settings', saved), ref=reference)
    if state.base.get('execution_resource_policy') is not None:
        return dict(settings=state.base, ref=dict(path='settings.json'))
    from . import configuration_updates
    selected = configuration_updates._current_revision(root, head=state)
    if selected is None:
        return dict(settings=state.base, ref=dict(path='settings.json'))
    return dict(settings=selected['settings'], ref=state['configuration']['ref'])


def _resource_owner(settings):
    policy = execution_resource_policy(settings.get('execution_resource_policy'))
    selected = settings.get('execution_config')
    if policy is None or selected is None or settings.get('execution_authority_policy') is None:
        raise ValueError('Explicit frozen execution resource owner is unavailable')
    value = dict(instance=settings['goal_id'], executor=selected['executor'])
    key = json.dumps(value, sort_keys=True, separators=(',', ':'))
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
                    or identity != security.request_digest(dict(owner=owner, binding=binding))
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
                    or any(not isinstance(ref,dict) or set(ref)!={'path','size_bytes'} for ref in proof.values())):
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
    root = Path(root).resolve()
    with _ledger_writer(root,context=operation_context) as base:
        state = accepted_budget.load(root, base=base)
        origin = _resource_origin(root, state=state)
        settings = origin['settings']; owner = _resource_owner(settings)
        events, parent, (grants, _) = _events_from_state(state, resources=True)
        if owner['key'] not in grants:
            event = _append(root, settings, events, parent, kind='resource_grant', owner=owner,
                previous_grant=None, allowance=settings['execution_resource_policy']['allowance'],
                resource_origin=origin,
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


@contextmanager
def _operator_writer_from_base(root, *, base):
    """Reuse the live controller boundary with an already produced base fact."""
    from .controller import _writer_from_base, require_writer
    try:
        require_writer(root)
    except RuntimeError:
        with _writer_from_base(Path(root).resolve(), base=base):
            yield
    else:
        from .protocol import require_current_base
        require_current_base(root, base=base)
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
        events, parent, (grants, _) = _events(root, resources=True)
        if operation_id is not None:
            operation = events.state.row('operation/'+operation_id)
            prior = events.state.event(operation['ref']) if operation is not None else None
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
    identity = security.request_digest(dict(owner=owner, binding=binding))
    q._read(root, request_ref); q._read(root, decision_ref)
    verified_rebind=None
    if unprepared_recovery is not None:
        from .execution_resume import verify_rebind
        found = accepted_budget.resource_for_call(accepted_budget.read(root), binding)
        if found is not None:
            _, original = found
            if original['binding']['scope'] == binding['scope'] and original['binding'] != binding:
                verify_rebind(root,unprepared_recovery,original['binding'],binding)
                verified_rebind=(original['ref'],original['binding'])
    with _ledger_writer(root,context=operation_context):
        events, parent, (grants, reservations) = _events(root, resources=True)
        grant = grants.get(owner['key'])
        if grant is None or grant_revision != grant['ref']:
            raise ValueError('Execution resource authorization revision changed')
        if identity in reservations:
            original = reservations[identity]
            if original['budget'] != budget or original['released'] or original['binding']!=binding:
                raise ValueError('Original execution reservation cannot be replaced')
            return original['ref']
        found = accepted_budget.resource_for_call(events.state, binding)
        same = [found] if found is not None and found[1]['binding']['scope'] == binding['scope'] else []
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
            total = events.state['resource']['held']
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
        for key, amount in events.state['resource']['held'].items():
            remaining[key] -= amount
        if any(budget[key] > remaining[key] for key in budget):
            raise ValueError('Cumulative execution resources are insufficient')
        event = _append(root, settings, events, parent, kind='resource_reserve', owner=owner,
            reservation_id=identity, binding=binding, budget=budget, grant_revision=grant_revision,
            effective_allowance={key: min(effective_allowance[key], grant['allowance'][key]) for key in budget})
        return _resource_ref(root, event)


def _reserved(state, reservation):
    _, rows = state
    if isinstance(rows, accepted_budget.ResourceRows):
        event = rows.state.event(reservation)
        identity = event['reservation_id']
        row = rows.get(identity)
        if row is None or row['ref'] != reservation:
            raise ValueError('Exact execution reservation is unavailable')
        return identity, row
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
        events, parent, state = _events(root, resources=True); identity, row = _reserved(state, reservation)
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
        # Binding/reconciliation retains consumed ownership after a Git export
        # is retired; actual input consumption is checked by its execution owner.
        if 'code_version' not in value:
            q._check(dict(path=prepared['local_path'], size_bytes=prepared['size_bytes']))
            q._check(value['script'])
        elif (value['script']['path']!=prepared['local_path']
                or value['script']['size_bytes']!=prepared['size_bytes']):
            raise ValueError('Accepted code differs from its original preparation')
        _append(root, settings, events, parent, kind='resource_bind', owner=row['owner'],
            reservation_id=identity, reservation=reservation, accepted_binding=original)
        return row['ref']


def settle_execution(root, *, reservation, observation, consumed):
    from . import experiments, qualification as q
    root = Path(root).resolve(); settings = contracts.settings(root)
    value = q._read(root, observation)
    experiments.verify_observation(value, lambda item: Path(item['path']),verify_files=False)
    consumed = _resource_dimensions(consumed, partial=True, zero=True)
    with _ledger_writer(root):
        events, parent, state = _events(root, resources=True); identity, row = _reserved(state, reservation)
        bound = row['bound']
        if (bound is None or row['released'] or value.get('call_id') != row['binding']['call_id']
                or value.get('scope') != row['binding']['scope']
                or any(experiments.proof(root/bound['accepted']['path']).get(key)!=item
                    for key,item in value.get('accepted',{}).items())):
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
    verified = experiments.verify_retirement(value, lambda item: Path(item['path']),verify_files=False)
    with _ledger_writer(root):
        events, parent, state = _events(root, resources=True); identity, row = _reserved(state, reservation)
        bound = row['bound']; binding = row['binding']
        if (bound is None or row['actual'] or verified.get('call_id') != binding['call_id']
                or verified.get('scope') != binding['scope'] or verified.get('authority') != binding['authority']
                or verified.get('prepared') != bound['prepared']
                or any(experiments.proof(root/bound['accepted']['path']).get(key)!=item
                    for key,item in verified.get('accepted',{}).items())):
            raise ValueError('Permanent retirement does not bind the original reserved preparation')
        if row['released']:
            return row['ref']
        _append(root, settings, events, parent, kind='resource_release', owner=row['owner'],
            reservation_id=identity, reservation=reservation, retirement=retirement)
        return row['ref']


def execution_accounting(root):
    root = Path(root).resolve(); settings = _resource_settings(root); owner = _resource_owner(settings)
    events, _, (grants, reservations) = _events(root, resources=True)
    grant = grants.get(owner['key'])
    remaining = dict(grant['allowance']) if grant else dict(settings['execution_resource_policy']['allowance'])
    if events.state['resource'] is not None:
        for key, amount in events.state['resource']['held'].items():
            remaining[key] -= amount
    count = 0 if events.state['resource'] is None else events.state['resource']['reservation_count']
    return dict(owner=owner, grant=grant, remaining=remaining, reservation_count=count,
        reservations_omitted_count=count, **(dict(reservations=[]) if not count else {}))


def audit_execution_accounting(root):
    """Explicit historical listing, never called by new admission."""
    root = Path(root).resolve()
    settings = _resource_settings(root)
    owner = _resource_owner(settings)
    _, _, (grants, reservations) = audit_events(root, resources=True)
    grant = grants.get(owner['key'])
    remaining = dict(grant['allowance']) if grant else dict(settings['execution_resource_policy']['allowance'])
    for row in reservations.values():
        if not row['released']:
            for key, amount in {**row['budget'], **row['actual']}.items():
                remaining[key] -= amount
    return dict(owner=owner, grant=grant, remaining=remaining, reservations=list(reservations.values()))


def execution_reservation(root, reservation):
    root = Path(root).resolve(); settings = contracts.settings(root)
    _, _, state = _events(root, resources=True)
    return _reserved(state, reservation)[1]


def authorization_revision(root):
    """Only explicit ceiling amendments revise authority; clocks never do."""
    events, _ = _events(root)
    return _authorization_revision(root, events)


def _authorization_revision(root, events):
    root = Path(root).resolve()
    reference = events.state['latest'].get('budget_amend')
    if reference is not None:
        return _resource_ref(root, events.event(reference))
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
    else:
        reserve = delivery_reserve(settings) or dict(seconds=0, tokens=0)
    return dict(seconds=budget_limits.remaining(limits['seconds_remaining'], reserve['seconds']),
                tokens=budget_limits.remaining(limits['max_tokens'],
                    usage.get('accounted_tokens', usage['observed_tokens']) + reserve['tokens']))


@contextmanager
def _ledger_writer(root, *, context=None):
    """Independent writer entry; produce base once, retaining the live gates."""
    with _ledger_writer_from_base(root, base=contracts.settings(root), context=context) as base:
        yield base


@contextmanager
def _ledger_writer_from_base(root, *, base, context=None):
    from .protocol import require_current_base
    from . import instance_owner
    with instance_owner.lease(root):
        require_current_base(root, base=base)
        fd = os.open(root / 'control-ledger.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            if context is None:fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                while True:
                    context.remaining_seconds()
                    try:
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:time.sleep(min(.05,context.remaining_seconds()))
            yield base
        finally:
            os.close(fd)


def _append(root, settings, events, parent, *, resource_origin=None, **fields):
    from .protocol import require_current_base
    require_current_base(root, base=events.state.base if isinstance(events, accepted_budget.AcceptedEvents) else settings)
    if isinstance(events, accepted_budget.AcceptedEvents):
        if 'reanchor' in fields:
            _verify_reanchor(root, settings, dict(fields, **clock_stamp()), events)
        return accepted_budget.append_budget_event(root, events, resource_origin=resource_origin, **fields)
    stamp = clock_stamp()
    event = dict(seq=len(events) + 1, parent_ref=parent,
                 settings_ref=contracts.record_ref(root / 'settings.json'), **stamp, **fields)
    if settings.get('time_budget_basis', 'elapsed') == 'active':
        event['uptime_accounting'] = True
        # Preserve every durable observation, including non-clock events,
        # when old native anchors have ambiguous UTC ordering across boots.
        event['observed_advance_seconds'] = _snapshot(root, settings, events, sample_stamp=stamp)['advance_seconds']
    if 'reanchor' in event:
        _verify_reanchor(root, settings, event, events)
    elif events:
        _duration(events[-1], stamp, legacy_elapsed=settings.get('time_budget_basis', 'elapsed') == 'elapsed')
    contracts.write(root / 'control-ledger' / f'{event["seq"]:06d}.json', event)
    return event


def paused_checkpoint(root):
    """Freeze only original paused ledger identity, never a second balance."""
    root = Path(root).resolve(); settings = contracts.settings(root)
    events, _ = _events(root)
    return _paused_checkpoint(root, settings, events)


def reanchor_paused_checkpoint(root, checkpoint_ref, transfer_ref):
    """Only the exclusive cold owner can bridge a proven paused active clock."""
    from . import instance_owner
    root = Path(root).resolve()
    with _ledger_writer(root):
        value = instance_owner.verify_transition(root, checkpoint_ref, transfer_ref)
        settings = contracts.settings(root); events, parent = _events(root)
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
    if isinstance(events, accepted_budget.AcceptedEvents):
        state = events.state
        return ({key: state['ceilings'][key] for key in ('max_tokens', 'max_seconds')},
            state['ceilings']['max_steps'], state['historical_max_steps'])
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
    events, _ = _events(root)
    limits, max_steps, _ = _ceiling_limits(events.state.base, events)
    return dict(**limits, max_steps=max_steps)


def snapshot(root, settings=None, *, sample_stamp=None):
    original = contracts.settings(root)
    if settings is not None and any(settings.get(key) != original.get(key) for key in ('goal_id', 'objective', 'public_root', 'global_materials')):
        raise ValueError('Budget view differs from original research identity')
    events, _ = _events_from_state(accepted_budget.load(root, base=original))
    return _snapshot(root, original, events, sample_stamp=sample_stamp)


def _accepted_advance_floors(root, start, events, now, *, basis):
    """Recover already persisted native observations only across boot histories."""
    stamps = [stamp for stamp in (start, *events, now) if stamp is not None]
    if basis != 'active' or len({stamp.get('boot_id') for stamp in stamps}) < 2:
        return {}
    from . import execution, fork_runtime
    floors, observations = {}, []
    for step in fork_runtime.local_steps(root, include_native=True):
        for _, intent in execution.accepted_intents(root, step):
            anchor = intent.get('budget_slice')
            if anchor is None:
                continue
            if (not isinstance(anchor, dict) or anchor.get('task_ref') != intent.get('task_ref')
                    or not isinstance(anchor.get('boot_id'), str) or not anchor['boot_id']
                    or type(anchor.get('advance_seconds')) not in (int, float)
                    or not math.isfinite(anchor['advance_seconds']) or anchor['advance_seconds'] < 0):
                raise ValueError('Accepted native budget observation changed')
            boot = anchor['boot_id']
            floors[boot] = max(floors.get(boot, 0.), anchor['advance_seconds'])
            observations.append((boot, anchor['advance_seconds'], intent.get('prepared_at')))
    observed = {stamp.get('boot_id') for stamp in stamps}
    boundaries = [(before, after) for before, after in zip(stamps, stamps[1:])
        if before.get('boot_id') != after.get('boot_id')]
    for boot, floor, prepared in observations:
        if boot in observed:
            continue
        # Old acceptance could leave no clock record on its boot. Locate its
        # original ordering before a subsequent observed boot, independently
        # of which later boot is currently being sampled. Missing old display
        # timestamps use the first known boundary, never a fabricated uptime.
        valid_time = type(prepared) in (int, float) and math.isfinite(prepared)
        boundary = next((pair for pair in boundaries if valid_time and pair[1]['at_unix'] >= prepared), boundaries[0])
        predecessor = boundary[0]['boot_id']
        floors[predecessor] = max(floors.get(predecessor, 0.), floor)
    return floors


def _clock_durations(start, events, now, *, basis, duration=None, advance_floors=None):
    """Fold clock facts for control and display; this grants no permission.

    A display-only reader may supply a nullable readability check. Unmarked
    closed clock intervals retain their historical charge; current intervals
    and new closures use every recorded uptime observation.
    """
    if duration is None:
        def duration(before, after, *, exclude_downtime=False):
            return _duration(before, after, legacy_elapsed=basis == 'elapsed', exclude_downtime=exclude_downtime)
    def plus(left, right):
        return left + right if left is not None and right is not None else None
    durations = dict(advance=0., waiting=0., paused=0., unclassified=0.)
    floors = advance_floors or {}
    phase, at = ('paused' if basis == 'active' else 'unclassified'), start
    previous, pending, retained = start, 0., 0.
    clock_seen = False
    pending_tail = unmeasured_tail = False
    def retain(boot):
        nonlocal retained
        floor = floors.get(boot, 0.)
        measured = plus(durations['advance'], pending if phase == 'advance' else 0.)
        if measured is not None:
            retained = max(retained, floor - measured)
    def retain_closed(event):
        if 'observed_advance_seconds' not in event:
            return
        value = event['observed_advance_seconds']
        if (event.get('uptime_accounting') is not True or type(value) not in (int, float)
                or not math.isfinite(value) or value < 0):
            durations['advance'] = None  # Saved display data is not admission-validated.
        elif durations['advance'] is not None:
            durations['advance'] = max(durations['advance'], value)
    def retain_pending(event):
        nonlocal retained
        if 'observed_advance_seconds' not in event:
            return
        value = event['observed_advance_seconds']
        if (event.get('uptime_accounting') is not True or type(value) not in (int, float)
                or not math.isfinite(value) or value < 0):
            durations['advance'] = None
            return
        measured = plus(durations['advance'], pending if phase == 'advance' else 0.)
        if measured is not None:
            # pending already contains this phase's measured uptime; retain
            # only the missing cumulative fact until its phase is committed.
            retained = max(retained, value - measured)
    for index, event in enumerate(events):
        if not clock_seen and event['kind'] != 'clock':
            # Configuration/resource facts may predate host start. Until the
            # first clock phase, only start anchors the initial idle interval.
            retain_pending(event)
            continue
        clock_seen = True
        prior_pending = pending
        prior_retained = retained
        if previous is not None:
            uptime = basis == 'active'
            if uptime and previous.get('boot_id') != event.get('boot_id'):
                retain(previous.get('boot_id'))
            pending = plus(pending, duration(previous, event, exclude_downtime=uptime))
            pending_tail |= uptime and previous.get('boot_id') != event.get('boot_id')
        previous = event
        if event['kind'] != 'clock':
            retain_pending(event)
            continue
        marked = event.get('uptime_accounting') is True
        if 'reanchor' in event:
            if at is not None and index:
                value = prior_pending if marked else duration(at, events[index - 1])
                durations['paused'] = plus(durations['paused'], value)
                if marked:
                    durations['advance'] = plus(durations['advance'], prior_retained)
            retain_closed(event)
            at, phase = event, 'paused'
            pending, pending_tail, retained = 0., False, 0.
            continue
        if at is not None:
            value = pending if marked else duration(at, event)
            durations[phase] = plus(durations[phase], value)
            if marked:
                durations['advance'] = plus(durations['advance'], retained)
            unmeasured_tail |= marked and pending_tail
        retain_closed(event)
        at, phase = event, event['phase']
        pending, pending_tail, retained = 0., False, 0.
    if at is not None and now is not None:
        uptime = basis == 'active'
        if uptime and previous.get('boot_id') != now.get('boot_id'):
            retain(previous.get('boot_id'))
        pending = plus(pending, duration(previous, now, exclude_downtime=uptime))
        if uptime:
            retain(now.get('boot_id'))
        pending_tail |= uptime and previous.get('boot_id') != now.get('boot_id')
        unmeasured_tail |= pending_tail
        value = pending if uptime else duration(at, now)
        durations[phase] = plus(durations[phase], value)
        durations['advance'] = plus(durations['advance'], retained)
    elif at is not None and basis == 'active':
        retain(previous.get('boot_id'))
        durations['advance'] = plus(durations['advance'], retained)
    return durations, phase, unmeasured_tail


def _snapshot(root, settings, events, *, sample_stamp=None):
    if isinstance(events, accepted_budget.AcceptedEvents):
        state = events.state
        clock = state['clock']
        start = clock['start']
        now = (clock_stamp() if state['seq'] or start is not None else None) if sample_stamp is None else sample_stamp
        if state['last_stamp'] is not None and now is not None:
            _duration(state['last_stamp'], now, legacy_elapsed=clock['basis'] == 'elapsed')
        if start is not None and now is not None and not clock['bridged']:
            _duration(start, now, legacy_elapsed=clock['basis'] == 'elapsed')
        durations, phase, tail = accepted_budget.clock_sample(clock, now)
        elapsed = (None if clock['bridged'] else _duration(start, now,
            legacy_elapsed=clock['basis'] == 'elapsed') if start is not None else 0.)
        if clock['basis'] == 'elapsed' and start is not None:
            elapsed = _duration(dict(at_unix=start['at_unix']), now)
        consumed = consumed_seconds(settings, durations, elapsed)
        return dict(state['ceilings'], original_max_steps=settings['max_steps'], time_budget_basis=clock['basis'],
            waiting_consumes_budget=not waiting_is_free(settings),
            advance_seconds=durations['advance'], waiting_seconds=durations['waiting'],
            paused_seconds=durations['paused'], unclassified_seconds=durations['unclassified'],
            elapsed_seconds=elapsed, consumed_seconds=consumed,
            seconds_remaining=budget_limits.remaining(state['ceilings']['max_seconds'], consumed), clock_phase=phase,
            ledger_events=state['seq'], historical_time='unknown_before_ledger' if clock['basis'] == 'elapsed'
                else 'recorded_active_with_unmeasured_paused_gap' if clock['bridged'] else 'recorded',
            **(dict(unmeasured_paused_gap=True) if clock['bridged'] else {}),
            **(dict(unmeasured_shutdown_tail=True) if tail else {}))
    basis = settings.get('time_budget_basis', 'elapsed')
    if basis not in {'active', 'elapsed'}:
        raise ValueError('Unknown frozen time-budget basis')
    def interval(before, after):
        return _duration(before, after, legacy_elapsed=basis == 'elapsed')
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
    floors = _accepted_advance_floors(root, start, events, now, basis=basis)
    durations, phase, unmeasured_tail = _clock_durations(start, events, now, basis=basis, advance_floors=floors)
    advance, waiting, paused, unclassified = (durations[key] for key in ('advance', 'waiting', 'paused', 'unclassified'))
    elapsed = None if bridged else interval(start, now) if start is not None else 0.
    if basis == 'elapsed' and start is not None:
        elapsed = _duration(dict(at_unix=start['at_unix']), now)
    consumed = consumed_seconds(settings, durations, elapsed)
    return dict(**limits, max_steps=max_steps, original_max_steps=settings['max_steps'], time_budget_basis=basis, advance_seconds=advance, waiting_seconds=waiting,
                waiting_consumes_budget=not waiting_is_free(settings),
                paused_seconds=paused, unclassified_seconds=unclassified, elapsed_seconds=elapsed, consumed_seconds=consumed,
                seconds_remaining=budget_limits.remaining(limits['max_seconds'], consumed), clock_phase=phase,
                ledger_events=len(events), historical_time='unknown_before_ledger' if basis == 'elapsed' else
                'recorded_active_with_unmeasured_paused_gap' if bridged else 'recorded',
                **(dict(unmeasured_paused_gap=True) if bridged else {}),
                **(dict(unmeasured_shutdown_tail=True) if unmeasured_tail else {}))


def status_snapshot(root):
    """Read closed active consumption at its recorded endpoint, without now.

    Open intervals and legacy elapsed budgets retain strict live-clock reads.
    This projection is not an admission or a clock continuation permission.
    """
    root = Path(root)
    settings = contracts.settings(root)
    if settings.get('time_budget_basis', 'elapsed') != 'active':
        return snapshot(root, settings)
    events, _ = _events(root)
    phase = events.state['clock']['phase']
    if phase != 'paused':
        return _snapshot(root, settings, events)
    started = root / 'host-started.json'
    stamp = events.state['last_stamp'] if events else contracts.read(started) if started.exists() else None
    if started.exists() and not events.state['clock']['clock_seen']:
        start = contracts.read(started)
        if (stamp is None or start.get('boot_id') == stamp.get('boot_id')
                and start.get('at_boottime', 0.) > stamp.get('at_boottime', 0.)):
            # A pre-start configuration fact is not the initial paused clock's
            # endpoint. Both choices are saved facts; never sample now here.
            stamp = start
    value = _snapshot(root, settings, events, sample_stamp=stamp)
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
        events, parent = _events(root)
        # Check rollback even when the phase is unchanged.
        _snapshot(root, settings, events)
        current = events.state['clock']['phase'] if events.state['clock']['clock_seen'] else None
        if current != phase:
            _append(root, settings, events, parent, kind='clock', phase=phase, reason=reason)


def execution_blocked(root, *, ignore_operation=None, include_managed=True, ignore_recovery=None):
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
                        or identity.get('task_ref') != action['intent']['operation_id']
                        or identity.get('boot_id') != clock_stamp()['boot_id']
                        or any(type(identity.get(key)) is not int or identity[key] <= 0 for key in ('pid', 'start_ticks', 'pgid', 'sid'))
                        or runtime.process_matches(identity, action['intent']['operation_id'])
                        or _delivery_group_live(identity)):
                    return True
    unresolved = state['unresolved_operation_count']
    if ignore_operation is not None:
        ignored = accepted_budget.read(root).row('delivery-operation/'+ignore_operation)
        unresolved -= int(ignored is not None and ignored['pending'] > 0)
    if unresolved > 0:
        return True
    state_head = accepted_budget.read(root)
    current_native = state_head['current_native']
    for step in (() if current_native is None else (current_native['step'],)):
        try:
            record = execution.record(root, step)
            if not (record / 'native-intent.json').exists():
                continue
            binding = contracts.record_ref(root / 'jobs' / f'{step:03d}.json')
            if contracts.read(record / 'native-intent.json')['task_ref'] != binding:
                return True
            outcome = contracts.read(record / 'native-outcome.json')
            child_path = record / 'native-process.json'
            if outcome.get('stop_reason') == 'cleanup_unknown' or outcome.get('cleanup') == 'cleanup_unknown':
                return True
            # A host preparation error can retain an unknown result/usage even
            # though native was never submitted. That uncertainty must not
            # keep an absent controller's advancement clock running. Conflicting
            # activity or any original native receipt still prevents inactivity.
            if outcome.get('stop_reason') == 'native_error_unknown' and outcome.get('native_started') is False:
                if (outcome.get('returncode') is not None or child_path.exists() or child_path.is_symlink()
                        or outcome.get('native_activity_observed') or outcome.get('terminal_observed')):
                    return True
                execution_key = execution.identity(root, step)
                finishing_self = False
                for name in ('runner-process.json', 'watchdog-process.json'):
                    receipt_path = record / name
                    if not receipt_path.exists() and not receipt_path.is_symlink():
                        continue
                    receipt = contracts.read(contracts.regular(receipt_path))
                    if (receipt.get('task_ref') != execution_key
                            or not isinstance(receipt.get('boot_id'), str) or not receipt['boot_id']
                            or any(type(receipt.get(key)) is not int or receipt[key] <= 0
                                for key in ('pid', 'start_ticks', 'pgid', 'sid', 'ppid'))):
                        return True
                    if runtime.process_matches(receipt, execution_key):
                        if name == 'runner-process.json' and receipt['pid'] == os.getpid():
                            # The runner's last completion call already ended
                            # native/checkpoint writes. Its exact watchdog only
                            # waits for this runner to exit; neither can submit.
                            finishing_self = True
                        elif not finishing_self:
                            return True
                continue
            if outcome.get('stop_reason') == 'native_error_unknown':
                return True
            if not (type(outcome.get('returncode')) is int or outcome.get('native_started') is False):
                return True
            if child_path.exists():
                child = contracts.read(child_path)
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
            member = runtime.process_identity(int(path.name), identity['task_ref'])
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
    events, _ = _events(root)
    return _ceiling_limits(settings, events)[1]


def historical_step_limit(root):
    """Read original granted or consumed steps, never a new grant.

    A lower current ceiling cannot hide original consumption or accepted work.
    Only future effects use effective_max_steps; historical validators retain
    every prepared original, including the exceptional final A, and its checks.
    """
    root = Path(root)
    settings = contracts.settings(root)
    events, _ = _events(root)
    return max(_ceiling_limits(settings, events)[2], events.state['max_local_step'])


step_limit = effective_max_steps


def task_step_available(root, step):
    """Check a new task's resource count independently of its logical label."""
    state = accepted_budget.read(root)
    if not state['is_fork']:
        return step <= step_limit(root)
    additional = int(state.row(f'job/{step:03d}') is None)
    return used_steps(root) + additional <= step_limit(root)


def usage_accounting(root, meter, *, events=None):
    """Retain reported consumption and unknown coverage without estimated fees."""
    # Missing billing coverage neither invents usage nor grants replay;
    # unresolved execution is checked through its own retained facts.
    return dict(reserved_tokens=0, accounted_tokens=meter['observed_tokens'],
        reserved_unknown_steps=[], blocking_unknown_steps=[], accounting_basis='observed_lower_bound')


def amend(root, *, max_tokens=None, max_seconds=None, max_steps=None, authorization_note, authorization_statement=None):
    from .controller import _writer
    with _writer(root):
        return _amend_locked(root, max_tokens=max_tokens, max_seconds=max_seconds, max_steps=max_steps,
                             authorization_note=authorization_note, authorization_statement=authorization_statement)


def _validate_unlimited_node_targets(limits, settings):
    """Unlimited globals need finite targets from this produced configuration."""
    if any(limits[key] is None for key in ('max_tokens', 'max_seconds')):
        targets = settings.get('node_budget_policy')
        if targets is None:
            raise ValueError('Unlimited global budgets require explicit finite all-role node budget targets')
        node_policy(targets)


def _validate_amendment(root, *, max_tokens=None, max_seconds=None, max_steps=None, authorization_note, authorization_statement=None, applicable_settings=None):
    """Operator entry only. A note records a decision; it authenticates nobody."""
    from .protocol import require_current
    require_current(root)
    if not isinstance(authorization_note, str) or not authorization_note.strip():
        raise ValueError('An explicit operator authorization note is required')
    if max_tokens is None and max_seconds is None and max_steps is None:
        raise ValueError('Specify a revised token, seconds or step ceiling')
    if max_tokens not in (None, 'unlimited') and (type(max_tokens) is not int or max_tokens <= 0):
        raise ValueError('max_tokens must be a positive integer or unlimited')
    if max_seconds not in (None, 'unlimited') and (type(max_seconds) not in (int, float) or not 0 < max_seconds < float('inf')):
        raise ValueError('max_seconds must be positive and finite or unlimited')
    if max_steps is not None and (type(max_steps) is not int or max_steps <= 0):
        raise ValueError('max_steps must be a positive integer')
    if max_steps is not None and authorization_statement is None:
        raise ValueError('A step ceiling amendment requires the original operator statement')
    if authorization_statement is not None and (not isinstance(authorization_statement, str) or not authorization_statement.strip()):
        raise ValueError('The original operator statement must be nonempty')
    if applicable_settings is not None:
        limits = {key: applicable_settings[key] for key in ('max_tokens', 'max_seconds')}
        for key, value in (('max_tokens', max_tokens), ('max_seconds', max_seconds)):
            if value is not None:
                limits[key] = None if value == 'unlimited' else value
        _validate_unlimited_node_targets(limits, applicable_settings)


def _amend_locked(root, *, max_tokens=None, max_seconds=None, max_steps=None, authorization_note, authorization_statement=None, operation_id=None, applicable_settings=None):
    """Original amendment policy, called only by an existing controller writer."""
    _validate_amendment(root, max_tokens=max_tokens, max_seconds=max_seconds, max_steps=max_steps,
                      authorization_note=authorization_note, authorization_statement=authorization_statement,
                      applicable_settings=applicable_settings)
    if execution_blocked(root):
        raise RuntimeError('Budget amendment requires no running or unknown original execution')
    settings = contracts.settings(root)
    with _ledger_writer(root):
        events, parent = _events(root)
        if operation_id is not None:
            operation = events.state.row('operation/'+operation_id)
            prior = events.state.event(operation['ref']) if operation is not None else None
            if prior is not None:
                return dict(status='budget_amended', amendment_seq=prior['seq'], before=prior['before'], after=prior['after'], consumption='retained')
        value = _snapshot(root, settings, events)
        before = {key: value[key] for key in ('max_tokens', 'max_seconds')}
        after = dict(before, **({} if max_tokens is None else dict(max_tokens=None if max_tokens == 'unlimited' else max_tokens)),
                     **({} if max_seconds is None else dict(max_seconds=None if max_seconds == 'unlimited' else max_seconds)))
        if applicable_settings is None:
            from . import configuration_updates
            applicable_settings = configuration_updates.current_policy(root, base=settings, head=events.state)
        _validate_unlimited_node_targets(after, applicable_settings)
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


def node_mode(root, *, settings=None):
    from .protocol import require_settings_format
    settings = require_settings_format(contracts.settings(root) if settings is None else settings)
    return settings['node_budget_mode']


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


def node_limits(root, job, *, settings=None):
    from . import configuration_updates
    from .protocol import require_settings_format
    if settings is None:
        settings = configuration_updates.for_new_job(root, job['step']) if 'configuration_revision' not in job else contracts.settings(root, job)
    require_settings_format(settings)
    policy = settings.get('node_budget_policy')
    if policy is None:
        request=job.get('supervision_request') or {}
        if ((settings.get('supervision_policy') or {}).get('version')==4
                and job.get('role')=='A' and request.get('automatic') is True
                and request.get('final') is False):
            # Reuse the original global ceilings solely as the existing soft
            # node anchor. No extra allocation or backend target is introduced.
            return dict(max_observed_tokens=settings['max_tokens'],max_seconds=settings['max_seconds'])
        if job.get('final_only') and job.get('role') == 'A':
            if job.get('purpose') == 'closeout' and settings.get('delivery_policy') is not None:
                return dict(settings['delivery_policy']['closeout_a'])
            if settings['max_tokens'] is None or settings['max_seconds'] is None:
                raise ValueError('Unlimited global budgets require explicit finite node budget targets for final A')
            return dict(max_observed_tokens=settings['max_tokens'], max_seconds=settings['max_seconds'])
        return None
    limits = node_policy(policy)[job['role']]
    if job.get('purpose') == 'closeout' and job['role']=='A':
        delivery = settings.get('delivery_policy')
        if delivery is None or job['role'] != 'A':
            raise ValueError('Unconfigured closeout node budget')
        limits = dict(delivery['closeout_a'])
    elif job['role'] in ('H', 'A') and settings.get('role_budget_policy') is not None:
        limits['max_observed_tokens'] = min(limits['max_observed_tokens'], settings['role_budget_policy']['thinking_node_target_tokens'])
    if job['role'] == 'W' and settings.get('search_policy') is not None:
        limits['max_seconds'] = min(limits['max_seconds'], settings['search_policy']['max_w_seconds'])
        limits['max_observed_tokens'] = min(limits['max_observed_tokens'], settings['search_policy']['max_w_observed_tokens'])
    return limits


def _final_a_policy(settings):
    from .protocol import require_settings_format
    return require_settings_format(settings)['final_a_completion_policy']


def protected_final_report(settings):
    """The frozen completion policy protects one explicitly configured final A."""
    _final_a_policy(settings)
    policy = settings.get('delivery_policy')
    if (settings.get('node_budget_policy') is None
            or policy is not None and policy['adapter'] is not None):
        return False
    return True


def _final_a_reserve(settings, job, events):
    if settings.get('delivery_policy') is None:
        return dict(tokens=0, seconds=0., steps=0)
    preparation = 0.
    if job.get('purpose') == 'closeout':
        begin = (events.state['closeout'] if isinstance(events, accepted_budget.AcceptedEvents) else
            next((row for row in reversed(events) if row['kind'] == 'closeout_begin'), None))
        if (begin is None or begin['mode'] not in {'model', 'final'} or job.get('source_cutoff') != begin['source_cutoff']
                or job['step'] < begin['first_step']):
            raise ValueError('Final A has no original model closeout boundary')
        preparation = begin['preparation_seconds']
    return closeout_floor(settings, preparation_seconds=preparation)


def prelaunch_budget_reason(root, step, record):
    """Recognize the host's exact refusal before native submission.

    Missing process receipts alone say nothing about submission. This requires
    the positive original refusal and its zero invocation meter instead.
    """
    intent = contracts.read(contracts.regular(record / 'native-intent.json'))
    outcome_path = record / 'native-outcome.json'
    if not outcome_path.exists():return None
    outcome = contracts.read(contracts.regular(outcome_path))
    if (set(outcome) != {'returncode', 'stop_reason', 'native_started', 'usage', 'thread_id', 'limitation'}
            or intent.get('task_ref') != contracts.record_ref(contracts.regular(Path(root) / 'jobs' / f'{step:03d}.json'))
            or outcome.get('native_started') is not False or outcome.get('returncode') is not None
            or outcome.get('stop_reason') not in {'wall_limit', 'token_limit'}
            or outcome.get('thread_id') is not None
            or outcome.get('limitation') != 'Budget stopped the original intent before native submission.'
            or outcome.get('raw_ref') is not None or outcome.get('terminal_observed')
            or not (record / 'usage.json').exists()
            or contracts.read(contracts.regular(record / 'usage.json')) != dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
            or (record / 'native-process.json').exists() or (record / 'native-process.json').is_symlink()):
        return None
    return outcome['stop_reason']


def stopped_report_original(root, step):
    """Bind a cleaned stopped native task; its scientific output stays unqualified."""
    job_path = Path(root) / 'jobs' / f'{step:03d}.json'
    record = contracts.host_record(contracts.workspace(root, step))
    from . import execution
    chain = execution.records(root, step) if (record / 'native-intent.json').exists() else [record]
    if len(chain) > 1:
        value = dict(step=step, job_ref=contracts.record_ref(contracts.regular(job_path)),
            records=_stopped_chain_records(record, chain))
        _validate_stopped_report(root, value)
        return value
    names = ['native-intent.json', 'native-outcome.json'] if (record / 'native-intent.json').exists() else []
    if names and prelaunch_budget_reason(root, step, record) is None:
        names.append('usage-observation.json')
    if (record / 'usage.json').exists():
        names.append('usage.json')
    value = dict(step=step, job_ref=contracts.record_ref(contracts.regular(job_path)),
                 records={name: contracts.record_ref(contracts.regular(record / name)) for name in names})
    _validate_stopped_report(root, value)
    return value


def _stopped_chain_records(origin, chain):
    """Use the existing original-relative record map for every accepted attempt."""
    from . import execution
    records = {}
    for record in chain:
        names = set(execution.proof(record))
        names.update(name for name in ('usage-observation.json', 'runner-process.json', 'native-process.json')
            if (record / name).exists())
        for name in sorted(names):
            records[(record / name).relative_to(origin).as_posix()] = contracts.record_ref(contracts.regular(record / name))
    return records


def _validate_stopped_report(root, value):
    if (not isinstance(value, dict) or set(value) != {'step', 'job_ref', 'records'}
            or type(value['step']) is not int or value['step'] <= 0 or not isinstance(value['records'], dict)
            or any(not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts
                for name in value['records'])
            or not any('/' in name for name in value['records']) and set(value['records']) not in (set(), {'native-intent.json', 'native-outcome.json', 'usage.json'},
                    {'native-intent.json', 'native-outcome.json', 'usage-observation.json'},
                    {'native-intent.json', 'native-outcome.json', 'usage-observation.json', 'usage.json'})):
        raise ValueError('Final report requires the exact stopped original')
    path = Path(root) / 'jobs' / f'{value["step"]:03d}.json'
    job = (contracts.read_job(path) if not value['records']
           else contracts.read(contracts.regular(path)))
    record = contracts.host_record(contracts.workspace(root, value['step']))
    if (contracts.record_ref(path) != value['job_ref']
            or (record / 'result-identity.json').exists()
            or any(contracts.record_ref(contracts.regular(record / name)) != proof for name, proof in value['records'].items())):
        raise ValueError('Stopped report original identity changed')
    if any('/' in name for name in value['records']):
        from . import continuation, execution
        chain = execution.records(root, value['step'])
        if (len(chain) < 2 or value['records'] != _stopped_chain_records(record, chain)
                or prelaunch_budget_reason(root, value['step'], chain[-1]) is None):
            raise ValueError('Final report requires the exact accepted chain and pre-submission budget refusal')
        continuation.validate_repair_chain(root, value['step'], chain)
        continuation.validate_fragment_chain(root, value['step'], chain)
        # Prior accepted work remains consumed. The last refusal has no new
        # invocation report; only its predecessor reports can supply this cost.
        last = contracts.read(chain[-1] / 'native-intent.json')
        tokens, seconds = _attempt_consumption(root, job, chain[:-1],
            automatic_successor=last if last.get('continuation_kind') == 'automatic_recovery' else None,
            operator_successor=last if last.get('continuation_kind') == 'operator_resume' else None,
            reentry_successor=last if last.get('continuation_kind')=='reentry' else None)
        anchor = last.get('budget_slice') or {}
        if anchor.get('previous_tokens') != tokens or anchor.get('previous_seconds') != seconds:
            raise ValueError('Stopped continuation changed its consumed original budget')
        for prior in chain[:-1]:
            if not (prior / 'native-outcome.json').exists():
                continuation.interruption(root, job, prior)
                continue
            outcome = contracts.read(prior / 'native-outcome.json')
            if (type(outcome.get('returncode')) is not int or outcome.get('cleanup') != 'observed_group_cleaned'
                    or outcome.get('stop_reason') in {'native_error_unknown', 'cleanup_unknown'}):
                raise ValueError('Final report cannot summarize a live or unknown-effect original')
        return value
    if (record / 'continuations').exists():
        raise ValueError('Stopped report omitted accepted native continuations')
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
    if set(value['records']) == {'native-intent.json', 'native-outcome.json', 'usage.json'}:
        if 'attempt' not in intent and prelaunch_budget_reason(root, value['step'], record) is not None:
            return value
        raise ValueError('Final report requires the exact native pre-submission budget refusal')
    if (intent.get('task_ref') != value['job_ref'] or 'attempt' in intent
            or type(outcome.get('returncode')) is not int or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('stop_reason') not in {'closeout_protected_token_limit', 'closeout_protected_time_limit',
                'token_limit', 'wall_limit', 'node_slice_token_limit', 'node_slice_time_limit'}):
        raise ValueError('Final report cannot summarize a live or unknown-effect original')
    return value


def validate_final_report_inputs(root, job, *, events=None):
    value = job.get('final_report')
    if value is None:
        return None
    policy = contracts.settings(root, job)
    if events is None:
        events, _ = _events(root)
    return _validate_final_report_inputs_from_facts(root, job, policy=policy, events=events)


def _validate_final_report_inputs_from_facts(root, job, *, policy, events):
    """Check this owner's FINAL inputs against its selected policy and head."""
    value = job.get('final_report')
    if value is None:
        return None
    delivery = policy.get('delivery_policy')
    begin = _final_boundary(events, job)
    final = begin is not None
    if (not (protected_final_report(policy) or final)
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
    return _usage_unknown(usage, allowed)


def _usage_unknown(usage, allowed=()):
    known = set(usage.get('blocking_unknown_steps', usage.get('unknown_steps', [])))
    count = usage.get('blocking_unknown_count', len(known))
    return count-len(known.intersection(allowed)) > 0


def _active_final(events):
    if isinstance(events, accepted_budget.AcceptedEvents):
        phase = events.state['closeout']
        return phase if phase is not None and phase['mode'] == 'final' else None
    phase = next((row for row in reversed(events) if row['kind'] in {'closeout_begin','closeout_resume'}), None)
    return phase if phase is not None and phase['kind'] == 'closeout_begin' and phase['mode'] == 'final' else None


def _final_boundary(events, job):
    if isinstance(events, accepted_budget.AcceptedEvents):
        row = events.state['closeout']
        return row if (row is not None and row['mode'] == 'final' and job.get('role') == 'A'
            and job.get('formal_review') is None and job.get('final_only') and job['step'] == row['first_step']) else None
    return next((row for row in reversed(events) if row['kind'] == 'closeout_begin' and row['mode'] == 'final'
        and job.get('role') == 'A' and job.get('formal_review') is None and job.get('final_only')
        and job['step'] == row['first_step']), None)


def normal_resume(root, before_step=None, *, events=None):
    if events is None:
        events, _ = _events(root)
    return _normal_resume_from_facts(root,before_step,events=events)


def _normal_resume_from_facts(root, before_step=None, *, events):
    """Project the owner's accepted resume without loading another head."""
    if events is None:
        raise ValueError('Normal resume requires its produced accepted events')
    if isinstance(events, accepted_budget.AcceptedEvents):
        row = events.state['resume']
        return events.event(row['ref']) if row is not None and (before_step is None or row['first_step'] <= before_step) else None
    return next((row for row in reversed(events) if row['kind'] == 'closeout_resume'
        and (before_step is None or row['first_step'] <= before_step)), None)


def _event_ref(root, event):
    return dict(seq=event['seq'], record=contracts.record_ref(Path(root)/'control-ledger'/f'{event["seq"]:06d}.json'))


def _validate_cursor(root, cursor, before_step, events):
    from . import fork_runtime, handoff_state
    fields = {'role','parents','instruction','evidence_return','correction_request','author_correction','source_refs'}
    if (not isinstance(cursor,dict) or set(cursor) not in (fields,fields|{'supervision_request'}) or cursor['role'] not in ('H','W','A','E')
            or not isinstance(cursor['instruction'],str) or not isinstance(cursor['parents'],list)
            or not isinstance(cursor['source_refs'],list)):
        raise ValueError('Final resume requires its exact pending scientific route')
    if 'supervision_request' in cursor:
        if cursor['role']!='A' or cursor['evidence_return'] is not None:
            raise ValueError('Final resume supervision requires its pending ordinary A route')
        contracts.supervision_binding(cursor['supervision_request'],step=before_step)
    steps = []
    for ref in cursor['source_refs']:
        number = ref.get('step')
        if (set(ref) != {'step','seal_ref'} or type(number) is not int or number <= 0
                or not isinstance(ref['seal_ref'], dict) or set(ref['seal_ref']) != {'path'}):
            raise ValueError('Final resume source differs from declared normal science')
        # These jobs describe the already accepted sequence. Reading their
        # saved route flags does not reopen original scientific verification.
        with handoff_state.loading():
            if fork_runtime.source_job(root, number).get('final_only'):
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
        with handoff_state.loading():
            expected = fork_runtime.science_steps(root, before_step)
    if (steps != sorted(set(steps)) or any(number not in steps for number in cursor['parents']) or steps != expected):
        raise ValueError('Final resume parents are outside its exact declared sequence')


def require_cursor_eligibility(root, cursor, before_step, events):
    """Bind the saved accepted sequence without revalidating its old science.

    FINAL routing retains exact source seals and parents. New execution still
    passes its current admission fences; old seals do not renew that permission.
    """
    _validate_cursor(root,cursor,before_step,events)
    if cursor['source_refs'] != _normal_source_refs(root,before_step,events):
        raise ValueError('Final resume source differs from accepted normal science')


def _threshold_raised(events, after_seq):
    if isinstance(events, accepted_budget.AcceptedEvents):
        ref = events.state['explicit_amendment']
        latest = events.event(ref) if ref is not None else None
        return latest is not None and latest['seq'] > after_seq and (any(
            latest['before'][key] is not None and (latest['after'][key] is None
                or latest['after'][key] > latest['before'][key]) for key in ('max_tokens','max_seconds'))
            or latest.get('after_max_steps',0) > latest.get('before_max_steps',0))
    latest = next((row for row in reversed(events) if row['kind'] == 'budget_amend' and row['seq'] > after_seq
                   and not str(row.get('operation_id', '')).startswith('configuration-')), None)
    return latest is not None and (any(latest['before'][key] is not None and (
        latest['after'][key] is None or latest['after'][key] > latest['before'][key]) for key in ('max_tokens','max_seconds'))
        or latest.get('after_max_steps',0) > latest.get('before_max_steps',0))


def _validate_resume_event(root, event, previous):
    if 'reconciliation_ref' in event:
        raise ValueError('FINAL reconciliation rescue events are no longer supported')
    begin = _active_final(previous)
    if begin is not None and isinstance(previous, accepted_budget.AcceptedEvents):
        begin = previous.event(begin['ref'])
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
        events,parent = _events(root)
        current = _active_final(events)
        if current is not None and begin['seq'] == current['seq']:
            begin = events.event(current['ref'])
        value = dict(kind='closeout_resume', source_final=_event_ref(root,begin), first_step=first_step)
        # Validate before publication, preserving the original final cycle.
        probe = dict(value,seq=len(events)+1)
        _validate_resume_event(root,probe,events)
        require_cursor_eligibility(root,begin['resume_cursor'],begin['source_cutoff']+1,events)
        return _append(root,settings,events,parent,**value)


def resume_cursor(root, resume, *, events=None):
    if events is None:
        events,_ = _events(root)
    if isinstance(events, accepted_budget.AcceptedEvents):
        saved = events.state['resume']
        original = events.event(saved['ref']) if saved is not None else None
        if saved is None or (resume != original and resume != saved):
            raise ValueError('Normal resume must be the exact accepted journal event')
        if _event_ref(root, original) != dict(seq=saved['seq'], record=saved['ref']):
            raise ValueError('Normal resume changed its accepted journal reference')
        begin = events.event(original['source_final']['record'])
        if original['source_final'] != _event_ref(root, begin):
            raise ValueError('Normal resume changed its final source')
        return begin['resume_cursor']
    if not any(row['kind'] == 'closeout_resume' and row == resume for row in events):
        raise ValueError('Normal resume must be the exact immutable journal event')
    begin = next(row for row in events if row['seq'] == resume['source_final']['seq'])
    if resume['source_final'] != _event_ref(root,begin):
        raise ValueError('Normal resume changed its final source')
    return begin['resume_cursor']


def _normal_source_refs(root, before_step, events):
    from . import fork_runtime, handoff_state
    if fork_runtime.is_fork(root):
        with handoff_state.loading():
            return [dict(step=number, seal_ref=history.source_seal_ref(originals.node(root,number)))
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
            refs.append(dict(step=number,seal_ref=contracts.record_ref(contracts.regular(seal))))
    return refs


def scientific_steps(root, before_step, *, resume=None, events=None):
    """Only a journal-bound resume may omit preserved non-scientific originals."""
    if events is None:
        events,_ = _events(root)
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


def final_a_admission(root, job, *, final_report=None, events=None):
    """Final eligibility comes only from the persisted phase, never its balance."""
    settings = contracts.settings(root, job)
    _final_a_policy(settings)
    if events is None:
        events, _ = _events(root)
    boundary = _final_boundary(events, job)
    active = _active_final(events)
    return 'final_state' if boundary is not None and active is not None and boundary['seq'] == active['seq'] else None


def _validate_final_a_event(root, settings, event, previous):
    """Replay only the original admission facts; never sample live budget here."""
    fields = {'seq', 'parent_ref', 'settings_ref', 'at_unix', 'at_boottime', 'boot_id',
              'kind', 'version', 'step', 'attempt', 'basis', 'anchor', 'selection', 'reserve'}
    _final_a_policy(settings)
    if (set(event) - {'uptime_accounting', 'observed_advance_seconds','head_updates','index_updates','control_files'} != fields
            or type(event['version']) is not int or event['version'] != 2
            or type(event['step']) is not int or event['step'] <= 0
            or type(event['attempt']) is not int or event['attempt'] != 0
            or any(row['kind'] == 'final_a_return' and row['step'] == event['step'] for row in previous)):
        raise ValueError('Invalid or reused final A return authorization')
    job_path = Path(root) / 'jobs' / f'{event["step"]:03d}.json'
    job = contracts.read(contracts.regular(job_path))
    settings = contracts.settings(root, job)
    anchor, selection, reserve = event['anchor'], event['selection'], event['reserve']
    ceilings, max_steps, _ = _ceiling_limits(contracts.settings(root), previous)
    if (not isinstance(anchor, dict) or set(anchor) != {'task_ref', 'role', 'boot_id', 'advance_seconds', 'limits', 'shared_limits'}
            or anchor['task_ref'] != contracts.record_ref(job_path) or anchor['role'] != 'A'
            or anchor['boot_id'] != event['boot_id']
            or type(anchor['advance_seconds']) not in (int, float) or not math.isfinite(anchor['advance_seconds'])
            or anchor['advance_seconds'] < 0 or not isinstance(anchor['limits'], dict) or anchor['limits'] != node_limits(root, job)
            or anchor['shared_limits'] != ceilings
            or not isinstance(selection, dict) or set(selection) != {'max_steps', 'used_steps', 'accounted_tokens', 'seconds_remaining', 'blocked_role', 'stopped_original'}
            or selection['blocked_role'] not in (None, 'H', 'W', 'A', 'E')
            or type(selection['max_steps']) is not int or selection['max_steps'] != max_steps
            or type(selection['used_steps']) is not int or not _selection_accounts_job(root, job, selection['used_steps'])
            or type(selection['accounted_tokens']) is not int or selection['accounted_tokens'] < 0
            or ceilings['max_seconds'] is None and selection['seconds_remaining'] is not None
            or ceilings['max_seconds'] is not None and (
                type(selection['seconds_remaining']) not in (int, float)
                or not math.isfinite(selection['seconds_remaining'])
                or not 0 <= selection['seconds_remaining'] <= ceilings['max_seconds'])
            or reserve != _final_a_reserve(settings, job, previous)
            or event['basis'] != 'final_state' or _final_boundary(previous, job) is None
            or _active_final(previous) != _final_boundary(previous, job)):
        raise ValueError('Final A return differs from its exact original admission')
    report = job.get('final_report')
    if report is not None and (not isinstance(report,dict) or set(report) != {'source_cutoff','stopped_original'}
            or type(report['source_cutoff']) is not int or not 0 <= report['source_cutoff'] < job['step']):
        raise ValueError('Final A return changed its declared report boundary')
    boundary = _final_boundary(previous, job)
    expected_stopped = boundary.get('stopped_original') if boundary is not None else report['stopped_original'] if report else None
    if selection['stopped_original'] != expected_stopped:
        raise ValueError('Final A return changed its exact report input boundary')


def final_a_return(root, job, intent, *, events=None):
    """Validate one final A identity, including its bounded output-only repairs."""
    policy = contracts.settings(root, job)
    _final_a_policy(policy)
    if events is None:
        events, _ = _events(root)
    return _final_a_return_from_facts(root, job, intent, policy=policy, events=events)


def _final_a_return_from_facts(root, job, intent, *, policy, events):
    """Consume the selected current policy without another admission sample."""
    _final_a_policy(policy)
    marker = (intent.get('budget_slice') or {}).get('final_return')
    if isinstance(events, accepted_budget.AcceptedEvents):
        accepted = events.state['final_return']
        if isinstance(marker, dict) and (accepted is None or accepted['seq'] != marker.get('seq')):
            accepted = events.event(marker['record'])
        if accepted is not None and accepted['step'] != job['step']:
            accepted = None
    else:
        accepted = next((row for row in events if row['kind'] == 'final_a_return'
            and (row['seq'] == marker.get('seq') if isinstance(marker,dict) else row['step'] == job['step'])), None)
    if marker is None:
        if accepted is not None and accepted['step'] == job['step']:
            raise ValueError('Final A return reference is missing from its original intent')
        return None
    reentry = intent.get('continuation_kind') == 'reentry'
    format_retry = (intent.get('continuation_kind') == 'automatic_recovery'
        and (intent.get('automatic_recovery') or {}).get('format_fallback') is True)
    repair = intent.get('continuation_kind') == 'repair' and isinstance(intent.get('output_repair'),dict) and 'work_repair' not in intent
    expected_anchor = dict(accepted['anchor'], final_return=marker) if accepted is not None else None
    if reentry and accepted is not None:
        # An administrative reattachment completes this same final return.
        # Its historical chain and cumulative consumption are checked below.
        expected_anchor.update({key:intent['budget_slice'].get(key) for key in
            ('boot_id','advance_seconds','previous_tokens','previous_seconds')})
    if format_retry and accepted is not None:
        from . import continuation, execution
        number=intent.get('attempt')
        if type(number) is not int or number < 1:
            raise ValueError('Final A format fallback changed its exact attempt')
        first=execution.origin(root,job['step'])
        prior_paths=[first,*(first/'continuations'/f'{previous:03d}' for previous in range(1,number))]
        if (intent.get('root_intent_ref')!=contracts.record_ref(contracts.regular(first/'native-intent.json'))
                or intent.get('previous')!=execution.proof(prior_paths[-1])):
            raise ValueError('Final A format fallback changed its exact source')
        continuation.validate_automatic(root,job['step'],intent,prior_record=prior_paths[-1],
            prior_intents=[contracts.read(contracts.regular(path/'native-intent.json')) for path in prior_paths])
        expected_anchor.update({key:intent['budget_slice'].get(key) for key in
            ('boot_id','advance_seconds','previous_tokens','previous_seconds')})
    if repair and accepted is not None:
        number=intent.get('attempt')
        limits=policy.get('resume_policy') or {}
        if (type(number) is not int or number < 1
                or not limits.get('output_repair',False)):
            raise ValueError('Final A return has no bounded output repair authorization')
        from . import continuation, execution
        first=execution.origin(root,job['step'])
        prior=first if number==1 else first/'continuations'/f'{number-1:03d}'
        prior_intents=[contracts.read(contracts.regular(first/'continuations'/f'{previous:03d}'/'native-intent.json'))
            for previous in range(1,number)]
        repairs = 1 + sum(item.get('continuation_kind', 'repair') not in {'fragment','reentry'}
            and not (item.get('continuation_kind')=='automatic_recovery'
                and (item.get('automatic_recovery') or {}).get('format_fallback') is True) for item in prior_intents)
        if repairs > limits.get('max_attempts',0):
            raise ValueError('Final A return has no bounded output repair authorization')
        if (intent.get('root_intent_ref')!=contracts.record_ref(contracts.regular(first/'native-intent.json'))
                or intent.get('previous')!=execution.proof(prior)):
            raise ValueError('Final A return output repair changed its exact source')
        continuation.validate_repair(root,job['step'],intent,prior_record=prior,verify_task=False)
        # Validate cumulative consumption in _node_duration; retain the original
        # ceilings, task and final-return marker while advancing this call's clock.
        expected_anchor.update({key:intent['budget_slice'].get(key) for key in
            ('boot_id','advance_seconds','previous_tokens','previous_seconds')})
    if (accepted is None or ('attempt' in intent and not (repair or reentry or format_retry))
            or intent.get('continuation_kind') is not None and not (repair or reentry or format_retry) or intent.get('step') != job['step']
            or job['role'] != 'A' or job.get('formal_review') is not None
            or intent.get('task_ref') != accepted['anchor']['task_ref']
            or accepted['step'] != job['step']
            or not isinstance(marker, dict) or set(marker) != {'seq', 'record'}
            or type(marker['seq']) is not int or marker['seq'] != accepted['seq']
            or marker['record'] != contracts.record_ref(Path(root) / 'control-ledger' / f'{accepted["seq"]:06d}.json')
            or intent['budget_slice'] != expected_anchor):
        raise ValueError('Final A return reference or original attempt changed')
    return accepted


def final_a_barrier(root, *, before_step=None, events=None):
    """Only the active FINAL cycle bars normal science."""
    if events is None:
        events,_ = _events(root)
    if isinstance(events, accepted_budget.AcceptedEvents):
        row = events.state['final_return']
        return row if row is not None and (before_step is None or row['step'] < before_step) else None
    active = _active_final(events)
    return next((row for row in reversed(events) if row['kind'] == 'final_a_return'
        and (active is not None and row['step'] == active['first_step'])
        and (before_step is None or row['step'] < before_step)), None)


def _accept_final_a(root, job, anchor, limits, *, final_report=None):
    settings = contracts.settings(root, job)
    _final_a_policy(settings)
    with _ledger_writer(root):
        events, parent = _events(root)
        active = _active_final(events)
        accepted = events.state['final_return'] if active is not None else None
        if accepted is not None:
            if accepted['step'] != job['step'] or accepted['anchor']['task_ref'] != anchor['task_ref']:
                raise NodeBudgetUnavailable('final_a_return_consumed')
        else:
            # Never carry a pre-lock admission sample into durable acceptance.
            stamp = clock_stamp()
            limits = _snapshot(root, contracts.settings(root), events, sample_stamp=stamp)
            anchor = dict(anchor, boot_id=stamp['boot_id'], advance_seconds=limits['advance_seconds'],
                          shared_limits={key: limits[key] for key in ('max_tokens', 'max_seconds')})
            from . import native
            meter = native.usage(root, budget_events=events)
            selection = dict(max_steps=limits['max_steps'], used_steps=used_steps(root, events=events),
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
        marker = dict(seq=accepted['seq'], record=contracts.record_ref(Path(root)/'control-ledger'/f'{accepted["seq"]:06d}.json'))
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
            shared_tokens_over_ceiling=None if ceiling is None else max(0, accounted-ceiling),
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
    base = contracts.settings(root)
    head = accepted_budget.load(root,base=base)
    policy = contracts.settings_for_job(root,job,base=base,head=head)
    accepted = _final_a_return_from_facts(root,job,intent,policy=policy,
        events=_events_from_state(head)[0])
    if accepted is not None and meter is None:
        record = contracts.host_record(contracts.workspace(root, job['step']))
        if intent.get('attempt') is not None:record = record/'continuations'/f'{intent["attempt"]:03d}'
        if (record / 'usage.json').exists():
            meter = contracts.read(contracts.regular(record / 'usage.json'))
    return _validate_final_a_receipt_from_facts(intent,outcome,
        policy=policy,meter=meter,accepted=accepted)


def _validate_final_a_receipt_from_facts(intent, outcome, *, policy, meter, accepted):
    """Check FINAL receipt gates using the owner's exact terminal facts."""
    if policy is None:
        raise ValueError('Final A completion requires its produced policy')
    expected = final_a_receipt(accepted, intent, meter, outcome['usage']) if accepted is not None else None
    if outcome.get('final_a_completion') != expected:
        raise ValueError('Final A completion or overrun receipt changed')
    if accepted is not None and outcome.get('returncode') == 0 and not outcome.get('stop_reason'):
        from .native_usage import tokens
        observation = outcome.get('metering') or {}
        if (not native_completion_known(outcome, policy)
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
    if (accepted is None or not isinstance(marker, dict) or set(marker) != {'seq', 'record'}
            or type(marker['seq']) is not int or marker['seq'] != accepted['seq']
            or marker['record'] != contracts.record_ref(Path(root) / 'control-ledger' / f'{accepted["seq"]:06d}.json')
            or _active_final(previous) is None or _active_final(previous)['first_step'] != accepted['step']
            or policy is None
            or event['max_seconds'] != policy['submit_seconds' if event['action_kind'] == 'submit' else 'confirm_seconds']
            or event['decision'].get('step') != accepted['step']
            or event['binding'].get('decision') != event['decision']
            or event['binding'].get('operation_id') != event['operation_id']
            or event['action_kind'] != ('submit' if not actions else 'confirm')):
        raise ValueError('Final delivery exceeds its exact bounded operation')
    # Authorization replay checks the immutable declared object references,
    # never execution results, scientific qualification or current admission.
    decision = event['decision']
    if (set(decision) != {'step','job_ref','raw_ref','seal_ref'}
            or decision['job_ref'] != accepted['anchor']['task_ref']
            or any(not isinstance(decision[key], dict) or 'path' not in decision[key]
                for key in ('job_ref','raw_ref','seal_ref'))
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
    if (decision.get('job_ref') != accepted['anchor']['task_ref'] or binding.get('decision') != decision):
        raise ValueError('Final delivery changed its declared object scope')
    return intent['budget_slice']['final_return']


def require_final_a_delivery_eligibility(root, decision, binding):
    """Verify the sealed choice before accepting a new host delivery action."""
    marker = final_a_delivery(root,decision,binding)
    if marker is None:return None
    step = decision['step']
    job = contracts.read(contracts.regular(Path(root)/'jobs'/f'{step:03d}.json'))
    from . import handoff_state
    result = handoff_state.read_sealed(root,step)
    from . import delivery
    if binding != delivery.binding(root,job,result):
        raise ValueError('Final delivery must retain its exact original binding')
    return marker


def _direct_delivery_begin(events):
    if isinstance(events, accepted_budget.AcceptedEvents):
        return events.state['closeout']
    return next((row for row in reversed(events) if row['kind'] == 'closeout_begin'), None)


def direct_delivery_scope(begin, decision, binding):
    """Consume the ordinary A's accepted closeout scope, without new science."""
    return (begin is not None and begin['mode'] == 'model'
        and begin.get('authorization_step') == begin['source_cutoff'] == decision.get('step')
        and type(decision.get('step')) is int and decision['step'] > 0
        and set(decision) == {'step', 'job_ref', 'raw_ref', 'seal_ref'}
        and all(isinstance(decision[key], dict) and 'path' in decision[key]
                for key in ('job_ref', 'raw_ref', 'seal_ref'))
        and binding.get('decision') == decision
        and isinstance(binding.get('operation_id'), str) and bool(binding['operation_id']))


def _direct_delivery_action_facts(events, begin):
    """Produce bounded accepted action status, or consume explicit audit values."""
    actions = [row for row in events if row['kind'] == 'action_intent'
        and row['decision'].get('step') == (begin or {}).get('authorization_step')]
    confirms = [row for row in actions if row['action_kind'] == 'confirm']
    if isinstance(events, accepted_budget.AcceptedEvents):
        statuses = [events.state.row('action/'+row['action_id']) for row in confirms]
        confirmed = any(row is not None and row['outcome_ref'] is not None and row['status'] == 'confirmed'
            for row in statuses)
    else:
        confirm_ids = {row['action_id'] for row in confirms}
        confirmed = any(row['kind'] == 'action_outcome' and row['action_id'] in confirm_ids
            and row['result']['status'] == 'confirmed' for row in events)
    return actions, confirmed


def _validate_direct_delivery_action(event, *, begin, actions, confirmed, settings):
    """Replay only supplied accepted choice, status and frozen host windows."""
    decision, binding = event['decision'], event['binding']
    policy = settings.get('delivery_policy')
    if (not direct_delivery_scope(begin, decision, binding) or policy is None or policy['adapter'] is None
            or event['operation_id'] != binding['operation_id']
            or event['max_seconds'] != policy['submit_seconds' if event['action_kind'] == 'submit' else 'confirm_seconds']
            or len(actions) >= 2
            or confirmed
            or event['action_kind'] != ('submit' if not actions else 'confirm')
            or actions and any(event[key] != actions[0][key] for key in ('operation_id', 'decision', 'binding'))):
        raise ValueError('Direct delivery exceeds its exact bounded operation')


def _attempt_consumption(root, job, paths, *, automatic_recovery=None, automatic_successor=None,
        operator_resume=None, operator_successor=None, reentry_successor=None):
    """Derive cumulative consumption from exact completed native facts."""
    policy=contracts.settings(root,job)
    intents=[contracts.read(contracts.regular(path/'native-intent.json')) for path in paths]
    receipts=[]
    for number,(path,intent) in enumerate(zip(paths,intents)):
        successor=intents[number+1] if number+1<len(intents) else reentry_successor or {}
        receipts.append(_attempt_receipt(root,job,path,intent=intent,
            interruption_ref=successor.get('interruption') if successor.get('continuation_kind')=='reentry' else None))
    return _attempt_consumption_from_facts(root,job,receipts,policy=policy,
        limits=node_limits(root,job,settings=policy),task_ref=contracts.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),automatic_recovery=automatic_recovery,
        automatic_successor=automatic_successor,operator_resume=operator_resume,operator_successor=operator_successor)


def _attempt_receipt(root,job,path,*,intent,interruption_ref=None):
    """Load a required predecessor's receipt, retaining missing billing."""
    if interruption_ref is not None and interruption_ref != dict(path=str(path/'interruption.json')):
        raise ValueError('Interrupted receipt changed its exact accepted successor')
    interrupted=interruption_ref is not None or not (path/'native-outcome.json').exists()
    if interrupted:
        from . import continuation
        saved=continuation.interruption(root,job,path)
        original=dict(budget_slice=saved['budget_slice'])
    else:original=contracts.read(contracts.regular(path/'native-outcome.json'))
    meter=contracts.read(contracts.regular(path/'usage.json')) if (path/'usage.json').exists() else None
    if interrupted and meter is None:
        meter=saved.get('retained_meter')
    return path,intent,original,meter,interrupted


def _attempt_consumption_from_facts(root,job,receipts,*,policy,limits,task_ref,
        automatic_recovery=None,automatic_successor=None,operator_resume=None,operator_successor=None):
    """Apply the original accounting predicates to the owner's read receipts."""
    from . import native
    tokens, seconds = 0, 0.
    for number,(path,intent,original,meter,interrupted) in enumerate(receipts):
        anchor = intent.get('budget_slice', {})
        from . import native_recovery
        if (meter is None and not interrupted and not native_recovery.never_started(original)
                and not native_completion_known(dict(original,stop_reason=None),policy)):
            from . import continuation
            successor = receipts[number+1][1] if number+1<len(receipts) else automatic_successor or operator_successor or {}
            automatic = ((automatic_recovery is not None and number == len(receipts) - 1)
                         or successor.get('continuation_kind') == 'automatic_recovery')
            from . import native_recovery
            negotiation = ((automatic_recovery or {}).get('format_fallback') is True and number == len(receipts)-1
                or (successor.get('automatic_recovery') or {}).get('format_fallback') is True)
            eligible = (continuation._format_source_from_facts(path,policy=policy,intent=intent,outcome=original) is not None if negotiation else
                automatic and native_recovery.recoverable_source(
                    continuation._automatic_source_from_facts(path,policy=policy,intent=intent,outcome=original)))
            explicit = operator_resume if number == len(receipts)-1 else None
            if successor.get('continuation_kind') == 'operator_resume':
                explicit = successor.get('operator_resume')
            operator = explicit is not None and continuation.operator_resume_source(path,intent,original,explicit)
            if not operator and (not automatic or not eligible):
                raise ValueError('Missing billing has no known original completion')
        # An absent meter contributes no reported tokens to the lower bound;
        # retain its absence and unknown coverage, never publish a zero meter.
        reported = meter or dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
        report = original.get('budget_slice', {})
        if (meter is not None and native._usage_event(dict(type='turn.completed', usage=meter)) != meter
                or intent.get('task_ref') != task_ref
                or anchor.get('limits') != limits or anchor.get('role') != job['role']
                or anchor.get('previous_tokens', 0) != tokens or anchor.get('previous_seconds', 0.) != seconds
                or bool(report.get('usage_observed')) != (meter is not None) or report.get('limits') != limits
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
    if (not isinstance(anchor, dict) or anchor.get('task_ref') != contracts.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
            or anchor.get('role') != job['role'] or anchor.get('limits') != node_limits(root, job)
            or final is None and anchor.get('shared_limits') != {key: limits[key] for key in ('max_tokens', 'max_seconds')}
            or type(anchor.get('advance_seconds')) not in (int, float)
            or not math.isfinite(anchor['advance_seconds']) or anchor['advance_seconds'] < 0):
        raise ValueError('Accepted node budget identity changed')
    prior = anchor.get('previous_seconds', 0.)
    prior_tokens = anchor.get('previous_tokens', 0)
    if type(prior_tokens) is not int or prior_tokens < 0:
        raise ValueError('Invalid original node token consumption')
    if intent.get('attempt') is not None:
        from . import execution
        number = intent['attempt']
        paths = execution.records(root, job['step'])
        if type(number) is not int or not 0 < number < len(paths):
            raise ValueError('Continuation has no exact original execution')
        accepted = contracts.read(contracts.regular(paths[number] / 'native-intent.json'))
        if accepted != intent:
            raise ValueError('Continuation budget lost its exact accepted intent')
        # The current accepted attempt is outside the prior-consumption slice.
        # Retain that verified boundary so its failed source's unknown billing
        # remains a lower bound during runner admission/reporting as at accept.
        original = _attempt_consumption(root, job, paths[:number],
            automatic_successor=accepted if accepted.get('continuation_kind') == 'automatic_recovery' else None,
            operator_successor=accepted if accepted.get('continuation_kind') == 'operator_resume' else None,
            reentry_successor=accepted if accepted.get('continuation_kind')=='reentry' else None)
        if prior != original[1] or prior_tokens != original[0]:
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
    # This observation lives for this read-only admission only. The next
    # boundary (and any writer after acquiring its lock) reads fresh facts.
    from . import configuration_updates
    original = contracts.settings(root)
    node_mode(root, settings=original)
    settings = configuration_updates.for_new_job(root, job['step']) if 'configuration_revision' not in job else contracts.settings(root, job)
    events, _ = _events(root)
    policy = node_limits(root, job, settings=settings)
    barrier = final_a_barrier(root, events=events)
    if not normal_resume_check and barrier is not None and barrier['step'] != job['step']:
        return 'final_a_return_consumed'
    begin = events.state['closeout']
    if not normal_resume_check and begin is not None and begin['mode'] == 'final' and (job.get('role') != 'A' or job['step'] != begin['first_step']):
        return 'final_a_return_consumed'
    if job.get('final_only') and (begin is None or begin['mode'] != 'final' or job['step'] != begin['first_step']):
        return 'final_cycle_closed'
    from . import native
    meter = native.usage(root, budget_events=events)
    if policy is None:
        return None
    limits = _snapshot(root, original, events)
    accepted = final_a_return(root, job, intent, events=events) if intent is not None else None
    report = validate_final_report_inputs(root, job, events=events) if job.get('final_report') is not None else None
    final_admission = final_a_admission(root, job, events=events)
    if final_admission is not None:
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
    if intent is None and final_admission is not None:
        return None
    if settings.get('resume_policy') is not None:
        additional = 0 if (root / 'jobs' / f'{job["step"]:03d}.json').exists() else 1
        if used_steps(root, events=events) + additional > limits['max_steps']:
            return 'step_limit'
    if intent is not None:
        _node_duration(root, job, intent, limits)
    if budget_limits.reached(limits['seconds_remaining']):
        return 'node_budget_unavailable_seconds'
    if final_report is None and budget_limits.reached(budget_limits.remaining(
            limits['max_tokens'], meter.get('accounted_tokens', meter['observed_tokens']))):
        return 'node_budget_unavailable_tokens'
    if job.get('purpose') == 'closeout':
        begin = events.state['closeout']
        if begin is None or begin['mode'] not in {'model', 'final'} or begin['source_cutoff'] != job['source_cutoff']:
            raise ValueError('Closeout node has no exact model boundary')
        floor = closeout_floor(settings, preparation_seconds=begin['preparation_seconds'])
        additional = 0 if (root / 'jobs' / f'{job["step"]:03d}.json').exists() else 1
        seconds_unavailable = budget_limits.reached(limits['seconds_remaining'], floor['seconds'])
        tokens_unavailable = budget_limits.reached(budget_limits.remaining(
            limits['max_tokens'], meter.get('accounted_tokens', meter['observed_tokens'])), floor['tokens'])
        if (seconds_unavailable or tokens_unavailable
                or limits['max_steps'] - used_steps(root, events=events) < additional + floor['steps']):
            return 'closeout_budget_unavailable'
        return None
    return exploration_admission(root, job, ignore_step=ignore_step, intent=intent,
        settings=settings, limits=limits, meter=meter, events=events)


def accept_node(root, job, *, final_report=None):
    from .protocol import require_current
    require_current(root,job)
    policy = node_limits(root, job)
    if policy is None:
        return None
    accepted = final_a_barrier(root)
    if accepted is not None and accepted['step'] == job['step']:
        if accepted['anchor']['task_ref'] != contracts.record_ref(Path(root) / 'jobs' / f'{job["step"]:03d}.json'):
            raise ValueError('Final A return cannot bind a replacement task')
        marker = dict(seq=accepted['seq'], record=contracts.record_ref(Path(root)/'control-ledger'/f'{accepted["seq"]:06d}.json'))
        return dict(accepted['anchor'], final_return=marker)
    if job.get('final_report') is not None and final_report is None and final_a_admission(root, job) is None:
        raise NodeBudgetUnavailable('final_a_report_requires_control_admission')
    reason = node_admission(root, job, final_report=final_report)
    if reason:
        raise NodeBudgetUnavailable(reason)
    limits, stamp = _observe_node_clock(root)
    anchor = dict(task_ref=contracts.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'), role=job['role'],
                boot_id=stamp['boot_id'], advance_seconds=limits['advance_seconds'], limits=policy,
                shared_limits={key: limits[key] for key in ('max_tokens', 'max_seconds')})
    return _accept_final_a(root, job, anchor, limits, final_report=final_report)


def _observe_node_clock(root):
    """Persist acceptance's observation using the existing clock event kind."""
    with _ledger_writer(root):
        settings = contracts.settings(root)
        events, parent = _events(root)
        phase = events.state['clock']['phase']
        if settings.get('time_budget_basis', 'elapsed') != 'active':
            stamp = clock_stamp()
            return _snapshot(root, settings, events, sample_stamp=stamp), stamp
        event = _append(root, settings, events, parent, kind='clock', phase=phase,
            reason='accepted_native_budget_observation')
        updated, _ = _events(root)
        return _snapshot(root, settings, updated, sample_stamp=event), event


def stop_node(root, job, intent, limits, meter):
    policy = node_limits(root, job)
    if (intent.get('budget_slice') or {}).get('final_return') is not None:
        final_a_return(root, job, intent)
        duration = _node_duration(root, job, intent, limits)
        return 'node_slice_time_limit' if duration >= policy['max_seconds'] else None
    if policy is None:
        return None
    duration = _node_duration(root, job, intent, limits)
    node_mode(root)
    return None


def node_report(root, job, intent, meter):
    duration = _node_duration(root, job, intent, snapshot(root))
    return dict(role=job['role'], advance_seconds=duration, input_tokens=meter['input_tokens'],
                cached_input_tokens=meter['cached_input_tokens'], output_tokens=meter['output_tokens'],
                observed_tokens=meter['input_tokens'] + meter['output_tokens'] + intent['budget_slice'].get('previous_tokens', 0), dispatches=1 + intent.get('attempt', 0),
                limits=intent['budget_slice']['limits'])


def accept_continuation(root, job, paths, *, output_repair=None, automatic_recovery=None, inventory=None,
        operator_resume=None, interruption_ref=None):
    from .protocol import require_current
    require_current(root,job)
    final=final_a_barrier(root)
    format_retry = automatic_recovery is not None and automatic_recovery.get('format_fallback') is True
    if final is not None:
        from . import continuation
        if (final['step']!=job['step'] or not format_retry and
                (output_repair is None or output_repair!=continuation.output_request(root,job['step'],paths[-1],inventory=inventory))):
            raise NodeBudgetUnavailable('final_a_return_consumed')
    from . import native
    if automatic_recovery is not None:
        from . import continuation
        current = continuation.automatic_decision(root, job['step'], inventory=inventory)
        if current != automatic_recovery or current.get('action') not in {'continue', 'retry'}:
            raise ValueError('Automatic recovery budget admission lost its exact source decision')
    if operator_resume is not None:
        from . import continuation
        source=paths[-1]
        original=contracts.read(contracts.regular(source/'native-intent.json'))
        outcome=contracts.read(contracts.regular(source/'native-outcome.json'))
        if not continuation.operator_resume_source(source,original,outcome,operator_resume):
            raise ValueError('Operator continuation budget lost its exact interrupted source')
    reentry=None
    if interruption_ref is not None:
        from . import continuation
        if operator_resume is not None or automatic_recovery is not None or interruption_ref != dict(path=str(paths[-1]/'interruption.json')):
            raise ValueError('Reentry budget admission changed its exact interrupted source')
        continuation.interruption(root,job,paths[-1])
        reentry=dict(continuation_kind='reentry',interruption=interruption_ref)
    tokens, seconds = _attempt_consumption(root, job, paths, automatic_recovery=automatic_recovery,
        operator_resume=operator_resume,reentry_successor=reentry)
    policy = node_limits(root, job)
    limits, stamp = _observe_node_clock(root)
    meter = native.usage(root)
    if final is not None:
        original=contracts.read(contracts.regular(paths[0]/'native-intent.json'))
        final_a_return(root,job,original)
        if seconds >= policy['max_seconds']:
            raise NodeBudgetUnavailable('worker_resume_node_exhausted')
        return dict(original['budget_slice'],boot_id=stamp['boot_id'],advance_seconds=limits['advance_seconds'],
            previous_tokens=tokens,previous_seconds=seconds)
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise NodeBudgetUnavailable('worker_resume_node_exhausted')
    settings = contracts.settings(root, job)
    node_mode(root)
    reserve = (closeout_floor(settings, preparation_seconds=closeout_state(root)['begin']['preparation_seconds'])
        if job.get('purpose') == 'closeout' else delivery_reserve(settings)) or dict(tokens=0, seconds=0, steps=0)
    unknown = _usage_unknown(meter, (job['step'],) if automatic_recovery is not None or operator_resume is not None or interruption_ref is not None else ())
    if (unknown
            or budget_limits.reached(budget_limits.remaining(
                limits['max_tokens'], meter.get('accounted_tokens', meter['observed_tokens'])), reserve['tokens'])
            or budget_limits.reached(limits['seconds_remaining'], reserve['seconds'])
            or step_limit(root) - used_steps(root) < 1 + reserve['steps']):
        raise NodeBudgetUnavailable('worker_resume_budget_unavailable')
    return dict(task_ref=contracts.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'), role=job['role'],
                boot_id=stamp['boot_id'], advance_seconds=limits['advance_seconds'], limits=policy,
                shared_limits={key: limits[key] for key in ('max_tokens', 'max_seconds')},
                previous_tokens=tokens, previous_seconds=seconds)


def closeout_state(root, *, events=None):
    """Only immutable control facts; no second balance or mutable mode file."""
    if events is None:
        events, _ = _events(root)
    if isinstance(events, accepted_budget.AcceptedEvents):
        state = events.state
        actions = []
        refs = [state['action_first'], state['action_last']]
        for ref in refs:
            if ref is not None:
                intent = events.event(ref)
                row = accepted_budget.action(root, intent['action_id'], head=state)
                if row not in actions:
                    actions.append(row)
        begin = events.event(state['closeout']['ref']) if state['closeout'] is not None else None
        resumed = events.event(state['resume']['ref']) if state['resume'] is not None else None
        return dict(begin=begin, resume=resumed, actions=actions,
            action_count=state['final_action_count'], unresolved_operation_count=state['unresolved_actions'],
            unresolved_operations=([state['first_unresolved_operation']]
                if state['first_unresolved_operation'] is not None else []),
            unresolved_operations_omitted_count=max(0, state['unresolved_actions']
                - int(state['first_unresolved_operation'] is not None)))
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
    # FINAL owns its finite completion/delivery windows independently of shared
    # consumption; ordinary work does not prepay a second author allowance.
    _final_a_policy(settings)
    return dict(tokens=0, seconds=0., steps=0)


def closeout_floor(settings, *, preparation_seconds=0.):
    """FINAL reserves no shared balance for its independent finite windows."""
    _final_a_policy(settings)
    return dict(tokens=0, seconds=0., steps=0)


def used_steps(root, *, events=None):
    state = accepted_budget.read(root) if events is None else events.state
    return ((state['prepared_count'] if state['is_fork'] else state['max_local_step'])
        + state['continuation_count'] + state['action_count'] + state['audit_count'])


def audit_used_steps(root, *, events=None):
    """Explicit audit of historical prepared originals, outside admission."""
    from . import execution
    from . import fork_runtime
    prepared = fork_runtime.local_steps(root)
    continuations = sum(1 for step in prepared for record,intent in execution.accepted_intents(root,step)
        if record != execution.origin(root,step) and intent.get('continuation_kind') != 'reentry')
    from . import candidate_audit
    from . import audit_recovery
    audits = sum(1 for _ in candidate_audit.intents(root)) + sum(1 for _ in audit_recovery.intents(root))
    return fork_runtime.node_count(root, prepared) + continuations + len(closeout_state(root, events=events)['actions']) + audits


def _selection_accounts_job(root, job, selected_used):
    from . import fork_runtime
    if not fork_runtime.is_fork(root):
        return job['step'] <= selected_used
    # The FINAL original was prepared before acceptance. Its logical label is
    # unrelated to consumption; later events may add more local originals.
    prepared = [number for number in fork_runtime.local_steps(root) if number <= job['step']]
    return job['step'] in prepared and len(prepared) <= selected_used


def exploration_admission(root, job, *, ignore_step=None, intent=None,
                          settings=None, limits=None, meter=None, events=None):
    from . import configuration_updates
    if settings is None:
        settings = configuration_updates.for_new_job(root, job['step']) if 'configuration_revision' not in job else contracts.settings(root, job)
    reserve = delivery_reserve(settings)
    if reserve is None or job.get('purpose') == 'closeout':
        return None
    from . import native
    if limits is None:
        limits = snapshot(root)
    if meter is None:
        meter = native.usage(root)
    node = node_limits(root, job, settings=settings)
    if _usage_unknown(meter, () if ignore_step is None else (ignore_step,)):
        return 'usage_unknown'
    node_mode(root, settings=settings)
    if budget_limits.reached(budget_limits.remaining(
            limits['max_tokens'], meter.get('accounted_tokens', meter['observed_tokens'])), reserve['tokens']):
        return 'closeout_reserve_tokens'
    if intent is not None and node is not None:
        _node_duration(root, job, intent, limits)
    if budget_limits.reached(limits['seconds_remaining'], reserve['seconds']):
        return 'closeout_reserve_seconds'
    additional = 0 if (root / 'jobs' / f'{job["step"]:03d}.json').exists() else 1
    if limits['max_steps'] - used_steps(root, events=events) < additional + reserve['steps']:
        return 'closeout_reserve_steps'
    return None


def begin_closeout(root, cutoff, first_step, reason, *, export_only=False, delivery_sources=None, preparation_seconds=0., authorization_step=None, final_only=False, stopped_original=None, resume_cursor=None):
    from .protocol import require_current
    require_current(root)
    settings = contracts.settings(root)
    with _ledger_writer(root):
        events, parent = _events(root)
        existing = closeout_state(root, events=events)['begin']
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
    author = contracts.read(contracts.regular(Path(root) / 'jobs' / f'{decision["step"]:03d}.json'))
    settings = configuration_updates.for_job(root, author)
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
    if final_return is None:
        # Admission owns this exact author read. An ordinary strategy-v1 A
        # requests FINAL; only the protected final or closeout A can authorize
        # the first adapter effect. Missing markers retain historical scope.
        if (author.get('research_strategy_version') in (1,2,3) and author.get('role') == 'A'
                and delivery_review.scope(author) != 'delivery_review'
                and not author.get('formal_review') and not author.get('final_only') and not author.get('final_report')
                and author.get('purpose') not in {'closeout', 'formal_review', 'worker_consultation'}):
            raise NodeBudgetUnavailable('final_author_required')
    direct = direct_delivery_scope(begin, decision, binding)
    if begin.get('authorization_step') is not None and not direct:
        raise ValueError('Direct delivery changed its accepted scientific choice')
    barrier = final_a_barrier(root)
    if barrier is not None and final_return is None:
        raise NodeBudgetUnavailable('final_a_return_consumed')
    # The final exception covers shared billing, not unresolved execution:
    # the original-effect gate above and the sealed-choice gate still apply.
    if final_return is None and not direct and (_usage_unknown(meter)
            or budget_limits.reached(budget_limits.remaining(
                limits['max_tokens'], meter.get('accounted_tokens', meter['observed_tokens'])))):
        raise NodeBudgetUnavailable('closeout_budget_unavailable')
    if final_return is None and not direct and (limits['seconds_remaining'] is not None
            and limits['seconds_remaining'] < max_seconds or used_steps(root) >= step_limit(root)):
        raise NodeBudgetUnavailable('closeout_budget_unavailable')
    with _ledger_writer(root):
        events, parent = _events(root)
        action_id = f'action-{len(events) + 1:06d}'
        if final_return is not None:
            if events.state['final_confirmed']:
                raise NodeBudgetUnavailable('final_a_delivery_already_confirmed')
            actions = [row for row in events if row['kind'] == 'action_intent' and row.get('final_return') == final_return]
            if kind != ('submit' if not actions else 'confirm'):
                raise NodeBudgetUnavailable('final_a_delivery_reserve_exhausted')
            confirmed_ids = {row['action_id'] for row in actions if row['action_kind'] == 'confirm'}
            if any(row['kind'] == 'action_outcome' and row['action_id'] in confirmed_ids
                   and row['result']['status'] == 'confirmed' for row in events):
                raise NodeBudgetUnavailable('final_a_delivery_already_confirmed')
            if max_seconds != settings['delivery_policy']['submit_seconds' if kind == 'submit' else 'confirm_seconds']:
                raise ValueError('Final delivery requires its frozen per-call window')
            _validate_final_a_action(root, dict(final_return=final_return, action_kind=kind,
                operation_id=operation_id, decision=decision, binding=binding, max_seconds=max_seconds), events)
        elif direct:
            row = events.state.current_job
            tip = events.state['prefix']['tip']
            if (row is None or row['step'] != decision['step'] or row['role'] != 'A'
                    or row.get('purpose') is not None or row['status'] != 'sealed'
                    or row['job_ref'] != decision['job_ref'] or tip is None
                    or tip['step'] != decision['step'] or tip['seal_ref'] != decision['seal_ref']):
                raise ValueError('Direct delivery requires its exact sealed ordinary author')
            direct_begin = _direct_delivery_begin(events)
            actions, confirmed = _direct_delivery_action_facts(events, direct_begin)
            if confirmed:
                raise NodeBudgetUnavailable('direct_delivery_already_confirmed')
            if len(actions) >= 2 or kind != ('submit' if not actions else 'confirm'):
                raise NodeBudgetUnavailable('direct_delivery_reserve_exhausted')
            _validate_direct_delivery_action(dict(action_kind=kind, operation_id=operation_id,
                decision=decision, binding=binding, max_seconds=max_seconds), begin=direct_begin,
                actions=actions, confirmed=confirmed, settings=settings)
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
        events, parent = _events(root)
        return _append(root, settings, events, parent, kind='action_outcome', action_id=intent['action_id'], result=result)


def exploration_stop(root, job, limits):
    node_mode(root)
    return None


def _validate_action_result(intent, result):
    binding = intent['binding']
    if (set(result) - {'failure_phase','diagnostic'} != {'status', 'operation_id', 'candidate_id', 'artifact_ref', 'note', 'cleanup_confirmed'}
            or 'failure_phase' in result and (result['status'] not in {'failed', 'rejected', 'engineering_error', 'unclassified', 'unknown'} or result['failure_phase'] not in {'audit', 'promote', 'confirm'})
            or 'diagnostic' in result and (not isinstance(result['diagnostic'],str) or len(result['diagnostic'].encode())>4096)
            or result['status'] not in {'confirmed', 'failed', 'unknown', 'rejected', 'engineering_error', 'unclassified'}
            or type(result['cleanup_confirmed']) is not bool
            or not result['cleanup_confirmed'] and result['status'] != 'unknown'
            or result['operation_id'] != intent['operation_id']
            or result['candidate_id'] != binding['candidate_id']
            or not isinstance(result['note'], str) or len(result['note'].encode()) > 4096
            or result['artifact_ref'] is not None and result['artifact_ref'] != binding['source']['objects']['artifact']
            or result['status'] == 'confirmed' and result['artifact_ref'] != binding['source']['objects']['artifact']):
        raise ValueError('Delivery result differs from its exact accepted candidate and artifact')


def action_process(root, intent, phase, identity):
    from .protocol import require_current
    require_current(root)
    if phase not in {'audit', 'promote', 'confirm'} or identity['task_ref'] != intent['operation_id']:
        raise ValueError('Delivery process differs from its exact action phase')
    path = root / 'host-delivery/action-processes' / (intent['action_id'] + '-' + phase + '.json')
    if path.exists() or path.is_symlink():
        raise ValueError('Delivery action phase cannot launch twice')
    contracts.write(path, dict(action_id=intent['action_id'], operation_id=intent['operation_id'], phase=phase, identity=identity))


def _advice_duration(intent, limits):
    """Project time from the already-accepted intent; never repeat acceptance."""
    anchor=intent['budget_slice']
    duration=limits['advance_seconds']-anchor['advance_seconds']+anchor.get('previous_seconds',0.)
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:
        raise ValueError('Node consumption cannot move backwards')
    return duration


def write_node_hint(root, job, intent, limits, meter, *, facts, observed, author_limits=None, notice_channel=None,
        context_observation=None):
    """Optional replaceable advice; never read as a grant, meter or evidence."""
    from . import checkpoint
    review=publish_review(root,job,intent,limits,meter,facts=facts,observed=observed,
        context_observation=context_observation)
    settings=facts['settings'];shared=facts['shared'];mode=review['mode']
    direction_review=review['direction_review']
    fragment_enabled=checkpoint.enabled_for_policy(job,settings=settings)
    context_max=(settings.get('worker_fragment_policy') or {}).get('max_context_windows') if fragment_enabled else None
    targets=review['targets'];duration=review['duration']
    prior=intent.get('budget_slice',{}).get('previous_tokens',0)
    known=prior+meter['input_tokens']+meter['output_tokens']
    consumed=known  # Retain prior/current reported counters even when coverage is unknown.
    node_seconds=max(0.,targets['max_seconds']-duration) if targets else None
    author=intent.get('continuation_kind')=='author_closeout'
    final_return=facts['final_return']
    boundary='node_slice' if targets else 'shared_only'
    if author:
        # The caller already verified this exact permit before native launch.
        # A reused W job's original cap is not this author attempt's allowance.
        targets=author_limits
        boundary='author_permit' if targets is not None else 'unavailable'
        duration=(duration-intent['budget_slice'].get('previous_seconds',0.)) if duration is not None else None
        consumed=meter['input_tokens']+meter['output_tokens']
    closeout = job.get('purpose') == 'closeout'
    reserve = (closeout_floor(settings, preparation_seconds=facts['closeout_preparation_seconds'])
        if closeout else delivery_reserve(settings)) or dict(tokens=0,seconds=0,steps=0)
    if final_return is not None:
        reserve=final_return['reserve']
        boundary='final_a_return'
    remaining=budget_limits.remaining(limits['max_tokens'], shared.get('accounted_tokens', shared['observed_tokens']))
    shared_seconds=limits['seconds_remaining']
    ordinary=not (delivery_review.scope(job) is not None or author or final_return is not None or job.get('formal_review') or closeout
        or job.get('final_report') or job.get('final_only') or intent.get('output_repair') or intent.get('work_repair'))
    followup_tokens={}
    if ordinary:
        for role in ('W','A') if job['role']=='H' else ('A',) if job['role']=='W' else ():
            suggestion=node_limits(root,dict(role=role),settings=settings)
            if suggestion is not None:followup_tokens[role]=suggestion['max_observed_tokens']
    available_tokens=budget_limits.remaining(remaining, reserve['tokens'])
    available_seconds=budget_limits.remaining(limits['seconds_remaining'], reserve['seconds'])
    if author:
        if targets is None or duration is None:
            available_tokens=available_seconds=None
        else:
            available_seconds=budget_limits.minimum(available_seconds,max(0.,targets['max_seconds']-duration))
    state=('reached' if targets and ((consumed is not None and consumed>=targets['max_observed_tokens']) or duration is not None and duration>=targets['max_seconds'])
           else 'near' if targets and ((consumed is not None and consumed>=.8*targets['max_observed_tokens']) or duration is not None and duration>=.8*targets['max_seconds']) else 'within')
    hint=dict(scope='advice_only_not_authorization_or_scientific_evidence',updated_at_unix=time.time(),mode=mode,
        notification_channel=notice_channel or dict(status='unavailable',reason='not_preflighted'),
        target_state=state,targets=targets,active_seconds=duration,observed_tokens_lower_bound=consumed,
        metering='observed_lower_bound' if observed else 'inflight_unknown',
        input_accounting='input_includes_cached_input_once; observed_total=input+output',
        inflight_usage_unknown=True,reporting_delay_unmeasured=True,per_request_admission='unavailable',
        final_response_allowance='not_guaranteed',current_work_boundary=boundary,
        target_kind='unavailable' if targets is None else 'soft_target',
        unknown_usage=not observed or bool(shared.get('unknown_steps')),
        shared_remaining_basis='configured_ceiling_minus_reported_usage',
        shared_observed_tokens_remaining_upper_bound=remaining,shared_active_seconds_remaining=shared_seconds,
        reserve_state='reached' if budget_limits.reached(remaining,reserve['tokens'])
            or budget_limits.reached(limits['seconds_remaining'],reserve['seconds']) else 'within',
        reserve_scope='external_closeout_floor' if closeout else 'minimal_closeout_reserve',
        current_work_observed_tokens_remaining_upper_bound=available_tokens,
        current_work_active_seconds_available=available_seconds,
        instruction='Near/reached limits, targets or reserve: finish bounded actions and return your scientific JSON. Unknown coverage is not zero usage; reported consumption is a lower bound, and remaining upper bounds are not grants or request/billing caps. Shared ceilings are checked before the next node, without terminating this accepted node for a soft target. Original finite external-call and resource limits still apply.')
    if ordinary and job['role']=='W' and fragment_enabled:
        hint['instruction'] = ('Continue useful work within original permissions; retain progress/best/residuals. '
            'Token targets alone do not require A. W time target bounds handoff: finish/reconcile original in-flight work; '
            'return a checkpoint for a fresh W to retain its focus and unfinished work. Direction supervision is separate, when the host ledger/control says due. '
            'Time is no stagnation/overfitting verdict; W adaptation/low scores alone do not end research. '
            'Never replay unknown calls. Near/reached shared budgets/reserves: finish bounded actions and return scientific JSON. '
            'Unknown usage!=0; remaining upper bounds are advice, not grants or billing/request caps. '
            'Notice: next local tool boundary only; running model requests and hosted web cannot be interrupted. '
            'Original finite external-call and resource limits still apply.')
        if context_max is not None:
            from . import native_usage
            hint['worker_context_windows'] = dict(maximum=context_max, advice_ratio=(.90 if settings['worker_fragment_policy'].get('context_handoff_version')==2 else .85),
                **(dict(context_handoff_version=2,handoff_ratio=.70,complete_windows=context_max)
                    if settings['worker_fragment_policy'].get('context_handoff_version')==2 else {}),
                **native_usage.context_windows((context_observation or {}).get('context_windows')))
            current_context=native_usage.context((context_observation or {}).get('context'))
            hint['worker_context'] = {key:current_context[key] for key in ('status','total_tokens',
                'model_context_window','ratio','reported_at','observed_at','current_window') if key in current_context}
            hint['instruction'] = ('Continue useful work within original permissions; preserve progress/best/residuals. '
                f'Handoff after exceeding {context_max} observed context windows or losing required event coverage. '
                'A current native report at 85% of the final allowed window advises preparing a safe checkpoint; '
                'missing fullness telemetry adds no stop. Reconcile originals; retain focus/unfinished work. '
                'Direction supervision is separate, when the host ledger/control says due. '
                'Node time/token targets do not set this cadence. Unknown calls never replay. '
                'Shared budgets and original finite experiment/resource limits hold. '
                'Soft next-tool/checkpoint boundary: native compaction/requests may overshoot; unknown usage is not zero.')
    if ordinary and job['role']=='W' and context_max is not None and settings['worker_fragment_policy'].get('context_handoff_version')==2:
        hint['instruction']=(f'Context policy: allow {context_max} complete native window(s), then use the next current window until occupancy exceeds 70%. '
            'At 90% within allowed complete windows, prepare a safe checkpoint; automatic compaction alone does not require handoff. '
            'Only a valid current-window occupancy above 70% after those complete windows requests a fresh W. '
            'Missing occupancy remains unknown. Reconcile originals; never replay. Shared budgets, independent reflection and original permissions hold.')
    if followup_tokens:
        hint['instruction'] += (' Allow for at least the suggested follow-up token targets '
            +', '.join(f'{role}={tokens}' for role,tokens in followup_tokens.items())
            +'; these are advice, not reserved tokens or authorization.')
    if ordinary and job['role']=='A':
        if contracts.guidance.advisory_assessment(job):
            hint['instruction'] += (' Return fallible advice with continue_w and decision_request=null; no material issue adds no constraints. '
                'W judges the concern and chooses its response. Shared exhaustion is handled by control admission and separate Final A; ordinary advice grants no new work or delivery.')
        elif job.get('research_strategy_version') in (1,2,3):
            hint['instruction'] += (' Finish the current assessment by evidence/shared balance; node targets do not end research. '
                'Existing productive work may continue with continue_w and decision_request=null. '
                'At exhausted shared budgets, summarize existing results and gaps and use finish/deliver to request the protected final A. '
                'Ordinary A does not authorize adapter submission; no new H/W/E is authorized at exhaustion.')
        else:
            hint['instruction'] += (' Node targets/shared risk advise finishing the current assessment: retain unmet goals '
                'and choose normal next_action by evidence/shared balance (continue_w/revise_h/extract allowed); '
                'node targets do not end research. If shared tokens remaining<=0 or shared time remaining<=0, '
                'ordinary A must conclude now: summarize existing results and deliver an eligible existing candidate '
                'or finish with gaps, with no new H/W/E or extra FINAL; permissions hold.')
    if author:
        hint['instruction'] = ('The author token target is soft: finish this original scientific response at the target. '
            'The verified author permit finite time limit remains enforced. No new experiment is authorized; all actual usage is retained.')
    if final_return is not None:
        hint.update(target_kind='final_a_token_target', final_response_allowance='original_final_a_token_overrun_authorized',
            current_work_observed_tokens_remaining_upper_bound=None,
            current_work_active_seconds_available=node_seconds,
            instruction='Complete this same final scientific return and explain completed work, unfinished work and any evidence gap. Token targets and reference ceilings do not terminate this original return; all actual usage is retained. The original finite call duration, resources and transport boundaries remain enforced; shared time and step exhaustion do not revoke this completion. No further scientific turn is authorized.')
    from . import budget_notice
    notices={}
    for dimension,used,target,shared_reached,shared_risk in (
            ('time',duration,targets['max_seconds'] if targets else None,budget_limits.reached(shared_seconds),
                shared_seconds is not None and limits['max_seconds'] is not None
                    and shared_seconds<.1*limits['max_seconds']),
            ('token',consumed,targets['max_observed_tokens'] if targets else None,budget_limits.reached(remaining),
                bool(followup_tokens) and budget_limits.reached(remaining,sum(followup_tokens.values())))):
        notice=budget_notice.closeout_notice(job,intent,dimension=dimension,
            target_reached=target is not None and used is not None and used>=target,
            target_near=target is not None and used is not None and used>=.8*target,
            shared_reached=shared_reached,shared_risk=shared_risk,
            shared_expired=dimension=='time' and budget_limits.reached(shared_seconds),
            shared_exhausted=budget_limits.reached(remaining) or budget_limits.reached(shared_seconds),
            followup_tokens=followup_tokens if dimension=='token' else None,
            author=author,final_return=final_return is not None)
        if notice is not None:notices[dimension]=notice
    progress_notice=budget_notice.worker_progress_notice(job,intent,active_seconds=duration,
        target_seconds=targets['max_seconds'] if targets else None,
        fragment_enabled=fragment_enabled,
        author=author,final_return=final_return is not None,
        max_context_windows=context_max,context_observation=context_observation,
        context_handoff_version=(settings.get('worker_fragment_policy') or {}).get('context_handoff_version'))
    if progress_notice is not None:
        notices['progress']=progress_notice
        hint['target_state']=('near' if progress_notice['text'] in {budget_notice.WORKER_CONTEXT_NEAR_TEXT,budget_notice.WORKER_CONTEXT_90_TEXT}
            and hint['target_state']!='reached' else 'reached')
    if direction_review is not None:
        hint['direction_review']=direction_review
        hint['target_state']='reached'
        notices['review']=budget_notice.worker_direction_notice(direction_review)
        hint['instruction']=notices['review']['text']
    if review.get('research_investment') is not None:
        hint['research_investment']=review['research_investment']
    if review.get('investment_notice'):
        notices['investment']=budget_notice.worker_investment_notice()
    if notices:hint['closeout_notices']=notices
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
    return Path(root)/'host-runtime/experiment-control'/f'{job["step"]:03d}'


def review_configuration(root,job):
    """Host-owned projection, shared by every implementation of this stage."""
    from .protocol import require_current
    require_current(root,job)
    from . import accepted_budget,execution,experiments
    from .protocol import stage_scope
    settings=contracts.settings(Path(root), job)
    if settings.get('managed_admission_version')!=experiments.ADMISSION_VERSION or job.get('managed_admission_version')!=experiments.ADMISSION_VERSION:
        raise experiments.UnsupportedExecution('Preserved review contract is read-only; prepare a new instance')
    node_mode(root,settings=settings)
    directory=_review_directory(root,job);directory.mkdir(parents=True,exist_ok=True)
    if directory.resolve()!=directory:raise ValueError('Host review directory changed')
    fd=os.open(directory/'accept.lock',os.O_RDONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o444);os.close(fd)
    record=execution.record(root,job['step'])
    intent=contracts.read(contracts.regular(record/'native-intent.json'))
    observed=(record/'usage.json').exists()
    meter=contracts.read(record/'usage.json') if observed else dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
    context_observation=(contracts.read(contracts.regular(record/'usage-observation.json'))
        if (record/'usage-observation.json').exists() else None)
    head=accepted_budget.read(root);stamp=clock_stamp()
    current=head['current_native'];live=None
    if current is not None:
        outcome_present=(record/'native-outcome.json').exists()
        metadata=contracts.native_outcome(record).get('metering') if outcome_present else context_observation
        live=dict(record=record.relative_to(root).as_posix(),meter=meter if observed else None,
            metering=metadata,outcome_present=outcome_present,interrupted=(record/'interruption.json').exists())
    shared=accepted_budget.usage_from_facts(head,job_rows=accepted_budget.load_usage_rows(head),current_native=live)
    facts=dict(settings=settings,head=head,shared=shared,stamp=stamp,
        task=contracts.record_ref(Path(root)/'jobs'/f'{job["step"]:03d}.json'),scope=stage_scope(root,job),
        native_attempt=dict(number=len(execution.records(root,job['step']))-1,execution_key=execution.identity(root,job['step'])))
    if job.get('research_strategy_version')==3 and job['role']=='W':
        from . import experiment_client,worker_research
        facts['research_investment']=worker_research.load_investment(dict(
            record_dir=str(experiment_client._record_directory_from_facts(root,job['step'],job)),
            scope=facts['scope']))
    limits=_snapshot(root,head.base,accepted_budget.AcceptedEvents(head),sample_stamp=stamp)
    publish_review(root,job,intent,limits,meter,facts=facts,observed=observed,
        context_observation=context_observation)
    return dict(version=experiments.ADMISSION_VERSION,mode='soft',directory=str(directory),
        stage_anchor=facts['task'])


def publish_review(root,job,intent,limits,meter,*,facts,observed,context_observation=None):
    """Derive replaceable facts from original anchors, never an additional budget."""
    from . import budget_notice,checkpoint,experiments,supervision
    root=Path(root);settings=facts['settings'];head=facts['head'];shared=facts['shared']
    if job.get('managed_admission_version')!=experiments.ADMISSION_VERSION or settings.get('managed_admission_version')!=experiments.ADMISSION_VERSION:
        raise experiments.UnsupportedExecution('Preserved review contract is read-only; prepare a new instance')
    mode=node_mode(root,settings=settings)
    directory=_review_directory(root,job);directory.mkdir(parents=True,exist_ok=True)
    if directory.resolve()!=directory:raise ValueError('Host review directory changed')
    fd=os.open(directory/'accept.lock',os.O_RDONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o444)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        stamp=facts['stamp'];task=facts['task']
        if intent.get('task_ref')!=task:raise ValueError('Review publication has no exact native task')
        targets=node_limits(root,job,settings=settings);duration=None
        if targets is not None:duration=_advice_duration(intent,limits)
        elif job['role']=='W' and job.get('search'):
            targets=dict(max_observed_tokens=job['search']['limits']['max_w_observed_tokens'],max_seconds=job['search']['limits']['max_w_seconds'])
            duration=limits['advance_seconds']-intent['search_slice']['advance_seconds']
        prior=intent.get('budget_slice',{}).get('previous_tokens',0)
        consumed=prior+meter['input_tokens']+meter['output_tokens'] if observed else prior
        reserve=delivery_reserve(settings) or dict(tokens=0,seconds=0,steps=0)
        basis=settings.get('supervision_budget_basis')
        current_search=head['search']
        if job.get('research_strategy_version')==3:
            current_search=supervision.with_live_investment(current_search,
                facts.get('research_investment'),scope=facts['scope'])
        fraction=(supervision_fraction(initial_tokens=basis['max_tokens'],
                initial_seconds=basis['max_seconds'],
                observed_tokens=shared.get('accounted_tokens',shared['observed_tokens']),
                active_seconds=limits['consumed_seconds']) if basis is not None else None)
        supervision_facts=supervision_budget_facts(basis,shared,limits) if basis is not None else None
        direction_review=supervision.worker_review(job,intent,summary=current_search,
            budget_fraction=fraction,budget_facts=supervision_facts,
            selected_policy=settings.get('supervision_policy') if settings.get('search_policy') is not None else None,
            current_work=dict(tokens=consumed,seconds=duration,
                calls=(facts.get('research_investment') or {}).get('total_calls')))
        investment=None;investment_notice=False
        if job.get('research_strategy_version')==3:
            chosen=supervision.policy(settings['supervision_policy']) if settings.get('supervision_policy') else None
            if chosen is not None and chosen['version'] in (2,3,4):
                status=supervision.due(current_search,budget_fraction=fraction,policy=chosen,budget_facts=supervision_facts)
                state=supervision.observe(current_search,fraction,selected_policy=chosen)
                counts=state['investment']
                live_observed=facts.get('research_investment') is not None
                investment=dict(total_calls=counts['total_calls'],consecutive_calls=counts['consecutive_calls'],
                    mechanism_ref=counts['mechanism_ref'],mode=chosen['mode'],
                    coverage='live' if live_observed else 'accepted_lower_bound',
                    due=status['due'] if live_observed or status['due'] or chosen['mode']=='off' else None)
                if chosen['version']==4:
                    investment['automatic_allowed']=status['automatic_allowed']
                    investment['suppression_reason']=status['suppression_reason']
                investment_notice=(chosen['mode']=='notice' and status['due'] and not status['cooldown']
                    and job.get('purpose') is None and not job.get('evidence_return')
                    and not intent.get('output_repair') and not intent.get('work_repair')
                    and not job.get('final_only') and not job.get('final_report'))
        # The original grant remains the ceiling for this accepted stage.
        grant=intent.get('budget_slice',{}).get('shared_limits',dict(max_tokens=settings['max_tokens'],max_seconds=settings['max_seconds']))
        available_tokens=budget_limits.remaining(budget_limits.minimum(limits['max_tokens'],grant['max_tokens']),
            shared.get('accounted_tokens', shared['observed_tokens'])+reserve['tokens'])
        available_seconds=budget_limits.remaining(budget_limits.minimum(limits['seconds_remaining'],
            budget_limits.remaining(grant['max_seconds'],limits['consumed_seconds'])),reserve['seconds'])
        # Shared ceilings retain priority. The ordinary W time target also
        # bounds its review window, without judging scientific progress:
        # pause new work, never accepted calls.
        remaining=available_seconds
        context_max=((settings.get('worker_fragment_policy') or {}).get('max_context_windows')
            if checkpoint.enabled_for_policy(job,settings=settings) else None)
        progress_due=budget_notice.worker_progress_due(job,intent,active_seconds=duration,
            target_seconds=targets['max_seconds'] if targets else None,
            fragment_enabled=checkpoint.enabled_for_policy(job,settings=settings),
            max_context_windows=context_max,context_observation=context_observation,
            context_handoff_version=(settings.get('worker_fragment_policy') or {}).get('context_handoff_version'))
        reason=('shared_usage_unknown' if _usage_unknown(shared, (job['step'],)) else
            'reserve_reached' if budget_limits.reached(available_seconds) or budget_limits.reached(available_tokens) else
            'worker_direction_review_due' if direction_review is not None else
            budget_notice.worker_context_reason(context_max,context_observation,version=(settings.get('worker_fragment_policy') or {}).get('context_handoff_version')) if context_max is not None and progress_due else
            'worker_progress_review_due' if progress_due else
            'target_time_reached' if context_max is None and targets and duration>=targets['max_seconds'] else
            'target_tokens_reached' if context_max is None and targets and consumed>=targets['max_observed_tokens'] else None)
        value=dict(version=experiments.ADMISSION_VERSION,stage_anchor=task,
            scope=facts['scope'],native_attempt=facts['native_attempt'],
            boot_id=stamp['boot_id'],observed_at=stamp['at_unix'],
            deadline_boottime=None if remaining is None else stamp['at_boottime']+remaining,
            valid_until_boottime=stamp['at_boottime']+10,pending=bool(reason),reason=reason,
            observed_tokens_lower_bound=consumed,inflight_usage_unknown=True,targets=targets)
        if direction_review is not None:value['direction_review']=direction_review
        path=directory/'review.json'
        if path.exists():
            old=contracts.read(contracts.regular(path))
            if old.get('stage_anchor')!=task or old.get('scope')!=value['scope']:
                raise ValueError('Host review projection binding changed')
        contracts.write(path,value);path.chmod(0o400)
        return dict(mode=mode,targets=targets,duration=duration,direction_review=direction_review,
            research_investment=investment,investment_notice=investment_notice)
    finally:os.close(fd)
