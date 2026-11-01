"""Accepted control facts, advanced once by the existing ledger writer.

The small head is the normal read surface. Exact ID records are point reads;
the original ledger remains the audit trail. Recovery consumes only the one
event that a crashed writer could have published beyond the accepted head.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from urllib.parse import quote

from . import atomic_files, contracts as c

DIRECTORY = 'host-history/accepted-budget'
HEAD = DIRECTORY + '/head.json'
EVENT_KINDS = frozenset(('clock','budget_amend','closeout_begin','closeout_resume','action_intent',
    'action_outcome','final_a_return','resource_grant','resource_reserve','resource_rebind',
    'resource_bind','resource_settle','resource_release','job_accept','native_accept','native_terminal',
    'scientific_seal','configuration_accept','configuration_bind','execution_recovery',
    'audit_accept','audit_complete','delivery_preparation','authority_accept'))


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode()


def _replace(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_files.replace_bytes(path, _bytes(value))


def _row_path(root, key):
    if not isinstance(key, str) or not key or any(part in ('', '.', '..') for part in key.split('/')):
        raise ValueError('Exact accepted control key required')
    parts = []
    for part in key.split('/'):
        encoded = quote(part, safe='%')
        parts.extend('c-'+encoded[offset:offset+96] for offset in range(0,len(encoded),96))
        parts.append('_')
    return Path(root) / DIRECTORY / 'rows' / '/'.join(parts) / 'row.json'


def _clock(start, basis):
    return dict(start=start, basis=basis, durations=dict(advance=0., waiting=0., paused=0., unclassified=0.),
        phase='paused' if basis == 'active' else 'unclassified', at=start, previous=start,
        pending=0., retained=0., clock_seen=False, pending_tail=False, unmeasured_tail=False,
        bridged=False, floor_boot=None, floor=0.)


def _retain(clock, boot):
    if clock['floor_boot'] == boot:
        measured = clock['durations']['advance'] + (clock['pending'] if clock['phase'] == 'advance' else 0.)
        clock['retained'] = max(clock['retained'], clock['floor']-measured)


def clock_step(clock, event):
    """The existing clock reducer's single-event transition, without history."""
    from .budget import _duration
    event = {key: event[key] for key in ('seq', 'kind', 'at_unix', 'at_boottime', 'boot_id',
        'phase', 'reanchor', 'uptime_accounting', 'observed_advance_seconds') if key in event}
    clock = copy.deepcopy(clock)
    marked = event.get('uptime_accounting') is True
    observed = event.get('observed_advance_seconds')
    if not clock['clock_seen'] and event['kind'] != 'clock':
        if observed is not None:
            measured = clock['durations']['advance'] + (clock['pending'] if clock['phase'] == 'advance' else 0.)
            clock['retained'] = max(clock['retained'], observed-measured)
        return clock
    clock['clock_seen'] = True
    previous = clock['previous']
    old_pending, old_retained = clock['pending'], clock['retained']
    if previous is not None:
        uptime = clock['basis'] == 'active'
        if uptime and previous.get('boot_id') != event.get('boot_id'):
            _retain(clock, previous.get('boot_id'))
        clock['pending'] += _duration(previous, event,
            legacy_elapsed=clock['basis'] == 'elapsed', exclude_downtime=uptime)
        clock['pending_tail'] |= uptime and previous.get('boot_id') != event.get('boot_id')
    clock['previous'] = event
    if event['kind'] != 'clock':
        if observed is not None:
            measured = clock['durations']['advance'] + (clock['pending'] if clock['phase'] == 'advance' else 0.)
            clock['retained'] = max(clock['retained'], observed-measured)
        return clock
    if 'reanchor' in event:
        clock['bridged'] = True
        if clock['at'] is not None and previous is not None:
            amount = old_pending if marked else _duration(clock['at'], previous,
                legacy_elapsed=clock['basis'] == 'elapsed')
            clock['durations']['paused'] += amount
            if marked:
                clock['durations']['advance'] += old_retained
        if observed is not None:
            clock['durations']['advance'] = max(clock['durations']['advance'], observed)
        clock.update(at=event, phase='paused', pending=0., pending_tail=False, retained=0.)
        return clock
    if clock['at'] is not None:
        amount = clock['pending'] if marked else _duration(clock['at'], event,
            legacy_elapsed=clock['basis'] == 'elapsed')
        clock['durations'][clock['phase']] += amount
        if marked:
            clock['durations']['advance'] += clock['retained']
        clock['unmeasured_tail'] |= marked and clock['pending_tail']
    if observed is not None:
        clock['durations']['advance'] = max(clock['durations']['advance'], observed)
    clock.update(at=event, phase=event['phase'], pending=0., pending_tail=False, retained=0.)
    return clock


def clock_sample(clock, now):
    from .budget import _duration
    clock = copy.deepcopy(clock)
    durations = clock['durations']
    if clock['at'] is not None and now is not None:
        uptime = clock['basis'] == 'active'
        previous = clock['previous']
        if uptime and previous.get('boot_id') != now.get('boot_id'):
            _retain(clock, previous.get('boot_id'))
        clock['pending'] += _duration(previous, now,
            legacy_elapsed=clock['basis'] == 'elapsed', exclude_downtime=uptime)
        if uptime:
            _retain(clock, now.get('boot_id'))
        clock['pending_tail'] |= uptime and previous.get('boot_id') != now.get('boot_id')
        clock['unmeasured_tail'] |= clock['pending_tail']
        amount = clock['pending'] if uptime else _duration(clock['at'], now,
            legacy_elapsed=clock['basis'] == 'elapsed')
        durations[clock['phase']] += amount
        durations['advance'] += clock['retained']
    elif clock['at'] is not None and clock['basis'] == 'active':
        _retain(clock, clock['previous'].get('boot_id'))
        durations['advance'] += clock['retained']
    return durations, clock['phase'], clock['unmeasured_tail']


