"""Sealed A assessments and immutable correction/ack facts, without a scheduler."""
from __future__ import annotations

from . import budget, execution

import json
import re
from pathlib import Path

from . import contracts as c, history

DEFAULT_POLICY = dict(review_after=3, pause_after=2)
REASONS = dict(no_progress='no_progress_unresolved', unknown='assessment_gap')
MAX_RECORD_BYTES = 16_000


def policy(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY)
            or any(type(item) is not int or item <= 0 for item in value.values())):
        raise ValueError('Correction policy requires two positive integer thresholds')
    return dict(value)


def schema(protocol=None):
    return dict(type=['object','null'],properties=dict(verdict=dict(type='string',enum=['progress','no_progress','unknown'])),
        required=['verdict'],additionalProperties=False,
        description='Scientific progress judgment for the exact assessed final stage; null means unknown. Explain support in ordinary science; control binds originals and counts, not scientific correctness.')


def _proofs(root, job, assessment):
    if assessment is None:
        return set()
    if (not isinstance(assessment, dict) or set(assessment) != {'verdict'}
            or not isinstance(assessment['verdict'],str)
            or assessment['verdict'] not in {'progress', 'no_progress', 'unknown'}):
        return set()  # Unparseable scientific advice is not progress evidence.
    if assessment['verdict'] != 'progress':
        return set()
    sources = history.direct_sources(root, job)
    current = next((row for row in sources if row[0]['step'] == job['step'] - 1), None)
    if current is None:
        return set()
    source, payload, seal = current
    if source['role'] == 'A' and payload.get('next_action') == 'audit':
        return _proofs(root, source, assessment)
    if source['role'] == 'W' or (source['role'] == 'H' and payload.get('next_action') == 'analyze' and not source.get('author_correction')):
        return {seal['raw_ref']['path']}
    if source['role'] == 'E':
        target = source.get('evidence_return') or {}
        requester = next((row for row in sources if row[0]['step'] == target.get('step')), None)
        e_sources = history.direct_sources(root, source)
        if (target.get('role') == 'A' and requester is not None and requester[0]['role'] == 'A'
                and requester[1].get('next_action') == 'extract'
                and any(row[0]['step'] == target['step'] for row in e_sources)):
            return {seal['raw_ref']['path']}
    return set()


def validate_payload(root, job, payload):
    if job.get('purpose') in {'closeout','formal_review'}:
        return
    if job.get('handoff_protocol', 1) >= 6 and job['role'] == 'A' and c.settings(root, job).get('correction_policy') is not None:
        _proofs(root, job, c.scientific_field(job,payload,'progress_assessment'))


def _records(root):
    identity = c.record_ref(root / 'settings.json')
    events, parent, seen = [], None, set()
    for seq, path in enumerate(sorted((root / 'correction-ledger').glob('*.json')), 1):
        raw = c.regular(path, limit=MAX_RECORD_BYTES).read_bytes()
        event = json.loads(raw)
        if (not isinstance(event, dict) or type(event.get('seq')) is not int or event.get('seq') != seq or path.name != f'{seq:06d}.json'
                or event.get('parent_ref') != parent or event.get('settings_ref') != identity
                or event.get('kind') not in {'trigger', 'ack'}):
            raise ValueError('Correction fact identity or sequence changed')
        common = {'seq', 'parent_ref', 'settings_ref', 'kind'}
        fields = {'trigger'} if event['kind'] == 'trigger' else {
            'trigger_id', 'cutoff', 'cutoff_seal_ref', 'authorization_note'}
        if set(event) != common | fields:
            raise ValueError('Correction fact fields changed')
        if event['kind'] == 'trigger':
            trigger = event['trigger']
            if (not isinstance(trigger, dict) or set(trigger) != {
                    'step', 'seal_ref', 'raw_ref', 'reason', 'level', 'trigger_id'}
                    or type(trigger['step']) is not int or trigger['step'] <= 0
                    or trigger['reason'] not in REASONS.values() or trigger['level'] not in {'review', 'pause'}
                    or any(not isinstance(trigger[key], dict) or 'path' not in trigger[key] for key in ('seal_ref', 'raw_ref'))
                    or not isinstance(trigger['trigger_id'], str)):
                raise ValueError('Correction trigger fields changed')
            if trigger['trigger_id'] in seen:
                raise ValueError('Correction trigger is duplicated')
            seen.add(trigger['trigger_id'])
        events.append(event)
        parent = c.record_ref(path)
    return events, parent