class AcceptedState(dict):
    """One explicit accepted-head observation, never a process-global cache."""
    def __init__(self, root, value, *, base, pending=None):
        super().__init__(value)
        self.root = Path(root)
        self.base = base
        self.pending = pending

    @property
    def prefix(self):
        return self['prefix']

    @property
    def current_job(self):
        return self['current_job']

    @property
    def prepared_step(self):
        return self['max_local_step']

    @property
    def parent(self):
        return self['head_ref']

    def row(self, key):
        if self.pending is not None and key in self.pending['index_updates']:
            return copy.deepcopy(self.pending['index_updates'][key]['current'])
        path = _row_path(self.root, key)
        if not path.exists():
            return None
        value = c.read(path)
        if value['seq'] <= self['seq']:
            return copy.deepcopy(value['current'])
        if value['previous_seq'] <= self['seq']:
            return copy.deepcopy(value['previous'])
        raise RuntimeError('Accepted control observation advanced; read a fresh head')

    def event(self, reference):
        if reference is None:
            return None
        path = Path(reference['path'])
        relative = path.relative_to(self['logical_root']) if path.is_absolute() else path
        return c.read(self.root / relative)

    def control_file(self, path):
        """Read one accepted redo original, including a not-yet-installed tail."""
        path = Path(path)
        relative = path.relative_to(self.root) if path.is_absolute() else path
        if '..' in relative.parts:
            raise ValueError('Accepted control original escaped its instance')
        expected = (None if self.pending is None else
            self.pending.get('control_files', {}).get(relative.as_posix()))
        actual = self.root/relative
        if not actual.exists():
            return copy.deepcopy(expected)
        value = c.read(c.regular(actual))
        if expected is not None and value != expected:
            raise ValueError('Accepted original control file changed')
        return value


class AcceptedEvents:
    """Bounded current facts for existing budget predicates, not a log list."""
    def __init__(self, state):
        self.state = state
        self.loaded = {}

    def __len__(self):
        return self.state['seq']

    def __bool__(self):
        return bool(self.state['seq'])

    def event(self, ref):
        if ref is None:
            return None
        key = ref['path']
        if key not in self.loaded:
            self.loaded[key] = self.state.event(ref)
        return self.loaded[key]

    def __getitem__(self, key):
        if key != -1:
            raise ValueError('Historical ledger indexing belongs to the explicit audit reader')
        return self.event(self.state['head_ref'])

    def __iter__(self):
        kinds = ('clock', 'budget_amend', 'closeout_begin', 'closeout_resume', 'final_a_return')
        references = [self.state['latest'].get(kind) for kind in kinds]
        references += [self.state.get('explicit_amendment'), self.state.get('action_first'),
            self.state.get('action_last')]
        resumed = self.state.get('resume')
        if resumed is not None:
            references.append(resumed['source_final']['record'])
        found = {}
        for ref in references:
            if ref is not None and ref['path'] not in found:
                found[ref['path']] = self.event(ref)
        return iter(sorted(found.values(), key=lambda row: row['seq']))

    def __reversed__(self):
        return reversed(list(iter(self)))


def initialize(root, settings, *, search_seed, search_ref, identity_root=None,
        prefix_seed=None, search_rows=None):
    from . import accepted_prefix
    root = Path(root)
    identity_root = Path(identity_root or root)
    value = dict(version=2, logical_root=str(identity_root),
        instance_id=settings['goal_id'], settings_ref=dict(path=str(identity_root/'settings.json')),
        seq=0, head_ref=None, last_stamp=None, latest={},
        ceilings={key: settings[key] for key in ('max_tokens', 'max_seconds', 'max_steps')},
        historical_max_steps=settings['max_steps'], max_local_step=0,
        prepared_count=0, continuation_count=0, action_count=0, audit_count=0,
        current_job=None, current_native=None, active_audit=None,
        delivery_preparation=dict(count=0, last_ref=None, unresolved_ref=None),
        usage=dict(input_tokens=0, cached_input_tokens=0, output_tokens=0,
            unknown_count=0, first_unknown_step=None),
        role_usage=dict(H=0, W=0, A=0, E=0, closeout=0),
        dispatch_count=0, resource=None, closeout=None, resume=None, final_return=None,
        unresolved_actions=0, first_unresolved_operation=None, explicit_amendment=None,
        action_first=None, action_last=None, final_action_count=0, final_confirmed=False,
        configuration=None, applied_configuration=0, is_fork=False,
        prefix=(accepted_prefix.empty(identity_root, settings)
            if prefix_seed is None else copy.deepcopy(prefix_seed)),
        search=copy.deepcopy(search_seed), search_ref=copy.deepcopy(search_ref),
        clock=_clock(None, settings.get('time_budget_basis', 'elapsed')))
    path = root / HEAD
    path.parent.mkdir(parents=True, exist_ok=True)
    for key, row in (search_rows or {}).items():
        if not key.startswith('search/'):
            raise ValueError('Birth search rows require an explicit search key')
        _replace(_row_path(root,key), dict(seq=0, previous_seq=0, previous=None,
            current=copy.deepcopy(row)))
    atomic_files.publish_bytes(path, _bytes(value))
    return AcceptedState(root, value, base=settings)


def _apply(state, event):
    from .budget import _duration
    if (type(event.get('seq')) is not int or event['seq'] != state['seq']+1
            or event.get('parent_ref') != state['head_ref'] or event.get('settings_ref') != state['settings_ref']
            or any(type(event.get(key)) not in (int, float) or not math.isfinite(event[key])
                for key in ('at_unix', 'at_boottime')) or event['at_boottime'] < 0
            or not isinstance(event.get('boot_id'), str) or not event['boot_id']
            or event.get('kind') not in EVENT_KINDS or not isinstance(event.get('head_updates'), dict)
            or not isinstance(event.get('index_updates'), dict)
            or set(event['head_updates'])-set(state)
            or set(event['head_updates']).intersection(('seq','head_ref','settings_ref','instance_id','logical_root','clock'))):
        raise ValueError('New control event lost its accepted predecessor')
    if event['kind'] == 'clock' and event.get('phase') not in ('advance', 'paused', 'waiting'):
        raise ValueError('Unknown new control clock phase')
    if 'observed_advance_seconds' in event and (event.get('uptime_accounting') is not True
            or type(event['observed_advance_seconds']) not in (int, float)
            or not math.isfinite(event['observed_advance_seconds']) or event['observed_advance_seconds'] < 0):
        raise ValueError('Invalid new advancement observation')
    if 'reanchor' in event:
        from . import budget
        budget._verify_reanchor(state.root, state.base, event, AcceptedEvents(state))
    if state['last_stamp'] is not None and 'reanchor' not in event:
        _duration(state['last_stamp'], event, legacy_elapsed=state['clock']['basis'] == 'elapsed')
    value = copy.deepcopy(dict(state))
    value.update(copy.deepcopy(event['head_updates']))
    value['clock'] = clock_step(state['clock'], event)
    anchor = event.get('native_floor')
    if anchor is not None:
        if (anchor['boot_id'] != event['boot_id'] or type(anchor.get('advance_seconds')) not in (int,float)
                or not math.isfinite(anchor['advance_seconds']) or anchor['advance_seconds'] < 0):
            raise ValueError('New native acceptance changed its clock boot')
        value['clock']['floor_boot'] = anchor['boot_id']
        value['clock']['floor'] = max(value['clock']['floor'], anchor['advance_seconds'])
    value['seq'] = event['seq']
    value['head_ref'] = dict(path=str(Path(state['logical_root'])/'control-ledger'/f'{event["seq"]:06d}.json'))
    value['last_stamp'] = {key: event[key] for key in ('at_unix', 'at_boottime', 'boot_id')}
    value['latest'][event['kind']] = value['head_ref']
    return AcceptedState(state.root, value, base=state.base, pending=event)


def read(root):
    """Independent entry: produce the original settings and one fresh head."""
    return load(root, base=c.settings(root))


def load(root, *, base):
    """Read a fresh mutable head using the caller's original settings fact."""
    root = Path(root)
    path = root / HEAD
    if not path.exists():
        raise ValueError('Accepted budget head is required; old instances are not rebuilt implicitly')
    value = c.read(path)
    if (value.get('version') != 2 or 'active_audit' not in value
            or 'delivery_preparation' not in value):
        raise ValueError('Unsupported accepted budget state')
    if (value['instance_id'] != base['goal_id']
            or value['settings_ref'] != dict(path=str(Path(value['logical_root'])/'settings.json'))):
        raise ValueError('Accepted budget head lost its original research identity')
    state = AcceptedState(root, value, base=base)
    if state['clock']['start'] is None and (root/'host-started.json').exists():
        start = c.read(root/'host-started.json')
        state['clock'] = _clock(start, state['clock']['basis'])
    following = root/'control-ledger'/f'{state["seq"]+1:06d}.json'
    if following.exists():
        state = _apply(state, c.read(following))
    return state


def _install_indexes(root, event):
    for key, value in event['index_updates'].items():
        _replace(_row_path(root, key), dict(seq=event['seq'], **value))
    for relative, value in event.get('control_files', {}).items():
        parts = Path(relative).parts
        if Path(relative).is_absolute() or '..' in parts:
            raise ValueError('Accepted control redo escaped its exact instance')
        path = Path(root)/relative
        if path.exists():
            if c.read(path) != value:
                raise ValueError('Accepted original control file changed')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if event['kind'] == 'authority_accept':
                # Authority originals retain qualification._save's wire bytes.
                from . import qualification as q
                q._save(root, path, value)
            else:
                atomic_files.publish_bytes(path, _bytes(value))


def recover(root, state):
    if state.pending is not None:
        _install_indexes(root, state.pending)
        _replace(Path(root)/HEAD, dict(state))
        state.pending = None
    return state


def append(root, state, *, kind, fields=None, updates=None, rows=None, control_files=None):
    """Independent row producer; caller holds the existing _ledger_writer."""
    from . import budget
    state = recover(root, state)
    stamp = budget.clock_stamp()
    index_updates = {key: dict(previous_seq=state['seq'], previous=state.row(key), current=value)
        for key, value in (rows or {}).items()}
    return _append_prepared(root, state, kind=kind, stamp=stamp, fields=fields,
        updates=updates, index_updates=index_updates, control_files=control_files)


def _append_prepared(root, state, *, kind, stamp, index_updates,
        fields=None, updates=None, control_files=None):
    """Publish explicit row generations under the writer, after recovery."""
    event = dict(seq=state['seq']+1, parent_ref=state['head_ref'], settings_ref=state['settings_ref'],
        **stamp, kind=kind, **(fields or {}), head_updates=updates or {}, index_updates=index_updates,
        control_files=control_files or {})
    if state['clock']['basis'] == 'active':
        durations, _, _ = clock_sample(state['clock'], stamp)
        event.update(uptime_accounting=True, observed_advance_seconds=durations['advance'])
    next_state = _apply(state, event)
    path = Path(root)/'control-ledger'/f'{event["seq"]:06d}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_files.publish_bytes(path, _bytes(event))
    _install_indexes(root, event)
    _replace(Path(root)/HEAD, dict(next_state))
    next_state.pending = None
    return event, next_state


def append_budget_event(root, events, **fields):
    """Accept one already gated domain event and its exact accounting delta."""
    state = events.state
    kind = fields.pop('kind')
    ref = dict(path=str(Path(state['logical_root'])/'control-ledger'/f'{state["seq"]+1:06d}.json'))
    updates, rows = {}, {}
    if kind == 'budget_amend':
        ceilings = dict(state['ceilings'], **fields['after'])
        ceilings['max_steps'] = fields.get('after_max_steps', ceilings['max_steps'])
        updates.update(ceilings=ceilings,
            historical_max_steps=max(state['historical_max_steps'], ceilings['max_steps']))
        if not str(fields.get('operation_id', '')).startswith('configuration-'):
            updates['explicit_amendment'] = ref
    elif kind == 'closeout_begin':
        brief = {key: copy.deepcopy(value) for key, value in fields.items()
            if key not in ('delivery_sources', 'resume_cursor')}
        brief.update(seq=state['seq']+1, kind=kind, ref=ref)
        updates.update(closeout=brief, final_return=None, action_first=None, action_last=None,
            final_action_count=0, final_confirmed=False)
    elif kind == 'closeout_resume':
        updates.update(closeout=None, final_return=None,
            resume=dict(fields, seq=state['seq']+1, kind=kind, ref=ref))
    elif kind == 'final_a_return':
        updates['final_return'] = dict(fields, seq=state['seq']+1, kind=kind, ref=ref)
    elif kind == 'action_intent':
        rows['action/'+fields['action_id']] = dict(intent_ref=ref, outcome_ref=None,
            operation_id=fields['operation_id'], status='pending')
        operation_key = 'delivery-operation/'+fields['operation_id']
        operation = state.row(operation_key) or dict(pending=0)
        increase = int(operation['pending'] == 0)
        operation['pending'] += 1
        rows[operation_key] = operation
        updates.update(action_count=state['action_count']+1, action_last=ref,
            action_first=state['action_first'] or ref,
            final_action_count=state['final_action_count']+1,
            unresolved_actions=state['unresolved_actions']+increase,
            first_unresolved_operation=fields['operation_id'] if not state['unresolved_actions'] else None)
    elif kind == 'action_outcome':
        key = 'action/'+fields['action_id']
        row = state.row(key)
        if row is None or row['outcome_ref'] is not None:
            raise ValueError('Delivery outcome lost its exact pending action')
        row.update(outcome_ref=ref, status=fields['result']['status'])
        rows[key] = row
        operation_key = 'delivery-operation/'+row['operation_id']
        operation = state.row(operation_key)
        before = operation['pending']
        if fields['result']['status'] != 'unknown':
            operation['pending'] -= 1
        if fields['result']['status'] == 'confirmed':
            operation['pending'] = 0
        rows[operation_key] = operation
        if before and not operation['pending']:
            updates['unresolved_actions'] = state['unresolved_actions']-1
            if not updates['unresolved_actions']:
                updates['first_unresolved_operation'] = None
        if fields['result']['status'] == 'confirmed':
            updates['final_confirmed'] = True
    elif kind.startswith('resource_'):
        updates, rows = resource_delta(state, ref, kind, fields)
    if fields.get('operation_id') is not None:
        rows['operation/'+fields['operation_id']] = dict(ref=ref)
    event, _ = append(root, state, kind=kind, fields=fields, updates=updates, rows=rows)
    return event