def _append(root, events, parent, **fields):
    # Caller owns the same host writer lock as dispatch and continue. There is
    # no independent correction writer or mutable episode state.
    from .protocol import require_current
    require_current(root)
    event = dict(seq=len(events) + 1, parent_ref=parent,
                 settings_ref=c.record_ref(root / 'settings.json'), **fields)
    serialized = json.dumps(event, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if len(serialized.encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('Correction fact exceeds its serialized UTF-8 byte bound')
    c.write(root / 'correction-ledger' / f'{event["seq"]:06d}.json', event)
    return event


def _inherited_events(root):
    from . import originals
    manifest = originals._manifest(root)
    refs = [] if manifest is None else manifest['cut'].get('correction_events', [])
    values, chains = [], {}
    for ref in refs:
        identity = ref.get('origin_identity')
        if identity is None:
            raise ValueError('Inherited correction fact lacks its exact instance identity')
        key = originals.identity_key(identity)
        path = Path(ref['path'])
        previous, sequence = chains.get(key, (None, 0))
        if path.as_posix() != f'correction-ledger/{sequence + 1:06d}.json':
            raise ValueError('Inherited correction fact sequence changed')
        actual = originals.resolve_root_ref(root, ref)
        event = c.read(actual)
        if (event.get('seq') != sequence + 1 or event.get('parent_ref') != previous
                or event.get('settings_ref') != identity['settings_ref']
                or event.get('kind') not in {'trigger', 'ack'}):
            raise ValueError('Inherited correction fact identity changed')
        chains[key] = (c.record_ref(actual), sequence + 1)
        values.append(event)
    return values


def freeze_prefix(root):
    """Freeze the actual pre-task control prefix, never reconstruct a later ack."""
    from . import originals
    manifest = originals._manifest(root)
    inherited = [] if manifest is None else list(manifest['cut'].get('correction_events', []))
    _inherited_events(root)
    events, _ = _records(root)
    return inherited + [dict(path=f'correction-ledger/{event["seq"]:06d}.json',
        size_bytes=(root / 'correction-ledger' / f'{event["seq"]:06d}.json').stat().st_size) for event in events]


def _view_events(root):
    return _inherited_events(root) + _records(root)[0]


def _trigger(root, step, seal, reason, level):
    from . import originals
    source = originals.node(root, step)
    value = dict(step=step, seal_ref=history.source_seal_ref(source),
                 raw_ref=seal['raw_ref'], reason=reason, level=level)
    identity = dict(settings_ref=source.origin_identity['settings_ref'], **value)
    value['trigger_id'] = json.dumps(identity, sort_keys=True, separators=(',', ':'))
    return value


def project(root, before_step=None):
    from . import configuration_updates
    settings = (configuration_updates.for_new_job(root, before_step) if before_step is not None
                else configuration_updates.current(root))
    limits = settings.get('correction_policy')
    enabled = limits is not None
    if not enabled and not any((root / 'correction-ledger').glob('*.json')) and not _inherited_events(root):
        return dict(enabled=False, counters=dict(no_progress=0, unknown=0), current_pause=None,
                    review_request=None, triggers=[], eligible=[], permit=None)
    if enabled:
        limits = policy(limits)
    # Disabling future assessments cannot revoke an already published pause.
    # Replay each original policy so its exact acknowledgment remains required.
    events = _view_events(root)
    published = {event['trigger']['trigger_id']: event['trigger'] for event in events if event['kind'] == 'trigger'}
    acks = {}
    for event in events:
        if event['kind'] == 'ack':
            key = event.get('trigger_id')
            if (key not in published or key in acks or not isinstance(event.get('authorization_note'), str)
                    or not event['authorization_note'].strip() or type(event.get('cutoff')) is not int):
                raise ValueError('Correction acknowledgment binding changed')
            acks[key] = event
    counters = dict(no_progress=0, unknown=0)
    reviews = dict(no_progress=None, unknown=None)
    used, eligible, triggers = set(), [], []
    pause = permit = None
    before_step = before_step or budget.historical_step_limit(root) + 1
    from . import originals
    for source in originals.source_sequence(root, before_step):
        step = source.step
        record = originals.source_record(source, 'result-identity.json').parent
        job, payload, seal, _ = history._source(root, step)
        outcome = c.native_outcome(originals.source_record(source, 'native-outcome.json').parent,
            original_record=Path(seal['raw_ref']['path']).parent,
            output_path=originals.resolve_ref(source, seal['raw_ref']))
        intent = c.read(originals.source_record(source, 'native-intent.json'))
        if (intent.get('task_ref') != seal['job_ref']
                or type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
                or outcome.get('stop_reason')
                or outcome.get('usage', {}).get('blocking_unknown_steps', outcome.get('usage', {}).get('unknown_steps')) != []
                    and not budget.native_completion_known(outcome, originals.source_settings(source))
                or outcome.get('raw_ref') != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}):
            raise ValueError('Correction replay requires verified original terminal nodes')
        if c.read(originals.source_record(source, 'result.json')) != c.result_projection(job, payload):
            raise ValueError('Correction result differs from original scientific fields')
        request = job.get('correction_request')
        if (job['role'] == 'H' and request is not None
                and payload.get('next_action') in {'experiment', 'analyze'}
                and seal.get('control_outcome', {}).get('search_status') != 'unresolved'):
            for review in reviews.values():
                if review is not None and review['trigger']['trigger_id'] == request['trigger_id']:
                    review['response_h'] = step
        if (job['role'] != 'A' or job.get('purpose') in {'closeout','formal_review'} or job.get('handoff_protocol', 1) < 6
                or job.get('author_correction') is not None
                or seal.get('control_outcome', {}).get('decision_status') == 'unresolved'
                or seal.get('control_outcome', {}).get('portfolio_status') == 'unresolved'
                or payload['next_action'] in {'extract', 'wait', 'audit','formal_review'}):
            continue
        seal_digest = c.record_ref(record / 'result-identity.json')
        eligible.append(dict(step=step, seal_ref=seal_digest))
        if 'progress_assessment' not in payload:
            continue
        sticky, consumed_pause = False, None
        if pause is not None:
            ack = acks.get(pause['trigger_id'])
            if ack is not None and ack['cutoff'] < step:
                permit = ack
                consumed_pause = pause
                pause = None  # The bounded permit is spent by this exact A.
            else:
                # A result arriving after an already published pause cannot
                # automatically erase it. Original facts still remain sealed.
                sticky = pause['trigger_id'] in published
        historical_limits = c.settings(root, job).get('correction_policy')
        if historical_limits is None:
            continue
        limits = policy(historical_limits)
        assessment = c.scientific_field(job,payload,'progress_assessment')
        proof = _proofs(root, job, assessment)
        verdict = (assessment or {}).get('verdict', 'unknown')
        if verdict == 'progress' and not proof - used:
            verdict = 'unknown'
        if verdict == 'progress':
            used.update(proof)
            # Fulfil the first H request before science can end that stage.
            if not sticky and all(review is None or review['response_h'] is not None for review in reviews.values()):
                counters = dict(no_progress=0, unknown=0)
                reviews = dict(no_progress=None, unknown=None)
                pause = None
        else:
            if verdict == 'no_progress':
                counters['unknown'] = 0
                if reviews['unknown'] is not None and reviews['unknown']['response_h'] is not None:
                    reviews['unknown'] = None
                counters['no_progress'] += 1
            else:
                counters['unknown'] += 1
            review = reviews[verdict]
            if review is None and counters[verdict] >= limits['review_after']:
                trigger = _trigger(root, step, seal, REASONS[verdict], 'review')
                triggers.append(trigger)
                reviews[verdict] = dict(trigger=trigger, response_h=None, after_h=0)
            elif review is not None and review['response_h'] is not None:
                review['after_h'] += 1
                if review['after_h'] >= limits['pause_after'] and not sticky:
                    pause = _trigger(root, step, seal, REASONS[verdict], 'pause')
                    triggers.append(pause)
        # One eligible A spends the permit even when its verdict changes
        # buckets. Unknown cannot erase an already unresolved no-progress
        # issue; genuine progress above can clear the original reviews.
        if consumed_pause is not None and pause is None:
            unresolved = [kind for kind, review in reviews.items() if review is not None
                          and review['response_h'] is not None and review['after_h'] >= limits['pause_after']]
            if unresolved:
                reason = next((REASONS[kind] for kind in unresolved
                               if REASONS[kind] == consumed_pause['reason']), REASONS[unresolved[0]])
                pause = _trigger(root, step, seal, reason, 'pause')
                triggers.append(pause)
        permit = None
    expected = {item['trigger_id']: item for item in triggers}
    for key, value in published.items():
        if value['step'] < before_step and expected.get(key) != value:
            raise ValueError('Published correction trigger differs from sealed facts')
    known_eligible = {item['step']: item['seal_ref'] for item in eligible}
    for ack in acks.values():
        if ack['cutoff'] < before_step and (
                known_eligible.get(ack['cutoff']) != ack.get('cutoff_seal_ref')
                or ack['cutoff'] < published[ack['trigger_id']]['step']
                or published[ack['trigger_id']]['level'] != 'pause'):
            raise ValueError('Correction acknowledgment cutoff changed')
    if pause is not None:
        ack = acks.get(pause['trigger_id'])
        if ack is not None and ack['cutoff'] in known_eligible:
            permit = ack
        pause = dict(**pause, published=pause['trigger_id'] in published)
    pending = [value['trigger'] for value in reviews.values()
               if value is not None and value['response_h'] is None]
    if not enabled:
        triggers = [item for item in triggers if item['trigger_id'] in published]
        if pause is not None and not pause['published']:
            pause = None
    return dict(enabled=enabled, counters=counters, current_pause=pause, permit=permit,
                review_request=max(pending, key=lambda item: item['step']) if enabled and pending else None,
                triggers=triggers, eligible=eligible)


def publish(root, cutoff):
    from .protocol import require_current
    require_current(root)
    events, parent = _records(root)
    known = {event['trigger']['trigger_id'] for event in events if event['kind'] == 'trigger'}
    known.update(event['trigger']['trigger_id'] for event in _inherited_events(root) if event['kind'] == 'trigger')
    while True:
        state = project(root, cutoff + 1)
        trigger = next((item for item in state['triggers'] if item['trigger_id'] not in known), None)
        if trigger is None:
            return state
        event = _append(root, events, parent, kind='trigger', trigger=trigger)
        events.append(event)
        parent = c.record_ref(root / 'correction-ledger' / f'{event["seq"]:06d}.json')
        known.add(trigger['trigger_id'])
        # Publish in original cutoff order and replay after each fact: the
        # first pause becomes sticky before any late accepted A is processed.


def effective_route(root, step, result, job, route):
    """Historical cutoff protects retained jobs from later progress or ack."""
    role, parents, instruction, evidence_return, current_h, current_w = route
    request = None
    state = project(root, step + 1)
    review = state['review_request']
    if job['role'] == 'A' and review is not None and result['next_action'] in {'continue_w', 'revise_h'}:
        role = 'H'
        request = dict(trigger_id=review['trigger_id'])
        instruction += f'\nControl requires H to reconsider {review["reason"]} after sealed A {review["step"]}; '
        instruction += 'own the scientific response; the prior A conclusion is unchanged.'
    elif job['role'] == 'E' and role == 'H':
        # Base E routing clears evidence_return; use its original requester.
        from . import originals
        requester = originals.source_job(originals.node(root, job['evidence_return']['step']))
        request = requester.get('correction_request')
    return role, parents, instruction, evidence_return, current_h, current_w, request


def validate_job(root, job):
    request = job.get('correction_request')
    if request is None:
        return
    if (job.get('handoff_protocol', 1) < 6 or job['role'] != 'H'
            or not isinstance(request, dict) or set(request) != {'trigger_id'}):
        raise ValueError('Correction H request binding is invalid')
    events = _view_events(root)
    trigger = next((event['trigger'] for event in events if event['kind'] == 'trigger'
                    and event['trigger']['trigger_id'] == request['trigger_id']), None)
    if trigger is None or trigger['level'] != 'review' or trigger['step'] >= job['step']:
        raise ValueError('Correction H request lacks its original review trigger')
    parents = [int(row['path'].split('/')[2]) for row in job['direct_refs']]
    if trigger['step'] in parents:
        return
    for parent in parents:
        previous = c.read(root / 'jobs' / f'{parent:03d}.json')
        if (job.get('handoff_protocol', 1) >= 8 and job.get('author_correction') is not None
                and previous['role'] == 'H' and previous.get('correction_request') == request):
            validate_job(root, previous)
            return
        if previous['role'] == 'E' and previous.get('evidence_return', {}).get('role') == 'H':
            requester = c.read(root / 'jobs' / f'{previous["evidence_return"]["step"]:03d}.json')
            if requester.get('correction_request') == request:
                return
    raise ValueError('Correction H response has no exact trigger/requester lineage')


def gate(root, cutoff):
    state = publish(root, cutoff)
    if state['current_pause'] is not None and state['permit'] is None:
        return state['current_pause']
    return None


def acknowledge(root, trigger_id, authorization_note):
    """Caller holds the controller writer lock; this note is not authentication."""
    from .protocol import require_current
    require_current(root)
    from . import budget
    if not isinstance(authorization_note, str) or not authorization_note.strip():
        raise ValueError('Correction acknowledgment needs an explicit operator decision')
    if budget.execution_blocked(root):
        raise RuntimeError('Correction acknowledgment requires no active or unknown original')
    state = project(root)
    pause = state['current_pause']
    if pause is None or not pause['published'] or pause['trigger_id'] != trigger_id:
        raise ValueError('Correction acknowledgment requires the exact current published pause trigger')
    events, parent = _records(root)
    if state['permit'] is not None:
        return state['permit']  # Retry cannot extend the original cutoff.
    cutoff = state['eligible'][-1]
    return _append(root, events, parent, kind='ack', trigger_id=trigger_id,
                   cutoff=cutoff['step'], cutoff_seal_ref=cutoff['seal_ref'],
                   authorization_note=authorization_note)