def search_row(root, key, *, head=None):
    return (read(root) if head is None else head).row(key)


def point_row(root, namespace, key, *, head=None):
    return search_row(root, namespace+'/'+str(key), head=head)


def action(root, action_id, *, head=None):
    state = read(root) if head is None else head
    row = state.row('action/'+action_id)
    if row is None:
        return None
    return dict(intent=state.event(row['intent_ref']), outcome=state.event(row['outcome_ref']))


def _proof(state, reference):
    from . import qualification as q
    path = Path(reference['path'])
    if path.is_absolute():
        path = path.relative_to(state['logical_root'])
    return q._ref(state.root, state.root/path)


def _resource_row(state, row):
    if row is None:
        return None
    row = copy.deepcopy(row)
    row['ref'] = _proof(state, row['ref'])
    return row


class ResourceRows:
    def __init__(self, state):
        self.state = state

    def get(self, key, default=None):
        return _resource_row(self.state, self.state.row('resource/'+key)) or default

    def __contains__(self, key):
        return self.get(key) is not None

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value


def resource_view(state):
    resource = state['resource']
    grants = {} if resource is None else {resource['owner']['key']: dict(
        ref=_proof(state, resource['grant_ref']), allowance=resource['allowance'])}
    return grants, ResourceRows(state)


def resource_for_call(state, binding):
    key = _resource_call_key(binding)
    found = state.row(key)
    return None if found is None else (found['reservation_id'],
        _resource_row(state, state.row('resource/'+found['reservation_id'])))


def _resource_call_key(binding):
    scope = binding['scope']
    part = (f'{scope["step"]:03d}' if type(scope.get('step')) is int else
        quote(json.dumps(scope,sort_keys=True,ensure_ascii=True,separators=(',',':')),safe=''))
    return 'resource-call/'+part+'/'+binding['call_id']


def resource_delta(state, reference, kind, fields):
    from .budget import _resource_dimensions, _resource_owner
    from scidiscovery import execution_security
    reference = dict(path=f'control-ledger/{state["seq"]+1:06d}.json')
    resource = copy.deepcopy(state['resource'])
    if kind == 'resource_grant':
        allowance = _resource_dimensions(fields['allowance'])
        if not isinstance(fields.get('authorization_note'), str) or not fields['authorization_note'].strip():
            raise ValueError('Execution grant requires its explicit operator decision')
        if resource is None:
            settings_ref = (state['configuration'] or {}).get('ref', dict(path='settings.json'))
            selected = c.read(state.root/settings_ref['path'])
            settings = selected.get('settings', selected)
            if (fields.get('previous_grant') is not None or fields['owner'] != _resource_owner(settings)
                    or allowance != settings['execution_resource_policy']['allowance']):
                raise ValueError('Initial resource grant differs from its frozen owner or allowance')
        elif fields['owner'] != resource['owner']:
            raise ValueError('Execution grant changed its cumulative resource owner')
        if resource is not None and fields.get('previous_grant') != _proof(state, resource['grant_ref']):
            raise ValueError('Execution grant changed its exact predecessor')
        resource = dict(owner=fields['owner'], allowance=allowance, grant_ref=reference,
            held=dict(wall_time_seconds=0, max_storage_bytes=0) if resource is None else resource['held'],
            reservation_count=0 if resource is None else resource['reservation_count'],
            settings_ref=((state['configuration'] or {}).get('ref', dict(path='settings.json'))
                if resource is None else resource['settings_ref']))
        return dict(resource=resource), {}
    if resource is None or fields['owner'] != resource['owner']:
        raise ValueError('Execution reservation has no exact accepted resource owner')
    identity = fields['reservation_id']
    key = 'resource/'+identity
    original = state.row(key)
    if kind == 'resource_reserve':
        if (original is not None or fields['grant_revision'] != _proof(state, resource['grant_ref'])
                or identity != execution_security.request_digest(dict(owner=fields['owner'], binding=fields['binding']))):
            raise ValueError('Execution reservation changed its original identity or grant')
        requested = _resource_dimensions(fields['budget'])
        ceiling = _resource_dimensions(fields['effective_allowance'])
        if any(ceiling[name] > resource['allowance'][name]
                or requested[name] > ceiling[name]-resource['held'][name] for name in requested):
            raise ValueError('Cumulative execution resources exceeded')
        original = dict(ref=reference, owner=fields['owner'], binding=fields['binding'], budget=requested,
            actual={}, bound=None, released=False, settled_observation=None)
        resource['reservation_count'] += 1
        for name, amount in requested.items():
            resource['held'][name] += amount
    else:
        if original is None or original['released'] or fields['reservation'] != _proof(state, original['ref']):
            raise ValueError('Execution resource event lost its exact reservation')
        if kind == 'resource_rebind':
            proof = fields.get('proof')
            binding = fields['binding']
            if (original['bound'] is not None or original['actual'] or fields['before'] != original['binding']
                    or set(binding) != set(original['binding']) or any(binding.get(name) != original['binding'].get(name)
                        for name in ('call_id','scope','native_attempt','interface'))
                    or not isinstance(proof, dict) or set(proof) != {'seal','allocation','pending_set'}
                    or any(not isinstance(row, dict) or set(row) != {'path','size_bytes'} for row in proof.values())):
                raise ValueError('Only the original never-prepared reservation may rebind')
            original['binding'] = fields['binding']
        elif kind == 'resource_bind':
            if (original['bound'] is not None or not isinstance(fields['accepted_binding'], dict)
                    or set(fields['accepted_binding']) != {'accepted','prepared'}):
                raise ValueError('Execution reservation already has an accepted binding')
            original['bound'] = fields['accepted_binding']
        elif kind == 'resource_settle':
            actual = _resource_dimensions(fields['actual'], partial=True, zero=True)
            if original['bound'] is None or not isinstance(fields.get('observation'), dict) or any(name in original['actual']
                    and original['actual'][name] != amount for name, amount in actual.items()):
                raise ValueError('Terminal usage changed the original proven resource cost')
            before = {**original['budget'], **original['actual']}
            original['actual'].update(actual)
            after = {**original['budget'], **original['actual']}
            for name in resource['held']:
                resource['held'][name] += after[name]-before[name]
            original['settled_observation'] = fields['observation']
        elif kind == 'resource_release':
            if original['bound'] is None or original['actual'] or not isinstance(fields.get('retirement'), dict):
                raise ValueError('Execution resources have no permanent non-submission proof')
            original['released'] = True
            for name, amount in original['budget'].items():
                resource['held'][name] -= amount
        else:
            raise ValueError('Unknown accepted execution resource transition')
    binding = original['binding']
    call_key = _resource_call_key(binding)
    return dict(resource=resource), {key: original, call_key: dict(reservation_id=identity)}


def accept_job(root, step, job):
    return accept_job_from_base(root, step, job, base=c.settings(root))


def accept_job_from_base(root, step, job, *, base):
    from . import budget
    root = Path(root)
    with budget._ledger_writer_from_base(root, base=base):
        state = recover(root, load(root, base=base))
        prior = state.row(f'job/{step:03d}')
        reference = c.record_ref(root/'jobs'/f'{step:03d}.json')
        if prior is not None:
            if prior['job_ref'] != reference:
                raise ValueError('Prepared job changed its accepted identity')
            authority_calls(state, step)
            return state
        current = state.current_job
        if current is not None and current['status'] != 'sealed':
            begin = state['closeout']
            if (not job.get('final_only') or begin is None or begin['mode'] != 'final'
                    or begin['first_step'] != step):
                raise ValueError('Another accepted scientific job remains open')
        row = dict(step=step, role=job['role'], job_ref=reference, status='prepared',
            native_record=None, authority_calls={},
            prefix=copy.deepcopy(state.prefix), purpose=job.get('purpose'), unknown_count=0,
            reported_tokens=0, complete=True, input_search_ref=copy.deepcopy(state['search_ref']))
        from . import user_guidance
        user_guidance.apply_job(root, job)
        _, state = append(root, state, kind='job_accept', fields=dict(step=step, job_ref=reference),
            updates=dict(current_job=row, prepared_count=state['prepared_count']+1,
                max_local_step=max(state['max_local_step'], step),
                is_fork=(root/'host-runtime/fork/manifest.json').exists()), rows={f'job/{step:03d}': row})
        return state


def authority_calls(state, step):
    """Exact originals accepted for this job, including pre-prepare allocations."""
    job = state.row(f'job/{step:03d}')
    if job is None or not isinstance(job.get('authority_calls'), dict):
        raise ValueError('Accepted job authority references are required')
    if (state.current_job is not None and state.current_job['step'] == step
            and state.current_job != job):
        raise ValueError('Accepted authority job row lost its current pointer')
    for call, row in job['authority_calls'].items():
        if (not isinstance(call, str) or not isinstance(row, dict)
                or set(row) != {'allocation', 'requests'} or not isinstance(row['requests'], list)):
            raise ValueError('Accepted job authority references changed')
        allocation = row['allocation']
        for ref in ([allocation] if allocation is not None else []) + row['requests']:
            if (not isinstance(ref, dict) or set(ref) != {'path', 'size_bytes'}
                    or type(ref['size_bytes']) is not int or ref['size_bytes'] <= 0):
                raise ValueError('Exact accepted authority original reference required')
        if allocation is not None and allocation['path'] != f'execution-authority/calls/{call}/allocation.json':
            raise ValueError('Accepted allocation changed its exact call')
        request_paths = [ref['path'] for ref in row['requests']]
        if len(set(request_paths)) != len(request_paths):
            raise ValueError('Accepted authority request was repeated')
        for relative in request_paths:
            parts = Path(relative).parts
            if (len(parts) != 4 or parts[:2] != ('execution-authority', 'requests')
                    or parts[-1] != 'request.json' or len(parts[2]) != 64
                    or any(char not in '0123456789abcdef' for char in parts[2])):
                raise ValueError('Accepted authority request path changed')
    return job['authority_calls']


def authority_original(state, reference):
    """Point-read an accepted authority original, even before tail installation."""
    from . import qualification as q
    value = state.control_file(reference['path'])
    if value is None:
        raise ValueError('Accepted authority original is unavailable')
    path = state.root/reference['path']
    if path.exists():
        actual = q._ref(state.root, path)
    else:
        actual = dict(path=reference['path'], size_bytes=len(
            (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')))
    if actual != reference:
        raise ValueError('Accepted authority original identity changed')
    return value


def accept_authority(root, *, step, call_id, path, value, supersedes=None, operation_context=None):
    """Accept one authority original and this job's reference in the same event.

    The authority writer is already held. The existing ledger writer and redo
    publish originals before returning; a renewal's successor belongs to this
    same acceptance rather than a second publication boundary.
    """
    from . import budget, qualification as q
    root, path = Path(root), Path(path)
    relative = path.relative_to(root).as_posix()
    allocation = relative == f'execution-authority/calls/{call_id}/allocation.json'
    parts = Path(relative).parts
    if (not allocation and (len(parts) != 4 or parts[:2] != ('execution-authority', 'requests')
            or parts[-1] != 'request.json' or len(parts[2]) != 64
            or any(char not in '0123456789abcdef' for char in parts[2]))):
        raise ValueError('Exact authority allocation or request original required')
    reference = dict(path=relative, size_bytes=len(
        (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')))
    with budget._ledger_writer(root, context=operation_context) as base:
        state = recover(root, load(root, base=base))
        calls = copy.deepcopy(authority_calls(state, step))
        job = state.row(f'job/{step:03d}')
        if (job['role'] != 'W' or value.get('call_id') != call_id
                or job['job_ref'] != c.record_ref(root/'jobs'/f'{step:03d}.json')):
            raise ValueError('Authority original lost its accepted W job')
        if allocation:
            if value['binding']['scope']['job_ref'] != job['job_ref'] or supersedes is not None:
                raise ValueError('Allocation changed its accepted job')
        elif value['job'] != q._ref(root, root/'jobs'/f'{step:03d}.json') or value.get('supersedes') != supersedes:
            raise ValueError('Authority request changed its accepted job or predecessor')
        row = calls.setdefault(call_id, dict(allocation=None, requests=[]))
        existing = row['allocation'] if allocation else next(
            (ref for ref in row['requests'] if ref['path'] == relative), None)
        controls = {relative: value}
        if supersedes is not None:
            if supersedes not in row['requests']:
                raise ValueError('Authority renewal predecessor was not accepted for this call')
            previous = authority_original(state, supersedes)
            if previous['call_id'] != call_id:
                raise ValueError('Authority renewal changed its original call')
            successor = (Path(supersedes['path']).parent/'successor.json').as_posix()
            controls[successor] = dict(previous=supersedes, successor=reference)
        if existing is not None:
            if existing != reference or authority_original(state, existing) != value:
                raise ValueError('Accepted authority original changed')
            for original, expected in controls.items():
                if state.control_file(original) != expected:
                    raise ValueError('Accepted authority successor changed')
            return reference
        if any((root/original).exists() for original in controls):
            raise ValueError('Authority original was published without its accepted job reference')
        if allocation:
            row['allocation'] = reference
        else:
            row['requests'].append(reference)
        job['authority_calls'] = calls
        updates = (dict(current_job=job) if state.current_job is not None
            and state.current_job['step'] == step else {})
        append(root, state, kind='authority_accept', fields=dict(step=step, call_id=call_id,
            original=reference), updates=updates, rows={f'job/{step:03d}': job}, control_files=controls)
        return reference


def accept_native(root, step, record, intent):
    from . import budget
    root, record = Path(root), Path(record)
    relative = record.relative_to(root).as_posix()
    key = 'attempt/'+relative.replace('/', ':')
    with budget._ledger_writer(root) as base:
        state = recover(root, load(root, base=base))
        prior = state.row(key)
        if prior is not None:
            if prior['intent'] != intent:
                raise ValueError('Native acceptance changed its exact original intent')
            if prior['status'] != 'open' or (state['current_native'] or {}).get('record') != relative:
                raise ValueError('A closed or superseded native original cannot be launched again')
            return state
        if state.current_job is None or state.current_job['step'] != step:
            raise ValueError('Native intent has no current accepted job')
        if state['current_native'] is not None:
            raise ValueError('An accepted native attempt is still open')
        if intent.get('task_ref') != state.current_job['job_ref']:
            raise ValueError('Native intent changed its accepted task')
        row = dict(step=step, record=relative, intent=intent, status='open')
        row['accepted_seq'] = state['seq']+1
        continuation = int('attempt' in intent and intent.get('continuation_kind') != 'reentry')
        fields = dict(step=step, record=relative)
        if intent.get('budget_slice') is not None:
            fields['native_floor'] = {key: intent['budget_slice'][key] for key in ('boot_id', 'advance_seconds')}
        job = dict(state.current_job, native_record=relative)
        _, state = append(root, state, kind='native_accept', fields=fields,
            updates=dict(current_native=row, current_job=job, dispatch_count=state['dispatch_count']+1,
                continuation_count=state['continuation_count']+continuation),
            rows={key: row, f'job/{step:03d}':job},
            control_files={relative+'/native-intent.json': intent})
        return state


def accept_terminal(root, step, record, *, meter, unknown, metering=None):
    return accept_terminal_from_base(root, step, record, base=c.settings(root),
        meter=meter, unknown=unknown, metering=metering)


def accept_terminal_from_base(root, step, record, *, base, meter, unknown, metering=None):
    from . import budget
    root, record = Path(root), Path(record)
    relative = record.relative_to(root).as_posix()
    unknown = bool(unknown or meter is None or isinstance(metering, dict) and metering.get('coverage') == 'unknown')
    key = 'attempt/'+relative.replace('/', ':')
    with budget._ledger_writer_from_base(root, base=base):
        state = recover(root, load(root, base=base))
        row = state.row(key)
        if row is None or row['step'] != step:
            raise ValueError('An orphan native file is not an accepted attempt')
        if row['status'] == 'closed':
            if row['meter'] != meter or row['unknown'] != unknown:
                raise ValueError('Closed native accounting changed')
            return state
        if state['current_native'] is None or state['current_native']['record'] != relative:
            raise ValueError('Terminal accounting changed its current attempt')
        if meter is not None:
            from .native import _usage_event
            if _usage_event(dict(type='turn.completed', usage=meter)) != meter:
                raise ValueError('Invalid current terminal usage')
        previous_row = row
        job_key = f'job/{step:03d}'
        previous_job = state.row(job_key)
        job = dict(previous_job)
        totals = dict(state['usage'])
        for name in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
            totals[name] += 0 if meter is None else meter[name]
        totals['unknown_count'] += int(bool(unknown) and job['unknown_count'] == 0)
        if unknown and totals['first_unknown_step'] is None:
            totals['first_unknown_step'] = step
        job['unknown_count'] += int(bool(unknown))
        roles = dict(state['role_usage'])
        roles['closeout' if job['purpose'] == 'closeout' else job['role']] += (0 if meter is None
            else meter['input_tokens']+meter['output_tokens'])
        job['reported_tokens'] += 0 if meter is None else meter['input_tokens']+meter['output_tokens']
        complete = (meter is not None and not unknown and isinstance(metering, dict)
            and metering.get('coverage') == 'terminal' and metering.get('reporting_delay_unmeasured') is False
            and metering.get('inflight_usage_unknown') is False)
        job['complete'] = job['complete'] and complete
        row = dict(previous_row, status='closed', meter=meter, unknown=bool(unknown))
        job['status'] = 'terminal'
        job['role_usage'] = roles
        _, state = _append_prepared(root, state, kind='native_terminal', stamp=budget.clock_stamp(),
            fields=dict(step=step, record=relative),
            updates=dict(usage=totals, role_usage=roles, current_native=None, current_job=job),
            index_updates={
                key: dict(previous_seq=state['seq'], previous=previous_row, current=row),
                job_key: dict(previous_seq=state['seq'], previous=previous_job, current=job)})
        return state


def native_usage_row(root, step, *, head=None):
    state = read(root) if head is None else head
    row = state.row(f'job/{step:03d}')
    if row is None:
        return None
    return dict(complete=row['complete'] and row['status'] in ('terminal', 'sealed'),
        reported_tokens=row['reported_tokens'], unknown_count=row['unknown_count'])


def native_attempt(root, step, record, *, head=None):
    state = read(root) if head is None else head
    relative = Path(record).relative_to(Path(root)).as_posix()
    row = state.row('attempt/'+relative.replace('/', ':'))
    if row is not None and row['step'] != step:
        raise ValueError('Accepted native attempt changed its exact job')
    return row


def usage(root, *, head=None):
    """Closed accepted totals plus the one current attempt's fresh meter."""
    state = read(root) if head is None else head
    totals = {key: state['usage'][key] for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')}
    count = state['usage']['unknown_count']
    unknown = [] if not count else [state['usage']['first_unknown_step']]
    current_job = state.current_job
    stopped = (state['closeout'] or {}).get('stopped_original')
    for step in (None if current_job is None else current_job['step'],
            None if stopped is None else stopped['step']):
        if step is not None and step not in unknown:
            row = state.row(f'job/{step:03d}')
            if row is not None and row['unknown_count']:
                unknown.append(step)
    current = state['current_native']
    if current is not None:
        record = Path(root)/current['record']
        meter_path = record/'usage.json'
        missing = not meter_path.exists()
        if not missing:
            meter = c.read(meter_path)
            from .native import _usage_event
            if _usage_event(dict(type='turn.completed', usage=meter)) != meter:
                raise ValueError('Current native usage observation changed')
            for key in totals:
                totals[key] += meter[key]
        outcome = record/'native-outcome.json'
        metadata = c.native_outcome(record).get('metering') if outcome.exists() else None
        observation = record/'usage-observation.json'
        if metadata is None and observation.exists():
            metadata = c.read(observation)
        missing |= isinstance(metadata, dict) and metadata.get('coverage') == 'unknown'
        missing |= (record/'interruption.json').exists() and not outcome.exists()
        if missing:
            row = state.row(f'job/{current["step"]:03d}')
            if not row['unknown_count']:
                count += 1
            if current['step'] not in unknown:
                unknown.append(current['step'])
    return dict(totals, observed_tokens=totals['input_tokens']+totals['output_tokens'],
        accounted_tokens=totals['input_tokens']+totals['output_tokens'], reserved_tokens=0,
        unknown_steps=unknown, unknown_step_count=count, blocking_unknown_count=count,
        unknown_steps_omitted_count=max(0, count-len(unknown)),
        reserved_unknown_steps=[], blocking_unknown_steps=unknown,
        accounting_basis='observed_lower_bound')


def unknown_except(root, meter, allowed=(), *, head=None):
    """Count retained unknown steps without enumerating their historical IDs."""
    count = meter.get('blocking_unknown_count', len(meter.get('blocking_unknown_steps', meter.get('unknown_steps', []))))
    state = read(root) if head is None else head
    for step in set(allowed):
        row = state.row(f'job/{step:03d}')
        if row is not None and row['unknown_count']:
            count -= 1
        elif step in meter.get('blocking_unknown_steps', meter.get('unknown_steps', [])):
            count -= 1
    return count > 0


def role_usage(root, through_step):
    state = read(root)
    if through_step >= state['max_local_step']:
        totals = dict(state['role_usage'])
    else:
        row = state.row(f'job/{through_step:03d}')
        if row is None or 'role_usage' not in row:
            raise ValueError('Exact historical role accounting is unavailable')
        totals = dict(row['role_usage'])
    current = state['current_native']
    if current is not None and current['step'] <= through_step:
        meter = Path(root)/current['record']/'usage.json'
        if meter.exists():
            value = c.read(meter)
            from .native import _usage_event
            if _usage_event(dict(type='turn.completed', usage=value)) != value:
                raise ValueError('Current native role usage changed')
            job = state.current_job
            totals['closeout' if job['purpose'] == 'closeout' else job['role']] += value['input_tokens']+value['output_tokens']
    observed = usage(root, head=state)
    return dict(totals, thinking=totals['H']+totals['A'], unknown_steps=observed['unknown_steps'],
        unknown_step_count=observed['unknown_step_count'])


def publish_recovery(root, *, step, call_id, recovery_ref, search_delta, search_ref, control_files=None):
    """Publish already reconciled same-call facts, with no replay or refund."""
    from . import budget
    if (search_delta is None) != (search_ref is None):
        raise ValueError('Recovery search facts require their exact saved snapshot reference')
    with budget._ledger_writer(Path(root)) as base:
        state = recover(root, load(root, base=base))
        key = f'execution-recovery/{step:03d}/{call_id}'
        prior = state.row(key)
        if prior is not None and prior['ref'] == recovery_ref:
            return state
        rows = {} if search_delta is None else dict(search_delta['rows'])
        rows[key] = dict(ref=recovery_ref)
        _, state = append(root, state, kind='execution_recovery',
            fields=dict(step=step, call_id=call_id, recovery_ref=recovery_ref),
            updates={} if search_delta is None else dict(search=search_delta['summary'],search_ref=search_ref), rows=rows,
            control_files=control_files)
        return state


def accept_audit(root, *, step, operation_id, path, intent):
    """Accept this audit/retry intent and its sole active pointer before effects."""
    from . import budget
    root, path = Path(root), Path(path)
    relative = path.relative_to(root).as_posix()
    expected = (f'native-evidence/{step:03d}/candidate-audit/intent.json',
        f'host-delivery/audit-recovery/{operation_id}/intent.json')
    if relative not in expected or intent.get('operation_id') != operation_id:
        raise ValueError('Audit acceptance lost its exact original declaration')
    pointer = dict(step=step, operation_id=operation_id)
    with budget._ledger_writer(root) as base:
        state = recover(root, load(root, base=base))
        if state['active_audit'] is not None and state['active_audit'] != pointer:
            raise ValueError('Another accepted audit remains active')
        if path.exists():
            if state['active_audit'] != pointer or c.read(c.regular(path)) != intent:
                raise ValueError('Audit intent was not accepted with its active pointer')
            return state
        _, state = append(root, state, kind='audit_accept',
            fields=dict(step=step, operation_id=operation_id, intent_path=relative),
            updates=dict(active_audit=pointer, audit_count=state['audit_count']+1),
            control_files={relative: intent})
        return state


def finish_audit(root, operation_id):
    """Release only an exact original whose audit and retry cleanup are known."""
    from . import budget, candidate_audit, audit_recovery
    root = Path(root)
    with budget._ledger_writer(root) as base:
        state = recover(root, load(root, base=base))
        pointer = state['active_audit']
        if pointer is None or pointer['operation_id'] != operation_id:
            return state
        path = candidate_audit._directory(root, pointer['step'])/'intent.json'
        if path.exists():
            intent = c.read(c.regular(path))
            if intent['operation_id'] == operation_id:
                value = candidate_audit._outcome(root, pointer['step'])
                if value is None or not value['result']['cleanup_confirmed']:
                    return state
        retry = audit_recovery._fact(root, operation_id)
        if retry is not None and (retry[1] is None or not retry[1]['cleanup_confirmed']):
            return state
        _, state = append(root, state, kind='audit_complete',
            fields=dict(operation_id=operation_id), updates=dict(active_audit=None))
        return state


def accept_preparation_failure(root, value):
    """Keep the existing preparation count, last original and first unknown cleanup."""
    from . import budget
    root = Path(root)
    with budget._ledger_writer(root) as base:
        state = recover(root, load(root, base=base))
        prior = state['delivery_preparation']
        count = prior['count']+1
        relative = f'host-delivery/preparation/{count:03d}.json'
        ref = dict(path=relative)
        summary = dict(count=count, last_ref=ref,
            unresolved_ref=prior['unresolved_ref'] or (None if value['cleanup_confirmed'] else ref))
        _, state = append(root, state, kind='delivery_preparation',
            fields=dict(preparation_ref=ref), updates=dict(delivery_preparation=summary),
            control_files={relative: value})
        return state


def accept_configuration(root, reference, row):
    from . import budget
    with budget._ledger_writer(Path(root)) as base:
        state = recover(root, load(root, base=base))
        current = state['configuration']
        if current is not None and current['revision'] == row['revision']:
            if current['ref'] != reference:
                raise ValueError('Accepted configuration changed its exact original')
            return state
        if current is not None and current['revision'] >= row['revision']:
            raise ValueError('Configuration acceptance cannot move backwards')
        _, state = append(root, state, kind='configuration_accept', fields=dict(revision=row['revision']),
            updates=dict(configuration=dict(revision=row['revision'], ref=reference)),
            rows={f'configuration/{row["revision"]:06d}': dict(ref=reference)})
        return state


def accept_configuration_binding(root, step, binding):
    return accept_configuration_binding_from_base(root, step, binding, base=c.settings(root))


def accept_configuration_binding_from_base(root, step, binding, *, base):
    from . import budget
    with budget._ledger_writer_from_base(Path(root), base=base):
        state = recover(root, load(root, base=base))
        existing = state.row(f'configuration-binding/{step:06d}')
        if existing is not None:
            if existing != binding:
                raise ValueError('Accepted configuration binding changed')
            return state
        revision = (binding['configuration_revision'] or {}).get('revision', 0)
        _, state = append(root, state, kind='configuration_bind', fields=dict(step=step),
            updates=dict(applied_configuration=max(state['applied_configuration'], revision)),
            rows={f'configuration-binding/{step:06d}': binding},
            control_files={f'host-control/configuration/bindings/{step:06d}.json': binding})
        return state


def publish_scientific_seal(root, step, *, prefix, role, result_ref, search_ref, search_delta=None):
    from . import budget
    root = Path(root)
    with budget._ledger_writer(root) as base:
        state = recover(root, load(root, base=base))
        job_key = f'job/{step:03d}'
        job = state.row(job_key)
        if job is None or job['role'] != role:
            raise ValueError('Scientific seal has no exact accepted job')
        if job['status'] == 'sealed':
            if job['result_ref'] != result_ref or job['output_search_ref'] != search_ref:
                raise ValueError('Scientific seal changed its accepted result')
            return state
        if (state.current_job is None or state.current_job['step'] != step
                or state['current_native'] is not None or job['status'] != 'terminal'):
            raise ValueError('Scientific seal no longer owns the accepted current slot')
        event_ref = dict(path=str(Path(state['logical_root'])/'control-ledger'/f'{state["seq"]+1:06d}.json'))
        previous_job = job
        job = dict(previous_job, status='sealed', result_ref=result_ref, role_usage=state['role_usage'],
            output_event_ref=event_ref,
            output_search_ref=copy.deepcopy(search_ref), search_ref=copy.deepcopy(search_ref))
        rows = {job_key: job}
        updates = dict(current_job=job, prefix=prefix, search_ref=copy.deepcopy(search_ref))
        if search_delta is not None:
            updates['search'] = copy.deepcopy(search_delta['summary'])
            rows.update(search_delta['rows'])
        stamp = budget.clock_stamp()
        index_updates = {key: dict(previous_seq=state['seq'],
            previous=previous_job if key == job_key else state.row(key), current=value)
            for key, value in rows.items()}
        _, state = _append_prepared(root, state, kind='scientific_seal', stamp=stamp,
            fields=dict(step=step, result_ref=result_ref), updates=updates, index_updates=index_updates)
        return state
